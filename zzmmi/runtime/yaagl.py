"""Discovery and launch environment for a Yaagl ZZZ installation.

Yaagl (https://github.com/yaagl/yet-another-anime-game-launcher) owns the Wine
runtime, the prefix and the DXMT build. We reuse all of it and reproduce the
exact pre/post-launch steps Yaagl performs (see neutralinojs.log), so that a
game started by us behaves the same as one started from Yaagl's Launch button.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

APP_SUPPORT = Path.home() / "Library" / "Application Support"
YAAGL_DIR_NAMES = ("Yaagl ZZZ OS", "Yaagl ZZZ")
GAME_EXE = "ZenlessZoneZero.exe"
MACHO_MAGIC = bytes.fromhex("cffaedfe")

# Host variables passed through to Wine. Everything else is dropped: Wine copies the Unix
# environment into the Windows one, so whatever the launching shell exports reaches the game.
# That leaks secrets (API keys, tokens) into the game process and its crash reports. It can
# also break the game: e.g. NoDefaultCurrentDirectoryInExePath (exported by some tools)
# stops Unity from finding UnityCrashHandler64.exe, and ZZZ then spins at startup.
ENV_PASSTHROUGH = ("HOME", "USER", "LOGNAME", "PATH", "SHELL", "TMPDIR", "LANG", "LC_ALL",
                   "LC_CTYPE", "__CF_USER_TEXT_ENCODING")

# DLLs Yaagl moves to *.bak and replaces with DXMT for the duration of a game session.
DXMT_SWAPPED = ("d3d10core.dll", "d3d11.dll", "dxgi.dll")
HIDDEN_SUFFIX = ".zzmmi-hidden"
# HoYo's crash uploaders (game-relative). Unity starts one after a crash and it sends the
# report, module list included, to HoYoverse. Neither runs during a normal session.
# (Blocking them with WINEDLLOVERRIDES instead makes ZZZ hang at startup under Yaagl's Wine.)
CRASH_UPLOADERS = ("ZenlessZoneZero_Data/Plugins/x86_64/crashreport.exe", "APMCrashReporter/crashreport.exe")


class YaaglError(RuntimeError):
    pass


def to_windows_path(path: Path) -> str:
    """Map a macOS path to the Z: drive Wine exposes for the host filesystem."""
    return "Z:" + str(Path(path).resolve()).replace("/", "\\")


@dataclass
class YaaglRuntime:
    root: Path

    @classmethod
    def discover(cls, root: Path | None = None) -> "YaaglRuntime":
        candidates = [Path(root)] if root else [APP_SUPPORT / n for n in YAAGL_DIR_NAMES]
        for path in candidates:
            if (path / "wine" / "bin" / "wine").exists() and (path / "wineprefix").is_dir():
                return cls(path)
        raise YaaglError(
            "Yaagl ZZZ installation not found (looked in: "
            + ", ".join(str(p) for p in candidates) + "). Install the game and Wine with Yaagl first."
        )

    # --- Yaagl settings -------------------------------------------------------------------------

    def setting(self, key: str, default: str | None = None) -> str | None:
        path = self.root / ".storage" / f"{key}.neustorage"
        try:
            return path.read_text().strip()
        except FileNotFoundError:
            return default

    def flag(self, key: str, default: bool = False) -> bool:
        value = self.setting(key)
        return default if value is None else value == "true"

    # --- Paths ----------------------------------------------------------------------------------

    @property
    def wine(self) -> Path:
        return self.root / "wine" / "bin" / "wine"

    @property
    def wineserver(self) -> Path:
        return self.root / "wine" / "bin" / "wineserver"

    @property
    def prefix(self) -> Path:
        return self.root / "wineprefix"

    @property
    def dxmt_dir(self) -> Path:
        return self.root / "dxmt"

    @property
    def lib_windows(self) -> Path:
        return self.root / "wine" / "lib" / "wine" / "x86_64-windows"

    @property
    def lib_unix(self) -> Path:
        return self.root / "wine" / "lib" / "wine" / "x86_64-unix"

    @property
    def system32(self) -> Path:
        return self.prefix / "drive_c" / "windows" / "system32"

    @property
    def syswow64(self) -> Path:
        return self.prefix / "drive_c" / "windows" / "syswow64"

    @property
    def game_dir(self) -> Path:
        value = self.setting("game_install_dir")
        if not value:
            raise YaaglError("Yaagl has no game install directory configured.")
        return Path(value)

    @property
    def game_exe(self) -> Path:
        return self.game_dir / GAME_EXE

    @property
    def game_vulkan_dll(self) -> Path:
        return self.game_dir / "ZenlessZoneZero_Data" / "Plugins" / "x86_64" / "vulkan-1.dll"

    def wine_is_real(self) -> bool:
        """False if Yaagl's wine binary is replaced by a script (e.g. the old XXMI gate hack)."""
        try:
            with open(self.wine, "rb") as f:
                return f.read(4) == MACHO_MAGIC
        except OSError:
            return False

    # --- Environment ----------------------------------------------------------------------------

    def env(self, *, winedebug: str = "fixme-all,err-unwind,+timestamp",
            dxmt_log_path: Path | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {k: os.environ[k] for k in ENV_PASSTHROUGH if k in os.environ}
        env.update({
            "WINEPREFIX": str(self.prefix),
            "WINEDEBUG": winedebug,
            "WINEMSYNC": "1",
            "MTL_HUD_ENABLED": "0",
            "DXMT_LOG_PATH": str(dxmt_log_path or self.root) + "/",
            "DXMT_CONFIG_FILE": str(self.root / "dxmt.conf"),
            "GST_PLUGIN_FEATURE_RANK": "atdec:MAX,avdec_h264:MAX",
        })
        if self.flag("config_timeout_fix", True):
            env["WINE_ENABLE_TIMEOUT_FIX"] = "1"
        if extra:
            env.update(extra)
        return env

    def wine_command(self, *args: str) -> list[str]:
        """Host command line that runs a Windows command line (args) in this runtime."""
        return [str(self.wine), *args]

    def game_windows_command(self, game_args: list[str] | None = None) -> list[str]:
        """Windows command line Yaagl uses to start the game (via the protonextras steam.exe shim if enabled)."""
        command = [to_windows_path(self.game_exe)]
        if self.flag("config_steam_patch", True):
            command.insert(0, r"C:\windows\system32\steam.exe")
        return command + list(game_args or [])

    def game_command(self, game_args: list[str] | None = None) -> list[str]:
        return self.wine_command(*self.game_windows_command(game_args))

    # --- Wine helpers ---------------------------------------------------------------------------

    def run_wine(self, *args: str, env: dict[str, str] | None = None, timeout: float = 120,
                 **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(self.wine_command(*args), env=env or self.env(), timeout=timeout,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)

    def wait_wineserver(self, timeout: float = 60) -> bool:
        """`wineserver -w`: block until every process in the prefix has exited. Kills on timeout."""
        try:
            subprocess.run([str(self.wineserver), "-w"], env=self.env(), timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            subprocess.run([str(self.wineserver), "-k"], env=self.env(), timeout=30)
            return False

    def prelaunch_config(self) -> None:
        """Registry tweaks Yaagl applies before every launch (no-ops if the files are missing)."""
        for name, args in (("fix_webview.reg", ("reg", "import")),
                           ("winedrv_config.bat", ("cmd", "/c"))):
            path = self.root / name
            if path.exists():
                self.run_wine(*args, to_windows_path(path))

    def set_app_dll_overrides(self, exe_name: str, overrides: dict[str, str]) -> None:
        """Per-program Wine DLL load order (HKCU\\Software\\Wine\\AppDefaults\\<exe>\\DllOverrides).

        Wine applies load order even to LoadLibrary with a full path, so a native DLL named
        like a builtin (d3d11.dll) is replaced by the builtin unless the loading program has
        a native-first override.
        """
        key = rf"HKEY_CURRENT_USER\Software\Wine\AppDefaults\{exe_name}\DllOverrides"
        for dll, order in overrides.items():
            self.run_wine("reg", "add", key, "/v", dll, "/d", order, "/f")

    # --- DXMT / shim deployment ----------------------------------------------------------------

    def _deployments(self) -> list[tuple[Path, Path]]:
        """(source, destination) pairs copied in place (no backup) before launch."""
        pairs = [
            (self.dxmt_dir / "winemetal.dll", self.lib_windows / "winemetal.dll"),
            (self.dxmt_dir / "winemetal.so", self.lib_unix / "winemetal.so"),
            (self.dxmt_dir / "winemetal.dll", self.system32 / "winemetal.dll"),
        ]
        if self.flag("config_steam_patch", True):
            extras = self.root / "sidecar" / "protonextras"
            pairs += [
                (extras / "steam64.exe", self.system32 / "steam.exe"),
                (extras / "steam32.exe", self.syswow64 / "steam.exe"),
                (extras / "lsteamclient64.dll", self.system32 / "lsteamclient.dll"),
                (extras / "lsteamclient32.dll", self.syswow64 / "lsteamclient.dll"),
            ]
        return pairs

    def _swapped_targets(self, dxmt_dir: Path) -> list[tuple[Path, Path]]:
        return [(dxmt_dir / n, self.lib_windows / n) for n in DXMT_SWAPPED]

    @staticmethod
    def _bak(path: Path) -> Path:
        return path.with_name(path.name + ".bak")

    def install_dxmt(self, dxmt_dir: Path | None = None) -> None:
        dxmt_dir = Path(dxmt_dir or self.dxmt_dir)
        for src, dst in self._swapped_targets(dxmt_dir):
            if not src.exists():
                raise YaaglError(f"DXMT file missing: {src}")
            # A leftover .bak means an earlier session wasn't restored: dst is already a
            # DXMT copy, and the .bak holds Wine's original, which must not be overwritten.
            if not self._bak(dst).exists():
                dst.rename(self._bak(dst))
            shutil.copy2(src, dst)
        for src, dst in self._deployments():
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        if self.game_vulkan_dll.exists():
            self.game_vulkan_dll.rename(self._bak(self.game_vulkan_dll))

    def restore_dxmt(self) -> list[Path]:
        """Undo install_dxmt(): put Wine's own DLLs and the game's vulkan-1.dll back."""
        restored = []
        for dst in [self.lib_windows / n for n in DXMT_SWAPPED] + [self.game_vulkan_dll]:
            bak = self._bak(dst)
            if bak.exists():
                bak.replace(dst)
                restored.append(dst)
        return restored

    def crash_uploaders(self) -> list[Path]:
        return [self.game_dir / rel for rel in CRASH_UPLOADERS]

    @staticmethod
    def hide(paths: list[Path]) -> None:
        for path in paths:
            if path.exists():
                path.replace(path.with_name(path.name + HIDDEN_SUFFIX))

    @staticmethod
    def unhide(paths: list[Path]) -> list[Path]:
        restored = []
        for path in paths:
            hidden = path.with_name(path.name + HIDDEN_SUFFIX)
            if hidden.exists():
                hidden.replace(path)
                restored.append(path)
        return restored

    @contextmanager
    def hidden(self, paths: list[Path]):
        self.hide(paths)
        try:
            yield
        finally:
            self.unhide(paths)

    def dxmt_installed(self) -> bool:
        return all(self._bak(self.lib_windows / n).exists() for n in DXMT_SWAPPED)

    @contextmanager
    def dxmt_session(self, dxmt_dir: Path | None = None):
        self.install_dxmt(dxmt_dir)
        try:
            yield
        finally:
            self.restore_dxmt()
