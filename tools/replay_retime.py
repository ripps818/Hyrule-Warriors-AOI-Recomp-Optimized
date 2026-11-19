#!/usr/bin/env python3
"""Make a recorded input session (TOTK_INPUT_RECORD / --record-input) more robust to load-time variation.

A recording times its events on the world clock from the first present, so a load that takes longer than when it was
recorded makes the later inputs land too early. This tool
  * stretches the largest idle gap of the recording (the load between the last menu press and the first gameplay input)
    by --delay seconds, or at --at <seconds> if the load is elsewhere, and
  * lengthens button presses to at least --min-hold ms (a press of one or two frames can fall between frames while the
    game is loading).
usage: replay_retime.py in.txt out.txt [--delay 8] [--at SECONDS] [--min-hold 100]
"""
import argparse
import re


def parse(path):
    rows = []   # (kind, time_ms, name, rest, raw)
    for line in open(path):
        raw = line.rstrip("\n")
        m = re.match(r"@([0-9.]+) (\S+)\s*(.*)", raw)
        if m:
            rows.append(("timed", float(m[1]), m[2], m[3], raw))
        else:
            rows.append(("other", None, None, None, raw))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--delay", type=float, default=8.0, help="seconds added at the load gap")
    ap.add_argument("--at", type=float, help="insert the delay at this time (s) instead of the largest gap")
    ap.add_argument("--min-hold", type=float, default=100.0, help="minimum button hold in ms")
    a = ap.parse_args()
    rows = parse(a.src)
    timed = sorted((r for r in rows if r[0] == "timed"), key=lambda r: r[1])
    end = 0.0
    gaps = []
    for r in timed:
        gaps.append((r[1] - end, end, r[1]))
        end = r[1] + (float(r[3].split()[-1]) if r[3] and re.match(r"^-?[0-9.]+$", r[3].split()[-1]) else 0)
    split = a.at * 1000 if a.at is not None else max(gaps)[2]
    print(f"inserting {a.delay:.1f} s at {split / 1000:.1f} s (largest gap {max(gaps)[0] / 1000:.1f} s)" if a.at is None
          else f"inserting {a.delay:.1f} s at {a.at:.1f} s")
    buttons = {"A", "B", "X", "Y", "L", "R", "ZL", "ZR", "PLUS", "MINUS", "LEFT", "UP", "RIGHT", "DOWN", "LCLICK", "RCLICK"}
    out = []
    for kind, t, name, rest, raw in rows:
        if kind != "timed":
            out.append(raw)
            continue
        t2 = t + (a.delay * 1000 if t >= split else 0)
        if name in buttons:
            parts = rest.split()
            hold = float(parts[0]) if parts else 66.7
            rest = f"{max(hold, a.min_hold):.1f}"
        out.append(f"@{t2:.1f} {name} {rest}".rstrip())
    out.sort(key=lambda l: (0 if not l.startswith("@") else 1, float(l[1:].split()[0]) if l.startswith("@") else 0))
    open(a.dst, "w").write("\n".join(out) + "\n")
    print(f"wrote {a.dst}: {sum(1 for l in out if l.startswith('@'))} timed events")


if __name__ == "__main__":
    main()
