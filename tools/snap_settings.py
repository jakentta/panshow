"""Open the settings window (with throw-away settings), take a picture of just that window,
then close it (Cancel). Only ever touches the window of the program it started itself.
Usage: snap_settings.py <picture.png> [--settings-json '{...}'] <program> [program args...]"""
import json
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import tempfile
import time

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
import wincapture  # noqa: E402
import wintools  # noqa: E402

u32 = ctypes.windll.user32
TITLE = "Panshow settings"
out = sys.argv[1]
args = sys.argv[2:]
env = wintools.test_env(tempfile.mkdtemp(prefix="panshow-snap-"))
if args[:1] == ["--settings-json"]:
    # start the window with these settings (e.g. a test photo folder, never the owner's own)
    os.makedirs(os.path.join(env["APPDATA"], "Panshow"), exist_ok=True)
    with open(os.path.join(env["APPDATA"], "Panshow", "settings.json"), "w", encoding="utf-8") as f:
        json.dump(json.loads(args[1]), f)
    args = args[2:]
before = set(wintools.windows_titled(TITLE))
proc = subprocess.Popen(args + ["--settings"], env=env)
hwnd = 0
for _ in range(60):
    time.sleep(0.25)
    hwnd = wintools.find_own_window(TITLE, before, proc.pid)
    if hwnd or proc.poll() is not None:
        break
if not hwnd:
    print("own settings window did not appear; nothing was touched")
    proc.kill()
    sys.exit(1)
time.sleep(1.0)
r = wt.RECT()
u32.GetWindowRect(hwnd, ctypes.byref(r))
wincapture.capture(hwnd).save(out)          # only this window, never anything covering it
print("window size", r.right - r.left, r.bottom - r.top)
u32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE = same as Cancel (only our own window)
for _ in range(40):
    if proc.poll() is not None:
        break
    time.sleep(0.25)
print("closed cleanly:", proc.poll() == 0)
if proc.poll() is None:
    proc.kill()
