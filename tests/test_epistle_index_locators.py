import pytest

from tools.indexing.epistle_index_locators import locator_key, parse_epistle_locators, roman_to_int


@pytest.mark.parametrize("raw, expected", [
    ("XV, 104.", ("register", 15, 104, None)),
    ("XIII 113.", ("register", 13, 113, None)),
    ("XVI,\n    165.", ("register", 16, 165, None)),
    ("XI, 103 bis.", ("register", 11, 103, "bis")),
    ("Suppl. 33 ter.", ("supplement", None, 33, "ter")),
    ("Supplem. 89 bis.", ("supplement", None, 89, "bis")),
    ("Supplementum 59.", ("supplement", None, 59, None)),
    ("Suppl., 138.", ("supplement", None, 138, None)),
    ("Registr. de neg.\n    imp. 24.", ("imperial_register", None, 24, None)),
    ("Registr. ad neg. imp. 49.", ("imperial_register", None, 49, None)),
    ("Registr. de imp. 180.", ("imperial_register", None, 180, None)),
    ("Registrum de negotiis imperii 180.", ("imperial_register", None, 180, None)),
    ("Registrum imperii 180.", ("imperial_register", None, 180, None)),
    ("Registr. de neg\n    . imp. 127.", ("imperial_register", None, 127, None)),
    ("Registr, de neg. imp. 68.", ("imperial_register", None, 68, None)),
    ("XIV.\n    Append. 2.", ("appendix", 14, 2, None)),
    ("XVI, Appen-\n    dix 7.", ("appendix", 16, 7, None)),
])
def test_printed_forms(raw, expected):
    result = parse_epistle_locators(raw, offset=100)
    assert not result["residuals"]
    assert len(result["locators"]) == 1
    locator = result["locators"][0]
    assert locator_key(locator) == expected
    assert raw[locator["char_start"] - 100:locator["char_end"] - 100] == locator["raw"]


def test_lists_preserve_local_context_and_reset_on_new_series():
    result = parse_epistle_locators("XV, 104, 105; XVI, 2; Suppl. 35 bis, 36; ibid., 37; ibid.")
    assert not result["residuals"]
    assert [locator_key(x) for x in result["locators"]] == [
        ("register", 15, 104, None), ("register", 15, 105, None),
        ("register", 16, 2, None), ("supplement", None, 35, "bis"),
        ("supplement", None, 36, None), ("supplement", None, 37, None),
        ("supplement", None, 37, None),
    ]
    inherited = result["locators"][1]
    assert inherited["inherited_from"] == {"char_start": 0, "char_end": 7}


@pytest.mark.parametrize("raw", ["104", "ibid.", "IIII, 104.", "I, 0", "I, 045", "I, unknown 104", "Append. 7"])
def test_does_not_guess_missing_or_corrupt_book(raw):
    result = parse_epistle_locators(raw)
    assert not result["locators"]
    assert result["residuals"]


def test_residue_breaks_inheritance_but_later_explicit_locator_survives():
    result = parse_epistle_locators("I, 4; prose 99; 5; II, 8")
    assert [locator_key(x) for x in result["locators"]] == [
        ("register", 1, 4, None), ("register", 2, 8, None)
    ]
    assert [x["raw"] for x in result["residuals"]] == ["prose 99", "5"]


def test_range_does_not_become_unproven_second_epistle():
    result = parse_epistle_locators("XV, 104–108")
    assert [x["epistle"] for x in result["locators"]] == [104]
    assert result["locators"][0]["warnings"] == ["range_endpoint_unparsed"]
    assert result["residuals"][0]["raw"] == "–108"


def test_separate_calls_never_inherit():
    parse_epistle_locators("I, 8")
    assert not parse_epistle_locators("9")["locators"]


def test_suffix_is_part_of_identity():
    keys = [locator_key(parse_epistle_locators(raw)["locators"][0])
            for raw in ["Suppl. 35", "Suppl. 35 bis", "Suppl. 35 ter"]]
    assert len(set(keys)) == 3
    assert roman_to_int("XV") == 15
    assert roman_to_int("VX") is None
