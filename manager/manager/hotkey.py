"""全局热键：把 "Ctrl+Alt+0" 这类写法变成全局监听（一个名字一个组合键）。

底层统一走 pynput（根目录 AGENTS.md §1：全局热键只能用 pynput，不手写 RegisterHotKey、
不用 keyboard 库、不上 AutoHotkey）。做法是一个 keyboard.Listener 收下全机的按下 / 抬起，
组合键自己判定，为的是几条实测过的坑：

  * 修饰键必须刚好等于配置里写的那几个——多按一个 Shift 就不触发（和以前一致）；
  * 触发键按住不放只触发一次，松开后才重新武装（等价原 RegisterHotKey 的 MOD_NOREPEAT，
    否则按定不放会连发一串）；
  * 钩子回调里只改状态、把活丢给新线程。回调一慢（超过 LowLevelHooksTimeout，Windows
    默认 300ms）就会被系统静默摘钩，表现是「按了没反应、日志里还什么都没有」；
  * 监听线程万一挂了或被系统摘掉，看门狗会重新拉起它，不用重启软件。

Windows 上 Ctrl / Alt 按住时拿到的字符会变成控制字符（Ctrl+E 拿到的是 0x05），
所以字母优先从控制字符反推、数字和标点用虚拟键码认，见 _key_of()。
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass

from . import config as cfgmod

try:  # 没有图形环境的机器上，pynput 在导入阶段就会抛
    from pynput import keyboard
except Exception as e:  # pragma: no cover
    keyboard = None
    _IMPORT_ERROR: Exception | None = e
else:
    _IMPORT_ERROR = None

MOD_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "shift": "shift",
    "win": "win", "meta": "win", "super": "win", "cmd": "win",
}

KEY_ALIASES = {
    "escape": "esc", "return": "enter", "del": "delete",
    "ins": "insert", "pgup": "pageup", "pgdn": "pagedown",
}

# 规范键名 → pynput 的 Key 属性名
NAMED_KEYS = {
    "esc": "esc", "enter": "enter", "space": "space", "tab": "tab",
    "backspace": "backspace", "delete": "delete", "insert": "insert",
    "home": "home", "end": "end", "pageup": "page_up", "pagedown": "page_down",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "capslock": "caps_lock", "printscreen": "print_screen", "pause": "pause",
}

MOD_ATTRS = {
    "ctrl": ("ctrl", "ctrl_l", "ctrl_r"),
    "alt": ("alt", "alt_l", "alt_gr", "alt_r"),
    "shift": ("shift", "shift_l", "shift_r"),
    "win": ("cmd", "cmd_l", "cmd_r"),
}

# 上档符号 → 它的基础键："Ctrl+Alt+>" 实际按的是 Ctrl+Alt+Shift+.
SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]",
    "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}

_FUNC_KEYS = {"f%d" % n for n in range(1, 25)}

# Windows 虚拟键码 → 规范键名。Ctrl / Alt 按住时 pynput 给的 char 会变成控制字符
# （Ctrl+E 是 "\x05"），字母还能从控制字符反推，数字和标点只能靠 vk 认。
_VK_NAMES: dict[int, str] = {}

if sys.platform == "win32":
    _VK_NAMES.update({0x30 + i: chr(0x30 + i) for i in range(10)})  # 主键盘数字
    _VK_NAMES.update({0x60 + i: chr(0x30 + i) for i in range(10)})  # 小键盘数字
    _VK_NAMES.update({0x41 + i: chr(0x61 + i) for i in range(26)})  # 字母
    _VK_NAMES.update({
        0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".",
        0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
    })

# pynput 的按键对象 → 规范名；两边各自建一张反查表，事件里只做字典查询
_REVERSE: dict[object, str] = {}
_MOD_REVERSE: dict[object, str] = {}

if keyboard is not None:
    for _name, _attr in NAMED_KEYS.items():
        _key = getattr(keyboard.Key, _attr, None)
        if _key is not None:
            _REVERSE[_key] = _name
    for _name in _FUNC_KEYS:
        _key = getattr(keyboard.Key, _name, None)
        if _key is not None:
            _REVERSE[_key] = _name
    for _mod, _attrs in MOD_ATTRS.items():
        for _attr in _attrs:
            _key = getattr(keyboard.Key, _attr, None)
            if _key is not None:
                _MOD_REVERSE[_key] = _mod


def _mod_of(key) -> str:
    """按下的是修饰键就返回规范名（ctrl / alt / shift / win），否则空串。"""
    if keyboard is None or not isinstance(key, keyboard.Key):
        return ""
    return _MOD_REVERSE.get(key, "")


def _key_of(key) -> str:
    """按下的键 → 规范键名；修饰键、认不出的键返回空串。"""
    if keyboard is None or key is None:
        return ""

    if isinstance(key, keyboard.Key):
        return _REVERSE.get(key, "")

    char = getattr(key, "char", None) or ""
    if len(char) == 1:
        if char.isprintable():
            return SHIFTED.get(char, char).lower()
        code = ord(char)
        if 1 <= code <= 26:  # Ctrl+字母 在这里是 \x01..\x1a
            return chr(code + 0x60)

    vk = getattr(key, "vk", None)
    if vk:
        return _VK_NAMES.get(vk, "")
    return ""


@dataclass(frozen=True)
class Accel:
    """一个组合键：要按住的修饰键（规范名）+ 触发键（规范名）。"""

    mods: frozenset[str]
    key: str

    def __str__(self) -> str:
        head = [m for m in ("ctrl", "alt", "shift", "win") if m in self.mods]
        return "+".join(head + [self.key])


def _known_key(key: str) -> bool:
    return len(key) == 1 or key in NAMED_KEYS or key in _FUNC_KEYS


def parse(spec: str | None) -> Accel | None:
    """把 "Ctrl+Alt+M" 解析成 Accel；认不出来返回 None。"""
    if not spec or not spec.strip():
        return None

    mods: set[str] = set()
    key = ""
    for raw in spec.split("+"):
        token = raw.strip().lower()
        if not token:
            continue
        if token in MOD_ALIASES:
            mods.add(MOD_ALIASES[token])
            continue
        if token in SHIFTED:  # ">" 其实要按 Shift+.
            mods.add("shift")
            token = SHIFTED[token]
        token = KEY_ALIASES.get(token, token)
        key = token

    if not _known_key(key):
        return None
    return Accel(frozenset(mods), key)


class _Listener:
    """pynput 全局监听 + 组合键判定，命中后调 on_fire(名字)。

    一个实例只管一件事：把「谁被按全了」算出来。打开 / 收起窗口那种重活
    由回调那边另开线程做，钩子线程里只改几个集合。
    """

    def __init__(self, on_fire) -> None:
        self._on_fire = on_fire
        self._combos: dict[str, Accel] = {}
        self._labels: list[str] = []
        self._mods: set[str] = set()
        self._keys: set[str] = set()
        self._fired: set[str] = set()
        self._lock = threading.Lock()
        self._listener = None
        self._stop = threading.Event()

    # ---------------------------------------------------------------- 生命周期

    def start(self, combos: dict[str, Accel], labels: list[str]) -> None:
        self.stop()
        self._combos = dict(combos)
        self._labels = [s for s in labels if s]
        if not self._combos:
            return
        if keyboard is None:
            cfgmod.log(f"[hotkey] pynput 用不了：{_IMPORT_ERROR!r}")
            return
        stop = threading.Event()
        self._stop = stop
        if not self._spawn(stop):
            return
        cfgmod.log("[hotkey] 已挂上 " + ", ".join(self._labels))
        threading.Thread(
            target=self._watch, args=(stop,), name="hotkey-guard", daemon=True
        ).start()

    def stop(self) -> None:
        self._stop.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        with self._lock:
            self._mods.clear()
            self._keys.clear()
            self._fired.clear()

    def _spawn(self, stop: threading.Event) -> bool:
        if keyboard is None or stop.is_set():
            return False
        try:
            listener = keyboard.Listener(
                on_press=self._on_press, on_release=self._on_release
            )
            listener.start()
        except Exception as e:  # 没权限、没有图形会话等等
            cfgmod.log(f"[hotkey] 键盘监听启动失败：{e!r}")
            return False
        if stop.is_set():  # 起来的一瞬间正好被叫停
            try:
                listener.stop()
            except Exception:
                pass
            return False
        self._listener = listener
        return True

    def _watch(self, stop: threading.Event) -> None:
        """看门狗：钩子线程被系统摘掉或异常退出时，重新挂一个。"""
        while not stop.wait(2.0):
            if stop.is_set():
                return
            listener = self._listener
            if listener is not None and listener.is_alive():
                continue
            if listener is not None:
                cfgmod.log("[hotkey] 监听线程不在了，重新挂上")
            if not self._spawn(stop):
                stop.wait(3.0)

    # ---------------------------------------------------------------- 按键

    def _on_press(self, key) -> None:
        # 跑在系统钩子线程里：只改状态，不做任何会阻塞的事
        mod = _mod_of(key)
        token = "" if mod else _key_of(key)
        hits: list[str] = []
        with self._lock:
            if mod:
                if mod in self._mods:
                    return  # 按住不放的重复事件
                self._mods.add(mod)
            else:
                if not token or token in self._keys:
                    return
                self._keys.add(token)

            now = frozenset(self._mods)
            for name, accel in self._combos.items():
                if name in self._fired or accel.key not in self._keys:
                    continue
                if accel.mods == now:
                    self._fired.add(name)
                    hits.append(name)

        for name in hits:
            threading.Thread(
                target=self._run, args=(name,), name=f"hotkey-{name}", daemon=True
            ).start()

    def _on_release(self, key) -> None:
        mod = _mod_of(key)
        token = "" if mod else _key_of(key)
        with self._lock:
            if mod:
                self._mods.discard(mod)
            elif token:
                self._keys.discard(token)

            # 触发键或它要的修饰键松开了，这个组合键重新武装
            for name, accel in self._combos.items():
                if name not in self._fired:
                    continue
                if (mod and mod in accel.mods) or token == accel.key:
                    self._fired.discard(name)

    def _run(self, name: str) -> None:
        try:
            self._on_fire(name)
        except Exception as e:
            cfgmod.log(f"[hotkey] {name} 回调失败：{e!r}")


class HotkeyManager:
    """一组全局热键：先 bind(名字, 回调)，再用 {名字: "Ctrl+Alt+X"} 启动。

    一个名字对一个组合键，彼此互不影响（各自严格校验修饰键），
    这样 Manager 的唤出键和应用启动器的 Alt+Space 可以同时挂在同一个监听器上。
    """

    def __init__(self) -> None:
        self._handlers: dict[str, object] = {}
        self._specs: dict[str, str] = {}
        self._hub = _Listener(self._fire)

    def bind(self, name: str, handler) -> None:
        self._handlers[name] = handler

    # ---------------------------------------------------------------- 生命周期

    def start(self, specs: dict[str, str]) -> None:
        self._specs = {
            k: v for k, v in (specs or {}).items() if isinstance(v, str) and v.strip()
        }
        combos: dict[str, Accel] = {}
        labels: list[str] = []
        for name, spec in self._specs.items():
            combo = parse(spec)
            if combo is None:
                cfgmod.log(f"[hotkey] 看不懂 {name} 的写法：{spec!r}")
                continue
            combos[name] = combo
            labels.append(spec)
        if not combos:
            cfgmod.log("[hotkey] 没有可用的全局热键")
            self._hub.stop()
            return
        self._hub.start(combos, labels)

    def update(self, specs: dict[str, str]) -> None:
        if (specs or {}) == self._specs:
            return
        self.stop()
        self.start(specs)

    def stop(self) -> None:
        self._hub.stop()

    # ---------------------------------------------------------------- 触发

    def _fire(self, name: str = "") -> None:
        # _Listener 已经在新线程里调用，这里直接干活：执行这个名字绑定的回调
        handler = self._handlers.get(name)
        if handler is not None:
            handler()  # type: ignore[operator]
