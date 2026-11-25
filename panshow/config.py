"""Settings: load/save a small JSON file in the user's AppData folder."""
import ctypes
import json
import logging
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field, fields

from .version import APP_NAME

OLD_NAME = "Photoshow"      # earlier name of the program (settings are carried over once)

ORDERS = ("shuffle", "date", "name")
TALL_STYLES = ("blur", "pan", "pair")
MOTIONS = ("gentle", "normal", "strong", "wild", "hyperdrive")
MUSIC_SOURCES = ("off", "folder", "output", "mic")


VIDEO_MUTEX = "Local\\PanshowVideoRunning"   # exists while a video is being made
VIDEO_SIZES = ("3840x2160", "1920x1080")


def video_running():
    """Is a video being made right now? (Then an update must wait: installing it would stop that.)"""
    try:
        k32 = ctypes.windll.kernel32
        k32.OpenMutexW.restype = ctypes.c_void_p
        h = k32.OpenMutexW(0x00100000, False, VIDEO_MUTEX)       # SYNCHRONIZE
        if h:
            k32.CloseHandle(ctypes.c_void_p(h))
            return True
    except Exception:
        pass
    return False


def default_videos_folder():
    return (_known_folder("18989B1D-99B5-455B-841C-AB7C74E4DDFC")
            or os.path.join(os.path.expanduser("~"), "Videos"))


def _known_folder(guid_str):
    """Ask Windows for a 'known folder' (e.g. the Pictures folder, which may live in OneDrive)."""
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                        ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]
        u = uuid.UUID(guid_str)
        g = GUID(u.fields[0], u.fields[1], u.fields[2],
                 (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
            path = p.value
            ctypes.windll.ole32.CoTaskMemFree(p)
            return path
    except Exception:
        pass
    return None


def default_pictures_folder():
    return (_known_folder("33E28130-4E1E-4676-835A-98395C3BC3BB")
            or os.path.join(os.path.expanduser("~"), "Pictures"))


def settings_dir():
    d = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def local_dir():
    d = os.path.join(os.environ.get("LOCALAPPDATA") or settings_dir(), APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def cache_dir():
    d = os.path.join(local_dir(), "cache")
    os.makedirs(d, exist_ok=True)
    return d


def log_path():
    return os.path.join(local_dir(), "panshow.log")


@dataclass
class Settings:
    folder: str = ""
    include_subfolders: bool = True
    seconds_per_photo: float = 10.0
    transition_seconds: float = 2.0
    tall_photo_style: str = "blur"      # "blur" = whole photo on blurred background, "pan" = slow pan,
                                        # "pair" = two tall photos side by side
    order: str = "shuffle"              # "shuffle", "date" (when taken), "name"
    resume: bool = False                # date/name order: continue after the last photo shown last time
    stop_at_end: bool = False           # after the last photo, end the slideshow (instead of starting again)
    motion: str = "normal"              # "gentle", "normal", "strong", "wild", "hyperdrive"
    skip_cloud_only: bool = False       # skip OneDrive files that are not downloaded to this PC
    max_stretch: float = 1.0            # never stretch photo pixels more than this on screen (1.0 = 1:1)
    display: str = "auto"               # "auto" = largest screen, or "<name>|<w>x<h>"
    auto_update: bool = True            # look for new versions on GitHub and install them
    curved_paths: bool = True           # camera travels on a gentle arc instead of a straight line
    tilt: bool = False                  # picture turns very slightly (under 1 degree) while it moves
    flow_moves: bool = True             # each photo's move continues the previous one's direction
    music: bool = False                 # (older versions: True = play songs from music_folder)
    music_source: str = ""              # "off", "folder", "output" (this computer's sound), "mic"
    media_key: bool = True              # Space also pauses/resumes the music app (source "output")
    music_folder: str = ""
    music_volume: int = 70              # 0..100
    beat_sync: bool = True              # time photo changes to the beat of the music
    # "Save as video" choices (remembered for next time)
    video_length: str = "music"         # "music" = as long as the songs, "minutes" = video_minutes,
                                        # "photos" = every photo once
    video_minutes: float = 5.0
    video_music: str = "folder"         # "none", "folder" (the song folder above), "files" (video_songs)
    video_songs: list = field(default_factory=list)
    video_size: str = "3840x2160"
    video_dir: str = ""                 # where the last video was saved

    def validated(self):
        s = Settings(**asdict(self))
        if not s.folder:
            s.folder = default_pictures_folder()
        s.seconds_per_photo = min(max(float(s.seconds_per_photo), 2.0), 300.0)
        s.transition_seconds = round(min(max(float(s.transition_seconds), 0.3), 10.0), 1)   # 0.1 s steps
        # the crossfade must be shorter than the time a photo is shown
        s.transition_seconds = min(s.transition_seconds, s.seconds_per_photo - 1.0)
        if s.tall_photo_style not in TALL_STYLES:
            s.tall_photo_style = "blur"
        if s.order not in ORDERS:
            s.order = "shuffle"
        s.resume = bool(s.resume)
        s.stop_at_end = bool(s.stop_at_end)
        if s.motion not in MOTIONS:
            s.motion = "normal"
        s.max_stretch = min(max(float(s.max_stretch), 1.0), 1.6)
        s.display = str(s.display or "auto")
        s.include_subfolders = bool(s.include_subfolders)
        s.skip_cloud_only = bool(s.skip_cloud_only)
        s.auto_update = bool(s.auto_update)
        s.curved_paths = bool(s.curved_paths)
        s.tilt = bool(s.tilt)
        s.flow_moves = bool(s.flow_moves)
        if s.music_source not in MUSIC_SOURCES:
            s.music_source = "folder" if s.music else "off"     # settings from version 1.2
        s.music = s.music_source != "off"
        s.media_key = bool(s.media_key)
        s.music_folder = str(s.music_folder or "")
        s.music_volume = int(min(max(int(s.music_volume), 0), 100))
        s.beat_sync = bool(s.beat_sync)
        if s.video_length not in ("music", "minutes", "photos"):
            s.video_length = "music"
        s.video_minutes = round(min(max(float(s.video_minutes), 0.5), 180.0), 1)
        if s.video_music not in ("none", "folder", "files"):
            s.video_music = "folder"
        s.video_songs = [str(p) for p in (s.video_songs or []) if isinstance(p, str)]
        if s.video_size not in VIDEO_SIZES:
            s.video_size = VIDEO_SIZES[0]
        s.video_dir = str(s.video_dir or "")
        return s


def settings_path():
    return os.path.join(settings_dir(), "settings.json")


def migrate_old_name():
    """One time: copy settings and face memory from the program's earlier name."""
    try:
        old_settings = os.path.join(os.environ.get("APPDATA") or "", OLD_NAME, "settings.json")
        if not os.path.exists(settings_path()) and os.path.exists(old_settings):
            shutil.copy2(old_settings, settings_path())
            logging.info("settings copied from %s", OLD_NAME)
        old_cache = os.path.join(os.environ.get("LOCALAPPDATA") or "", OLD_NAME, "cache")
        if os.path.isdir(old_cache) and not os.listdir(cache_dir()):
            for name in os.listdir(old_cache):
                shutil.copy2(os.path.join(old_cache, name), cache_dir())
            logging.info("face memory copied from %s", OLD_NAME)
    except Exception:
        logging.exception("could not copy old settings")


def load():
    s = Settings()
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        names = {f.name for f in fields(Settings)}
        for k, v in data.items():
            if k in names:
                setattr(s, k, v)
    except FileNotFoundError:
        pass
    except Exception:
        logging.exception("Could not read settings, using defaults")
    try:
        return s.validated()
    except Exception:
        logging.exception("Bad settings, using defaults")
        return Settings().validated()


def save(s):
    s = s.validated()
    tmp = settings_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(s), f, indent=2)
    os.replace(tmp, settings_path())
    return s
