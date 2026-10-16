# -*- coding: utf-8 -*-
"""纯 ctypes 实现的 Windows 托盘图标 —— 零第三方依赖。

为什么不用 pystray：
    挂件要在系统自带 Python 上「双击即用」，不能要求先 pip install。
    所以直接调用 shell32 的 Shell_NotifyIconW。

线程模型：
    托盘跑在自己的线程 + 自己的消息循环；点击事件塞进 queue 交给 tkinter 主线程处理。
    这样避免在 WNDPROC 里重入 Tcl 解释器。
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t

WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
HWND_MESSAGE = -3


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
user32.RegisterClassW.restype = wintypes.ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]


class TrayIcon:
    """系统托盘图标。点击事件通过 self.queue 交给主线程：

        ("toggle", 0, 0)     左键单击 / 双击
        ("menu", x, y)       右键单击（屏幕坐标）
    """

    def __init__(self, idle_ico: str, active_ico: str, tooltip: str = "Kill Zone") -> None:
        self.queue: queue.Queue = queue.Queue()
        self._idle_path = str(idle_ico)
        self._active_path = str(active_ico)
        self._tooltip = tooltip
        self._hwnd = None
        self._nid: NOTIFYICONDATAW | None = None
        self._icons: dict[str, int] = {}
        self._is_active: bool | None = None
        self._proc = None       # 必须持引用，否则回调被 GC
        self._thread: threading.Thread | None = None
        self.add_ok = False     # Shell_NotifyIconW(NIM_ADD) 是否成功

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="kz-tray", daemon=True)
        self._thread.start()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            m = lparam & 0xFFFF
            if m in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.queue.put(("toggle", 0, 0))
            elif m == WM_RBUTTONUP:
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                self.queue.put(("menu", pt.x, pt.y))
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        cls = f"KZLightTray_{id(self)}"
        self._proc = WNDPROC(self._wndproc)

        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = cls
        if not user32.RegisterClassW(ctypes.byref(wc)):
            return

        hwnd = user32.CreateWindowExW(
            0, cls, "KillZoneTray", 0, 0, 0, 0, 0,
            wintypes.HWND(HWND_MESSAGE), None, hinst, None,
        )
        if not hwnd:
            return
        self._hwnd = hwnd

        for key, path in (("idle", self._idle_path), ("active", self._active_path)):
            h = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                                  LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if h:
                self._icons[key] = h

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._icons.get("idle") or 0
        nid.szTip = self._tooltip[:127]
        self._nid = nid
        self.add_ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        self._is_active = False

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    # ------------------------------------------------------------------ #
    def update(self, tooltip: str, active: bool) -> None:
        """更新提示文字；active 时换成绿色图标。"""
        nid = self._nid
        if nid is None or not self._icons:
            return
        tooltip = tooltip[:127]
        if tooltip == self._tooltip and active == self._is_active:
            return
        self._tooltip, self._is_active = tooltip, active
        nid.uFlags = NIF_ICON | NIF_TIP
        nid.hIcon = self._icons.get("active" if active else "idle") or 0
        nid.szTip = tooltip
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def notify(self, title: str, text: str) -> None:
        """弹一个气泡通知（挂件隐藏时用）。"""
        nid = self._nid
        if nid is None:
            return
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)

    def join(self, timeout: float = 1.0) -> None:
        """等托盘线程把 NIM_DELETE 发完，避免留下幽灵图标。"""
        if self._thread:
            self._thread.join(timeout)
