"""Environment checks for `zzmmi doctor`."""
from __future__ import annotations

import json

from .debug_launch import game_running
from .runtime.xxmi_legacy import XxmiInPrefix
from .runtime.yaagl import DXMT_SWAPPED, YaaglError, YaaglRuntime

OK, WARN, FAIL = "ok", "warn", "FAIL"


def checks(runtime: YaaglRuntime | None = None) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []

    def add(status: str, name: str, detail: str = "") -> None:
        results.append((status, name, detail))

    try:
        runtime = runtime or YaaglRuntime.discover()
    except YaaglError as e:
        add(FAIL, "Yaagl installation", str(e))
        return results
    add(OK, "Yaagl installation", str(runtime.root))

    add(OK if runtime.wine_is_real() else FAIL, "Wine binary",
        f"{runtime.setting('wine_tag', '?')} (state: {runtime.setting('wine_state', '?')})"
        if runtime.wine_is_real() else f"{runtime.wine} is not a Mach-O binary (swapped by a script?)")

    try:
        exe = runtime.game_exe
        version = (runtime.game_dir / "version_info").read_text().strip() if (runtime.game_dir / "version_info").exists() else "?"
        add(OK if exe.exists() else FAIL, "Game", f"{exe} ({version})")
    except YaaglError as e:
        add(FAIL, "Game", str(e))

    missing = [n for n in (*DXMT_SWAPPED, "winemetal.dll", "winemetal.so") if not (runtime.dxmt_dir / n).exists()]
    add(FAIL if missing else OK, "DXMT", f"missing {missing}" if missing
        else f"version {runtime.setting('installed_dxmt_version', '?')} in {runtime.dxmt_dir}")

    running = game_running()
    if runtime.dxmt_installed() and not running:
        add(WARN, "Wine DLL state", "DXMT is still swapped into Wine with no game running; run `zzmmi restore`")
    add(WARN if running else OK, "Game process", "running" if running else "not running")

    xxmi = XxmiInPrefix(runtime)
    if xxmi.installed():
        detail = str(xxmi.root)
        try:
            cfg = json.loads((xxmi.root / "XXMI Launcher Config.json").read_text())
            pkgs = cfg.get("Packages", {}).get("packages", {})
            detail += " (" + ", ".join(f"{k} {v.get('deployed_version')}" for k, v in pkgs.items()
                                       if v.get("deployed_version")) + ")"
        except (OSError, ValueError, AttributeError):
            pass
        add(OK, "XXMI Launcher in prefix (Phase 0 injector)", detail)
        mods = sorted(p.name for p in (xxmi.zzmi_dir / "Mods").iterdir() if p.is_dir()) \
            if (xxmi.zzmi_dir / "Mods").is_dir() else []
        add(OK, "Mods", f"{len(mods)}: " + ", ".join(mods))
    else:
        add(WARN, "XXMI Launcher in prefix (Phase 0 injector)", f"not found at {xxmi.root}")
    return results


def main() -> int:
    results = checks()
    for status, name, detail in results:
        print(f"[{status:>4}] {name}: {detail}")
    return 1 if any(s == FAIL for s, _, _ in results) else 0
