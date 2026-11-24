"""Check GitHub for a newer ZZMMI release.

Release builds carry their release tag (build-YYYYMMDD-<sha>) in Info.plist under ZZMMIBuildTag,
stamped by macos/setup.py. A build is up to date when its tag is the latest release's tag.
"""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPO = "ImNotJahan/ZZMMI"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
TIMEOUT_SECONDS = 15


class UpdateCheckError(Exception):
    pass


def build_tag() -> str | None:
    """The release tag this copy was built as, or None if it isn't a release build.

    Outside the app bundle, ZZMMI_BUILD_TAG stands in for it (handy for testing the check).
    """
    if not getattr(sys, "frozen", False):
        return os.environ.get("ZZMMI_BUILD_TAG") or None
    info = Path(os.environ["RESOURCEPATH"]).parent / "Info.plist"
    try:
        with open(info, "rb") as f:
            return plistlib.load(f).get("ZZMMIBuildTag")
    except (OSError, plistlib.InvalidFileException):
        return None


def latest_tag() -> str:
    # curl rather than urllib: the app bundle's Python looks for CA certificates where they were
    # on the build machine, so its HTTPS can fail on users' Macs. curl uses the system's.
    command = [
        "/usr/bin/curl", "--fail", "--silent", "--show-error", "--location",
        "--max-time", str(TIMEOUT_SECONDS), "--header", "Accept: application/vnd.github+json",
        LATEST_RELEASE_API,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS + 5)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpdateCheckError(f"Couldn't reach GitHub: {e}") from None
    if result.returncode != 0:
        raise UpdateCheckError(result.stderr.strip() or f"curl exited with code {result.returncode}")
    try:
        return json.loads(result.stdout)["tag_name"]
    except (ValueError, KeyError, TypeError):
        raise UpdateCheckError("GitHub sent an unexpected response.") from None


def update_available() -> bool:
    """Whether a newer release exists. Raises UpdateCheckError if that can't be determined."""
    current = build_tag()
    if current is None:
        raise UpdateCheckError("This copy of ZZMMI wasn't built by the release workflow, "
                               "so there's no release to compare it with.")
    return latest_tag() != current
