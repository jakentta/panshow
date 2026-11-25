"""Prepare (and optionally publish) a Panshow release.

  release.py                 make dist\\release-<version>\\ with the installer, manifest.json and its signature
  release.py --publish       ...and upload it as a GitHub release (asks for confirmation)
  release.py --notes "text"  what is new in this version (shown on the GitHub release page)

The manifest is signed with the secret key from tools\\release_key.py; installed copies of
Panshow only accept an update whose manifest signature matches the public key built into them.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from panshow import version  # noqa: E402
import release_key  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def make_release(ver, setup_path, out_dir, notes="", key=None):
    """Write <out_dir>/<setup>, manifest.json and manifest.json.sig. Returns the file list."""
    key = key or release_key.load()
    pub = release_key.public_hex(key)
    if pub != version.UPDATE_PUBLIC_KEY:
        raise SystemExit("The signing key does not match the public key in panshow/version.py - stopping.")
    os.makedirs(out_dir, exist_ok=True)
    setup_name = "%s-Setup-%s.exe" % (version.APP_NAME, ver)
    dest = os.path.join(out_dir, setup_name)
    if os.path.abspath(setup_path) != os.path.abspath(dest):
        shutil.copy2(setup_path, dest)
    manifest = {
        "app": version.APP_NAME,
        "version": ver,
        "setup": setup_name,
        "size": os.path.getsize(dest),
        "sha256": sha256(dest),
        "notes": notes,
        "published": datetime.date.today().isoformat(),
    }
    data = json.dumps(manifest, indent=1).encode("utf-8")
    with open(os.path.join(out_dir, "manifest.json"), "wb") as f:
        f.write(data)
    with open(os.path.join(out_dir, "manifest.json.sig"), "wb") as f:
        f.write(key.sign(data))
    return [dest, os.path.join(out_dir, "manifest.json"), os.path.join(out_dir, "manifest.json.sig")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--yes", action="store_true", help="do not ask again before publishing")
    ap.add_argument("--notes", default="")
    a = ap.parse_args()
    ver = version.VERSION
    setup = os.path.join(ROOT, "dist", "%s-Setup-%s.exe" % (version.APP_NAME, ver))
    if not os.path.exists(setup):
        raise SystemExit("Not built yet: %s  (run build.ps1 first)" % setup)
    out = os.path.join(ROOT, "dist", "release-%s" % ver)
    files = make_release(ver, setup, out, a.notes)
    print("Release files ready in", out)
    for f in files:
        print("  ", os.path.basename(f), os.path.getsize(f), "bytes")
    if not a.publish:
        return
    if not a.yes and input("Publish Panshow %s publicly on GitHub (%s)? Type yes: " % (ver, version.UPDATE_REPO)) != "yes":
        print("Not published.")
        return
    cmd = ["gh", "release", "create", "v" + ver, "--repo", version.UPDATE_REPO,
           "--title", "%s %s" % (version.APP_NAME, ver), "--notes", a.notes or "Panshow %s" % ver] + files
    subprocess.run(cmd, check=True)
    print("Published: https://github.com/%s/releases/tag/v%s" % (version.UPDATE_REPO, ver))


if __name__ == "__main__":
    main()
