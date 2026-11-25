"""List every DLL that the built program needs but does not carry with it, and say whether
each one is a normal part of Windows. Usage: check_deps.py <dist folder>"""
import os
import re
import sys

import pefile

root = sys.argv[1]
present = set()
binaries = []
for d, _, files in os.walk(root):
    for f in files:
        present.add(f.lower())
        if f.lower().endswith((".dll", ".pyd", ".exe")):
            binaries.append(os.path.join(d, f))

# Part of every Windows 10/11 installation (no extra install needed)
WINDOWS = re.compile(r"^(api-ms-win-.*|ext-ms-.*|kernel32|kernelbase|user32|gdi32|gdiplus|advapi32|shell32|"
                     r"ole32|oleaut32|comctl32|comdlg32|ws2_32|wsock32|winmm|version|shlwapi|crypt32|bcrypt|"
                     r"ncrypt|secur32|rpcrt4|setupapi|cfgmgr32|imm32|msimg32|opengl32|glu32|dwmapi|uxtheme|"
                     r"dbghelp|psapi|iphlpapi|userenv|netapi32|mpr|winspool\.drv|dxgi|d3d11|d3d9|d3d12|dxva2|"
                     r"mfplat|mf|mfreadwrite|mfuuid|evr|propsys|wtsapi32|powrprof|ntdll|ucrtbase|msvcrt|"
                     r"shcore|hid|winhttp|wininet|urlmon|normaliz|mscoree|oleacc|usp10|dnsapi|msi|"
                     r"credui|cryptui|wintrust|bcryptprimitives|mswsock|avrt|ksuser|mmdevapi|comppkgsup|"
                     r"d2d1|dwrite|windowscodecs|vfw32|avicap32|msacm32|msvfw32|cabinet|sspicli|"
                     r"dbgeng|imagehlp|authz|activeds|wldap32|mlang)\.dll$|^winspool\.drv$", re.I)

missing = {}
for b in binaries:
    try:
        pe = pefile.PE(b, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                               pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        names = [e.dll.decode() for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]
        names += [e.dll.decode() for e in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])]
    except Exception as e:
        print("could not read", b, e)
        continue
    for n in names:
        if n.lower() not in present:
            missing.setdefault(n.lower(), set()).add(os.path.relpath(b, root))

bad = {n: users for n, users in missing.items() if not WINDOWS.match(n)}
print("binaries checked:", len(binaries))
print("needed from Windows itself (fine):", len(missing) - len(bad))
if bad:
    print("NOT part of Windows and NOT in the package:")
    for n, users in sorted(bad.items()):
        print("  %-28s needed by %s" % (n, ", ".join(sorted(users)[:4])))
else:
    print("Everything else is inside the package.")
