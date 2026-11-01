from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from . import debug_launch, doctor
from .debug_launch import DebugOptions, RUNGS, game_running
from .runtime.crossover import CrossOverRuntime
from .runtime.yaagl import YaaglError, YaaglRuntime

DEFAULT_RUNS = Path.cwd() / "runs"


def _debug_options(args: argparse.Namespace) -> DebugOptions:
    opts = replace(DebugOptions(), **RUNGS.get(args.rung, {}))
    if args.no_inject:
        opts.inject = False
    if args.no_core:
        opts.core = False
    if args.no_mods:
        opts.mods = []
    if args.mod:
        opts.mods = list(args.mod)
    if args.hunting:
        opts.hunting = True
    if args.crash_handler:
        opts.crash_handler = True
    for name in ("log_level", "winedebug", "dxmt_log_level"):
        if getattr(args, name) is not None:
            setattr(opts, name, getattr(args, name))
    for item in args.set or []:
        target, sep, value = item.partition("=")
        section, dot, key = target.partition(".")
        if not (sep and dot and section and key):
            raise YaaglError(f"--set expects SECTION.KEY=VALUE, got {item!r}")
        opts.ini_overrides.append((section, key, value))
    opts.max_seconds = args.max_seconds
    opts.game_args = list(args.game_arg or [])
    if args.injector:
        opts.injector = args.injector
    if args.probe:
        opts.probe = args.probe
    opts.block_crash_upload = not args.allow_crash_upload
    for item in args.env or []:
        key, _, value = item.partition("=")
        opts.extra_env[key] = value
    return opts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zzmmi", description="Mac-first XXMI-compatible mod loader for ZZZ")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check the Yaagl/Wine/DXMT/XXMI environment")

    dbg = sub.add_parser("debug-launch", help="Phase 0: launch with full logging and collect artifacts")
    dbg.add_argument("--rung", type=int, choices=sorted(RUNGS), help="preset from docs/PHASE0.md")
    dbg.add_argument("--no-inject", action="store_true", help="vanilla launch, no 3DMigoto")
    dbg.add_argument("--no-core", action="store_true", help="don't include Core/ZZMI/main.ini")
    dbg.add_argument("--no-mods", action="store_true", help="don't include the Mods folder")
    dbg.add_argument("--mod", action="append", metavar="FOLDER", help="include only this Mods/ folder (repeatable)")
    dbg.add_argument("--hunting", action="store_true", help="enable 3DMigoto hunting overlay")
    dbg.add_argument("--crash-handler", action="store_true",
                     help="enable 3DMigoto's crash handler (hangs ZZZ; only for debugging real crashes)")
    dbg.add_argument("--log-level", choices=["disabled", "warning", "info", "debug"])
    dbg.add_argument("--winedebug", help=f"WINEDEBUG for the game (default: {debug_launch.DEFAULT_WINEDEBUG})")
    dbg.add_argument("--dxmt-log-level", choices=["none", "error", "warn", "info", "debug"])
    dbg.add_argument("--env", action="append", metavar="KEY=VALUE", help="extra env var for the game")
    dbg.add_argument("--set", action="append", metavar="SECTION.KEY=VALUE",
                     help="override a d3dx.ini setting for this run (repeatable), e.g. System.load_library_redirect=0")
    dbg.add_argument("--max-seconds", type=float, help="end the session automatically after this many seconds")
    dbg.add_argument("--allow-crash-upload", action="store_true",
                     help="don't block HoYo's crashreport.exe (blocked by default during debug runs)")
    dbg.add_argument("--injector", choices=["system32", "xxmi", "bridge"],
                     help="how to load 3DMigoto (default: system32 proxy to DXMT)")
    dbg.add_argument("--probe", choices=sorted(debug_launch.PROBES),
                     help="inject a probe DLL instead of 3DMigoto (uses the bridge)")
    dbg.add_argument("--game-arg", action="append", metavar="ARG",
                     help="extra game command-line argument (repeatable), e.g. --game-arg=-use-d3d11")
    dbg.add_argument("--runtime", choices=["yaagl", "crossover"], default="yaagl",
                     help="Wine build to run the game with (default: Yaagl's)")
    dbg.add_argument("--bottle", default="ZZMMI", help="CrossOver bottle for --runtime crossover")
    dbg.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)

    summ = sub.add_parser("summarize", help="(re)generate summary.txt for a run directory")
    summ.add_argument("run_dir", type=Path)

    sub.add_parser("restore", help="put Wine's own d3d11/dxgi back after an interrupted session")

    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return doctor.main()
        if args.command == "debug-launch":
            opts = _debug_options(args)
            runtime = (CrossOverRuntime.discover(bottle=args.bottle) if args.runtime == "crossover"
                       else YaaglRuntime.discover())
            debug_launch.run(opts, args.runs_dir, runtime)
            return 0
        if args.command == "summarize":
            text = debug_launch.summarize(args.run_dir)
            (args.run_dir / "summary.txt").write_text(text)
            print(text)
            return 0
        if args.command == "restore":
            if game_running():
                raise YaaglError("ZZZ is running; quit it first.")
            runtime = YaaglRuntime.discover()
            restored = (runtime.restore_dxmt() + runtime.unhide(runtime.crash_uploaders())
                        + debug_launch.restore_system32_proxy(runtime, debug_launch.XxmiInPrefix(runtime).zzmi_dir))
            print("Restored: " + (", ".join(p.name for p in restored) or "nothing to restore"))
            return 0
    except YaaglError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 2
