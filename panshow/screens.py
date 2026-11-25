"""List the connected screens and pick the one to show photos on."""
import ctypes

import glfw


def _name(m):
    n = glfw.get_monitor_name(m)
    return n.decode("utf-8", "replace") if isinstance(n, bytes) else str(n)


def _addr(m):
    # glfw gives a new Python object for the same screen each time; compare the real address
    return ctypes.cast(m, ctypes.c_void_p).value if m else None


def describe():
    """Returns a list of dicts. Needs glfw.init() to have been called."""
    prim = _addr(glfw.get_primary_monitor())
    out = []
    seen = {}
    for i, m in enumerate(glfw.get_monitors() or []):
        vm = glfw.get_video_mode(m)
        x, y = glfw.get_monitor_pos(m)
        d = {"monitor": m, "index": i + 1, "name": _name(m), "w": vm.size.width, "h": vm.size.height,
             "hz": vm.refresh_rate, "primary": _addr(m) == prim, "pos": (x, y)}
        key = "%s|%dx%d|%d,%d" % (d["name"], d["w"], d["h"], x, y)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:                       # two identical screens in the same place (mirrored)
            key += "|%d" % seen[key]
        d["key"] = key
        d["label"] = "Screen %d: %s, %d x %d, %d Hz%s" % (
            d["index"], d["name"], d["w"], d["h"], d["hz"], " (main screen)" if d["primary"] else "")
        out.append(d)
    return out


def pick(setting):
    """'auto' = the screen with the most pixels (the 4K one when it is connected)."""
    screens = describe()
    if not screens:
        return None
    for d in screens:
        if d["key"] == setting:
            return d
    # a choice saved by an older version ("name|WxH"), or the screen moved: match name and size
    for d in screens:
        if setting and setting != "auto" and d["key"].startswith(setting.split("|")[0] + "|") and \
                d["key"].split("|")[1] == (setting.split("|") + ["", ""])[1]:
            return d
    return max(screens, key=lambda d: (d["w"] * d["h"], d["primary"]))


def list_for_settings():
    if not glfw.init():
        return []
    try:
        return [{k: v for k, v in d.items() if k != "monitor"} for d in describe()]
    finally:
        glfw.terminate()
