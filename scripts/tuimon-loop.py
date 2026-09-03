#!/usr/bin/env python3
"""
Run one of the waybar monitor scripts repeatedly inside a single interpreter.

Waybar can drive a custom module two ways: re-execute a script every `interval`
seconds, or read lines from a long-running "continuous" script. The one-shot
path pays interpreter startup plus `import psutil` on every single tick. On a
2013 Haswell laptop that measured ~0.2s of CPU per module per run, and with four
modules ticking every 5s it accounted for nearly all of waybar's cost (the bar's
cgroup burned 15% of a core while waybar itself used 1.5%).

This runner pays that cost once. It compiles the target script a single time,
then re-executes that code object in-process on each tick, so `sys.modules`
keeps psutil warm and only the actual measurement work is repeated.

The monitor scripts are not modified and remain fully usable one-shot, so both
modes stay available for comparison -- see `continuous` in the home-manager
module.

Usage:
    tuimon-loop.py SCRIPT INTERVAL [args passed to SCRIPT ...]
"""

import sys
import time
import traceback

if len(sys.argv) < 3:
    print(__doc__.strip(), file=sys.stderr)
    raise SystemExit(2)

script = sys.argv[1]
try:
    interval = float(sys.argv[2])
except ValueError:
    print(f"tuimon-loop: interval must be a number, got {sys.argv[2]!r}", file=sys.stderr)
    raise SystemExit(2)
if interval <= 0:
    print("tuimon-loop: interval must be > 0", file=sys.stderr)
    raise SystemExit(2)

script_args = sys.argv[3:]

with open(script) as fh:
    code = compile(fh.read(), script, "exec")

# The monitor scripts parse their own flags with argparse, so present argv the
# way they would see it when run directly.
sys.argv = [script] + script_args

while True:
    started = time.monotonic()
    # Fresh globals each tick so the scripts behave exactly as they do one-shot;
    # nothing leaks between iterations except the imported modules, which is the
    # whole point.
    globals_ns = {"__name__": "__main__", "__file__": script}
    try:
        exec(code, globals_ns)
    except Exception:
        # One bad sample must not take the module down. Waybar would keep
        # showing the last good value with nothing explaining why it froze, so
        # report on stderr (visible in the waybar journal) and carry on.
        traceback.print_exc()
    # Waybar reads stdout through a pipe, which is block-buffered, so an
    # unflushed line would sit in the buffer instead of updating the bar.
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        # Waybar went away; nothing left to write to.
        raise SystemExit(0)

    # Subtract the work we just did so the tick rate is the requested interval
    # rather than interval-plus-runtime. Several scripts block ~0.1s in
    # psutil.cpu_percent(), which would otherwise drift the period.
    remaining = interval - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)
