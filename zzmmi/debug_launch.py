"""Phase 0 harness: launch ZZZ with 3DMigoto under full logging and collect every artifact.

Each run gets its own runs/<timestamp>/ directory containing the Wine log, DXMT
logs, 3DMigoto's d3d11_log.txt, the XXMI Launcher log slice, the effective
d3dx.ini, any macOS crash reports, and a summary.txt with the interesting lines.
"""
from __future__ import annotations

import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .ini import IniDoc
from .runtime.xxmi_legacy import (ARMED_MARKERS, ERROR_MARKERS, FAILED_MARKERS, LOADED_MARKER,
                                  XxmiInPrefix)
from .runtime.yaagl import GAME_EXE, YaaglError, YaaglRuntime, to_windows_path

BRIDGE_BUILD = Path(__file__).resolve().parent.parent / "bridge" / "build"
BRIDGE_EXE = BRIDGE_BUILD / "zzmmi-bridge.exe"
# Probe DLLs injected instead of 3DMigoto to bisect what the anti-tamper reacts to.
PROBES = {
    "noop": BRIDGE_BUILD / "probes" / "noop" / "zzmmi_noop.dll",
    "noop-skip-crash": BRIDGE_BUILD / "probes" / "noop-skip-crash" / "zzmmi_noop_skip_crash.dll",
    "noop-patch-crash": BRIDGE_BUILD / "probes" / "noop-patch-crash" / "zzmmi_noop_patch_crash.dll",
    "noop-hide-module": BRIDGE_BUILD / "probes" / "noop-hide-module" / "zzmmi_noop_hide_module.dll",
    "passthru": BRIDGE_BUILD / "probes" / "passthru" / "d3d11.dll",
    # Same DLL, but no injection: copied into the game folder for the session, so Unity's
    # LoadLibrary("d3d11.dll") picks it up (the game has a d3d11=native,builtin override).
    "passthru-gamedir": BRIDGE_BUILD / "probes" / "passthru" / "d3d11.dll",
}
GAMEDIR_PROBES = {"passthru-gamedir"}
NATIVE_D3D11 = {"d3d11": "native,builtin", "d3dcompiler_47": "native,builtin"}
BRIDGE_STATUS_RE = re.compile(r"^(HOOKED|STARTED \d+|INJECTED|ERROR \w+ -?\d+)\s*$", re.M)
DEFAULT_WINEDEBUG = "fixme-all,err-unwind,+timestamp,+loaddll"
CRASH_REPORTS = Path.home() / "Library" / "Logs" / "DiagnosticReports"
UNITY_CRASHES = Path("drive_c/users/crossover/AppData/Local/Temp/miHoYo/ZenlessZoneZero/Crashes")
UNITY_PLAYER_LOG = Path("drive_c/users/crossover/AppData/LocalLow/miHoYo/ZenlessZoneZero/Player.log")


@dataclass
class DebugOptions:
    inject: bool = True
    core: bool = True
    mods: list[str] | None = None  # None: all mods, []: no mods, [names]: only those
    hunting: bool = False
    # 3DMigoto's crash handler replaces the process's unhandled-exception filter. ZZZ's
    # MHYPBase.dll raises access violations on purpose and handles them itself; with the
    # handler installed, every one of those becomes a minidump and the game hangs on a
    # black screen before its first frame. Only enable it to debug a real crash.
    crash_handler: bool = False
    log_level: str = "info"  # 3DMigoto: disabled | warning | info | debug
    winedebug: str = DEFAULT_WINEDEBUG
    dxmt_log_level: str = "info"
    extra_env: dict[str, str] = field(default_factory=dict)
    ini_overrides: list[tuple[str, str, str]] = field(default_factory=list)  # (section, key, value)
    max_seconds: float | None = None  # kill the session after this long
    block_crash_upload: bool = True
    injector: str = "system32"  # system32 proxy | xxmi: XXMI Launcher | bridge: zzmmi-bridge.exe
    probe: str | None = None  # inject a PROBES DLL instead of 3DMigoto (bridge only)
    game_args: list[str] = field(default_factory=list)  # e.g. -use-d3d11 (what Twintail passes with XXMI)


# The Phase 0 ladder (docs/PHASE0.md). Rungs 4-5 are rung 3 plus --mod NAME.
RUNGS = {
    1: dict(inject=False),
    2: dict(core=False, mods=[], hunting=True),
    3: dict(mods=[], hunting=True),
}


def log(msg: str) -> None:
    print(f"[zzmmi] {msg}", flush=True)


def game_running() -> bool:
    out = subprocess.run(["pgrep", "-f", re.escape(GAME_EXE)], capture_output=True, text=True).stdout
    for pid in out.split():
        cmd = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True, text=True).stdout
        if not re.search(r"(^|/)(sh|bash|zsh)( |$)|pgrep", cmd):
            return True
    return False


def bridge_status(game_log: Path) -> list[str]:
    if not game_log.exists():
        return []
    text = game_log.read_text(encoding="utf-8", errors="replace")
    return [m.group(1) for m in BRIDGE_STATUS_RE.finditer(text)]


def patch_d3dx(path: Path, opts: DebugOptions, loader_exe: str | None = None) -> None:
    doc = IniDoc.load(path)
    if loader_exe:
        # 3DMigoto only initializes in the target and in the [Loader] loader process.
        doc.set("Loader", "loader", loader_exe)
    doc.set("Logging", "log_level", opts.log_level)
    doc.set("Logging", "unbuffered", 1)
    doc.set("Logging", "show_warnings", 1)
    doc.set("Logging", "crash", 1 if opts.crash_handler else 0)
    doc.set("Hunting", "hunting", 1 if opts.hunting else 0)
    if not opts.core:
        doc.disable("Include", "include", r"Core\ZZMI\main.ini")
    if opts.mods is not None:
        doc.disable("Include", "include_recursive", "Mods")
        for mod in opts.mods:
            doc.add("Include", "include_recursive", f"Mods\\{mod}")
    for section, key, value in opts.ini_overrides:
        doc.set(section, key, value)
    doc.save(path)


SYSTEM32_PROXY_DLL = "dxmt_d3d11.dll"
SYSTEM32_ZZMI_LINKS = ("d3dx.ini", "d3dx_user.ini", "d3d11_log.txt",
                       "Core", "Mods", "ShaderFixes", "ShaderCache")
SYSTEM32_BACKUP_SUFFIX = ".zzmmi-system32-backup"
SYSTEM32_NEW_SUFFIX = ".zzmmi-system32-new"


def restore_system32_proxy(runtime: YaaglRuntime, zzmi_dir: Path) -> list[Path]:
    """Undo a system32 3DMigoto -> DXMT deployment, including after a hard kill."""
    restored = []
    system32 = runtime.system32
    for name in SYSTEM32_ZZMI_LINKS:
        path = system32 / name
        if path.is_symlink():
            path.unlink()
            restored.append(path)

    new_files = [
        (system32 / SYSTEM32_PROXY_DLL, runtime.dxmt_dir / "d3d11.dll"),
        (runtime.lib_windows / SYSTEM32_PROXY_DLL, runtime.dxmt_dir / "d3d11.dll"),
        (system32 / "D3DCompiler_47.dll", zzmi_dir / "d3dcompiler_47.dll"),
    ]
    for path, expected in new_files:
        marker = path.with_name(path.name + SYSTEM32_NEW_SUFFIX)
        if marker.exists():
            if not path.exists() or path.read_bytes() == expected.read_bytes():
                path.unlink(missing_ok=True)
                marker.unlink()
                restored.append(path)

    for name in ("d3d11.dll", "D3DCompiler_47.dll"):
        path = system32 / name
        backup = path.with_name(path.name + SYSTEM32_BACKUP_SUFFIX)
        if backup.exists():
            backup.replace(path)
            restored.append(path)
    return restored


def install_system32_proxy(runtime: YaaglRuntime, zzmi_dir: Path) -> None:
    """Load 3DMigoto from system32 and chain it to DXMT under another name.

    Called inside dxmt_session(), which owns restoration of lib_windows/d3d11.dll.
    Adjacent backups and markers let `zzmmi restore` recover after an interrupted run.
    """
    restore_system32_proxy(runtime, zzmi_dir)  # clean up an interrupted previous session
    system32 = runtime.system32
    for path in (system32 / SYSTEM32_PROXY_DLL, runtime.lib_windows / SYSTEM32_PROXY_DLL):
        shutil.copy2(runtime.dxmt_dir / "d3d11.dll", path)
        path.with_name(path.name + SYSTEM32_NEW_SUFFIX).write_text("")

    for name, source in (("d3d11.dll", zzmi_dir / "d3d11.dll"),
                         ("D3DCompiler_47.dll", zzmi_dir / "d3dcompiler_47.dll")):
        path = system32 / name
        if path.exists():
            backup = path.with_name(path.name + SYSTEM32_BACKUP_SUFFIX)
            shutil.copy2(path, backup)
            shutil.copy2(source, path)
        else:
            shutil.copy2(source, path)
            path.with_name(path.name + SYSTEM32_NEW_SUFFIX).write_text("")
    shutil.copy2(zzmi_dir / "d3d11.dll", runtime.lib_windows / "d3d11.dll")

    for name in SYSTEM32_ZZMI_LINKS:
        source = zzmi_dir / name
        path = system32 / name
        if path.exists() or path.is_symlink():
            raise YaaglError(f"Refusing to replace existing {path}; run `zzmmi restore` or move it away.")
        path.symlink_to(source, target_is_directory=source.is_dir())


def _copy_if_newer(src: Path, dst_dir: Path, since: float) -> bool:
    if src.exists() and src.stat().st_mtime >= since:
        shutil.copy2(src, dst_dir / src.name)
        return True
    return False


def _grep(path: Path, pattern: str, limit: int, exclude: str | None = None) -> list[str]:
    if not path.exists():
        return []
    rx, ex = re.compile(pattern, re.I), re.compile(exclude, re.I) if exclude else None
    hits = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if rx.search(line) and not (ex and ex.search(line)):
                hits.append(line.rstrip())
                if len(hits) >= limit:
                    hits.append("  ... (truncated)")
                    break
    return hits


def _loaded_modules(wine_log: Path) -> list[tuple[int, str]]:
    mods = set()
    if wine_log.exists():
        for line in open(wine_log, encoding="utf-8", errors="replace"):
            m = re.search(r'Loaded L"([^"]+)" at ([0-9A-Fa-f]+)', line)
            if m:
                mods.add((int(m.group(2), 16), m.group(1).replace("\\\\", "\\")))
    return sorted(mods)


def _exception_modules(migoto_log: Path, wine_log: Path) -> list[str]:
    """Count 3DMigoto crash-handler exception addresses and attribute them to loaded modules."""
    counts: dict[int, int] = {}
    for line in open(migoto_log, encoding="utf-8", errors="replace"):
        m = re.search(r"ExceptionAddress: 0x([0-9A-Fa-f]+)", line)
        if m:
            addr = int(m.group(1), 16)
            counts[addr] = counts.get(addr, 0) + 1
    mods = _loaded_modules(wine_log)
    out = []
    for addr, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        owner = [(base, name) for base, name in mods if base <= addr]
        where = f"{owner[-1][1]}+0x{addr - owner[-1][0]:x}" if owner else "unknown module"
        out.append(f"{n:5d} x 0x{addr:x}  {where}")
    return out


def summarize(run_dir: Path) -> str:
    sections: list[tuple[str, list[str]]] = []
    wine_log = run_dir / "wine_game.log"
    sections.append(("Graphics DLLs loaded into Wine processes (native = 3DMigoto/game folder, builtin = Wine/DXMT)",
                     _grep(wine_log, r"loaddll.*(d3d11|dxgi|d3d10core|d3dcompiler|3dmloader|winemetal|nvapi)", 60)))
    sections.append(("Wine errors and crashes",
                     _grep(wine_log, r"unhandled exception|page fault|backtrace|^wine: |err:(?!kerberos|winediag)",
                           80, exclude=r"err:kerberos|SOCK_RAW")))
    migoto = run_dir / "d3d11_log.txt"
    if migoto.exists():
        with open(migoto, encoding="utf-8", errors="replace") as f:
            head = [next(f, "").rstrip() for _ in range(40)]
        sections.append(("3DMigoto log (first 40 lines)", [h for h in head if h]))
        sections.append(("3DMigoto device/swapchain/hook/error lines",
                         _grep(migoto, r"D3D11CreateDevice|HackerDevice|HackerContext|HackerSwapChain|"
                                       r"CreateSwapChain|hook|error|fail|warning|exception", 120)))
    else:
        sections.append(("3DMigoto log", ["(no d3d11_log.txt written during this run)"]))
    for dxmt_log in sorted(run_dir.glob("*_d3d11.log")) + sorted(run_dir.glob("*_dxgi.log")):
        sections.append((f"DXMT {dxmt_log.name} (errors/warnings)", _grep(dxmt_log, r"err|warn|fail|unsupported|not implemented", 80)))
    sections.append(("XXMI Launcher", _grep(run_dir / "xxmi_launcher.log", r"hook|error|fail|successfully|waiting", 40)))
    if migoto.exists():
        sections.append(("3DMigoto first frame / config reload",
                         _grep(migoto, r"reload|\[Present\] command list", 10, exclude=r"possible reloading|reload_\w+=")
                         or ["(none: 3DMigoto never reached a frame)"]))
        sections.append(("Exceptions caught by 3DMigoto's crash handler, by module",
                         _exception_modules(migoto, wine_log)))
    unity_logs = sorted((run_dir / "unity_crash").glob("*/Player.log")) if (run_dir / "unity_crash").is_dir() else []
    if unity_logs:
        frames = _grep(unity_logs[0], r"^0x[0-9A-F]+ \(", 400)
        counted: list[str] = []
        for frame in frames:
            if counted and counted[-1].endswith(frame):
                n, _, rest = counted[-1].partition(" x ")
                counted[-1] = f"{int(n) + 1} x {rest}"
            else:
                counted.append(f"1 x {frame}")
        sections.append((f"Unity crash ({len(unity_logs)} report(s)); stack of the first", counted))
    else:
        sections.append(("Unity crash", ["(none)"]))
    dumps = sorted((run_dir / "minidumps").glob("*.dmp")) if (run_dir / "minidumps").is_dir() else []
    crashes = sorted(p.name for p in run_dir.glob("*.ips")) + ([f"{len(dumps)} minidumps in minidumps/"] if dumps else [])
    sections.append(("Crash reports / minidumps collected", crashes or ["(none)"]))
    out = []
    for title, lines in sections:
        out.append(f"== {title} ==")
        out.extend(lines or ["(nothing matched)"])
        out.append("")
    return "\n".join(out)


def run(opts: DebugOptions, runs_root: Path, runtime: YaaglRuntime | None = None) -> Path:
    runtime = runtime or YaaglRuntime.discover()
    # ZZMI files (3dmloader, 3DMigoto, d3dx.ini) always live in Yaagl's prefix; other runtimes
    # reach them through Z:.
    xxmi = XxmiInPrefix(YaaglRuntime(runtime.root))
    foreign_runtime = type(runtime) is not YaaglRuntime

    if game_running():
        raise YaaglError("ZZZ is already running. Close it first.")
    if not runtime.wine_is_real():
        raise YaaglError(f"{runtime.wine} is not the real Wine binary (old XXMI gate script left it swapped?). "
                         "Reinstall Wine from Yaagl or restore wine.xxmi-real.")
    if opts.probe and opts.probe not in PROBES:
        raise YaaglError(f"Unknown probe {opts.probe!r}; choose from {', '.join(PROBES)}")
    if opts.inject and (opts.injector == "bridge" or opts.probe):
        needed = [BRIDGE_EXE] + ([PROBES[opts.probe]] if opts.probe else [])
        if not all(p.exists() for p in needed):
            raise YaaglError("zzmmi-bridge isn't built. Run `make -C bridge` (needs `brew install mingw-w64`).")
    if foreign_runtime and opts.inject and opts.injector == "xxmi" and not opts.probe:
        raise YaaglError("XXMI Launcher only runs in Yaagl's prefix; use --injector bridge or --probe.")
    if opts.inject and not xxmi.installed():
        raise YaaglError(f"XXMI Launcher not found in the prefix at {xxmi.root}")
    if opts.mods:
        missing = [m for m in opts.mods if not (xxmi.zzmi_dir / "Mods" / m).is_dir()]
        if missing:
            raise YaaglError(f"Not in {xxmi.zzmi_dir / 'Mods'}: {', '.join(missing)}")

    run_dir = runs_root / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    (run_dir / "options.txt").write_text(repr(opts) + "\n")
    log(f"Run directory: {run_dir}")

    d3dx = xxmi.zzmi_dir / "d3dx.ini"
    d3dx_user = xxmi.zzmi_dir / "d3dx_user.ini"
    d3dx_backup = run_dir / "d3dx.ini.xxmi"
    user_backup = run_dir / "d3dx_user.ini.before"
    if d3dx_user.exists():
        shutil.copy2(d3dx_user, user_backup)

    # Turn SIGTERM/SIGHUP into an exception so the cleanup below always runs.
    def _raise(signum, _frame):
        raise KeyboardInterrupt(f"signal {signum}")
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _raise)

    start = time.time()
    offset = xxmi.log_offset()
    xxmi_proc = game = None
    use_system32 = opts.inject and opts.injector == "system32" and not opts.probe
    try:
        log("Applying Yaagl's pre-launch registry config and waiting for Wine to go idle...")
        runtime.prelaunch_config()
        runtime.wait_wineserver()

        with runtime.dxmt_session(), \
                runtime.hidden(runtime.crash_uploaders() if opts.block_crash_upload else []):
            log("DXMT installed into Wine for this session.")
            env = runtime.env(winedebug=opts.winedebug, dxmt_log_path=run_dir,
                              extra={"DXMT_LOG_LEVEL": opts.dxmt_log_level, **opts.extra_env})
            gamedir_probe = opts.probe in GAMEDIR_PROBES
            use_bridge = opts.inject and not gamedir_probe and (opts.injector == "bridge" or bool(opts.probe))
            if use_bridge:
                runtime.set_app_dll_overrides(BRIDGE_EXE.name, NATIVE_D3D11)
            if gamedir_probe and opts.probe is not None:
                runtime.set_app_dll_overrides(GAME_EXE, NATIVE_D3D11)
                placed = runtime.game_dir / "d3d11.dll"
                if placed.exists():
                    raise YaaglError(f"{placed} already exists; refusing to overwrite it.")
                shutil.copy2(PROBES[opts.probe], placed)
                log(f"Probe {opts.probe}: placed {PROBES[opts.probe].name} in the game folder (no injection).")
            if opts.inject and not use_bridge and not use_system32 and not gamedir_probe:
                log("Starting XXMI Launcher (no GUI) to arm the 3dmloader hook...")
                xxmi_proc = xxmi.start(runtime.env(winedebug="-all"), run_dir / "xxmi_stdout.log")
                state = xxmi.wait_for(offset, ARMED_MARKERS, ERROR_MARKERS, 120, xxmi_proc)
                if state != "ok":
                    hits = [l for l in xxmi.log_since(offset).splitlines()
                            if any(m in l for m in ERROR_MARKERS)]
                    raise YaaglError(f"XXMI Launcher did not arm the hook ({state}); see {xxmi.log}"
                                     + "".join(f"\n  {l}" for l in hits[-3:]))
                log("Hook armed.")
            if opts.inject and not opts.probe:
                # XXMI rewrites d3dx.ini on every start, so patch it only now and restore it after.
                shutil.copy2(d3dx, d3dx_backup)
                patch_d3dx(d3dx, opts, loader_exe=BRIDGE_EXE.name if use_bridge else None)
                if use_system32:
                    doc = IniDoc.load(d3dx)
                    doc.set("System", "proxy_d3d11", SYSTEM32_PROXY_DLL)
                    doc.save(d3dx)
                shutil.copy2(d3dx, run_dir / "d3dx.ini")
                log("d3dx.ini patched for this run.")
            if use_system32:
                log("Installing 3DMigoto in system32 with DXMT as its proxy...")
                install_system32_proxy(runtime, xxmi.zzmi_dir)

            game_log = run_dir / "wine_game.log"
            if use_bridge:
                dll = PROBES[opts.probe] if opts.probe else xxmi.zzmi_dir / "d3d11.dll"
                log(f"Arming zzmmi-bridge with {dll.name}" + (f" (probe: {opts.probe})" if opts.probe else ""))
                command = runtime.wine_command(
                    to_windows_path(BRIDGE_EXE),
                    "--loader", to_windows_path(xxmi.zzmi_dir / "3dmloader.dll"),
                    "--dll", to_windows_path(dll), "--target", GAME_EXE, "--timeout", "60",
                    "--cwd", to_windows_path(runtime.game_dir), "--", *runtime.game_windows_command(opts.game_args))
            else:
                command = runtime.game_command(opts.game_args)
            log("Launching the game. Reproduce the problem, then quit the game normally.")
            with open(game_log, "wb") as out:
                game = subprocess.Popen(command, env=env, stdout=out, stderr=subprocess.STDOUT,
                                        cwd=runtime.game_dir)
            reported = not opts.inject
            bridge_seen: set[str] = set()
            deadline = time.monotonic() + opts.max_seconds if opts.max_seconds else None
            # The bridge exits once the DLL is injected, so also watch for the game itself.
            while game.poll() is None or game_running():
                if deadline and time.monotonic() > deadline:
                    log(f"--max-seconds {opts.max_seconds:g} reached: ending the session.")
                    subprocess.run([str(runtime.wineserver), "-k"], env=runtime.env(), timeout=30)
                    game.wait(timeout=30)
                    break
                if use_bridge:
                    for line in bridge_status(game_log):
                        if line not in bridge_seen:
                            bridge_seen.add(line)
                            log(f"bridge: {line}")
                elif not reported:
                    text = xxmi.log_since(offset)
                    if LOADED_MARKER in text:
                        log("XXMI confirms 3DMigoto is loaded in the game process.")
                        reported = True
                    elif any(m in text for m in FAILED_MARKERS):
                        log("XXMI could NOT verify 3DMigoto in the game process.")
                        reported = True
                time.sleep(0.5)
            log(f"Game process exited ({game.returncode}). Waiting for Wine to shut down...")
            runtime.wait_wineserver()
    except KeyboardInterrupt:
        log("Interrupted: killing the Wine session.")
        subprocess.run([str(runtime.wineserver), "-k"], env=runtime.env(), timeout=30)
        raise
    finally:
        if opts.probe is not None and opts.probe in GAMEDIR_PROBES:
            placed = runtime.game_dir / "d3d11.dll"
            if placed.exists() and placed.read_bytes() == PROBES[opts.probe].read_bytes():
                placed.unlink()
        if xxmi_proc and xxmi_proc.poll() is None:
            xxmi_proc.terminate()
        if use_system32:
            restore_system32_proxy(runtime, xxmi.zzmi_dir)
        if d3dx_backup.exists():
            shutil.copy2(d3dx_backup, d3dx)
        if opts.mods is not None and user_backup.exists():
            # 3DMigoto drops persisted vars of mods it didn't load; don't lose the user's toggles.
            shutil.copy2(user_backup, d3dx_user)
        collect(run_dir, start, xxmi, offset, runtime)

    log(f"Done. Summary: {run_dir / 'summary.txt'}")
    return run_dir


def collect(run_dir: Path, since: float, xxmi: XxmiInPrefix, offset: int, runtime: YaaglRuntime) -> None:
    for name in ("d3d11_log.txt", "d3d11_profile_log.txt"):
        _copy_if_newer(xxmi.zzmi_dir / name, run_dir, since)
    for name in ("zzmmi_veh_probe.log", "zzmmi_patch_probe.log", "zzmmi_hide_probe.log"):
        _copy_if_newer(runtime.game_dir / name, run_dir, since)
    # 3DMigoto writes minidumps to the process working directory (the game folder). Move
    # them out so they don't pile up next to the game's files.
    for folder in (xxmi.zzmi_dir, xxmi.runtime.game_dir):
        for dump in folder.glob("3DM-*.dmp"):
            if dump.stat().st_mtime >= since:
                (run_dir / "minidumps").mkdir(exist_ok=True)
                shutil.move(dump, run_dir / "minidumps" / dump.name)
    (run_dir / "xxmi_launcher.log").write_text(xxmi.log_since(offset))
    prefix = runtime.prefix  # Unity writes Player.log and crash reports into the runtime's prefix
    _copy_if_newer(prefix / UNITY_PLAYER_LOG, run_dir, since)
    crashes = prefix / UNITY_CRASHES
    if crashes.is_dir():
        for crash in crashes.iterdir():
            if crash.is_dir() and crash.stat().st_mtime >= since:
                shutil.copytree(crash, run_dir / "unity_crash" / crash.name, dirs_exist_ok=True)
    if CRASH_REPORTS.is_dir():
        for report in CRASH_REPORTS.iterdir():
            if re.search(r"wine|preloader|zenless", report.name, re.I):
                _copy_if_newer(report, run_dir, since)
    (run_dir / "summary.txt").write_text(summarize(run_dir))
