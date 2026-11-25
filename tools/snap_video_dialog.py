"""Open the 'Save as video' dialog in this process (with throw-away settings), take a picture
of it, and close it again. Usage: snap_video_dialog.py <picture.png>"""
import ctypes
import os
import sys
import tempfile

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
tmp = tempfile.mkdtemp(prefix="panshow-snap-")
os.environ["APPDATA"] = os.path.join(tmp, "roam")
os.environ["LOCALAPPDATA"] = os.path.join(tmp, "local")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import wincapture  # noqa: E402

from panshow import config, settings_ui  # noqa: E402

out = sys.argv[1]
root = tk.Tk()
root.title("Panshow settings (test)")
style = ttk.Style(root)
if "vista" in style.theme_names():
    style.theme_use("vista")
root.geometry("900x200+100+100")
px = root.winfo_fpixels("1i") / 96.0
ns = config.Settings(folder=tmp, music_folder=tmp, video_music="none").validated()
settings_ui.video_dialog(root, ns, px, photo_count=403)


def snap():
    dlg = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
    dlg.update_idletasks()
    x, y = dlg.winfo_rootx(), dlg.winfo_rooty()
    wincapture.capture(ctypes.windll.user32.GetParent(dlg.winfo_id())).save(out)
    print("dialog size", dlg.winfo_width(), dlg.winfo_height())
    root.destroy()


root.after(1500, snap)
root.mainloop()
