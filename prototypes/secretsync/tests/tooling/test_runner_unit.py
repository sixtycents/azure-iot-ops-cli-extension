import json
import subprocess
from types import SimpleNamespace as NS

import pytest

from secretsync_bdd import runner, whitebox


def success_cases(run):
    data = {"status": "passed", "expected": 1, "collected": 1, "errors": [], "outcomes": {"passed": 1}}
    (run / "cases.json").write_text(json.dumps(data))
    return data


def coverage_file(run, source, *, missing=(), excluded=(), statements=2, omitted=False, relative=False):
    files = {}
    for path in source.rglob("*.py"):
        if not omitted:
            name = str(path.relative_to(runner.PROTOTYPE)) if relative else str(path)
            files[name] = {"summary": {"num_statements": statements, "missing_lines": len(missing)},
                           "missing_lines": list(missing), "excluded_lines": list(excluded)}
    (run / "coverage.json").write_text(json.dumps({"files": files}))


def test_fresh_output_and_environment(tmp_path, monkeypatch):
    output = tmp_path / "output"
    first, second = runner.new_run(output), runner.new_run(output)
    assert first != second
    for variable in ("COVERAGE_FILE", "COV_CORE_SOURCE", "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
                     "SECRETSYNC_E2E_PROFILE"):
        monkeypatch.setenv(variable, "untrusted")
    env = runner.environment(first)
    assert env["COVERAGE_FILE"] == str(first / ".coverage")
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert "PYTEST_ADDOPTS" not in env
    assert "SECRETSYNC_E2E_PROFILE" not in env
    assert env["PYTHONPATH"] == str(runner.PROTOTYPE / "src")
    assert not (first / ".coverage").exists()


@pytest.mark.parametrize("suffix", ["", "src", "src/nested", "tests", "tests/tooling"])
def test_output_cannot_overwrite_source(suffix):
    with pytest.raises(ValueError, match="protected"):
        runner.new_run(runner.PROTOTYPE / suffix)


@pytest.mark.parametrize("suffix", ["", "tests", "edge"])
def test_output_cannot_overwrite_native_extension(suffix):
    with pytest.raises(ValueError, match="protected"):
        runner.new_run(runner.REPOSITORY / "azext_edge" / suffix)


def test_subprocess_uses_interpreter_no_shell_and_plugins(tmp_path, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return NS(returncode=0)
    monkeypatch.setattr(runner.subprocess, "run", run)
    runner.execute(["-m", "pytest"], {"safe": "yes"}, 7)
    assert calls[0][0] == [runner.sys.executable, "-m", "pytest"]
    assert calls[0][1]["timeout"] == 7
    assert calls[0][1]["env"] == {"safe": "yes"}
    assert not calls[0][1].get("shell")
    offline = runner.pytest_arguments(tmp_path)
    live = runner.pytest_arguments(tmp_path, True)
    assert "--disable-socket" in offline
    assert "pytest_cov.plugin" in offline
    assert "--secretsync-e2e" in live
    assert "-m" in live and "e2e" in live
    assert "pytest_cov.plugin" not in live and "--disable-socket" not in live
    assert str(runner.PROTOTYPE / "tests" / "tooling") not in live


@pytest.mark.parametrize("change", [{}, {"status": "failed"}, {"expected": 0}, {"collected": 2},
                                    {"errors": ["failure"]}, {"outcomes": {"skipped": 1}}])
def test_completeness_readback(tmp_path, change):
    report = success_cases(tmp_path)
    report.update(change)
    (tmp_path / "cases.json").write_text(json.dumps(report))
    if change:
        with pytest.raises(ValueError, match="completeness"):
            runner.completeness(tmp_path)
    else:
        assert runner.completeness(tmp_path) == report


def test_completeness_rejects_nonobject_artifact(tmp_path):
    (tmp_path / "cases.json").write_text("[]")
    with pytest.raises(ValueError, match="Invalid completeness"):
        runner.completeness(tmp_path)


@pytest.mark.parametrize("code", [0, 1, None])
def test_revision_is_optional(monkeypatch, code):
    def run(*a, **k):
        if code is None:
            raise FileNotFoundError("git unavailable")
        return NS(returncode=code, stdout="revision\n")
    monkeypatch.setattr(runner.subprocess, "run", run)
    report = runner.metadata()
    assert report["revision"] == ("revision" if code == 0 else None)
    assert report["dirty_worktree"] == (True if code == 0 else None)
    assert report["source_digest"]["files"] > 0
    assert report["spec_digest"]["files"] > 0


def test_metadata_clean_tree_and_timeout(monkeypatch):
    def clean(args, **kwargs):
        return NS(returncode=0, stdout="" if "status" in args else "revision")
    monkeypatch.setattr(runner.subprocess, "run", clean)
    assert runner.metadata()["dirty_worktree"] is False

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 10)
    monkeypatch.setattr(runner.subprocess, "run", timeout)
    assert runner.metadata()["dirty_worktree"] is None


def test_digest_binds_paths_and_contents_not_creation_order(tmp_path):
    (tmp_path / "b.py").write_text("value = 1")
    (tmp_path / "a.py").write_text("value = 2")
    before = runner.tree_digest(tmp_path, "*.py")
    assert before["files"] == 2
    (tmp_path / "ignored.txt").write_text("not source")
    assert runner.tree_digest(tmp_path, "*.py") == before
    (tmp_path / "a.py").write_text("value = 3")
    changed = runner.tree_digest(tmp_path, "*.py")
    assert changed != before
    (tmp_path / "a.py").rename(tmp_path / "renamed.py")
    assert runner.tree_digest(tmp_path, "*.py") != changed
    assert runner.tree_digest(tmp_path / "missing", "*.py")["files"] == 0


def test_finish_prints_status_gates_and_report_path(tmp_path, capsys):
    assert runner.finish(tmp_path, {"status": "failed", "gates": {"coverage": "failed"}}) == 1
    output = capsys.readouterr().out
    assert "SecretSync failed" in output and "coverage=failed" in output
    assert str(tmp_path / "result.html") in output


@pytest.mark.parametrize("mode", ["complete", "relative", "missing", "excluded", "unmeasured", "empty", "zero"])
def test_coverage_checks_every_file(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(runner, "PROTOTYPE", tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    if mode != "empty":
        (source / "unimported.py").write_text("value = 1\n")
    coverage_file(tmp_path, source, missing=(1,) if mode == "missing" else (),
                  excluded=(2,) if mode == "excluded" else (),
                  omitted=mode == "unmeasured", statements=0 if mode == "zero" else 2,
                  relative=mode == "relative")
    report = whitebox.inspect_coverage(tmp_path, source)
    assert report["status"] == ("passed" if mode in ("complete", "relative") else "failed")
    if mode == "unmeasured":
        assert "Unmeasured" in report["errors"][0]


@pytest.fixture
def offline(tmp_path, monkeypatch):
    root = tmp_path / "prototype"
    source = root / "src"
    source.mkdir(parents=True)
    (source / "unimported.py").write_text("value = 1\n")
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setattr(runner, "PROTOTYPE", root)
    monkeypatch.setattr(runner, "new_run", lambda path: run)
    monkeypatch.setattr(runner, "metadata", lambda: {"revision": None})
    return run, source


def fake_offline(monkeypatch, run, source, mode):
    def execute(args, env):
        assert env["COVERAGE_FILE"] == str(run / ".coverage")
        assert "exclude_lines =" in (run / "coverage.ini").read_text()
        if mode == "timeout":
            raise subprocess.TimeoutExpired("pytest", 1)
        if mode == "missing_tool":
            raise FileNotFoundError("python")
        if mode != "no_cases":
            report = success_cases(run)
            if mode == "incomplete":
                report["status"] = "failed"
                (run / "cases.json").write_text(json.dumps(report))
        coverage_file(run, source, missing=(1,) if mode == "missing_line" else ())
        if mode == "invalid_coverage":
            (run / "coverage.json").write_text("{}")
        for artifact in ("junit.xml", ".coverage", "coverage-html/index.html"):
            path = run / artifact
            path.parent.mkdir(exist_ok=True)
            if mode != artifact:
                path.write_text('<testsuite><testcase name="offline-unit"/></testsuite>'
                                if artifact == "junit.xml" else "artifact")
        return NS(returncode=1 if mode == "pytest_failed" else 0,
                  stdout="offline pytest output", stderr="offline pytest diagnostics")
    monkeypatch.setattr(runner, "execute", execute)


@pytest.mark.parametrize("mode", ["success", "pytest_failed", "no_cases", "incomplete", "missing_line",
                                  "invalid_coverage", "junit.xml", ".coverage", "coverage-html/index.html",
                                  "timeout", "missing_tool"])
def test_whitebox_runner_failure_artifacts(offline, monkeypatch, mode):
    run, source = offline
    fake_offline(monkeypatch, run, source, mode)
    assert whitebox.main(["--output", str(run)]) == (0 if mode == "success" else 1)
    report = json.loads((run / "result.json").read_text())
    assert report["status"] == ("passed" if mode == "success" else
                                "blocked" if mode in ("timeout", "missing_tool") else "failed")
    assert (run / "result.html").exists()
    if mode not in ("timeout", "missing_tool"):
        assert (run / "pytest.stdout.txt").read_text() == "offline pytest output"
        assert (run / "pytest.stderr.txt").read_text() == "offline pytest diagnostics"
    if mode in ("incomplete", "no_cases"):
        assert report["gates"]["coverage"] == "passed"
        assert report["gates"]["completeness"] == "failed"
    if mode == "incomplete":
        assert report["cases"]["status"] == "failed"


def test_runner_rejects_pytest_selectors(tmp_path):
    with pytest.raises(SystemExit) as error:
        whitebox.main(["--output", str(tmp_path), "-k", "only_this_case"])
    assert error.value.code == 2


def test_whitebox_module_entrypoint(offline, monkeypatch):
    import runpy
    import sys
    run, source = offline
    fake_offline(monkeypatch, run, source, "success")
    monkeypatch.setattr(sys, "argv", ["whitebox", "--output", str(run)])
    with pytest.raises(SystemExit) as error:
        runpy.run_module("secretsync_bdd.whitebox", run_name="__main__")
    assert error.value.code == 0
