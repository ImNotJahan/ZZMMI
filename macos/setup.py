"""Build ZZMMI.app with py2app.

    pip install . py2app
    cd macos && python setup.py py2app

The app ends up in macos/dist/ZZMMI.app. The zzmmi package must be installed in the
building environment so py2app can find it.
"""
import os

from setuptools import setup

from zzmmi import __version__

# Release tag of this build (set by .github/workflows/release.yml). The app reads it back from
# Info.plist to check for updates; local builds leave it out.
build_tag = {"ZZMMIBuildTag": os.environ["ZZMMI_BUILD_TAG"]} if os.environ.get("ZZMMI_BUILD_TAG") else {}

setup(
    name="ZZMMI",
    app=["app_main.py"],  # not ZZMMI.py: on a case-insensitive disk py2app mistakes it for the zzmmi package
    options={
        "py2app": {
            "packages": ["zzmmi"],
            "iconfile": "icon.icns",
            "plist": {
                "CFBundleName": "ZZMMI",
                "CFBundleDisplayName": "ZZMMI",
                "CFBundleIdentifier": "io.github.imnotjahan.zzmmi",
                "CFBundleShortVersionString": __version__,
                "CFBundleVersion": __version__,
                "LSMinimumSystemVersion": "12.0",
                "NSHumanReadableCopyright": "MIT License",
                **build_tag,
            },
        }
    },
    setup_requires=["py2app"],
)
