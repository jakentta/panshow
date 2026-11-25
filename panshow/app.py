"""Entry point.

  Panshow.exe                start the slideshow (the settings window comes first on the very first run)
  Panshow.exe --settings     open the settings window first
  Panshow.exe --video JOB    make a video file (started by the settings window's "Save as video")
Extra options for testing:
  --window [WxH]   run in a normal window instead of full screen
  --sim WxH        draw at this size (e.g. 3840x2160) and shrink it into the window
  --seconds N      stop after N seconds and print timing statistics
  --folder PATH    use this photo folder instead of the saved one
"""
import ctypes
import logging
import os
import sys


def _dpi_aware():
    # Without this, Windows would draw us at a lower size and blow it up (blurry) on scaled screens.
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def _size(text):
    w, h = text.lower().split("x")
    return int(w), int(h)


def _parse(argv):
    opts = {"settings": False, "mode": "fullscreen", "size": (1600, 900), "seconds": None,
            "folder": None, "sim": None, "swap": 1, "keys": None}
    i = 0
    while i < len(argv):
        a = argv[i].lower()
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if a in ("--settings", "/c") or a.startswith("/c:"):
            opts["settings"] = True
        elif a == "--window":
            opts["mode"] = "window"
            if nxt and "x" in nxt and nxt.split("x")[0].isdigit():
                opts["size"] = _size(nxt)
                i += 1
        elif a == "--sim" and nxt:
            opts["sim"] = _size(nxt)
            i += 1
        elif a == "--keys" and nxt:          # e.g. "6:right,9:left,12:space"
            opts["keys"] = [(float(k.split(":")[0]), k.split(":")[1].strip().lower()) for k in nxt.split(",")]
            i += 1
        elif a == "--swap" and nxt:
            opts["swap"] = int(nxt)
            i += 1
        elif a == "--seconds" and nxt:
            opts["seconds"] = float(nxt)
            i += 1
        elif a == "--folder" and nxt:
            opts["folder"] = nxt
            i += 1
        i += 1
    return opts


def main(argv=None):
    import multiprocessing
    multiprocessing.freeze_support()
    _dpi_aware()
    from . import config, update, version, worker
    args = sys.argv[1:] if argv is None else argv
    if len(args) >= 2 and args[0].lower() == "--video":
        # a separate process that makes a video file, with its own small progress window
        worker.setup_logging("panshow-video.log")
        logging.info("start %s %s (video)", version.APP_NAME, version.VERSION)
        try:
            from .video import run_job
            return run_job(args[1])
        except Exception:
            logging.exception("video process failed")
            try:
                ctypes.windll.user32.MessageBoxW(
                    None, "Panshow could not make the video.\n\nDetails are in:\n%s"
                    % os.path.join(config.local_dir(), "panshow-video.log"), "Panshow", 0x10)
            except Exception:
                pass
            return 1
    worker.setup_logging("panshow.log")
    opts = _parse(args)
    logging.info("start %s %s %s", version.APP_NAME, version.VERSION, sys.argv)
    config.migrate_old_name()
    first_run = not os.path.exists(config.settings_path())
    want_settings = opts["settings"] or (first_run and not opts["folder"])
    from_show = False
    try:
        # Updates (only for a copy installed by the setup program; see update.py)
        try:
            if update.maybe_roll_back():
                return 0
            update.after_start()
            ready = update.pending()
            # (not while a video is being made: installing would stop that)
            if ready and config.load().auto_update and not config.video_running() \
                    and update.apply(ready, relaunch=True):
                return 0                     # the installer restarts Panshow when it is done
        except Exception:
            logging.exception("update step failed; carrying on")
        while True:
            if want_settings:
                from .settings_ui import run_settings
                answer = run_settings()
                if answer != "start" and not from_show:
                    return 0
            s = config.load()
            if opts["folder"]:
                s.folder = os.path.abspath(opts["folder"])
                s = s.validated()
            from .show import Show
            show = Show(s, mode=opts["mode"], size=opts["size"], seconds=opts["seconds"], sim_size=opts["sim"],
                        swap=opts["swap"], keys=opts["keys"])
            try:
                update.note_start()
            except Exception:
                logging.exception("could not note the start")
            result = show.run()
            if opts["seconds"] and sys.stdout:
                print("STATS", getattr(show, "stats", None))
            if result != "settings":
                # Quitting: a downloaded update is installed now, quietly in the background.
                try:
                    ready = update.pending()
                    if ready and s.auto_update and not config.video_running():
                        update.apply(ready, relaunch=False)
                    elif ready and s.auto_update:
                        logging.info("a video is being made: the update waits until next time")
                except Exception:
                    logging.exception("could not start the update")
                return 0
            want_settings, from_show = True, True
    except Exception:
        logging.exception("fatal error")
        try:
            ctypes.windll.user32.MessageBoxW(
                None, "Panshow stopped because of an error.\n\nDetails are in:\n%s" % config.log_path(),
                "Panshow", 0x10)
        except Exception:
            pass
        return 1
