"""Start the program with no arguments (like a double-click) using EMPTY settings folders and
check that the settings window opens on a first run; take a picture of it, then close it.
Only ever touches the window of the program it started itself.
Usage: first_run_check.py <exe> <picture.png>"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import tempfile
import time

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
import wincapture  # noqa: E402
import wintools  # noqa: E402

u32 = ctypes.windll.user32
TITLE = "Panshow settings"
before = set(wintools.windows_titled(TITLE))
env = wintools.test_env(tempfile.mkdtemp(prefix="panshow-firstrun-"))
proc = subprocess.Popen([sys.argv[1]], env=env)
hwnd = 0
for _ in range(80):
    time.sleep(0.25)
    hwnd = wintools.find_own_window(TITLE, before, proc.pid)
    if hwnd or proc.poll() is not None:
        break
if not hwnd:
    print("own settings window did NOT appear; exit code:", proc.poll())
    proc.kill()
    sys.exit(1)
time.sleep(1.0)
r = wt.RECT()
u32.GetWindowRect(hwnd, ctypes.byref(r))
wincapture.capture(hwnd).save(sys.argv[2])
u32.PostMessageW(hwnd, 0x0010, 0, 0)          # close = Cancel (only our own window)
for _ in range(40):
    if proc.poll() is not None:
        break
    time.sleep(0.25)
print("settings window appeared on first run; closed cleanly:", proc.poll() == 0)
if proc.poll() is None:
    proc.kill()
