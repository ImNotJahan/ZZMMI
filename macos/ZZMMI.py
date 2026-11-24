"""Entry point for the ZZMMI.app bundle.

With no arguments it opens the GUI. With arguments it behaves like the `zzmmi` CLI; the GUI
relies on this to run `debug-launch` as a child process of the bundled interpreter.
"""
import sys

args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]  # Finder's process serial number

if args:
    from zzmmi.cli import main

    sys.exit(main(args))
else:
    from zzmmi.gui import main

    sys.exit(main())
