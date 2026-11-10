"""用纯 ctypes 调 IShellLink 生成 .lnk —— 不依赖 PowerShell，也不依赖 pywin32。

为什么不用 PowerShell 的 WScript.Shell：
- 从 pythonw.exe（无控制台）里起 powershell 有概率起不来，而且进程照样返回 0，
  于是「标记文件写了、快捷方式没生成」这种假成功就出现了，之后也不会重试；
- 安全软件拦 PowerShell 时同样是静默失败；
- 起一次 powershell 要 300~800ms，COM 调用是毫秒级，还是同步的。
"""

from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes
from pathlib import Path

CLSID_ShellLink = "00021401-0000-0000-C000-000000000046"
IID_IShellLinkW = "000214F9-0000-0000-C000-000000000046"
IID_IPersistFile = "0000010B-0000-0000-C000-000000000046"

CLSCTX_INPROC_SERVER = 0x1
COINIT_APARTMENTTHREADED = 0x2

# IShellLinkW 虚表下标（IUnknown 占 0-2）
_SL_GET_PATH = 3
_SL_SET_DESCRIPTION = 7
_SL_SET_WORKING_DIR = 9
_SL_GET_ARGUMENTS = 10
_SL_SET_ARGUMENTS = 11
_SL_SET_SHOW_CMD = 15
_SL_SET_ICON_LOCATION = 17
_SL_SET_PATH = 20
# IPersistFile
_PF_SAVE = 6
_PF_LOAD = 5
_RELEASE = 2
_QUERY_INTERFACE = 0

_ole32 = ctypes.WinDLL("ole32", use_last_error=True)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, text: str) -> "_GUID":
        raw = uuid.UUID(text).bytes_le
        g = cls()
        g.Data1 = int.from_bytes(raw[0:4], "little")
        g.Data2 = int.from_bytes(raw[4:6], "little")
        g.Data3 = int.from_bytes(raw[6:8], "little")
        for i in range(8):
            g.Data4[i] = raw[8 + i]
        return g


def _call(ptr: ctypes.c_void_p, index: int, restype, argtypes, *args):
    """按虚表下标调 COM 方法。"""
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtable[index])(ptr, *args)


def _hr(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} 失败，HRESULT=0x{hr & 0xFFFFFFFF:08X}")


def create(
    lnk: str | Path,
    target: str,
    arguments: str = "",
    workdir: str = "",
    description: str = "",
    icon: str = "",
    icon_index: int = 0,
    show_cmd: int = 1,
) -> None:
    """生成（覆盖）一个 .lnk。失败抛 OSError。"""
    path = Path(lnk)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 已经初始化过（S_FALSE）或线程模型不同（RPC_E_CHANGED_MODE）都不影响后面用
    _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)

    clsid = _GUID.parse(CLSID_ShellLink)
    iid_link = _GUID.parse(IID_IShellLinkW)
    iid_file = _GUID.parse(IID_IPersistFile)

    link = ctypes.c_void_p()
    _ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _ole32.CoCreateInstance.restype = ctypes.c_long
    _hr(
        _ole32.CoCreateInstance(
            ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER,
            ctypes.byref(iid_link), ctypes.byref(link),
        ),
        "CoCreateInstance(ShellLink)",
    )

    persist = ctypes.c_void_p()
    try:
        _hr(
            _call(
                link, _QUERY_INTERFACE, ctypes.c_long,
                [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)],
                ctypes.byref(iid_file), ctypes.byref(persist),
            ),
            "QueryInterface(IPersistFile)",
        )

        _hr(_call(link, _SL_SET_PATH, ctypes.c_long, [ctypes.c_wchar_p], str(target)),
            "SetPath")
        if arguments:
            _hr(_call(link, _SL_SET_ARGUMENTS, ctypes.c_long, [ctypes.c_wchar_p], arguments),
                "SetArguments")
        if workdir:
            _hr(_call(link, _SL_SET_WORKING_DIR, ctypes.c_long, [ctypes.c_wchar_p], workdir),
                "SetWorkingDirectory")
        if description:
            _hr(_call(link, _SL_SET_DESCRIPTION, ctypes.c_long, [ctypes.c_wchar_p], description),
                "SetDescription")
        if icon:
            _hr(_call(link, _SL_SET_ICON_LOCATION, ctypes.c_long,
                      [ctypes.c_wchar_p, ctypes.c_int], str(icon), int(icon_index)),
                "SetIconLocation")
        _hr(_call(link, _SL_SET_SHOW_CMD, ctypes.c_long, [ctypes.c_int], int(show_cmd)),
            "SetShowCmd")

        _hr(_call(persist, _PF_SAVE, ctypes.c_long, [ctypes.c_wchar_p, wintypes.BOOL],
                  str(path), True),
            "IPersistFile::Save")
    finally:
        if persist:
            _call(persist, _RELEASE, ctypes.c_ulong, [])
        _call(link, _RELEASE, ctypes.c_ulong, [])


def read(lnk: str | Path) -> tuple[str, str]:
    """读出 .lnk 的 (目标, 参数)。读不出来就返回 ("", "")。"""
    _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    clsid = _GUID.parse(CLSID_ShellLink)
    iid_link = _GUID.parse(IID_IShellLinkW)
    iid_file = _GUID.parse(IID_IPersistFile)

    link = ctypes.c_void_p()
    _ole32.CoCreateInstance.restype = ctypes.c_long
    if _ole32.CoCreateInstance(
        ctypes.byref(clsid), None, CLSCTX_INPROC_SERVER,
        ctypes.byref(iid_link), ctypes.byref(link),
    ) < 0:
        return "", ""

    persist = ctypes.c_void_p()
    try:
        if _call(link, _QUERY_INTERFACE, ctypes.c_long,
                 [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)],
                 ctypes.byref(iid_file), ctypes.byref(persist)) < 0:
            return "", ""
        # IPersistFile::Load(pszFileName, dwMode=STGM_READ)
        if _call(persist, _PF_LOAD, ctypes.c_long, [ctypes.c_wchar_p, wintypes.DWORD],
                 str(lnk), 0) < 0:
            return "", ""

        target = ctypes.create_unicode_buffer(1024)
        if _call(link, _SL_GET_PATH, ctypes.c_long,
                 [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                 target, 1024, None, 0) < 0:
            return "", ""

        args = ctypes.create_unicode_buffer(1024)
        if _call(link, _SL_GET_ARGUMENTS, ctypes.c_long,
                 [ctypes.c_wchar_p, ctypes.c_int], args, 1024) < 0:
            args = ctypes.create_unicode_buffer("")
        return target.value, args.value
    finally:
        if persist:
            _call(persist, _RELEASE, ctypes.c_ulong, [])
        _call(link, _RELEASE, ctypes.c_ulong, [])


def read_target(lnk: str | Path) -> str:
    return read(lnk)[0]
