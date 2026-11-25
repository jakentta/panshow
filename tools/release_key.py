"""Create (once) the secret key that signs Panshow updates, and print its public half.

The secret key is stored OUTSIDE the project, in %USERPROFILE%\\.panshow-release\\.
Back it up (e.g. to a USB stick). If it is lost, installed copies can no longer be updated.
Never put it in the project folder, a build, or GitHub."""
import os
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KEY_DIR = os.path.join(os.path.expanduser("~"), ".panshow-release")
KEY_PATH = os.path.join(KEY_DIR, "signing_key.pem")


def load():
    with open(KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def public_hex(key):
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def main():
    if os.path.exists(KEY_PATH):
        print("Key already exists (not changed):", KEY_PATH)
    else:
        os.makedirs(KEY_DIR, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        with open(KEY_PATH, "xb") as f:
            f.write(pem)
        print("New signing key created:", KEY_PATH)
    print("Public key (goes into panshow/version.py):", public_hex(load()))


if __name__ == "__main__":
    sys.exit(main())
