"""Guess which characters a mod is for, from the names inside it.

Most skin mods are made with XXMI-Tools, which names everything after the character's model:
Jane.ini, [TextureOverrideJaneBodyA], [ResourceJaneHairADiffuse], JaneHairADiffuse.dds. The model
name is usually the character's name, sometimes with extras (Zhao2, NormaHammy), or an internal
name (Soldier0 is Soldier 0 Anby, Pheonix is Phoenix).

The guess, strongest evidence first:
1. Section names. Each [TextureOverride…] or [Resource…] section that starts with a character's
   name (or alias) counts toward that character. Characters with a quarter of the top count or less
   are dropped: a Qingyi skin that also restyles Hugo's face in one section is a Qingyi mod. A
   name not in ROSTER still counts if at least COMPONENT_MIN_SECTIONS sections are named
   <Name>Body…, <Name>Hair…, etc. and some file's name starts with it too (XXMI-Tools exports
   <Name>.ini and <Name>BodyADiffuse.dds), so new characters show up before the roster lists them.
   The file check keeps out a mod's own naming schemes, like [ResourceNativeBody].
2. If no section names a character (mods that match by hash only, like [TextureOverride4816de84IB]):
   whole words in the mod's folder name, then in its file names. Finding more than
   FALLBACK_MAX characters there means a UI or icon pack covering everyone, so it gets no tags.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path

# (display name, aliases). Aliases are model names as they appear in mod files, in CamelCase; the
# display name with spaces removed is an alias too.
ROSTER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Alice", ()), ("Anby", ()), ("Anton", ()), ("Aria", ()), ("Astra Yao", ("Astra",)),
    ("Banyue", ()), ("Belle", ()), ("Ben", ()), ("Billy", ()), ("Burnice", ()), ("Caesar", ()),
    ("Cissia", ()), ("Claret", ()), ("Corin", ()), ("Dialyn", ()), ("Ellen", ()), ("Evelyn", ()),
    ("Grace", ()), ("Harumasa", ()), ("Hugo", ()), ("Jane Doe", ("Jane",)), ("Ju Fufu", ("Fufu",)),
    ("Koleda", ()), ("Lighter", ()), ("Lucia", ()), ("Lucy", ()), ("Lycaon", ()), ("Manato", ()),
    ("Miyabi", ()), ("Nangong Yu", ()), ("Nekomata", ()), ("Nicole", ()), ("Norma", ()),
    ("Orphie & Magus", ("Orphie", "Magus")), ("Pan Yinhu", ("Pan",)), ("Phoenix", ("Pheonix",)),
    ("Piper", ()), ("Promeia", ()), ("Pulchra", ()), ("Pyrois", ()), ("Qingyi", ()), ("Remielle", ()),
    ("Rina", ()), ("Roxy", ()), ("Seed", ()), ("Seth", ()), ("Severian", ()), ("Sigrid", ()),
    ("Soldier 0 Anby", ("Soldier0", "S0Anby")), ("Soldier 11", ("Soldier11",)), ("Soukaku", ()),
    ("Sunna", ()), ("Trigger", ()), ("Velina", ()), ("Vivian", ()), ("Wise", ()), ("Yanagi", ()),
    ("Ye Shunguang", ()), ("Yidhari", ()), ("Yixuan", ()), ("Yuzuha", ()), ("Zhao", ()),
    ("Zhu Yuan", ()),
)

SHARE_MIN = 0.25  # a character needs more than this share of the top character's section count
COMPONENT_MIN_SECTIONS = 3
FALLBACK_MAX = 2
INI_MAX_BYTES = 20_000_000  # skip anything bigger; it isn't a hand-made mod ini

_SECTION = re.compile(rb"^[ \t]*\[(?:TextureOverride|Resource)([A-Za-z0-9_]+)\]", re.IGNORECASE | re.MULTILINE)
_PARTS = ("Body", "Hair", "Face", "Head")
# An unlisted model name followed by a part: PheonixBodyA -> Pheonix, NewGuy2HairB -> NewGuy.
_COMPONENT = re.compile(rf"([A-Z][A-Za-z]*?[a-z])\d*(?:{'|'.join(_PARTS)})(?![a-z])")
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _words(text: str) -> list[str]:
    """Split on punctuation and camelCase: "CM_JaneDoe1" -> ["cm", "jane", "doe", "1"]."""
    return [w.lower() for w in _WORD.findall(text)]


def _aliases() -> list[tuple[str, str]]:
    """(alias, display name), longest alias first so JaneDoe wins over Jane."""
    pairs = {(alias, display) for display, extra in ROSTER
             for alias in (display.replace(" ", "").replace("&", ""), *extra)}
    return sorted(pairs, key=lambda p: -len(p[0]))


_ALIASES = _aliases()
_ALIAS_WORDS = [(tuple(_words(alias)), display) for alias, display in _ALIASES] + \
               [(tuple(_words(display)), display) for display, _ in ROSTER]


def _roster_prefix(name: str) -> str | None:
    """The character whose alias `name` starts with, at a camelCase boundary (Zhao2Body, not Panties)."""
    folded = name.casefold()
    for alias, display in _ALIASES:
        if folded.startswith(alias.casefold()) and not name[len(alias):len(alias) + 1].islower():
            return display
    return None


def _display(component: str) -> str:
    return " ".join(w.capitalize() for w in _words(component) if not w.isdigit())


def _from_sections(names: set[str], stems: list[str]) -> list[str]:
    """Characters named by `names` (section names minus TextureOverride/Resource), given the
    mod's file names without extensions."""
    counts: Counter[str] = Counter()
    unknown: Counter[str] = Counter()  # by unlisted model name, as written
    for name in names:
        if (display := _roster_prefix(name)) is not None:
            counts[display] += 1
        elif (m := _COMPONENT.match(name)) and m.group(1) not in _PARTS:
            unknown[m.group(1)] += 1
    folded = [s.casefold() for s in stems]
    for component, n in unknown.items():
        if n >= COMPONENT_MIN_SECTIONS and any(s.startswith(component.casefold()) for s in folded):
            counts[_display(component)] += n
    if not counts:
        return []
    top = max(counts.values())
    return [name for name, n in counts.items() if n > top * SHARE_MIN]


def _from_words(text: str) -> list[str]:
    words = _words(text)
    found = set()
    for alias, display in _ALIAS_WORDS:
        n = len(alias)
        if any(tuple(words[i:i + n]) == alias for i in range(len(words) - n + 1)):
            found.add(display)
    return sorted(found)


def _files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    found = []
    for folder, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        found += [Path(folder, f) for f in files if not f.startswith(".")]
    return found


def character_tags(path: Path) -> tuple[str, ...]:
    """The characters the mod at `path` (a folder or a single .ini) appears to be for, sorted.

    Empty for mods that aren't for a particular character (UI tweaks, loading screens, icon packs)
    and for ones the heuristic can't place. Unreadable files are skipped.
    """
    files = _files(path)
    sections: set[str] = set()
    for f in files:
        if f.suffix.lower() != ".ini":
            continue
        try:
            if f.stat().st_size > INI_MAX_BYTES:
                continue
            data = f.read_bytes()
        except OSError:
            continue
        sections.update(m.decode("ascii") for m in _SECTION.findall(data))
    stems = [f.stem for f in files]
    if found := _from_sections(sections, stems):
        return tuple(sorted(found))
    name = path.stem if path.is_file() else path.name
    for text in (name, " ".join(stems)):
        found = _from_words(text)
        if found:
            return tuple(found) if len(found) <= FALLBACK_MAX else ()
    return ()
