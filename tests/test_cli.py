
from zzmmi.cli import main


def parse(*argv):
    # Reuse the real parser by intercepting debug-launch's options.
    captured = {}
    import zzmmi.cli as cli
    real, real_discover = cli.debug_launch.run, cli.YaaglRuntime.discover
    cli.debug_launch.run = lambda opts, runs_dir, runtime=None: captured.setdefault("opts", opts)
    cli.YaaglRuntime.discover = classmethod(lambda cls, root=None: None)
    try:
        assert main(["debug-launch", *argv]) == 0
    finally:
        cli.debug_launch.run = real
        cli.YaaglRuntime.discover = real_discover
    return captured["opts"]


def test_rung_preset_and_overrides():
    opts = parse("--rung", "2", "--set", "System.load_library_redirect=0",
                 "--set", "System.dll_initialization_delay=5000", "--max-seconds", "45")
    assert opts.inject and not opts.core and opts.mods == [] and opts.hunting
    assert opts.ini_overrides == [("System", "load_library_redirect", "0"),
                                  ("System", "dll_initialization_delay", "5000")]
    assert opts.max_seconds == 45
    assert opts.block_crash_upload and not opts.crash_handler


def test_bad_set_is_rejected(capsys):
    assert main(["debug-launch", "--set", "no_section=1"]) == 1
    assert "SECTION.KEY=VALUE" in capsys.readouterr().err
