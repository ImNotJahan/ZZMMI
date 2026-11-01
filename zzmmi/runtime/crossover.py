"""Run the Yaagl-installed game under a CrossOver bottle instead of Yaagl's Wine.

Used to test whether the game's anti-tamper behaves differently under another Wine build.
Game files, the protonextras steam.exe shim and Yaagl's registry tweaks still come from the
Yaagl installation. Only the Wine build and prefix change. CrossOver selects DXMT itself
(bottle setting CX_GRAPHICS_BACKEND=dxmt), so nothing is swapped into Wine.
"""
from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .yaagl import ENV_PASSTHROUGH, YaaglError, YaaglRuntime

CROSSOVER_APPS = (Path.home() / "Applications" / "CrossOver.app", Path("/Applications/CrossOver.app"))
BOTTLES = Path.home() / "Library" / "Application Support" / "CrossOver" / "Bottles"
DEFAULT_BOTTLE = "ZZMMI"


@dataclass
class CrossOverRuntime(YaaglRuntime):
    bottle: str = DEFAULT_BOTTLE
    app: Path | None = None

    @classmethod
    def discover(cls, root: Path | None = None, bottle: str = DEFAULT_BOTTLE) -> "CrossOverRuntime":
        yaagl = YaaglRuntime.discover(root)
        app = next((a for a in CROSSOVER_APPS if a.exists()), None)
        if not app:
            raise YaaglError("CrossOver.app not found in ~/Applications or /Applications.")
        runtime = cls(yaagl.root, bottle=bottle, app=app)
        if not (runtime.prefix / "cxbottle.conf").exists():
            raise YaaglError(f"CrossOver bottle {bottle!r} not found. Create it with: "
                             f"cxbottle --bottle {bottle} --create --template win10_64 "
                             f"--param 'EnvironmentVariables:CX_GRAPHICS_BACKEND=dxmt'")
        return runtime

    @property
    def cx_bin(self) -> Path:
        return self.app / "Contents" / "SharedSupport" / "CrossOver" / "bin"

    @property
    def wine(self) -> Path:
        return self.cx_bin / "wine"

    @property
    def wineserver(self) -> Path:
        return self.cx_bin / "wineserver"

    @property
    def prefix(self) -> Path:
        return BOTTLES / self.bottle

    def wine_is_real(self) -> bool:
        return self.wine.exists()

    def wine_command(self, *args: str) -> list[str]:
        return [str(self.wine), "--bottle", self.bottle, *args]

    def env(self, *, winedebug: str = "fixme-all,err-unwind,+timestamp",
            dxmt_log_path: Path | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {k: os.environ[k] for k in ENV_PASSTHROUGH if k in os.environ}
        env.update({
            "CX_BOTTLE": self.bottle,
            "WINEPREFIX": str(self.prefix),
            # CrossOver's wine script forces WINEDEBUG=-all unless CX_DEBUGMSG is set, and only
            # sends traces to stderr ("-") when CX_LOG is set.
            "CX_DEBUGMSG": winedebug,
            "CX_LOG": "-",
            "MTL_HUD_ENABLED": "0",
            "DXMT_LOG_PATH": str(dxmt_log_path or self.prefix) + "/",
            "GST_PLUGIN_FEATURE_RANK": "atdec:MAX,avdec_h264:MAX",
        })
        if extra:
            env.update(extra)
        return env

    def install_dxmt(self, dxmt_dir: Path | None = None) -> None:
        """CrossOver brings its own DXMT; only deploy Yaagl's steam.exe shim and hide vulkan-1.dll."""
        for src, dst in self._deployments():
            if src.exists() and "protonextras" in src.parts:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        if self.game_vulkan_dll.exists():
            self.game_vulkan_dll.rename(self._bak(self.game_vulkan_dll))

    def restore_dxmt(self) -> list[Path]:
        bak = self._bak(self.game_vulkan_dll)
        if bak.exists():
            bak.replace(self.game_vulkan_dll)
            return [self.game_vulkan_dll]
        return []

    def dxmt_installed(self) -> bool:
        return False

    @contextmanager
    def dxmt_session(self, dxmt_dir: Path | None = None):
        self.install_dxmt()
        try:
            yield
        finally:
            self.restore_dxmt()
