"""Test the update download + checks against a local web server, without installing anything.
Runs in a throw-away data folder. Usage: test_update.py <work dir>"""
import http.server
import os
import shutil
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

work = os.path.abspath(sys.argv[1])
if os.path.isdir(work):
    shutil.rmtree(work)
os.makedirs(os.path.join(work, "feed"))
os.makedirs(os.path.join(work, "data"))
os.environ["LOCALAPPDATA"] = os.path.join(work, "data")      # keep test files away from the real ones
os.environ["APPDATA"] = os.path.join(work, "data")

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import release  # noqa: E402
import release_key  # noqa: E402
from panshow import update, version  # noqa: E402

# A throw-away key pair: these tests never use (or need) the real signing key.
_test_key = Ed25519PrivateKey.generate()
release_key.load = lambda: _test_key
version.UPDATE_PUBLIC_KEY = release_key.public_hex(_test_key)

feed = os.path.join(work, "feed")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=feed, **k)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["PANSHOW_UPDATE_URL"] = "http://127.0.0.1:%d/" % srv.server_port

fake_setup = os.path.join(work, "fake-setup.exe")
with open(fake_setup, "wb") as f:
    f.write(os.urandom(3_000_000))
results = []


def case(name, expect, fn):
    try:
        got = fn()
    except Exception as e:
        got = "error: %s" % e
    ok = (got == expect) if not callable(expect) else expect(got)
    results.append(ok)
    print("%-55s -> %-12s %s" % (name, got, "OK" if ok else "FAILED"))


def clear_state():
    shutil.rmtree(update.updates_dir(), ignore_errors=True)


newer = ".".join(str(x) for x in (version.parse(version.VERSION)[:-1] + (version.parse(version.VERSION)[-1] + 1,)))

# 1. a correctly signed newer version is downloaded and marked ready
release.make_release(newer, fake_setup, feed, "test")
clear_state()
case("signed newer version is downloaded and verified", newer, update.check_and_download)
case("...and is then waiting to be installed", newer, lambda: (update.pending() or {}).get("version"))

# 2. same version as installed -> nothing to do
release.make_release(version.VERSION, fake_setup, feed, "test")
clear_state()
case("same version as installed is ignored", None, update.check_and_download)

# 3. signed with a different (attacker's) key -> refused
release.make_release(newer, fake_setup, feed, "test")
other = Ed25519PrivateKey.generate()
with open(os.path.join(feed, "manifest.json"), "rb") as f:
    data = f.read()
with open(os.path.join(feed, "manifest.json.sig"), "wb") as f:
    f.write(other.sign(data))
clear_state()
case("manifest signed with a different key is refused", None, update.check_and_download)

# 4. manifest changed after signing (e.g. different checksum) -> refused
release.make_release(newer, fake_setup, feed, "test")
with open(os.path.join(feed, "manifest.json"), "rb") as f:
    data = f.read()
with open(os.path.join(feed, "manifest.json"), "wb") as f:
    f.write(data.replace(b'"test"', b'"evil"'))
clear_state()
case("manifest edited after signing is refused", None, update.check_and_download)

# 5. installer swapped on the server -> checksum mismatch, discarded
release.make_release(newer, fake_setup, feed, "test")
with open(os.path.join(feed, "%s-Setup-%s.exe" % (version.APP_NAME, newer)), "r+b") as f:
    f.seek(1000)
    f.write(b"tampered")
clear_state()
case("installer changed on the server is discarded", None, update.check_and_download)
case("...and nothing is waiting to be installed", None, update.pending)

# 6. a version marked as 'not working' is skipped
release.make_release(newer, fake_setup, feed, "test")
clear_state()
s = update.load_state()
s["bad"] = [newer]
update.save_state(s)
case("version marked as not working is skipped", None, update.check_and_download)

# 7. not installed by the setup program (like this test) -> never tries to install
release.make_release(newer, fake_setup, feed, "test")
clear_state()
update.check_and_download()
case("a copy not installed by Setup never installs updates", False, lambda: update.apply(update.pending(), False))

srv.shutdown()
print("\nALL PASSED" if all(results) else "\nSOME TESTS FAILED")
sys.exit(0 if all(results) else 1)
