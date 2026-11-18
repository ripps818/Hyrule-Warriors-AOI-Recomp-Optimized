#!/usr/bin/env python3
"""Replay a recorded input session and summarise frame times, to compare settings or builds on the same scene.

Record once from a known save state, so every replay starts the same (the runtime writes one line per press and stick
segment). From a loaded save: copy the profile to work/saves/battle and to work/saves/battle-pristine, then
    python tools/hwaoi.py launch --profile-dir work/saves/battle --record-input work/replays/battle.txt
(load the save from the title menu, start the battle, play ~1-2 minutes, close the game) and replay with
    python tools/replay_bench.py work/replays/battle.txt --profile-dir work/saves/battle --restore-save work/saves/battle-pristine
From a fresh profile instead, record on an empty work/saves/hwaoi196608; the bench then deletes it before each run.
Replay and measure:
    python tools/replay_bench.py work/replays/intro.txt --seconds 150
A/B two settings, alternating runs so drift cancels:
    python tools/replay_bench.py work/replays/intro.txt --seconds 150 --runs 3 \\
        --a TOTK_BARRIER_MERGE=0 --b TOTK_BARRIER_MERGE=1

Each run starts the game with --replay-script (TOTK_INPUT_SCRIPT is ignored by the launcher), lets it play for --seconds, stops it and reads the "perf: frame N:
interval avg ..." lines (one per 60 presents) from the run's stderr.log. The first --skip windows are left out (loading).
Replay timing follows game time (see runtime/input.c), but the game is not deterministic (AI allies and enemies move
randomly), so the scene differs run to run. Compare means over several runs: use --runs 5 or more for small effects.
The summary prints the run-to-run sd and an approximate 95% interval for B - A; a difference inside it is noise.
"""
import argparse
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERF = re.compile(r"perf: frame (\d+): interval avg ([0-9.]+) ms max ([0-9.]+) ms \(([0-9.]+) fps\)")
GPU = re.compile(r"perf: frame (\d+): gpu avg ([0-9.]+) ms")


def run_once(script, seconds, env_extra, label, profile, snapshot, fresh):
    if snapshot:   # every run starts from the same copy of the save
        shutil.rmtree(profile, ignore_errors=True)
        shutil.copytree(snapshot, profile)
    elif fresh:
        shutil.rmtree(profile, ignore_errors=True)
    env = dict(os.environ)
    env.setdefault("HWAOI_TRACE", "sched,perf")
    # Unattended: silent (explicit HWAOI_AUDIO=none mode), do not take the focus when the window opens, and tell the game
    # it keeps the focus so it does not pause when another window is in front.
    env.setdefault("HWAOI_AUDIO", "none")
    env.setdefault("SDL_WINDOW_ACTIVATE_WHEN_SHOWN", "0")
    env.setdefault("TOTK_ASSUME_FOCUS", "1")
    env.update(env_extra)
    proc = subprocess.run([sys.executable, str(ROOT / "tools/hwaoi.py"), "launch", "--profile-dir", str(profile),
                           "--replay-script", str(Path(script).resolve())], env=env, cwd=ROOT,
                          capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit(f"launch failed: {proc.stderr.strip() or proc.stdout.strip()}")
    start = proc.stdout.find("{")
    info = json.loads(proc.stdout[start:]) if start >= 0 else {}
    pid, report = info.get("pid"), Path(info.get("report_directory", ""))
    if not pid or not report.is_dir():
        raise SystemExit("launch printed no pid/report_directory")
    time.sleep(seconds)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    time.sleep(2)
    log = (report / "stderr.log").read_text(errors="replace")
    gpu = {int(m[1]): float(m[2]) for m in GPU.finditer(log)}
    rows = [(int(m[1]), float(m[2]), float(m[3]), float(m[4]), gpu.get(int(m[1]), 0.0)) for m in PERF.finditer(log)]
    fatal = [l for l in log.splitlines() if "FATAL" in l or "can't create resource" in l]
    return {"label": label, "report": str(report), "rows": rows, "fatal": fatal}


def summarize(res, skip):
    rows = res["rows"][skip:]
    if not rows:
        return None
    return {"windows": len(rows), "interval_ms": statistics.mean(r[1] for r in rows),
            "fps": statistics.mean(r[3] for r in rows), "worst_ms": max(r[2] for r in rows),
            "p95_interval_ms": sorted(r[1] for r in rows)[int(0.95 * (len(rows) - 1))],
            "gpu_ms": statistics.mean(r[4] for r in rows)}


def parse_env(items):
    out = {}
    for item in items or []:
        key, _, value = item.partition("=")
        out[key] = value
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", help="a recording made with TOTK_INPUT_RECORD")
    ap.add_argument("--seconds", type=float, default=150, help="how long each run plays")
    ap.add_argument("--skip", type=int, default=5, help="leading 60-frame windows to ignore (loading)")
    ap.add_argument("--profile-dir", default="work/saves/hwaoi196608", help="the profile the game runs on (under work/saves)")
    ap.add_argument("--restore-save", metavar="DIR", help="copy this snapshot over the profile before every run (a recording made from a loaded save)")
    ap.add_argument("--keep-save", action="store_true", help="do not reset the profile before each run")
    ap.add_argument("--runs", type=int, default=1, help="runs per side")
    ap.add_argument("--a", nargs="*", help="KEY=VALUE settings of side A (default: none)")
    ap.add_argument("--b", nargs="*", help="KEY=VALUE settings of side B; without it only A runs")
    args = ap.parse_args()
    if not Path(args.script).is_file():
        raise SystemExit(f"no such recording: {args.script}")
    profile = (ROOT / args.profile_dir).resolve()
    snapshot = Path(args.restore_save).resolve() if args.restore_save else None
    if snapshot and not snapshot.is_dir():
        raise SystemExit(f"no such snapshot: {snapshot}")
    sides = [("A", parse_env(args.a))] + ([("B", parse_env(args.b))] if args.b is not None else [])
    results = {name: [] for name, _ in sides}
    for i in range(args.runs):
        for name, env in sides:   # alternate A, B, A, B
            print(f"run {i + 1}/{args.runs} side {name} {env or ''}", flush=True)
            res = run_once(args.script, args.seconds, env, name, profile, snapshot, not args.keep_save)
            if res["fatal"]:
                print("  FAILED:", res["fatal"][0])
            summary = summarize(res, args.skip)
            print("  ", summary or "no perf windows", res["report"])
            if summary:
                results[name].append(summary)
    print()
    for name, _ in sides:
        runs = results[name]
        if runs:
            print(f"{name}: interval {statistics.mean(r['interval_ms'] for r in runs):.2f} ms, "
                  f"{statistics.mean(r['fps'] for r in runs):.1f} fps, gpu {statistics.mean(r['gpu_ms'] for r in runs):.2f} ms, p95 {statistics.mean(r['p95_interval_ms'] for r in runs):.2f} ms, "
                  f"worst {max(r['worst_ms'] for r in runs):.0f} ms over {len(runs)} run(s)")
    def spread(values):
        return statistics.stdev(values) if len(values) > 1 else float("nan")
    for name, _ in sides:
        runs = results[name]
        if len(runs) > 1:
            print(f"{name}: run-to-run sd: interval {spread([r['interval_ms'] for r in runs]):.2f} ms, "
                  f"gpu {spread([r['gpu_ms'] for r in runs]):.2f} ms (the scene is not deterministic: judge a difference against this)")
    def diff(metric, label):
        xa, xb = [r[metric] for r in results["A"]], [r[metric] for r in results["B"]]
        if len(xa) < 2 or len(xb) < 2:
            return
        d = statistics.mean(xb) - statistics.mean(xa)
        se = (statistics.variance(xa) / len(xa) + statistics.variance(xb) / len(xb)) ** 0.5
        verdict = "real" if abs(d) > 2 * se else "within noise"
        print(f"B - A {label}: {d:+.3f} ms +/- {1.96 * se:.3f} (95%) -> {verdict}")
    if len(sides) == 2 and results["A"] and results["B"]:
        diff("interval_ms", "interval")
        diff("gpu_ms", "gpu")
    if len(sides) == 2 and results["A"] and results["B"]:
        a = statistics.mean(r["interval_ms"] for r in results["A"])
        b = statistics.mean(r["interval_ms"] for r in results["B"])
        print(f"B - A interval: {b - a:+.2f} ms ({(b - a) / a * 100:+.1f}%)")
        ga = statistics.mean(r["gpu_ms"] for r in results["A"])
        gb = statistics.mean(r["gpu_ms"] for r in results["B"])
        print(f"B - A gpu: {gb - ga:+.2f} ms ({(gb - ga) / ga * 100:+.1f}%)" if ga else "")


if __name__ == "__main__":
    main()
