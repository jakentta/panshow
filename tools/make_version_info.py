"""Write the Windows version information (product name, version...) that PyInstaller puts into
Panshow.exe. Usage: make_version_info.py <output file>"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from panshow import version  # noqa: E402


def main(out):
    parts = [int(x) for x in version.VERSION.split(".")]
    numbers = tuple((parts + [0, 0, 0, 0])[:4])
    strings = [
        ("CompanyName", version.APP_NAME),
        ("FileDescription", "%s photo slideshow" % version.APP_NAME),
        ("FileVersion", version.VERSION),
        ("InternalName", version.APP_NAME),
        ("LegalCopyright", "MIT License"),
        ("OriginalFilename", version.APP_NAME + ".exe"),
        ("ProductName", version.APP_NAME),
        ("ProductVersion", version.VERSION),
    ]
    text = "VSVersionInfo(\n  ffi=FixedFileInfo(filevers=%r, prodvers=%r, mask=0x3F, flags=0x0, OS=0x40004,\n" \
           "    fileType=0x1, subtype=0x0, date=(0, 0)),\n  kids=[\n    StringFileInfo([StringTable('040904B0', [\n" \
           % (numbers, numbers)
    text += "".join("      StringStruct(%r, %r),\n" % kv for kv in strings)
    text += "    ])]),\n    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n  ]\n)\n"
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    main(sys.argv[1])
