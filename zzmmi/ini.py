"""Comment-preserving editor for 3DMigoto INI files.

3DMigoto INIs aren't plain INI: keys repeat within a section (include_recursive,
exclude_recursive, run, ...), section names are case-insensitive, and users expect
their comments and line endings to survive. So this editor works on raw lines and
only touches the lines it changes.
"""
from __future__ import annotations

import re
from pathlib import Path

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]")
_KEY_RE = re.compile(r"^\s*([^;=\s][^=]*?)\s*=\s*(.*?)\s*$")
DISABLED_PREFIX = "; zzmmi-disabled: "


class IniDoc:
    def __init__(self, text: str):
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.lines = text.splitlines()
        self._trailing_newline = text.endswith(("\n", "\r"))

    @classmethod
    def load(cls, path: Path) -> "IniDoc":
        # newline="" keeps CRLF intact so __init__ can detect and preserve it.
        with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
            return cls(f.read())

    def save(self, path: Path) -> None:
        Path(path).write_text(str(self), encoding="utf-8", errors="surrogateescape", newline="")

    def __str__(self) -> str:
        text = self.newline.join(self.lines)
        return text + self.newline if self._trailing_newline else text

    # --- Lookup ---------------------------------------------------------------------------------

    def _section_range(self, section: str) -> tuple[int, int] | None:
        """(index of header line, index one past the section's last line)."""
        start = None
        for i, line in enumerate(self.lines):
            m = _SECTION_RE.match(line)
            if not m:
                continue
            if start is not None:
                return start, i
            if m.group(1).strip().lower() == section.lower():
                start = i
        return (start, len(self.lines)) if start is not None else None

    def _key_lines(self, section: str, key: str) -> list[int]:
        rng = self._section_range(section)
        if not rng:
            return []
        hits = []
        for i in range(rng[0] + 1, rng[1]):
            m = _KEY_RE.match(self.lines[i])
            if m and m.group(1).lower() == key.lower():
                hits.append(i)
        return hits

    def get_all(self, section: str, key: str) -> list[str]:
        return [_KEY_RE.match(self.lines[i]).group(2) for i in self._key_lines(section, key)]

    def get(self, section: str, key: str) -> str | None:
        values = self.get_all(section, key)
        return values[-1] if values else None

    # --- Mutation -------------------------------------------------------------------------------

    def _insert_index(self, section: str) -> int:
        rng = self._section_range(section)
        if not rng:
            if self.lines and self.lines[-1].strip():
                self.lines.append("")
            self.lines.append(f"[{section}]")
            return len(self.lines)
        end = rng[1]
        while end - 1 > rng[0] and not self.lines[end - 1].strip():
            end -= 1
        return end

    def set(self, section: str, key: str, value: object) -> None:
        """Set a single-valued key: rewrite the last occurrence, or append to the section."""
        hits = self._key_lines(section, key)
        line = f"{key} = {value}"
        if hits:
            self.lines[hits[-1]] = line
        else:
            self.lines.insert(self._insert_index(section), line)

    def add(self, section: str, key: str, value: object) -> None:
        """Append another occurrence of a multi-valued key."""
        self.lines.insert(self._insert_index(section), f"{key} = {value}")

    def disable(self, section: str, key: str, value: str | None = None) -> int:
        """Comment out matching lines in a way enable() can reverse. Returns lines changed."""
        changed = 0
        for i in self._key_lines(section, key):
            if value is None or _KEY_RE.match(self.lines[i]).group(2).lower() == value.lower():
                self.lines[i] = DISABLED_PREFIX + self.lines[i]
                changed += 1
        return changed

    def enable(self, section: str) -> int:
        """Re-enable every line in the section that disable() commented out."""
        rng = self._section_range(section)
        changed = 0
        if rng:
            for i in range(rng[0] + 1, rng[1]):
                if self.lines[i].startswith(DISABLED_PREFIX):
                    self.lines[i] = self.lines[i][len(DISABLED_PREFIX):]
                    changed += 1
        return changed
