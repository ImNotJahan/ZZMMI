"""Find mods that override the same thing, which breaks both in game.

A mod replaces part of a character through [TextureOverride…] sections. Each one matches a draw or
buffer by `hash`, and, when one index buffer holds several parts (HairA, HairB), by
`match_first_index` too. When two loaded mods have a section with the same pair, 3DMigoto logs a
duplicate and the two mods' replacements clash: two hair mods for Norma both override hash
a2150d3b at index 0, for instance.

Sections with a non-zero `match_priority` are skipped: the author set it on purpose to layer over
other mods. (XXMI-Tools writes `match_priority = 0`, the default, on many sections; that means nothing.)

Only files 3DMigoto loads count: .ini files, minus anything named DISABLED* (d3dx.ini's
`exclude_recursive = DISABLED*`), which is how mods keep backups and toggled-off variants.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from itertools import combinations
from pathlib import Path

INI_MAX_BYTES = 20_000_000  # see characters.INI_MAX_BYTES

Override = tuple[str, str]  # (hash, match_first_index or "")

_SECTION = re.compile(r"^\s*\[([^\]]+)\]")
_KEY = re.compile(r"^\s*(hash|match_first_index|match_priority)\s*=\s*(\S+)", re.IGNORECASE)


def _loaded(name: str) -> bool:
    return not (name.startswith(".") or name.upper().startswith("DISABLED") or name.lower() == "desktop.ini")


def _ini_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    found = []
    for folder, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if _loaded(d)]
        found += [Path(folder, f) for f in files if _loaded(f) and f.lower().endswith(".ini")]
    return found


def parse_overrides(text: str) -> set[Override]:
    """The (hash, match_first_index) of each [TextureOverride…] section in an ini's text."""
    found: set[Override] = set()
    section: dict[str, str] | None = None  # keys of the current TextureOverride section

    def close() -> None:
        if section and "hash" in section and section.get("match_priority", "0") in ("0", "-0"):
            found.add((section["hash"], section.get("match_first_index", "")))

    for line in text.splitlines():
        if m := _SECTION.match(line):
            close()
            section = {} if m.group(1).strip().lower().startswith("textureoverride") else None
        elif section is not None and (m := _KEY.match(line)):
            section.setdefault(m.group(1).lower(), m.group(2).lower())
    close()
    return found


def mod_overrides(path: Path) -> set[Override]:
    """Everything the mod at `path` (a folder or a single .ini) overrides. Unreadable files are skipped."""
    found: set[Override] = set()
    for f in _ini_files(path):
        try:
            if f.stat().st_size > INI_MAX_BYTES:
                continue
            found |= parse_overrides(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return found


def find_conflicts(overrides: dict[str, set[Override]]) -> list[tuple[str, str]]:
    """Pairs of mod names (in the order of `overrides`) whose overrides overlap."""
    by_override: dict[Override, list[str]] = defaultdict(list)
    for name, keys in overrides.items():
        for key in keys:
            by_override[key].append(name)
    order = {name: i for i, name in enumerate(overrides)}
    pairs = {pair for names in by_override.values() if len(names) > 1 for pair in combinations(names, 2)}
    return sorted(pairs, key=lambda p: (order[p[0]], order[p[1]]))
