from zzmmi.conflicts import find_conflicts, mod_overrides, parse_overrides

HAIR = """
[TextureOverrideNormaHairA]
hash = A2150D3B
match_first_index = 0
handling = skip

[TextureOverrideNormaHairB]
hash = a2150d3b
match_first_index = 21237

[TextureOverrideNormaHairBlend]
hash = 0a09cb32

[ResourceNormaHairADiffuse]
hash = ffffffff
filename = NormaHairADiffuse.dds
"""


def test_parse_overrides():
    assert parse_overrides(HAIR) == {("a2150d3b", "0"), ("a2150d3b", "21237"), ("0a09cb32", "")}


def test_parts_of_one_index_buffer_are_separate():
    a = parse_overrides("[TextureOverrideA]\nhash = 1234abcd\nmatch_first_index = 0\n")
    b = parse_overrides("[TextureOverrideB]\nhash = 1234abcd\nmatch_first_index = 500\n")
    assert find_conflicts({"a": a, "b": b}) == []


def test_deliberate_priority_is_not_a_conflict():
    layered = "[TextureOverrideX]\nhash = 0a09cb32\nmatch_priority = 20\n"
    default = "[TextureOverrideX]\nhash = 0a09cb32\nmatch_priority = 0\n"
    assert parse_overrides(layered) == set()
    assert parse_overrides(default) == {("0a09cb32", "")}


def test_find_conflicts_pairs_in_order():
    hair = parse_overrides(HAIR)
    overrides = {"Hamster": hair, "mechanic": hair, "Lucy": {("deadbeef", "")}, "ShortHair": {("0a09cb32", "")}}
    assert find_conflicts(overrides) == [("Hamster", "mechanic"), ("Hamster", "ShortHair"), ("mechanic", "ShortHair")]


def test_only_loaded_inis_count(tmp_path):
    mod = tmp_path / "mod"
    (mod / "DISABLED_old").mkdir(parents=True)
    (mod / "DISABLED_old" / "Norma.ini").write_text(HAIR)
    (mod / "DISABLED_BACKUP_1.Norma.ini").write_text(HAIR)
    (mod / "notes.txt").write_text(HAIR)
    (mod / "Norma.ini").write_text("[TextureOverrideNormaBodyBlend]\nhash = 11111111\n")
    assert mod_overrides(mod) == {("11111111", "")}


def test_single_ini_mod(tmp_path):
    ini = tmp_path / "Norma.ini"
    ini.write_text(HAIR)
    assert len(mod_overrides(ini)) == 3
