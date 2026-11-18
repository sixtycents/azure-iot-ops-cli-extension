import json

import pytest

from secretsync_bdd import reporting, runner


def test_report_has_readable_tests_cases_and_coverage_without_html_injection(tmp_path):
    payload = {
        "status": "failed", "gates": {"tests": "failed", "coverage": "failed"},
        "tests": [{"name": "<unsafe-test>", "outcome": "failure", "seconds": 1.0}],
        "cases": {"cases": [{"scenario_id": "SS-1", "example_name": None,
                             "outcome": "failed", "feature_path": "<feature>"}]},
        "coverage": {"files": {"src/example.py": {"statements": 2, "missing_lines": [2], "excluded_lines": []}}},
    }
    reporting.write_report(tmp_path, "result", payload)
    text = (tmp_path / "result.html").read_text()
    assert all(heading in text for heading in ("All tests", "Feature executions", "Scoped line coverage", "failure"))
    assert "<unsafe-test>" not in text and "&lt;unsafe-test&gt;" in text
    assert json.loads((tmp_path / "result.json").read_text())["tests"] == payload["tests"]


@pytest.mark.parametrize("outcome", ["passed", "failure", "error", "skipped"])
def test_junit_extracts_outcomes_without_serializing_failure_diagnostics(tmp_path, outcome):
    child = "" if outcome == "passed" else f'<{outcome}>sensitive-diagnostic</{outcome}>'
    (tmp_path / "junit.xml").write_text(
        f'<testsuite><testcase name="case" classname="module" time="1.25">{child}</testcase></testsuite>')
    result = runner.junit_results(tmp_path)
    assert result == [{"name": "case", "class": "module", "outcome": outcome, "seconds": 1.25}]
    assert "sensitive-diagnostic" not in json.dumps(result)


@pytest.mark.parametrize("body", ["not XML", "<testsuites/>"])
def test_malformed_or_empty_junit_is_not_a_pass(tmp_path, body):
    (tmp_path / "junit.xml").write_text(body)
    with pytest.raises(ValueError, match="JUnit"):
        runner.junit_results(tmp_path)
