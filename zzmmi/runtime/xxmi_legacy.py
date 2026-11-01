"""Drive the XXMI Launcher already installed inside the Yaagl prefix.

This is a Phase 0 stopgap: XXMI Launcher arms 3dmloader's hook and then waits for
the game to be started "manually", which we then do ourselves. Phase 1 replaces it
with zzmmi-bridge.exe.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .yaagl import YaaglRuntime

ARMED_MARKERS = ("Waiting for user to manually launch",)
# Only user-facing errors. XXMI logs harmless tracebacks on every start (e.g. its Epic Games
# install check fails on a non-EGS game folder), so "Traceback" must not count as failure.
ERROR_MARKERS = ("ShowError",)
LOADED_MARKER = "Successfully passed late"
FAILED_MARKERS = ("Failed to verify",)


@dataclass
class XxmiInPrefix:
    runtime: YaaglRuntime

    @property
    def root(self) -> Path:
        return self.runtime.prefix / "drive_c" / "XXMI Launcher"

    @property
    def exe(self) -> Path:
        return self.root / "Resources" / "Bin" / "XXMI Launcher.exe"

    @property
    def log(self) -> Path:
        return self.root / "XXMI Launcher Log.txt"

    @property
    def zzmi_dir(self) -> Path:
        return self.root / "ZZMI"

    def installed(self) -> bool:
        return self.exe.exists() and (self.zzmi_dir / "d3d11.dll").exists()

    def log_offset(self) -> int:
        return self.log.stat().st_size if self.log.exists() else 0

    def log_since(self, offset: int) -> str:
        if not self.log.exists():
            return ""
        with open(self.log, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", errors="replace")

    def start(self, env: dict[str, str], stdout_path: Path) -> subprocess.Popen:
        out = open(stdout_path, "wb")
        return subprocess.Popen(
            [str(self.runtime.wine), "XXMI Launcher.exe", "--nogui", "--xxmi", "ZZMI"],
            cwd=self.exe.parent, env=env, stdout=out, stderr=subprocess.STDOUT,
        )

    def wait_for(self, offset: int, good: tuple[str, ...], bad: tuple[str, ...],
                 timeout: float, proc: subprocess.Popen | None = None) -> str:
        """Poll the launcher log. Returns 'ok', 'error', 'exited' or 'timeout'."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.log_since(offset)
            if any(m in text for m in bad):
                return "error"
            if any(m in text for m in good):
                return "ok"
            if proc is not None and proc.poll() is not None:
                return "exited"
            time.sleep(0.1)
        return "timeout"
