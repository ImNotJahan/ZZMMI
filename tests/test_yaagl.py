from pathlib import Path

import pytest

from zzmmi.runtime.yaagl import DXMT_SWAPPED, MACHO_MAGIC, YaaglError, YaaglRuntime, to_windows_path


@pytest.fixture
def yaagl(tmp_path: Path) -> YaaglRuntime:
    root = tmp_path / "Yaagl ZZZ OS"
    game = tmp_path / "ZZZ"
    (root / "wine" / "bin").mkdir(parents=True)
    (root / "wine" / "bin" / "wine").write_bytes(MACHO_MAGIC + b"rest")
    lib_win = root / "wine" / "lib" / "wine" / "x86_64-windows"
    lib_win.mkdir(parents=True)
    (root / "wine" / "lib" / "wine" / "x86_64-unix").mkdir()
    (root / "wineprefix" / "drive_c" / "windows" / "system32").mkdir(parents=True)
    (root / "wineprefix" / "drive_c" / "windows" / "syswow64").mkdir(parents=True)
    (root / "dxmt").mkdir()
    for name in DXMT_SWAPPED + ("winemetal.dll", "winemetal.so"):
        (root / "dxmt" / name).write_text(f"dxmt {name}")
    for name in DXMT_SWAPPED:
        (lib_win / name).write_text(f"wine {name}")
    extras = root / "sidecar" / "protonextras"
    extras.mkdir(parents=True)
    for name in ("steam64.exe", "steam32.exe", "lsteamclient64.dll", "lsteamclient32.dll"):
        (extras / name).write_text(name)
    (root / ".storage").mkdir()
    (root / ".storage" / "game_install_dir.neustorage").write_text(str(game))
    (root / ".storage" / "config_steam_patch.neustorage").write_text("true")
    plugins = game / "ZenlessZoneZero_Data" / "Plugins" / "x86_64"
    plugins.mkdir(parents=True)
    (plugins / "vulkan-1.dll").write_text("game vulkan")
    return YaaglRuntime.discover(root)


def test_discover_rejects_missing_install(tmp_path):
    with pytest.raises(YaaglError):
        YaaglRuntime.discover(tmp_path / "nope")


def test_settings_and_command(yaagl):
    assert yaagl.wine_is_real()
    assert yaagl.flag("config_steam_patch")
    assert not yaagl.flag("config_block_net")
    assert yaagl.game_command(["-use-d3d11"])[-1] == "-use-d3d11"
    cmd = yaagl.game_command()
    assert cmd[1] == r"C:\windows\system32\steam.exe"
    assert cmd[2].startswith("Z:\\") and cmd[2].endswith("\\ZZZ\\ZenlessZoneZero.exe")
    env = yaagl.env(extra={"DXMT_LOG_LEVEL": "debug"})
    assert env["WINEPREFIX"] == str(yaagl.prefix)
    assert env["WINEMSYNC"] == "1" and env["WINE_ENABLE_TIMEOUT_FIX"] == "1"
    assert env["DXMT_LOG_LEVEL"] == "debug"


def test_to_windows_path():
    assert to_windows_path(Path("/Users/x/ZZZ/a b.exe")) == "Z:\\Users\\x\\ZZZ\\a b.exe"


def test_dxmt_session_swaps_and_restores(yaagl):
    with yaagl.dxmt_session():
        for name in DXMT_SWAPPED:
            assert (yaagl.lib_windows / name).read_text() == f"dxmt {name}"
            assert (yaagl.lib_windows / f"{name}.bak").read_text() == f"wine {name}"
        assert (yaagl.system32 / "steam.exe").read_text() == "steam64.exe"
        assert (yaagl.system32 / "winemetal.dll").exists()
        assert not yaagl.game_vulkan_dll.exists()
        assert yaagl.dxmt_installed()
    for name in DXMT_SWAPPED:
        assert (yaagl.lib_windows / name).read_text() == f"wine {name}"
        assert not (yaagl.lib_windows / f"{name}.bak").exists()
    assert yaagl.game_vulkan_dll.read_text() == "game vulkan"
    assert not yaagl.dxmt_installed()


def test_stale_backup_is_never_overwritten(yaagl):
    yaagl.install_dxmt()  # simulate a session that was never restored
    yaagl.install_dxmt()
    assert (yaagl.lib_windows / "d3d11.dll.bak").read_text() == "wine d3d11.dll"
    yaagl.restore_dxmt()
    assert (yaagl.lib_windows / "d3d11.dll").read_text() == "wine d3d11.dll"


def test_crash_uploaders_hidden_and_restored(yaagl):
    uploader = yaagl.crash_uploaders()[0]
    uploader.write_text("exe")
    with yaagl.hidden(yaagl.crash_uploaders()):
        assert not uploader.exists()
    assert uploader.read_text() == "exe"
    yaagl.hide([uploader])  # interrupted session: `zzmmi restore` path
    assert yaagl.unhide(yaagl.crash_uploaders()) == [uploader]


def test_env_does_not_leak_host_variables(yaagl, monkeypatch):
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("HOME", "/Users/x")
    env = yaagl.env()
    assert "NoDefaultCurrentDirectoryInExePath" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert env["HOME"] == "/Users/x"


def test_crossover_runtime_commands(yaagl, tmp_path):
    from zzmmi.runtime.crossover import CrossOverRuntime
    cx = CrossOverRuntime(yaagl.root, bottle="ZZMMI", app=tmp_path / "CrossOver.app")
    cmd = cx.game_command(["-use-d3d11"])
    assert cmd[1:3] == ["--bottle", "ZZMMI"]
    assert cmd[3] == r"C:\windows\system32\steam.exe" and cmd[-1] == "-use-d3d11"
    env = cx.env()
    assert env["CX_BOTTLE"] == "ZZMMI" and "WINEMSYNC" not in env
    assert cx.prefix.name == "ZZMMI"


def test_system32_proxy_install_and_restore(yaagl):
    from zzmmi.debug_launch import (SYSTEM32_PROXY_DLL, SYSTEM32_ZZMI_LINKS,
                                    install_system32_proxy, restore_system32_proxy)
    zzmi = yaagl.prefix / "drive_c" / "XXMI Launcher" / "ZZMI"
    for name in ("Core", "Mods", "ShaderFixes", "ShaderCache"):
        (zzmi / name).mkdir(parents=True)
    for name in ("d3dx.ini", "d3dx_user.ini", "d3d11_log.txt"):
        (zzmi / name).write_text(name)
    (zzmi / "d3d11.dll").write_bytes(b"migoto")
    (zzmi / "d3dcompiler_47.dll").write_bytes(b"zzmi compiler")
    (yaagl.system32 / "d3d11.dll").write_bytes(b"wine d3d11")
    (yaagl.system32 / "D3DCompiler_47.dll").write_bytes(b"wine compiler")
    (yaagl.lib_windows / "d3d11.dll").write_bytes(b"dxmt d3d11")

    install_system32_proxy(yaagl, zzmi)
    assert (yaagl.system32 / "d3d11.dll").read_bytes() == b"migoto"
    assert (yaagl.lib_windows / "d3d11.dll").read_bytes() == b"migoto"
    assert (yaagl.system32 / SYSTEM32_PROXY_DLL).read_bytes() == b"dxmt d3d11.dll"
    for name in SYSTEM32_ZZMI_LINKS:
        assert (yaagl.system32 / name).is_symlink()

    restore_system32_proxy(yaagl, zzmi)
    assert (yaagl.system32 / "d3d11.dll").read_bytes() == b"wine d3d11"
    assert (yaagl.system32 / "D3DCompiler_47.dll").read_bytes() == b"wine compiler"
    assert not (yaagl.system32 / SYSTEM32_PROXY_DLL).exists()
    assert not (yaagl.lib_windows / SYSTEM32_PROXY_DLL).exists()
    for name in SYSTEM32_ZZMI_LINKS:
        assert not (yaagl.system32 / name).exists()
    assert restore_system32_proxy(yaagl, zzmi) == []
