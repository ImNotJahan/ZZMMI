import pytest

from zzmmi import mods
from zzmmi.debug_launch import DebugOptions, patch_d3dx
from zzmmi.ini import IniDoc
from zzmmi.runtime.yaagl import YaaglError


@pytest.fixture
def library(tmp_path):
    (tmp_path / "Mods" / "Piper").mkdir(parents=True)
    (tmp_path / "Mods" / "DISABLED_Lucy").mkdir()
    (tmp_path / "Mods" / "loose.ini").write_text("")
    (tmp_path / "Mods" / ".DS_Store").write_text("")
    (tmp_path / "Mods" / "readme.txt").write_text("")
    (tmp_path / "ModsNotUsing" / "Anby").mkdir(parents=True)
    return mods.ModLibrary(tmp_path)


def state(library):
    return {m.name: m.enabled for m in library.mods()}


def test_lists_what_3dmigoto_would_include(library):
    assert state(library) == {"Anby": False, "loose.ini": True, "Lucy": False, "Piper": True}


def test_disable_moves_to_mods_not_using(library, tmp_path):
    library.set_enabled("Piper", False)
    assert (tmp_path / "ModsNotUsing" / "Piper").is_dir()
    assert not (tmp_path / "Mods" / "Piper").exists()
    library.set_enabled("Piper", True)
    assert (tmp_path / "Mods" / "Piper").is_dir()


def test_enable_strips_disabled_prefix(library, tmp_path):
    library.set_enabled("Lucy", True)
    assert (tmp_path / "Mods" / "Lucy").is_dir()
    assert state(library)["Lucy"] is True


def test_disable_creates_folder_and_refuses_to_clobber(library, tmp_path):
    (tmp_path / "ModsNotUsing" / "Anby").rename(tmp_path / "Anby-elsewhere")
    (tmp_path / "ModsNotUsing").rmdir()
    library.set_enabled("Piper", False)
    assert (tmp_path / "ModsNotUsing" / "Piper").is_dir()
    (tmp_path / "Mods" / "Piper").mkdir()
    with pytest.raises(YaaglError, match="ambiguous"):
        library.set_enabled("Piper", True)


def test_unknown_mod(library):
    with pytest.raises(YaaglError, match="No mod"):
        library.set_enabled("Nope", True)


def test_reload_is_a_noop_without_the_game(monkeypatch):
    monkeypatch.setattr(mods, "game_running", lambda: False)
    assert mods.reload_mods() is False


def test_toggle_presses_f6_and_reload_presses_f10(monkeypatch, tmp_path):
    from zzmmi.runtime.yaagl import YaaglRuntime
    sent = []
    monkeypatch.setattr(mods, "game_running", lambda: True)
    monkeypatch.setattr(mods, "KEYS_EXE", tmp_path / "zzmmi-keys.exe")
    (tmp_path / "zzmmi-keys.exe").write_bytes(b"")
    monkeypatch.setattr(mods.subprocess, "run",
                        lambda cmd, **kw: sent.append(cmd[-1]) or mods.subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(YaaglRuntime, "env", lambda self, **kw: {})
    runtime = YaaglRuntime(tmp_path)
    assert mods.toggle_all_mods(runtime) and mods.reload_mods(runtime)
    assert sent == ["F6", "F10"]


def test_launch_patch_lets_reload_key_through_when_unfocused(tmp_path):
    ini = tmp_path / "d3dx.ini"
    ini.write_text("[System]\ncheck_foreground_window = 1\n")
    patch_d3dx(ini, DebugOptions())
    assert IniDoc.load(ini).get("System", "check_foreground_window") == "0"
