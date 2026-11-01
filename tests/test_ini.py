from zzmmi.debug_launch import DebugOptions, patch_d3dx
from zzmmi.ini import IniDoc

SAMPLE = """\
; header comment
[Loader]
target = ZenlessZoneZero.exe
loader = XXMI Launcher.exe

[Include]
; include = Core\\Debugger\\Debugger.ini
include = Core\\ZZMI\\main.ini
include_recursive = Mods
exclude_recursive = DISABLED*

[Hunting]
hunting = 0

[logging]
log_level = disabled
"""


def test_round_trip_is_lossless():
    assert str(IniDoc(SAMPLE)) == SAMPLE
    crlf = SAMPLE.replace("\n", "\r\n")
    assert str(IniDoc(crlf)) == crlf


def test_load_save_preserves_crlf(tmp_path):
    path = tmp_path / "d3dx.ini"
    path.write_bytes(SAMPLE.replace("\n", "\r\n").encode())
    doc = IniDoc.load(path)
    doc.set("Hunting", "hunting", 1)
    doc.save(path)
    data = path.read_bytes()
    assert data.count(b"\r\n") == data.count(b"\n")
    assert b"hunting = 1\r\n" in data


def test_get_is_case_insensitive_and_skips_comments():
    doc = IniDoc(SAMPLE)
    assert doc.get("LOGGING", "Log_Level") == "disabled"
    assert doc.get_all("Include", "include") == ["Core\\ZZMI\\main.ini"]
    assert doc.get("Hunting", "missing") is None


def test_set_rewrites_in_place_or_appends_to_section():
    doc = IniDoc(SAMPLE)
    doc.set("Hunting", "hunting", 1)
    doc.set("Hunting", "marking_mode", "skip")
    doc.set("NewSection", "x", "y")
    text = str(doc)
    assert "hunting = 1\nmarking_mode = skip\n\n[logging]" in text
    assert text.endswith("[NewSection]\nx = y\n")


def test_disable_enable_and_add():
    doc = IniDoc(SAMPLE)
    assert doc.disable("Include", "include_recursive", "mods") == 1
    doc.add("Include", "include_recursive", "Mods\\One")
    assert doc.get_all("Include", "include_recursive") == ["Mods\\One"]
    assert doc.enable("Include") == 1
    assert doc.get_all("Include", "include_recursive") == ["Mods", "Mods\\One"]


def test_patch_d3dx_isolates_mods(tmp_path):
    path = tmp_path / "d3dx.ini"
    path.write_text(SAMPLE)
    patch_d3dx(path, DebugOptions(core=False, mods=["Some Mod"], hunting=True, log_level="info"))
    doc = IniDoc.load(path)
    assert doc.get_all("Include", "include") == []
    assert doc.get_all("Include", "include_recursive") == ["Mods\\Some Mod"]
    assert doc.get_all("Include", "exclude_recursive") == ["DISABLED*"]
    assert doc.get("Hunting", "hunting") == "1"
    assert doc.get("Logging", "log_level") == "info"
    assert doc.get("Logging", "unbuffered") == "1"
    assert doc.get("Logging", "crash") == "0"  # crash handler hangs ZZZ (MHYPBase exceptions)
