"""Build ZZMMI.app with py2app.

    pip install . py2app
    cd macos && python setup.py py2app

The app ends up in macos/dist/ZZMMI.app. The zzmmi package must be installed in the
building environment so py2app can find it.
"""
from setuptools import setup

from zzmmi import __version__

setup(
    name="ZZMMI",
    app=["ZZMMI.py"],
    options={
        "py2app": {
            "packages": ["zzmmi"],
            "plist": {
                "CFBundleName": "ZZMMI",
                "CFBundleDisplayName": "ZZMMI",
                "CFBundleIdentifier": "io.github.imnotjahan.zzmmi",
                "CFBundleShortVersionString": __version__,
                "CFBundleVersion": __version__,
                "LSMinimumSystemVersion": "12.0",
                "NSHumanReadableCopyright": "MIT License",
            },
        }
    },
    setup_requires=["py2app"],
)
