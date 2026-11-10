"""本工具自己的单实例和退出通道，不依赖 Manager。

Windows 沿用命名互斥体/事件；其它平台使用文件锁和本地 socket。
"""
from __future__ import annotations

import os
import threading

from . import config as cfgmod

_IDENTITY = 'PlugManager'
if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, args, result in (
        ('CreateMutexW', [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        ('OpenMutexW', [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        ('CreateEventW', [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        ('OpenEventW', [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        ('SetEvent', [wintypes.HANDLE], wintypes.BOOL),
        ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
        ('WaitForMultipleObjects', [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD], wintypes.DWORD),
    ):
        fn = getattr(_kernel, name)
        fn.argtypes, fn.restype = args, result


class SingleInstance:
    def __init__(self):
        self._mutex = None
        self._events = []
        self._lock = None
        self._sock = None
        self._thread = None
        self._stopped = threading.Event()
        self._owns = False

    def _event_name(self, command):
        return 'Local\\' + _IDENTITY + {'quit': '.Quit', 'reload': '.Reload'}.get(command, '.Show')

    def acquire(self):
        if os.name == 'nt':
            self._mutex = _kernel.CreateMutexW(None, False, 'Local\\' + _IDENTITY + '.SingleInstance')
            if not self._mutex:
                raise OSError(ctypes.get_last_error(), 'Cannot create single-instance mutex')
            if ctypes.get_last_error() == 183:
                _kernel.CloseHandle(self._mutex)
                self._mutex = None
                return False
            for command in ('show', 'quit', 'reload'):
                event = _kernel.CreateEventW(None, False, False, self._event_name(command))
                if not event:
                    self.stop()
                    raise OSError(ctypes.get_last_error(), 'Cannot create instance event')
                self._events.append(event)
        else:
            import fcntl
            self._lock = open(cfgmod.data_dir() / 'instance.lock', 'a')
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self._lock.close()
                self._lock = None
                return False
        self._owns = True
        return True

    def is_running(self):
        if os.name == 'nt':
            handle = _kernel.OpenMutexW(0x00100000, False, 'Local\\' + _IDENTITY + '.SingleInstance')
            if handle:
                _kernel.CloseHandle(handle)
            return bool(handle)
        import fcntl
        with open(cfgmod.data_dir() / 'instance.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
        return False

    def signal(self, command='show'):
        for _ in range(40):
            if os.name == 'nt':
                handle = _kernel.OpenEventW(0x0002, False, self._event_name(command))
                if handle:
                    try:
                        return bool(_kernel.SetEvent(handle))
                    finally:
                        _kernel.CloseHandle(handle)
            else:
                import socket
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(0.5)
                        client.connect(str(cfgmod.data_dir() / 'instance.sock'))
                        client.sendall(command.encode('ascii'))
                        return True
                except OSError:
                    pass
            self._stopped.wait(0.05)
        return False

    def request_quit(self):
        return not self.is_running() or self.signal('quit')

    def request_reload(self):
        return self.is_running() and self.signal('reload')

    def listen(self, callback, on_quit, on_reload=None):
        if not self._owns:
            return
        if os.name != 'nt':
            import socket
            path = cfgmod.data_dir() / 'instance.sock'
            path.unlink(missing_ok=True)
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.bind(str(path))
            self._sock.listen(4)
            self._sock.settimeout(0.5)

        def loop():
            while not self._stopped.is_set():
                try:
                    if os.name == 'nt':
                        handles = (wintypes.HANDLE * 3)(*self._events)
                        index = _kernel.WaitForMultipleObjects(3, handles, False, 500)
                        if index not in (0, 1, 2):
                            continue
                        command = ('show', 'quit', 'reload')[index]
                    else:
                        try:
                            conn, _ = self._sock.accept()
                        except TimeoutError:
                            continue
                        with conn:
                            conn.settimeout(1.0)
                            command = conn.recv(32).decode('ascii', 'ignore').strip()
                    if self._stopped.is_set():
                        return
                    if command == 'quit':
                        on_quit()
                    elif command == 'reload' and on_reload is not None:
                        on_reload()
                    elif command == 'show':
                        callback()
                except Exception as exc:
                    if not self._stopped.is_set():
                        cfgmod.log('[manager] instance: %r' % (exc,))

        self._thread = threading.Thread(target=loop, name='instance', daemon=True)
        self._thread.start()

    def stop(self):
        self._stopped.set()
        if self._sock is not None:
            self._sock.close()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.5)
        if os.name == 'nt':
            for handle in self._events:
                _kernel.CloseHandle(handle)
            self._events.clear()
            if self._mutex:
                _kernel.CloseHandle(self._mutex)
                self._mutex = None
        elif self._owns:
            (cfgmod.data_dir() / 'instance.sock').unlink(missing_ok=True)
            if self._lock:
                self._lock.close()
                self._lock = None
        self._owns = False
