"""Automatic updates ("over the air").

How it works:
1. In the background (helper process), Panshow downloads `manifest.json` and its signature
   `manifest.json.sig` from the latest GitHub release. The signature must match the public key
   in version.py, otherwise nothing happens.
2. If the manifest names a newer version, the installer it names is downloaded and its SHA-256
   checksum compared with the (signed) manifest. Only then is it marked "ready".
3. When you quit Panshow, a ready update is installed silently. (If Panshow was not quit
   normally, it is installed at the next start instead, and Panshow restarts by itself.)
4. Before installing, the current program folder is copied to a backup. If the new version
   then fails to start twice in a row, the backup is put back and that version is skipped.

Nothing about the user or their photos is sent: just a normal web request for the files.
"""
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request

from . import config, version

MANIFEST = "manifest.json"
MAX_SETUP_BYTES = 400 * 1024 * 1024
DETACHED = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP (for the installer)
# PowerShell does not run at all when started DETACHED (no console); give it a hidden one instead.
HIDDEN_CONSOLE = 0x08000000 | 0x00000200    # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP


# ------------------------------------------------------------------ files and state
def updates_dir():
    d = os.path.join(config.local_dir(), "updates")
    os.makedirs(d, exist_ok=True)
    return d


def _state_path():
    return os.path.join(updates_dir(), "state.json")


def load_state():
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            s = json.load(f)
            return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def save_state(s):
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, _state_path())


def _mark_bad(s, ver, why):
    bad = s.setdefault("bad", [])
    if ver not in bad:
        bad.append(ver)
    logging.warning("update %s will be skipped: %s", ver, why)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------ download side
def base_url():
    # PANSHOW_UPDATE_URL is only for testing; the signature check applies to any address.
    return os.environ.get("PANSHOW_UPDATE_URL") or \
        "https://github.com/%s/releases/latest/download/" % version.UPDATE_REPO


def _open(url):
    req = urllib.request.Request(url, headers={"User-Agent": "%s/%s" % (version.APP_NAME, version.VERSION)})
    return urllib.request.urlopen(req, timeout=30)


def _fetch(url, limit):
    with _open(url) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("file too large: " + url)
    return data


def verify(data, signature):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(version.UPDATE_PUBLIC_KEY))
    try:
        key.verify(signature, data)
        return True
    except InvalidSignature:
        return False


def check_and_download():
    """Look for a newer version and download it. Returns the new version or None."""
    base = base_url()
    data = _fetch(base + MANIFEST, 64 * 1024)
    sig = _fetch(base + MANIFEST + ".sig", 1024)
    if not verify(data, sig):
        logging.warning("update check: signature does NOT match, ignoring this update")
        return None
    m = json.loads(data.decode("utf-8"))
    new = str(m.get("version", ""))
    if m.get("app") != version.APP_NAME or version.parse(new) <= version.parse(version.VERSION):
        logging.info("update check: up to date (%s, latest %s)", version.VERSION, new or "?")
        return None
    s = load_state()
    if new in s.get("bad", []):
        logging.info("update check: %s is marked as not working, skipping", new)
        return None
    setup = os.path.basename(str(m["setup"]))
    size, sha = int(m["size"]), str(m["sha256"]).lower()
    if not setup.lower().endswith(".exe") or not (0 < size < MAX_SETUP_BYTES) or len(sha) != 64:
        logging.warning("update check: manifest looks wrong, ignoring")
        return None
    ready = s.get("ready") or {}
    path = os.path.join(updates_dir(), setup)
    if ready.get("version") == new and os.path.exists(path) and _sha256(path) == sha:
        return new
    logging.info("update check: downloading %s %s (%.1f MB)", version.APP_NAME, new, size / 1e6)
    part = path + ".part"
    h = hashlib.sha256()
    got = 0
    with _open(base + setup) as r, open(part, "wb") as f:
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            got += len(block)
            if got > size:
                raise ValueError("download larger than announced")
            h.update(block)
            f.write(block)
            time.sleep(0.002)               # be gentle with the computer while photos play
    if got != size or h.hexdigest() != sha:
        os.remove(part)
        logging.warning("update download did not match its checksum; discarded")
        return None
    os.replace(part, path)
    s = load_state()
    old = (s.get("ready") or {}).get("file")
    if old and old != setup:
        try:
            os.remove(os.path.join(updates_dir(), old))
        except OSError:
            pass
    s["ready"] = {"version": new, "file": setup, "sha256": sha, "notes": str(m.get("notes", ""))[:2000]}
    save_state(s)
    logging.info("update %s downloaded and verified; it will be installed when Panshow closes", new)
    return new


def background_loop(enabled):
    """Runs in a thread of the helper process: first check after a minute, then twice a day."""
    if not enabled:
        return
    time.sleep(float(os.environ.get("PANSHOW_UPDATE_DELAY", "60")))
    while True:
        try:
            check_and_download()
        except Exception as e:
            logging.info("update check failed (will try again later): %s", e)
        time.sleep(12 * 3600)


# ------------------------------------------------------------------ install side
def install_dir():
    """Folder of the installed program, or None when not installed by the setup program."""
    if not getattr(sys, "frozen", False):
        return None
    d = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.exists(os.path.join(d, "unins000.exe")) and \
            os.path.exists(os.path.join(d, version.APP_NAME + ".exe")):
        return d
    return None


def pending():
    s = load_state()
    r = s.get("ready")
    if not r:
        return None
    path = os.path.join(updates_dir(), os.path.basename(r.get("file", "")))
    if version.parse(r.get("version")) <= version.parse(version.VERSION) or r.get("version") in s.get("bad", []):
        return None
    if not os.path.exists(path) or _sha256(path) != r.get("sha256"):
        return None
    return dict(r, path=path)


def apply(r, relaunch):
    """Start the silent installer for update r (from pending()). Returns True if started."""
    d = install_dir()
    if d is None:
        return False
    s = load_state()
    tries = s.setdefault("attempts", {})
    n = tries.get(r["version"], 0)
    if n >= 2:
        _mark_bad(s, r["version"], "installing it failed twice")
        save_state(s)
        return False
    tries[r["version"]] = n + 1
    save_state(s)
    # back up the working version first, so it can be put back if the new one does not start
    backup = os.path.join(updates_dir(), "backup")
    try:
        if os.path.isdir(backup):
            shutil.rmtree(backup)
        shutil.copytree(d, backup)
        with open(os.path.join(backup, "backup-version.txt"), "w", encoding="utf-8") as f:
            f.write(version.VERSION)
    except Exception:
        logging.exception("could not back up the current version; installing anyway")
    args = [r["path"], "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/CLOSEAPPLICATIONS"]
    if relaunch:
        args.append("/RELAUNCH")
    logging.info("installing update %s (restart afterwards: %s)", r["version"], relaunch)
    subprocess.Popen(args, cwd=updates_dir(), creationflags=DETACHED, close_fds=True)
    return True


def after_start():
    """Tidy up once a new version runs."""
    s = load_state()
    r = s.get("ready")
    if r and version.parse(r.get("version")) <= version.parse(version.VERSION):
        try:
            os.remove(os.path.join(updates_dir(), os.path.basename(r.get("file", ""))))
        except OSError:
            pass
        s.pop("ready", None)
        s.get("attempts", {}).pop(r.get("version"), None)
        save_state(s)
        logging.info("now running version %s", version.VERSION)


# ------------------------------------------------------------------ health / fallback
def _health_path():
    return os.path.join(updates_dir(), "health.json")


def note_start():
    """Called just before the slideshow window opens."""
    h = {}
    try:
        with open(_health_path(), "r", encoding="utf-8") as f:
            h = json.load(f)
    except Exception:
        pass
    if h.get("version") != version.VERSION:
        h = {"version": version.VERSION, "failed_starts": 0}
    h["failed_starts"] = int(h.get("failed_starts", 0)) + 1     # cleared again by note_ok()
    with open(_health_path(), "w", encoding="utf-8") as f:
        json.dump(h, f)


def note_ok():
    """Called once the slideshow is drawing."""
    with open(_health_path(), "w", encoding="utf-8") as f:
        json.dump({"version": version.VERSION, "failed_starts": 0}, f)


def maybe_roll_back():
    """If this version failed to start twice and a backup of an older one exists, put it back.
    Returns True when the fallback was started (the caller should then exit)."""
    d = install_dir()
    if d is None:
        return False
    try:
        with open(_health_path(), "r", encoding="utf-8") as f:
            h = json.load(f)
    except Exception:
        return False
    if h.get("version") != version.VERSION or int(h.get("failed_starts", 0)) < 2:
        return False
    backup = os.path.join(updates_dir(), "backup")
    try:
        with open(os.path.join(backup, "backup-version.txt"), "r", encoding="utf-8") as f:
            old = f.read().strip()
    except Exception:
        return False
    if version.parse(old) >= version.parse(version.VERSION) or \
            not os.path.exists(os.path.join(backup, version.APP_NAME + ".exe")):
        return False
    s = load_state()
    _mark_bad(s, version.VERSION, "it did not start twice in a row")
    save_state(s)
    os.remove(_health_path())
    exe = os.path.join(d, version.APP_NAME + ".exe")
    script = os.path.join(updates_dir(), "roll-back.ps1")
    with open(script, "w", encoding="utf-8") as f:
        f.write("Wait-Process -Id %d -Timeout 60 -ErrorAction SilentlyContinue\n" % os.getpid())
        f.write("robocopy '%s' '%s' /MIR /XF backup-version.txt /R:3 /W:1 /NFL /NDL /NJH /NJS | Out-Null\n"
                % (backup.replace("'", "''"), d.replace("'", "''")))
        f.write("Start-Process -FilePath '%s'\n" % exe.replace("'", "''"))
    logging.warning("version %s did not start twice; going back to %s", version.VERSION, old)
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", script], creationflags=HIDDEN_CONSOLE, close_fds=True)
    return True
