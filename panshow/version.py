"""Name, version and update source of the program. Change VERSION for every release."""
APP_NAME = "Panshow"
VERSION = "1.5.1"

# Releases are published on GitHub; Panshow looks for newer versions there.
UPDATE_REPO = "jakentta/panshow"

# Public half of the key that signs every update. Panshow refuses any update whose signature
# does not match, so nobody else can push a fake update. (The secret half never leaves the
# computer the releases are made on.)
UPDATE_PUBLIC_KEY = "a498f06fb23c8667a247fcaca39afcf0c1b23c0dc81677b9f16e8ee0edfa03e1"


def parse(v):
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split("."))
    except ValueError:
        return (0,)
