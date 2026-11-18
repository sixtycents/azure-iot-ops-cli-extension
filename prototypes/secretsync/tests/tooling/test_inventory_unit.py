import importlib

import pytest

from secretsync_bdd.inventory import load_inventory
from secretsync_bdd.runner import FEATURES


def feature(tmp_path, text):
    (tmp_path / "sample.feature").write_text(text, encoding="utf-8")
    return tmp_path


def test_approved_inventory():
    # Plugins import the inventory before pytest-cov starts. Exercise fresh startup too.
    from secretsync_bdd import inventory
    cases = importlib.reload(inventory).load_inventory(FEATURES)
    assert len(cases) == 179
    assert len({c.scenario_id for c in cases}) == 85
    assert len({c.key for c in cases}) == 179
    assert len(load_inventory(FEATURES, e2e=True)) == 10


def test_official_expansion_rules_and_inheritance(tmp_path):
    root = feature(tmp_path, """@e2e @suite
Feature: inherited
  Background:
    Given feature background
  Rule: nested
    Background:
      Given rule background
    @scenario-SS-001
    Scenario Outline: hello <value>
      When escaped <value>
      Then done
      Examples: first
        | case | value |
        | one  | a\\|b |
      Examples: second
        | case | value |
        | two  | next  |
    @scenario-SS-002
    Scenario: plain
      Then fine
""")
    cases = load_inventory(root, True)
    assert len(cases) == 3
    assert cases[0].example_name == "one"
    assert cases[0].name == "hello a|b"
    assert cases[0].steps == ("feature background", "rule background", "escaped a|b", "done")
    assert cases[0].tags == ("e2e", "scenario-SS-001", "suite")
    assert cases[0].scenario_line < cases[0].example_line
    assert cases[0].key == "sample.feature:SS-001:12:14"
    assert cases[1].key == "sample.feature:SS-001:15:17"
    assert cases[2].example_line is None
    assert cases[2].feature_path == "sample.feature"


@pytest.mark.parametrize("text, message", [
    ("Feature: bad\n Scenario: missing ID\n  Then no\n", "require one"),
    ("Feature: bad\n @scenario-SS-x\n Scenario: ID\n  Then no\n", "require one"),
    ("Feature: bad\n @scenario-SS-1 @scenario-SS-2\n Scenario: ID\n  Then no\n", "require one"),
    ("Feature: bad\n @scenario-SS-1\n Scenario Outline: no rows\n  Then no\n", "empty outline"),
    ("Feature: bad\n @scenario-SS-1\n Scenario Outline: no rows\n  Then no\n  Examples:\n   | case |\n",
     "empty Examples"),
    ("not valid gherkin", "invalid Gherkin"),
    ("Feature: bad\n @scenario-SS-1\n Scenario: first\n  Then no\n"
     " @scenario-SS-1\n Scenario: duplicate\n  Then no\n", "Duplicate"),
])
def test_invalid_specification(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        load_inventory(feature(tmp_path, text))


def test_empty_and_non_e2e(tmp_path):
    with pytest.raises(ValueError, match="No feature"):
        load_inventory(tmp_path)
    feature(tmp_path, "Feature: plain\n @scenario-SS-1\n Scenario: plain\n  Then done\n")
    assert load_inventory(tmp_path, True) == []


def test_outline_without_case_still_has_location_identity(tmp_path):
    feature(tmp_path, """Feature: no case column
 @scenario-SS-1
 Scenario Outline: <x>
  Then <x>
  Examples:
   | x |
   | a |
   | b |
""")
    cases = load_inventory(tmp_path)
    assert cases[0].example_name is None
    assert cases[0].key != cases[1].key
