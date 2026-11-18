"""Shared subprocess and artifact plumbing. No shell, inherited pytest options, or coverage config."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from .reporting import write_report


PROTOTYPE = Path(__file__).resolve().parents[2]
REPOSITORY = PROTOTYPE.parents[1]
FEATURES = REPOSITORY / "features" / "secretsync"


def new_run(output: Path) -> Path:
    output = output.resolve()
    # Refuse source trees and their ancestors: output must never contain a source directory.
    for protected in (PROTOTYPE / "src", PROTOTYPE / "tests", FEATURES, REPOSITORY / "azext_iot"):
        if output == protected or output in protected.parents or protected in output.parents:
            raise ValueError("Output overlaps a protected source directory")
    output.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=output))


def environment(run: Path) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("COV", "PYTEST", "SECRETSYNC_E2E_")):
            del env[key]
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", COVERAGE_FILE=str(run / ".coverage"),
               PYTHONPATH=str(PROTOTYPE / "src"))
    return env


def execute(arguments: list[str], env: dict, timeout: int = 1800):
    return subprocess.run([sys.executable, *arguments], cwd=PROTOTYPE, env=env,
                          capture_output=True, text=True, timeout=timeout, check=False)


def pytest_arguments(run: Path, live: bool = False) -> list[str]:
    args = ["-m", "pytest", "-o", "addopts=", "-o", "python_files=test_*.py *_unit.py",
            "--confcutdir=" + str(PROTOTYPE / "tests" / "e2e" if live else PROTOTYPE / "tests"),
            "-p", "pytest_bdd.plugin", "-p", "secretsync_bdd.plugin",
            "--secretsync-features=" + str(FEATURES), "--secretsync-report=" + str(run),
            "--junitxml=" + str(run / "junit.xml")]
    if live:
        return args + [str(PROTOTYPE / "tests" / "e2e"), "-m", "e2e", "--secretsync-e2e"]
    return args + ["-p", "pytest_cov.plugin", "-p", "pytest_socket", "--disable-socket",
                   str(PROTOTYPE / "tests" / "whitebox"), str(PROTOTYPE / "tests" / "tooling"),
                   "--cov=" + str(PROTOTYPE / "src"), "--cov-config=" + str(run / "coverage.ini"),
                   "--cov-report=json:" + str(run / "coverage.json"),
                   "--cov-report=html:" + str(run / "coverage-html"), "--cov-fail-under=100"]


def completeness(run: Path) -> dict:
    report = json.loads((run / "cases.json").read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Invalid completeness artifact")
    if (report["status"] != "passed" or not report["expected"]
            or report["collected"] != report["expected"] or report["errors"]
            or report["outcomes"] != {"passed": report["expected"]}):
        raise ValueError("Scenario completeness failed")
    return report


def junit_results(run: Path) -> list[dict]:
    try:
        root = ET.parse(run / "junit.xml").getroot()
    except ET.ParseError as error:
        raise ValueError("Invalid JUnit artifact") from error
    results = []
    for case in root.iter("testcase"):
        outcome = next((name for name in ("error", "failure", "skipped") if case.find(name) is not None), "passed")
        results.append({"name": case.attrib["name"], "class": case.get("classname", ""),
                        "outcome": outcome, "seconds": float(case.get("time", 0))})
    if not results:
        raise ValueError("Empty JUnit artifact")
    return results


def tree_digest(root: Path, pattern: str) -> dict:
    """SHA256 of sorted relative UTF-8 paths, NUL, 8-byte big-endian size, and file bytes."""
    digest = hashlib.sha256()
    paths = sorted(root.rglob(pattern), key=lambda path: path.relative_to(root).as_posix())
    for path in paths:
        content = path.read_bytes()
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return {"sha256": digest.hexdigest(), "files": len(paths)}


def metadata() -> dict:
    report = {"revision": None, "dirty_worktree": None,
              "source_digest": tree_digest(PROTOTYPE / "src", "*.py"),
              "spec_digest": tree_digest(FEATURES, "*.feature")}
    try:
        result = subprocess.run(["git", "-C", str(REPOSITORY), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=False, timeout=10)
        report["revision"] = result.stdout.strip() if result.returncode == 0 else None
        status = subprocess.run(["git", "-C", str(REPOSITORY), "status", "--porcelain", "--untracked-files=normal"],
                                capture_output=True, text=True, check=False, timeout=10)
        report["dirty_worktree"] = bool(status.stdout.strip()) if status.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return report
    return report


def finish(run: Path, report: dict) -> int:
    write_report(run, "result", report)
    gates = ", ".join(f"{key}={value}" for key, value in sorted(report.get("gates", {}).items()))
    print(f"SecretSync {report['status']}: {gates}\nReport: {run / 'result.html'}")
    return 0 if report["status"] == "passed" else 1
