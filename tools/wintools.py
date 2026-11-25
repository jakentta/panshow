"""Safe helpers for test tools that open program windows.

The owner uses Panshow on this PC too, so a window title alone is NOT enough to find "our"
window: a test once closed the owner's own settings window by mistake. These helpers only
return a window that (1) did not exist before the test started its program and (2) belongs to
that program's own processes. Tests should also run with throw-away data folders (test_env)."""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess

u32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def windows_titled(title):
    found = []

    def cb(hwnd, _):
        n = u32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == title and u32.IsWindowVisible(hwnd):
                found.append(hwnd)
        return True
    u32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def window_pid(hwnd):
    pid = wt.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def process_tree(root_pid):
    """root_pid and all its child processes (the venv python starts the real one as a child)."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
                         capture_output=True, text=True).stdout.split()
    pairs = list(zip(map(int, out[0::2]), map(int, out[1::2])))
    tree = {root_pid}
    grew = True
    while grew:
        grew = False
        for pid, parent in pairs:
            if parent in tree and pid not in tree:
                tree.add(pid)
                grew = True
    return tree


def find_own_window(title, before, root_pid):
    """A window with this title that is new since `before` and belongs to root_pid's processes."""
    new = [h for h in windows_titled(title) if h not in before]
    if not new:
        return 0
    tree = process_tree(root_pid)
    mine = [h for h in new if window_pid(h) in tree]
    return mine[0] if mine else 0


def test_env(folder):
    """Environment for a test program: its own settings/log folders, away from the owner's."""
    env = dict(os.environ)
    env["APPDATA"] = os.path.join(folder, "Roaming")
    env["LOCALAPPDATA"] = os.path.join(folder, "Local")
    os.makedirs(env["APPDATA"], exist_ok=True)
    os.makedirs(env["LOCALAPPDATA"], exist_ok=True)
    return env
