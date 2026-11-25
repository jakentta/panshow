"""End-to-end test of listening to this computer's own sound: quietly play a generated test song
and check that the beats predicted from what is heard match the song's real beats in time.
Usage: test_listen_live.py <song file>"""
import os
import queue
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from panshow import music  # noqa: E402

if "wasapi" in sys.argv[2:]:
    import sounddevice as sd
    was = [i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"]][0]
    sd.default.device = (None, sd.query_hostapis()[was]["default_output_device"])
    sd.default.extra_settings = (None, sd.WasapiSettings(auto_convert=True))
    print("playing through WASAPI")
track = music.Track(sys.argv[1])
track.analyse()
source = "mic" if "mic" in sys.argv[2:] else "output"
player = music.Player(track, gain=0.25 if source == "mic" else 0.06)   # quiet (a bit louder for the mic)
while player.start_perf is None:
    time.sleep(0.01)
print("player says its sound reaches the speaker %.0f ms after being sent" % (1000 * player.stream.latency))
truth = player.start_perf + np.array(track.beats)  # when each beat really sounds
ctl, out = queue.Queue(), queue.Queue()
threading.Timer(16.0, lambda: ctl.put(("stop",))).start()
print("listening through:", source)
music.listen(source, ctl, out)                      # listens until stopped
player.stop_fade = time.perf_counter() + 0.3
time.sleep(0.5)
player.close()

grids = []
while not out.empty():
    m = out.get()
    if m[1] is not None:
        grids.append(m)
print("beat reports: %d (with a steady beat: %d)" % (len(grids) + 0, len(grids)))
errs = []
for _, beat_time, period, conf, bar in grids:
    pred = beat_time + period * np.arange(1, 8)
    e = (pred[:, None] - truth[None, :])
    nearest = e[np.arange(len(pred)), np.abs(e).argmin(axis=1)]
    errs.append(np.median(nearest))
if errs:
    errs = np.array(errs)
    print("tempo heard: %.1f BPM (song: %.1f BPM)" % (60.0 / grids[-1][2], track.bpm))
    print("predicted beats vs real beats: median %+.0f ms, %.0f%% of reports within 70 ms"
          % (1000 * np.median(errs), 100 * np.mean(np.abs(errs) < 0.07)))
