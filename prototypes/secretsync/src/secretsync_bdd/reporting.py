"""Versioned JSON plus escaped, self-contained HTML. Never serialize process environments."""

import html
import json
from collections import Counter
from pathlib import Path


def table(headers, rows):
    head = "".join(f"<th>{html.escape(str(value))}</th>" for value in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def document(report, text):
    cases = report.get("cases") or []
    if isinstance(cases, dict):
        cases = cases.get("cases", [])
    tests = report.get("tests") or []
    counts = dict(Counter(test["outcome"] for test in tests))
    sections = [
        "<h1>SecretSync qualification: " + html.escape(report["status"]) + "</h1>",
        "<p>" + html.escape(str(counts or report.get("outcomes", {}))) + "</p>",
        table(("Gate", "Outcome"), sorted(report.get("gates", {}).items())),
    ]
    coverage = report.get("coverage") or {}
    if coverage.get("files"):
        sections += ["<h2>Scoped line coverage</h2>", table(
            ("Source file", "Executable lines", "Missing lines", "Excluded lines"),
            ((name, value["statements"], value["missing_lines"], value["excluded_lines"])
             for name, value in sorted(coverage["files"].items())))]
    if cases:
        sections += ["<h2>Feature executions</h2>", table(
            ("Scenario", "Example", "Outcome", "Feature"),
            ((case["scenario_id"], case.get("example_name") or "", case["outcome"], case["feature_path"])
             for case in cases))]
    if tests:
        sections += ["<h2>All tests</h2>", table(
            ("Test", "Outcome", "Seconds"), ((test["name"], test["outcome"], test["seconds"]) for test in tests))]
    return (
        '<!doctype html><meta charset="utf-8"><title>SecretSync qualification</title>'
        "<style>body{font:15px system-ui;margin:2em}table{border-collapse:collapse;width:100%;margin-bottom:2em}"
        "th,td{text-align:left;padding:.4em;border:1px solid #ccc}th{background:#eee}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
        '<nav><a href="result.html">Summary</a> | <a href="cases.html">Scenarios</a> | '
        '<a href="coverage-html/index.html">Coverage (offline)</a> | <a href="junit.xml">JUnit</a></nav>'
        + "".join(sections)
        + f"<details><summary>Full JSON evidence</summary><pre>{html.escape(text)}</pre></details>"
    )


def write_report(directory: Path, name: str, report: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"schema_version": 1, **report}, indent=2, sort_keys=True)
    (directory / f"{name}.json").write_text(text, encoding="utf-8")
    (directory / f"{name}.html").write_text(document(report, text), encoding="utf-8")
