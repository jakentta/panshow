"""Run the background worker pipeline without a window and print timings.

Usage: python tools\\bench_worker.py [folder] [count]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panshow import config, worker  # noqa: E402


def main():
    s = config.load()
    if len(sys.argv) > 1:
        s.folder = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    s = s.validated()
    t0 = time.perf_counter()
    w = worker.Worker(s, 2560, 1600)
    print("startup+scan %.2fs, files=%d" % (time.perf_counter() - t0, len(w.order.files) if w.order else 0))
    kinds = {}
    for i in range(count):
        t1 = time.perf_counter()
        item = w.produce()
        dt = time.perf_counter() - t1
        for plan, tex in item["parts"]:
            kinds[plan["kind"]] = kinds.get(plan["kind"], 0) + 1
            z = plan["r0"][2] / plan["r1"][2]
            print("%5.0fms  %-14s %-5s faces=%d/%d zoom=%.2f tex=%dx%d bicubic=%s low=%s%s" % (
                dt * 1000, plan["kind"], plan["mode"], plan["main_faces"], len(plan["faces"]), z,
                tex.size[0], tex.size[1], plan["bicubic"], plan["low_res"],
                "  (pair)" if len(item["parts"]) > 1 else ""))
    w.close()
    print(kinds)


if __name__ == "__main__":
    main()
