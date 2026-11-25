"""The settings window. run_settings() returns "start" or "cancel"."""
import dataclasses
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import config, screens, version

ORDER_LABELS = {"shuffle": "Shuffled", "date": "By date taken (oldest first)", "name": "By file name"}
TALL_LABELS = {"blur": "Show the whole photo on a blurred background", "pan": "Fill the screen and pan slowly",
               "pair": "Show two tall photos side by side, each with its own camera move"}
VIDEO_MUSIC = {"none": "No music", "folder": "Songs from the song folder (in random order)",
               "files": "Songs I choose"}
VIDEO_SIZES = {"3840x2160": "4K (3840 x 2160), 60 frames per second: about 300 MB per minute",
               "1920x1080": "Full HD (1920 x 1080), 60 frames per second: about 100 MB per minute"}
MOTION_LABELS = {"gentle": "Gentle", "normal": "Normal", "strong": "Stronger",
                 "wild": "Wild: fast moves, big tilts, quick cuts",
                 "hyperdrive": "Hyperdrive: fastest moves and cuts, gentle pulses on the beat"}
STRETCH_LABELS = {1.0: "Sharpest: never enlarge photo pixels (less zoom room)",
                  1.2: "Allow slight enlargement (up to 1.2x): more movement",
                  1.4: "Allow more enlargement (up to 1.4x): most movement"}
PATH_LABELS = {True: "Gently curved (more natural)", False: "Straight lines (classic)"}
MUSIC_LABELS = {"off": "No music",
                "folder": "Songs from a folder (below)",
                "output": "Listen to this computer's sound (Spotify, YouTube Music, …)",
                "mic": "Listen through the microphone (music from another speaker)"}


def _listen_report(src, res):
    """Plain-language result of 'Test listening'."""
    if res.get("error"):
        return "Could not listen: %s" % res["error"]
    if res.get("confidence", 0) >= 0.25:
        return ("Heard music with a steady beat: about %.0f beats per minute.\n\n"
                "Photo changes can follow this beat." % res["bpm"])
    if res.get("level", 0) < 1e-4:
        if src == "mic":
            return ("Heard (almost) nothing from the microphone.\n\nIs the music playing and loud enough? "
                    "If it is, Windows may be blocking the microphone: Settings > Privacy & security > "
                    "Microphone > 'Let desktop apps access your microphone'.")
        return "Heard nothing. Start the music in your music app first, then test again."
    return ("Heard sound, but no steady beat yet.\n\nTry again in the middle of a song with a clear "
            "rhythm%s. Without a steady beat, photos simply change at their normal time."
            % (", or turn the music up / move the microphone closer" if src == "mic" else ""))
KEYS_HELP = ("While the slideshow runs:   Esc = quit    S = settings    Space = pause / continue    "
             "Left / Right arrow = previous / next photo")


def _duration(sec):
    """Plain-language length: '45 s', '6 min 40 s', '34 min', '2 h 05 min'."""
    sec = int(round(sec))
    if sec < 60:
        return "%d s" % sec
    if sec < 600:
        return "%d min %02d s" % (sec // 60, sec % 60)
    minutes = int(round(sec / 60))
    if minutes < 60:
        return "%d min" % minutes
    return "%d h %02d min" % (minutes // 60, minutes % 60)


def _inv(d, label):
    for k, v in d.items():
        if v == label:
            return k
    return next(iter(d))


def video_dialog(root, ns, px, photo_count=None):
    """'Save as video': choose music, length, picture size and file, then start the video
    process. ns: the settings as they are in the settings window right now; photo_count: how
    many photos the folder has (if known)."""
    from . import music, video

    dlg = tk.Toplevel(root)
    dlg.title("Save the slideshow as a video")
    dlg.resizable(False, False)
    dlg.transient(root)
    f = ttk.Frame(dlg, padding=16)
    f.grid(sticky="nsew")
    wrap = int(600 * px)
    ttk.Label(f, text="Makes a video file (MP4) of the slideshow, using the settings in the settings window: "
                      "photo folder, seconds per photo, amount of movement and so on. Photo changes follow "
                      "the beat of the music, just like the slideshow.", wraplength=wrap).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
    r = 1
    chosen = list(ns.video_songs)
    vmusic = tk.StringVar(value=VIDEO_MUSIC[ns.video_music])
    ttk.Label(f, text="Music:").grid(row=r, column=0, sticky="w", padx=(0, 12))
    mb = ttk.Frame(f)
    mb.grid(row=r, column=1, sticky="w")
    ttk.Combobox(mb, textvariable=vmusic, values=list(VIDEO_MUSIC.values()), state="readonly",
                 width=44).pack(side="left")
    choose_btn = ttk.Button(mb, text="Choose songs…")
    choose_btn.pack(side="left", padx=(6, 0))
    r += 1
    info = tk.StringVar()
    ttk.Label(f, textvariable=info, foreground="#555", wraplength=wrap).grid(row=r, column=1, sticky="w")
    r += 1

    vlen = tk.StringVar(value=ns.video_length)
    vmin = tk.StringVar(value="%g" % ns.video_minutes)
    ttk.Label(f, text="Length:").grid(row=r, column=0, sticky="nw", padx=(0, 12), pady=(8, 0))
    lb = ttk.Frame(f)
    lb.grid(row=r, column=1, sticky="w", pady=(8, 0))
    rb_music = ttk.Radiobutton(lb, text="As long as the music (at most 3 hours)", value="music", variable=vlen)
    rb_music.pack(anchor="w")
    once_text = "Every photo once, then the video ends"
    if photo_count:
        once_text += " (%s photos: about %s)" % (format(photo_count, ","),
                                                 _duration(photo_count * ns.seconds_per_photo))
    ttk.Radiobutton(lb, text=once_text, value="photos", variable=vlen).pack(anchor="w")
    lrow = ttk.Frame(lb)
    lrow.pack(anchor="w")
    ttk.Radiobutton(lrow, text="", value="minutes", variable=vlen).pack(side="left")
    ttk.Spinbox(lrow, from_=0.5, to=180, increment=0.5, textvariable=vmin, width=6,
                command=lambda: vlen.set("minutes")).pack(side="left")
    ttk.Label(lrow, text="minutes (songs repeat if needed; the music fades out at the end)").pack(
        side="left", padx=(4, 0))
    r += 1

    vsize = tk.StringVar(value=VIDEO_SIZES[ns.video_size])
    ttk.Label(f, text="Picture:").grid(row=r, column=0, sticky="w", padx=(0, 12), pady=(8, 0))
    ttk.Combobox(f, textvariable=vsize, values=list(VIDEO_SIZES.values()), state="readonly",
                 width=64).grid(row=r, column=1, sticky="w", pady=(8, 0))
    r += 1

    folder = ns.video_dir if os.path.isdir(ns.video_dir) else config.default_videos_folder()
    vout = tk.StringVar(value=os.path.join(folder, "Panshow %s.mp4" % time.strftime("%Y-%m-%d %H.%M")))
    ttk.Label(f, text="Save as:").grid(row=r, column=0, sticky="w", padx=(0, 12), pady=(8, 0))
    ob = ttk.Frame(f)
    ob.grid(row=r, column=1, sticky="ew", pady=(8, 0))
    ttk.Entry(ob, textvariable=vout, width=58).pack(side="left", fill="x", expand=True)

    def browse_out():
        p = filedialog.asksaveasfilename(parent=dlg, title="Save the video as", defaultextension=".mp4",
                                         initialdir=os.path.dirname(vout.get()) or None,
                                         initialfile=os.path.basename(vout.get()),
                                         filetypes=[("MP4 video", "*.mp4")])
        if p:
            vout.set(os.path.normpath(p))
    ttk.Button(ob, text="Browse…", command=browse_out).pack(side="left", padx=(6, 0))
    r += 1

    ttk.Label(f, text="Making the video takes a while: at 4K roughly as long as the video itself, less in "
                      "Full HD. A small window shows how far it is, and you can keep using the computer.\n\n"
                      "Songs are built into the video. For your own TV any music is fine; if you post the "
                      "video online, use music you are allowed to share (sites may mute or block videos "
                      "with commercial songs).", foreground="#555", wraplength=wrap).grid(
        row=r, column=0, columnspan=2, sticky="w", pady=(14, 0))
    r += 1

    def songs_now():
        m = _inv(VIDEO_MUSIC, vmusic.get())
        if m == "folder":
            return music.scan(ns.music_folder) if ns.music_folder and os.path.isdir(ns.music_folder) else []
        if m == "files":
            return [p for p in chosen if os.path.isfile(p)]
        return []

    def update_info(*_):
        m = _inv(VIDEO_MUSIC, vmusic.get())
        n = len(songs_now())
        if m == "folder":
            info.set("%d songs in %s" % (n, ns.music_folder) if n else
                     "No songs found: choose a song folder in the settings window first (Music).")
        elif m == "files":
            names = [os.path.basename(p) for p in chosen if os.path.isfile(p)]
            info.set(("%d songs: %s" % (n, ", ".join(names[:4]) + (", …" if n > 4 else ""))) if n else
                     "Click 'Choose songs…' to pick them (you can pick several at once).")
        else:
            info.set("Photos change at their normal time. You can play any music with the video later.")
        if m == "none":
            rb_music.state(["disabled"])
            if vlen.get() == "music":
                vlen.set("minutes")
        else:
            rb_music.state(["!disabled"])

    def choose():
        start_dir = os.path.dirname(chosen[0]) if chosen else (ns.music_folder or None)
        paths = filedialog.askopenfilenames(
            parent=dlg, title="Choose songs for the video (in the order they should play)",
            initialdir=start_dir, filetypes=[("Music", "*.mp3 *.flac *.wav *.ogg *.oga"), ("All files", "*.*")])
        if paths:
            chosen[:] = [os.path.normpath(p) for p in paths]
            vmusic.set(VIDEO_MUSIC["files"])
            update_info()
    choose_btn.config(command=choose)
    vmusic.trace_add("write", update_info)
    update_info()

    def make():
        m = _inv(VIDEO_MUSIC, vmusic.get())
        songs = songs_now()
        if m != "none" and not songs:
            messagebox.showerror("Panshow", "There are no songs to use. Choose songs, or 'No music'.", parent=dlg)
            return
        length = vlen.get()
        try:
            minutes = float(vmin.get())
        except ValueError:
            minutes = 0.0
        if length == "minutes" and not 0.5 <= minutes <= 180:
            messagebox.showerror("Panshow", "Please give a length between 0.5 and 180 minutes.", parent=dlg)
            return
        out = vout.get().strip()
        if not out.lower().endswith(".mp4"):
            out += ".mp4"
        out = os.path.abspath(out)
        if not os.path.isdir(os.path.dirname(out)):
            messagebox.showerror("Panshow", "That folder does not exist:\n%s" % os.path.dirname(out), parent=dlg)
            return
        if os.path.exists(out) and not messagebox.askyesno(
                "Panshow", "%s already exists. Replace it?" % os.path.basename(out), parent=dlg):
            return
        size = _inv(VIDEO_SIZES, vsize.get())
        # remember these choices for next time (the rest of the settings window is not saved here)
        try:
            saved = config.load()
            config.save(dataclasses.replace(saved, video_music=m, video_songs=list(chosen), video_length=length,
                                            video_minutes=minutes or saved.video_minutes, video_size=size,
                                            video_dir=os.path.dirname(out)))
        except Exception:
            pass
        job = video.write_job(ns.validated(), out, size,
                              length if length in ("music", "photos") else minutes * 60.0,
                              songs, shuffle=(m == "folder"))
        try:
            video.start_job(job)
        except Exception as e:
            messagebox.showerror("Panshow", "Could not start making the video: %s" % e, parent=dlg)
            return
        dlg.destroy()
        messagebox.showinfo("Panshow", "The video is being made now. A small window shows how far it is.\n\n"
                                       "You can close the settings window; the video carries on.", parent=root)

    bb = ttk.Frame(f)
    bb.grid(row=r, column=0, columnspan=2, sticky="e", pady=(16, 0))
    ttk.Button(bb, text="Make video", command=make).pack(side="left", padx=4)
    ttk.Button(bb, text="Cancel", command=dlg.destroy).pack(side="left")
    dlg.bind("<Escape>", lambda e: dlg.destroy())
    dlg.update_idletasks()
    x = root.winfo_rootx() + (root.winfo_width() - dlg.winfo_reqwidth()) // 2
    y = root.winfo_rooty() + 40
    dlg.geometry("+%d+%d" % (max(0, x), max(0, y)))
    dlg.grab_set()
    dlg.focus_force()


def run_settings():
    s = config.load()
    monitors = screens.list_for_settings()
    answer = {"value": "cancel"}

    root = tk.Tk()
    root.title("Panshow settings")
    root.resizable(False, False)
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    try:
        from .analysis import resource_path
        root.iconbitmap(resource_path("panshow.ico"))
    except Exception:
        pass
    px = root.winfo_fpixels("1i") / 96.0      # screen scaling, e.g. 1.75 on a 175% laptop screen

    frm = ttk.Frame(root, padding=16)
    frm.grid(sticky="nsew")
    row = 0

    def label(text):
        ttk.Label(frm, text=text).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))

    folder = tk.StringVar(value=s.folder)
    label("Photo folder:")
    fbox = ttk.Frame(frm)
    fbox.grid(row=row, column=1, sticky="ew")
    ttk.Entry(fbox, textvariable=folder, width=52).pack(side="left", fill="x", expand=True)

    def browse():
        d = filedialog.askdirectory(initialdir=folder.get() or None, title="Choose your photo folder")
        if d:
            folder.set(os.path.normpath(d))
    ttk.Button(fbox, text="Browse…", command=browse).pack(side="left", padx=(6, 0))
    row += 1

    subs = tk.BooleanVar(value=s.include_subfolders)
    sbox2 = ttk.Frame(frm)
    sbox2.grid(row=row, column=1, sticky="w", pady=(0, 6))
    ttk.Checkbutton(sbox2, text="Include photos in sub-folders", variable=subs).pack(side="left")
    count_info = tk.StringVar(value="")         # "403 photos: one round takes about 34 min"
    ttk.Label(sbox2, textvariable=count_info, foreground="#555").pack(side="left", padx=(16, 0))
    row += 1

    screen_labels = {"auto": "Automatic: the largest screen"}
    for m in monitors:
        screen_labels[m["key"]] = m["label"]
    if s.display not in screen_labels:
        s.display = "auto"
    screen = tk.StringVar(value=screen_labels[s.display])
    label("Show on screen:")
    ttk.Combobox(frm, textvariable=screen, values=list(screen_labels.values()), state="readonly",
                 width=56).grid(row=row, column=1, sticky="w")
    row += 1

    secs = tk.StringVar(value="%g" % s.seconds_per_photo)
    label("Seconds per photo:")
    ttk.Spinbox(frm, from_=2, to=120, increment=1, textvariable=secs, width=8).grid(row=row, column=1, sticky="w")
    row += 1

    fade = tk.StringVar(value="%.1f" % s.transition_seconds)
    label("Crossfade length (seconds):")
    ttk.Spinbox(frm, from_=0.3, to=10.0, increment=0.1, format="%.1f", textvariable=fade,
                width=8).grid(row=row, column=1, sticky="w")
    row += 1

    order = tk.StringVar(value=ORDER_LABELS[s.order])
    resume = tk.BooleanVar(value=s.resume)
    label("Order:")
    obox = ttk.Frame(frm)
    obox.grid(row=row, column=1, sticky="w")
    ttk.Combobox(obox, textvariable=order, values=list(ORDER_LABELS.values()), state="readonly",
                 width=30).pack(side="left")
    resume_box = ttk.Checkbutton(obox, text="Continue where the last slideshow stopped", variable=resume)
    resume_box.pack(side="left", padx=(12, 0))

    def order_changed(*_):
        # (only means something for the date and file name orders)
        resume_box.state(["disabled"] if _inv(ORDER_LABELS, order.get()) == "shuffle" else ["!disabled"])
    order.trace_add("write", order_changed)
    order_changed()
    row += 1
    stop = tk.BooleanVar(value=s.stop_at_end)
    ttk.Checkbutton(frm, text="Stop after the last photo (instead of starting again from the beginning)",
                    variable=stop).grid(row=row, column=1, sticky="w", pady=(2, 0))
    row += 1

    motion = tk.StringVar(value=MOTION_LABELS[s.motion])
    label("Amount of movement:")
    ttk.Combobox(frm, textvariable=motion, values=list(MOTION_LABELS.values()), state="readonly",
                 width=56).grid(row=row, column=1, sticky="w")
    row += 1

    stretch_key = min(STRETCH_LABELS, key=lambda k: abs(k - s.max_stretch))
    stretch = tk.StringVar(value=STRETCH_LABELS[stretch_key])
    label("Sharpness or movement:")
    ttk.Combobox(frm, textvariable=stretch, values=list(STRETCH_LABELS.values()), state="readonly",
                 width=56).grid(row=row, column=1, sticky="w")
    row += 1

    path = tk.StringVar(value=PATH_LABELS[bool(s.curved_paths)])
    label("Camera path:")
    ttk.Combobox(frm, textvariable=path, values=list(PATH_LABELS.values()), state="readonly",
                 width=56).grid(row=row, column=1, sticky="w")
    row += 1

    tilt = tk.BooleanVar(value=s.tilt)
    ttk.Checkbutton(frm, text="Gentle tilt: the photo turns very slightly while it moves "
                              "(never for photos with horizons or buildings)",
                    variable=tilt).grid(row=row, column=1, sticky="w", pady=(2, 0))
    row += 1

    flow = tk.BooleanVar(value=s.flow_moves)
    ttk.Checkbutton(frm, text="Flowing moves: each photo's camera movement continues the previous one",
                    variable=flow).grid(row=row, column=1, sticky="w", pady=(2, 6))
    row += 1

    # ---- music
    source = tk.StringVar(value=MUSIC_LABELS[s.music_source])
    label("Music:")
    sbox = ttk.Frame(frm)
    sbox.grid(row=row, column=1, sticky="w")
    ttk.Combobox(sbox, textvariable=source, values=list(MUSIC_LABELS.values()), state="readonly",
                 width=56).pack(side="left")
    test_btn = ttk.Button(sbox, text="Test listening…")
    test_btn.pack(side="left", padx=(6, 0))
    row += 1

    music_folder = tk.StringVar(value=s.music_folder)
    mbox = ttk.Frame(frm)
    mbox.grid(row=row, column=1, sticky="ew", pady=(2, 0))
    ttk.Label(mbox, text="Song folder:").pack(side="left")
    ttk.Entry(mbox, textvariable=music_folder, width=34).pack(side="left", fill="x", expand=True, padx=(4, 0))

    def browse_music():
        d = filedialog.askdirectory(initialdir=music_folder.get() or None,
                                    title="Choose a folder with music (mp3, flac, wav or ogg)")
        if d:
            music_folder.set(os.path.normpath(d))
            source.set(MUSIC_LABELS["folder"])
    ttk.Button(mbox, text="Browse…", command=browse_music).pack(side="left", padx=(6, 0))
    volume = tk.IntVar(value=s.music_volume)
    ttk.Label(mbox, text="Volume").pack(side="left", padx=(12, 0))
    ttk.Scale(mbox, from_=0, to=100, variable=volume, length=int(110 * px),
              command=lambda v: volume.set(int(float(v)))).pack(side="left", padx=4)
    ttk.Label(mbox, textvariable=volume, width=4).pack(side="left")
    row += 1

    beat = tk.BooleanVar(value=s.beat_sync)
    ttk.Checkbutton(frm, text="Change photos on the beat (each photo's time varies a little)",
                    variable=beat).grid(row=row, column=1, sticky="w", pady=(2, 0))
    row += 1
    media = tk.BooleanVar(value=s.media_key)
    ttk.Checkbutton(frm, text="Space also pauses and resumes the music app on this computer",
                    variable=media).grid(row=row, column=1, sticky="w", pady=(2, 6))
    row += 1

    def test_listening():
        """Listen for a few seconds (in a helper process) and say what was heard."""
        src = _inv(MUSIC_LABELS, source.get())
        if src not in ("output", "mic"):
            messagebox.showinfo("Panshow", "Choose one of the two 'Listen…' music options first.")
            return
        import multiprocessing as mp
        from . import music
        q = mp.get_context("spawn").Queue()
        proc = mp.get_context("spawn").Process(target=music.test_listen_main, args=(src, q, 8.0), daemon=True)
        proc.start()
        test_btn.config(state="disabled", text="Listening (8 s)…")

        def check():
            try:
                res = q.get_nowait()
            except Exception:
                if proc.is_alive():
                    root.after(300, check)
                    return
                res = {"error": "the listening helper stopped"}
            test_btn.config(state="normal", text="Test listening…")
            messagebox.showinfo("Panshow", _listen_report(src, res))
        root.after(300, check)
    test_btn.config(command=test_listening)

    tall = tk.StringVar(value=s.tall_photo_style)
    label("Tall (portrait) photos:")
    tbox = ttk.Frame(frm)
    tbox.grid(row=row, column=1, sticky="w")
    for k, v in TALL_LABELS.items():
        ttk.Radiobutton(tbox, text=v, value=k, variable=tall).pack(anchor="w")
    row += 1

    cloud = tk.BooleanVar(value=s.skip_cloud_only)
    ttk.Checkbutton(frm, text="Skip OneDrive photos that are not downloaded to this PC",
                    variable=cloud).grid(row=row, column=1, sticky="w", pady=(6, 0))
    row += 1

    auto_update = tk.BooleanVar(value=s.auto_update)
    ttk.Checkbutton(frm, text="Update automatically (new versions come from GitHub and install when "
                              "you quit Panshow)", variable=auto_update).grid(row=row, column=1, sticky="w",
                                                                             pady=(6, 0))
    row += 1

    ttk.Label(frm, text=KEYS_HELP, foreground="#444").grid(row=row, column=0, columnspan=2, sticky="w",
                                                           pady=(14, 0))
    row += 1
    ttk.Label(frm, text="Face positions are remembered, so the first time through a big folder is "
                        "slower than later ones.", foreground="#666", wraplength=int(620 * px)).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))
    row += 1

    # ---- how many photos, and how long one round takes (counted in the background)
    counts = {}                                 # (folder, sub-folders, skip cloud) -> number, or None
    timer = {"id": None}

    def count_key():
        return (folder.get().strip(), bool(subs.get()), bool(cloud.get()))

    def show_count(*_):
        key = count_key()
        n = counts.get(key)
        if not os.path.isdir(key[0]) or (key in counts and n is not None and n < 0):
            count_info.set("")
            return
        if n is None:
            count_info.set("Counting photos…")
            return
        try:
            sec = min(max(float(secs.get()), 2.0), 300.0)
        except ValueError:
            sec = None
        text = "%s photo%s" % (format(n, ","), "" if n == 1 else "s")
        if sec and n:
            text += (": the slideshow lasts about %s" if stop.get() else ": one round takes about %s") \
                % _duration(n * sec)
            if tall.get() == "pair":
                text += " (less with tall photos side by side)"
        count_info.set(text)

    def start_count():
        timer["id"] = None
        key = count_key()
        if not os.path.isdir(key[0]) or key in counts:
            show_count()
            return
        counts[key] = None

        def work():                             # (a helper thread: no window calls in here)
            try:
                from . import imaging, worker
                files, _ = worker.scan(key[0], key[1], key[2], imaging.EXTENSIONS)
                counts[key] = len(files)
            except Exception:
                counts[key] = -1
        threading.Thread(target=work, daemon=True).start()

        def wait():
            if counts.get(key) is None:
                root.after(250, wait)
            show_count()
        wait()

    def recount(*_):
        if timer["id"] is not None:
            root.after_cancel(timer["id"])
        timer["id"] = root.after(600, start_count)   # (wait until typing in the folder box stops)
    for v in (folder, subs, cloud):
        v.trace_add("write", recount)
    for v in (secs, tall, stop):
        v.trace_add("write", show_count)
    root.after(300, start_count)

    def collect():
        try:
            sec_v, fade_v = float(secs.get()), float(fade.get())
        except ValueError:
            messagebox.showerror("Panshow", "Please enter numbers for the seconds.")
            return None
        if not os.path.isdir(folder.get()):
            messagebox.showerror("Panshow", "That photo folder does not exist.")
            return None
        src = _inv(MUSIC_LABELS, source.get())
        if src == "folder" and not os.path.isdir(music_folder.get()):
            messagebox.showerror("Panshow", "That song folder does not exist.")
            return None
        # (settings not shown in this window, e.g. the video choices, are kept as they are)
        return dataclasses.replace(
            s, folder=folder.get(), include_subfolders=subs.get(),
            seconds_per_photo=sec_v, transition_seconds=fade_v,
            tall_photo_style=tall.get(), order=_inv(ORDER_LABELS, order.get()), resume=resume.get(),
            stop_at_end=stop.get(),
            motion=_inv(MOTION_LABELS, motion.get()), skip_cloud_only=cloud.get(),
            max_stretch=_inv(STRETCH_LABELS, stretch.get()),
            display=_inv(screen_labels, screen.get()), auto_update=auto_update.get(),
            curved_paths=_inv(PATH_LABELS, path.get()), tilt=tilt.get(),
            flow_moves=flow.get(), music_source=src, media_key=media.get(),
            music_folder=music_folder.get(), music_volume=int(volume.get()),
            beat_sync=beat.get())

    def start():
        ns = collect()
        if ns is None:
            return
        config.save(ns)
        answer["value"] = "start"
        root.destroy()

    def save_video():
        ns = collect()
        if ns is not None:
            n = counts.get(count_key())
            video_dialog(root, ns, px, photo_count=n if n is not None and n >= 0 else None)

    # bottom row: making a video on the far left, well away from starting the slideshow
    bottom = ttk.Frame(frm)
    bottom.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(16, 0))
    ttk.Button(bottom, text="Save as video…", command=save_video).pack(side="left")
    ttk.Label(bottom, text="%s version %s" % (version.APP_NAME, version.VERSION), foreground="#888").pack(
        side="left", padx=(16, 0))
    ttk.Button(bottom, text="Cancel", command=root.destroy).pack(side="right")
    ttk.Button(bottom, text="Start slideshow", command=start).pack(side="right", padx=4)
    root.bind("<Escape>", lambda e: root.destroy())
    root.bind("<Return>", lambda e: start())
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry("+%d+%d" % (x, y))
    root.lift()
    root.focus_force()
    root.mainloop()
    return answer["value"]
