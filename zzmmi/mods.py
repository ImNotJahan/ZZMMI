"""Enable/disable mods, and reload or toggle them in the running game.

Enabling and disabling happen on disk. A disabled mod is moved from ZZMI\\Mods to
ZZMI\\ModsNotUsing, so 3DMigoto's `include_recursive = Mods` no longer sees it. 3DMigoto also skips
anything under Mods named DISABLED* (`exclude_recursive = DISABLED*`, the XXMI convention), so
those entries count as disabled too.

3DMigoto has no IPC, so reloading goes through its `reload_config` hotkey (F10 in XXMI's d3dx.ini).
It works with hunting=0. It polls that key with GetAsyncKeyState every frame, which under Wine reads
the key state wineserver keeps for the desktop. zzmmi-keys.exe, run in the same prefix, presses F10
there with SendInput. ZZMI's F6 (toggle all mods) works the same way. No macOS events or
Accessibility permission are involved. The game is usually not Wine's foreground window while
ZZMMI is in front, so launches set `check_foreground_window = 0` (see debug_launch.patch_d3dx).
Without it 3DMigoto ignores the press. Wine's macOS driver maps Command to Alt, so Cmd+Tab out of
the game leaves Alt held in Wine, and 3DMigoto ignores the `no_modifiers` F10/F6 hotkeys. So
zzmmi-keys.exe first releases any modifier Wine thinks is held.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .debug_launch import game_running
from .runtime.xxmi_legacy import XxmiInPrefix
from .runtime.yaagl import YaaglError, YaaglRuntime, to_windows_path

KEYS_EXE = Path(__file__).resolve().parent / "resources" / "bin" / "zzmmi-keys.exe"
DISABLED_DIR = "ModsNotUsing"
DISABLED_PREFIX = "DISABLED"
RELOAD_KEY = "F10"  # d3dx.ini [Hunting] reload_config = no_modifiers VK_F10
TOGGLE_MODS_KEY = "F6"  # Core\\ZZMI\\main.ini [KeyToggleMods]: cycles $costume_mods 0/1


@dataclass(frozen=True)
class Mod:
    name: str  # as shown to the user: folder name without any DISABLED prefix
    path: Path
    enabled: bool


def _entries(folder: Path) -> list[Path]:
    """What 3DMigoto would include from `folder`: subfolders and .ini files, minus hidden/system files."""
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir()
                   if not p.name.startswith(".") and p.name.lower() != "desktop.ini"
                   and (p.is_dir() or p.suffix.lower() == ".ini")),
                  key=lambda p: p.name.lower())


def _strip_disabled(name: str) -> str:
    if name.upper().startswith(DISABLED_PREFIX):
        return name[len(DISABLED_PREFIX):].lstrip("_ -") or name
    return name


class ModLibrary:
    def __init__(self, zzmi_dir: Path) -> None:
        self.zzmi_dir = zzmi_dir
        self.enabled_dir = zzmi_dir / "Mods"
        self.disabled_dir = zzmi_dir / DISABLED_DIR

    @classmethod
    def discover(cls, runtime: YaaglRuntime | None = None) -> "ModLibrary":
        return cls(XxmiInPrefix(runtime or YaaglRuntime.discover()).zzmi_dir)

    def mods(self) -> list[Mod]:
        found = []
        for path in _entries(self.enabled_dir):
            disabled = path.name.upper().startswith(DISABLED_PREFIX)
            found.append(Mod(_strip_disabled(path.name), path, not disabled))
        found += [Mod(p.name, p, False) for p in _entries(self.disabled_dir)]
        return sorted(found, key=lambda m: m.name.lower())

    def get(self, name: str) -> Mod:
        matches = [m for m in self.mods() if m.name == name or m.path.name == name]
        if not matches:
            raise YaaglError(f"No mod named {name!r} in {self.enabled_dir} or {self.disabled_dir}")
        if len(matches) > 1:
            raise YaaglError(f"{name!r} is ambiguous: " + ", ".join(str(m.path) for m in matches))
        return matches[0]

    def set_enabled(self, name: str, enabled: bool) -> Mod:
        """Move a mod into Mods (enabled) or ModsNotUsing (disabled). Returns its new state."""
        mod = self.get(name)
        if mod.enabled == enabled:
            return mod
        target = (self.enabled_dir if enabled else self.disabled_dir) / mod.name
        if target.exists():
            raise YaaglError(f"Can't move {mod.path.name!r}: {target} already exists")
        target.parent.mkdir(exist_ok=True)
        mod.path.rename(target)
        return Mod(mod.name, target, enabled)


def key_command(runtime: YaaglRuntime, *keys: str) -> list[str]:
    return runtime.wine_command(to_windows_path(KEYS_EXE), *keys)


def press(*keys: str, runtime: YaaglRuntime | None = None, timeout: float = 30) -> bool:
    """Press `keys` (zzmmi-keys.exe syntax, e.g. "F10" or "0x11", "0x79") in the running game.

    Returns False if the game isn't running. Takes about 0.5s; call it off the Tk main thread.
    """
    if not game_running():
        return False
    if not KEYS_EXE.exists():
        raise YaaglError(f"{KEYS_EXE.name} isn't built. Run `make -C bridge keys` (needs `brew install mingw-w64`).")
    runtime = runtime or YaaglRuntime.discover()
    # Output must not be a pipe: Wine helper processes inherit it and keep it open, so capturing
    # it would block until the Wine session ends.
    result = subprocess.run(key_command(runtime, *keys), env=runtime.env(winedebug="-all"), timeout=timeout,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        raise YaaglError(f"{KEYS_EXE.name} failed (exit {result.returncode})")
    return True


def reload_mods(runtime: YaaglRuntime | None = None) -> bool:
    """Make the running game's 3DMigoto reload d3dx.ini and every mod (like pressing F10 in game).

    Returns False if the game isn't running (changes then apply on the next launch). The reload
    also turns mods back on if toggle_all_mods() had turned them off.
    """
    return press(RELOAD_KEY, runtime=runtime)


def toggle_all_mods(runtime: YaaglRuntime | None = None) -> bool:
    """Flip every loaded mod on/off at once (ZZMI's F6, like pressing it in game).

    The game holds this state (ZZMI's $costume_mods) and nothing outside the game can read it.
    It starts on at every launch and goes back to on after every reload_mods(), and the user can
    press F6 in game too, so a remembered on/off value in ZZMMI is only a guess.
    Returns False if the game isn't running.
    """
    return press(TOGGLE_MODS_KEY, runtime=runtime)
