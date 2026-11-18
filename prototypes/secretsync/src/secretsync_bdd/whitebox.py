"""Run: python -m secretsync_bdd.whitebox --output PATH (no selectors accepted)."""

import argparse
import json
from pathlib import Path
import subprocess

from . import runner


def inspect_coverage(run: Path, source: Path) -> dict:
    data = json.loads((run / "coverage.json").read_text(encoding="utf-8"))
    files = {Path(p).resolve() if Path(p).is_absolute() else (runner.PROTOTYPE / p).resolve(): details
             for p, details in data["files"].items()}
    expected = sorted(source.rglob("*.py"))
    errors, statements, results = [], 0, {}
    if not expected:
        errors.append("No source files")
    for path in expected:
        entry = files.get(path.resolve())
        if entry is None:
            errors.append(f"Unmeasured file: {path.relative_to(source)}")
            continue
        summary = entry["summary"]
        count = summary["num_statements"]
        statements += count
        missing = entry["missing_lines"]
        excluded = entry.get("excluded_lines", [])
        results[path.relative_to(source).as_posix()] = {
            "statements": count, "missing_lines": missing, "excluded_lines": excluded}
        if missing or summary["missing_lines"] or excluded:
            errors.append(f"Incomplete or excluded lines: {path.relative_to(source)}")
    if statements == 0:
        errors.append("Zero executable statements")
    return {"status": "failed" if errors else "passed", "statements": statements,
            "files": results, "errors": errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    run = runner.new_run(args.output)
    report = {"mode": "whitebox", "specification_baseline": "0.1.0",
              "status": "blocked", "errors": [], "cases": None, "coverage": None,
              "gates": {"tests": "not_run", "completeness": "not_run", "coverage": "not_run"}}
    try:
        report.update(runner.metadata())
        # Empty exclusion list replaces coverage.py's default pragma exclusions as well.
        (run / "coverage.ini").write_text("[run]\nbranch = false\n[report]\nexclude_lines =\n", encoding="utf-8")
        result = runner.execute(runner.pytest_arguments(run), runner.environment(run))
        (run / "pytest.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (run / "pytest.stderr.txt").write_text(result.stderr, encoding="utf-8")
        report["pytest_logs"] = {"stdout": "pytest.stdout.txt", "stderr": "pytest.stderr.txt"}
        report["gates"]["tests"] = "passed" if result.returncode == 0 else "failed"
        report["pytest_exit_code"] = result.returncode
        report["status"] = "failed"
        try:
            report["cases"] = json.loads((run / "cases.json").read_text(encoding="utf-8"))
            report["cases"] = runner.completeness(run)
            report["gates"]["completeness"] = "passed"
        except (OSError, ValueError, KeyError) as error:
            report["gates"]["completeness"] = "failed"
            report["errors"].append(f"Completeness: {error}")
        report["gates"]["coverage"] = "failed"
        coverage = inspect_coverage(run, runner.PROTOTYPE / "src")
        report["coverage"] = coverage
        report["gates"]["coverage"] = coverage["status"]
        for artifact in ("junit.xml", "coverage-html/index.html", ".coverage"):
            if not (run / artifact).is_file():
                raise ValueError(f"Missing artifact: {artifact}")
        report["tests"] = runner.junit_results(run)
        if all(value == "passed" for value in report["gates"].values()):
            report["status"] = "passed"
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    return runner.finish(run, report)


if __name__ == "__main__":
    raise SystemExit(main())
