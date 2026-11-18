import hashlib
import json
import subprocess
from types import SimpleNamespace as NS
from zipfile import ZipFile

import pytest

from secretsync_bdd import e2e, runner


def profile():
    return {
        "environment_type": "dev", "subscription_id": "test-subscription", "resource_group": "test-rg",
        "instance_name": "run-owned-aio", "vault_resource_id": "/vault", "identity_resource_id": "/identity",
        "cluster_name": "test-cluster", "cluster_resource_group": "test-rg", "namespace": "test-namespace",
        "ownership_token": "run-owned-token", "dedicated_instance": True,
    }


def wheel(path, metadata="Name: iot-secretsync-prototype\nVersion: 0.1.0\n", count=1):
    with ZipFile(path, "w") as archive:
        for index in range(count):
            archive.writestr(f"package{index}.dist-info/METADATA", metadata)
    return path


@pytest.mark.parametrize("change", [
    None, {"password": "forbidden"}, {"dedicated_instance": False}, {"environment_type": "prod"},
    {"instance_name": ""}, {"subscription_id": 5}, {"timeout_seconds": 0}, {"timeout_seconds": 7201},
    {"timeout_seconds": True}, {"timeout_seconds": 1.5},
    {"environment_type": []}, {"environment_type": {}}, {"namespace": []},
    {"dedicated_instance": []},
])
def test_profile_validation_no_secrets_in_error(tmp_path, change):
    data = profile()
    if change is None:
        del data["namespace"]
    else:
        data.update(change)
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"environment": data}))
    with pytest.raises(ValueError) as error:
        e2e.load_profile(path, "environment")
    assert "forbidden" not in str(error.value).lower().replace("credentials forbidden", "")


def test_profile_selection_and_explicit_timeout(tmp_path):
    path = tmp_path / "profiles.json"
    data = profile()
    path.write_text(json.dumps({"environment": data}))
    assert e2e.load_profile(path, "environment") == data
    with pytest.raises(ValueError, match="Unknown"):
        e2e.load_profile(path, "absent")
    data.update(environment_type="ppe", timeout_seconds=30)
    path.write_text(json.dumps({"environment": data}))
    assert e2e.load_profile(path, "environment")["timeout_seconds"] == 30
    path.write_text("[]")
    with pytest.raises(ValueError, match="JSON object"):
        e2e.load_profile(path, "environment")


@pytest.mark.parametrize("mode", ["suffix", "empty", "multiple", "name", "version", "valid"])
def test_wheel_identity_from_actual_metadata(tmp_path, mode):
    path = tmp_path / ("candidate.zip" if mode == "suffix" else "candidate.whl")
    metadata = "Name: iot-secretsync-prototype\nVersion: 0.1.0\n"
    if mode == "name":
        metadata = "Name: other-extension\nVersion: 0.1.0\n"
    if mode == "version":
        metadata = "Name: iot-secretsync-prototype\n"
    wheel(path, metadata, count={"empty": 0, "multiple": 2}.get(mode, 1))
    if mode == "valid":
        result = e2e.candidate_metadata(path)
        assert result["version"] == "0.1.0"
        assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        with pytest.raises(ValueError):
            e2e.candidate_metadata(path)


@pytest.fixture
def live_setup(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    profiles = tmp_path / "profiles.json"
    profiles.write_text(json.dumps({"isolated": profile()}))
    candidate = wheel(tmp_path / "candidate.whl")
    monkeypatch.setattr(runner, "new_run", lambda path: run)
    monkeypatch.setattr(runner, "metadata", lambda: {"revision": None})
    args = ["--profiles", str(profiles), "--environment", "isolated",
            "--candidate", str(candidate), "--output", str(run)]
    return NS(run=run, profiles=profiles, candidate=candidate, args=args)


def fake_live(monkeypatch, setup, mode):
    calls = []

    def execute(args, env, timeout):
        calls.append((args, dict(env)))
        assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert env["AZURE_EXTENSION_DIR"] == str(setup.run / "extensions")
        if mode == "timeout":
            raise subprocess.TimeoutExpired("az", 1)
        if "add" in args:
            assert args == ["-m", "azure.cli", "extension", "add", "--source",
                            str(setup.candidate.resolve()), "--yes"]
            return NS(returncode=1 if mode == "install" else 0)
        if "show" in args:
            installed = setup.run / "extensions" / "iot-secretsync-prototype"
            if mode != "absent_dir":
                installed.mkdir()
            actual = {"name": "iot-secretsync-prototype", "version": "0.1.0", "path": str(installed)}
            if mode in actual:
                actual[mode] = "wrong"
            if mode == "missing_key":
                del actual["path"]
            return NS(returncode=1 if mode == "verify" else 0,
                      stdout="not json" if mode == "json" else json.dumps(actual))
        assert "e2e" in args and "--secretsync-e2e" in args
        assert str(runner.PROTOTYPE / "tests" / "whitebox") not in args
        assert json.loads(env["SECRETSYNC_E2E_PROFILE"]) == profile()
        assert env["SECRETSYNC_E2E_CANDIDATE_DIR"] == str(
            setup.run / "extensions" / "iot-secretsync-prototype")
        report = {"status": "failed" if mode == "incomplete" else "passed", "expected": 10,
                  "collected": 10, "outcomes": {"passed": 10}, "errors": []}
        (setup.run / "cases.json").write_text(json.dumps(report))
        if mode != "junit":
            (setup.run / "junit.xml").write_text('<testsuite><testcase name="live-case"/></testsuite>')
        return NS(returncode=1 if mode == "pytest" else 0)
    monkeypatch.setattr(runner, "execute", execute)
    return calls


@pytest.mark.parametrize("mode", ["success", "install", "verify", "name", "version", "path", "absent_dir",
                                  "missing_key", "json", "timeout", "pytest", "incomplete", "junit"])
def test_live_runner_only_mocked_subprocess_boundary(live_setup, monkeypatch, mode):
    calls = fake_live(monkeypatch, live_setup, mode)
    result = e2e.main(live_setup.args + ["--allow-live", "--allow-destructive"])
    assert result == (0 if mode == "success" else 1)
    report = json.loads((live_setup.run / "result.json").read_text())
    assert report["candidate"]["sha256"]
    assert report["environment"] == "isolated"
    assert "ownership_token" not in json.dumps(report)
    assert (live_setup.run / "result.html").exists()
    assert not (live_setup.run / "pytest.stdout.txt").exists()
    assert not (live_setup.run / "pytest.stderr.txt").exists()
    if mode in ("success", "pytest", "incomplete", "junit"):
        assert len(calls) == 3
        assert report["status"] == ("passed" if mode == "success" else "failed")
    else:
        assert report["status"] == "blocked"
        assert report["errors"]
    if mode == "incomplete":
        assert report["cases"]["status"] == "failed"


@pytest.mark.parametrize("flags", [[], ["--allow-live"], ["--allow-destructive"]])
def test_opt_in_required_before_side_effects(live_setup, monkeypatch, flags):
    def unexpected(*a, **k):
        pytest.fail("No live side effects without both authorizations")
    monkeypatch.setattr(runner, "execute", unexpected)
    assert e2e.main(live_setup.args + flags) == 1
    report = json.loads((live_setup.run / "result.json").read_text())
    assert report["status"] == "blocked"


@pytest.mark.parametrize("mode", ["missing_profile", "bad_profile", "bad_wheel", "missing_wheel"])
def test_setup_failures_still_report(live_setup, monkeypatch, mode):
    if mode == "missing_profile":
        live_setup.profiles.unlink()
    elif mode == "bad_profile":
        live_setup.profiles.write_text("invalid json")
    elif mode == "bad_wheel":
        live_setup.candidate.write_text("not a wheel")
    else:
        live_setup.candidate.unlink()

    def unexpected(*args, **kwargs):
        pytest.fail("Validation must precede Azure CLI")
    monkeypatch.setattr(runner, "execute", unexpected)
    assert e2e.main(live_setup.args + ["--allow-live", "--allow-destructive"]) == 1
    assert json.loads((live_setup.run / "result.json").read_text())["status"] == "blocked"


def test_exception_type_survives_without_exposing_exception_text(live_setup, monkeypatch):
    sensitive_text = "caller-supplied-sensitive-diagnostic"

    def unavailable(*args, **kwargs):
        raise OSError(sensitive_text)
    monkeypatch.setattr(runner, "execute", unavailable)
    assert e2e.main(live_setup.args + ["--allow-live", "--allow-destructive"]) == 1
    report = json.loads((live_setup.run / "result.json").read_text())
    assert report["status"] == "blocked"
    assert report["blocked_or_failed_stage"].startswith("isolated candidate installation")
    assert report["errors"][0].startswith("OSError: isolated candidate installation")
    assert sensitive_text not in (live_setup.run / "result.json").read_text()
    assert sensitive_text not in (live_setup.run / "result.html").read_text()


def test_live_module_entrypoint(live_setup, monkeypatch):
    import runpy
    import sys
    fake_live(monkeypatch, live_setup, "success")
    monkeypatch.setattr(sys, "argv", ["e2e", *live_setup.args, "--allow-live", "--allow-destructive"])
    with pytest.raises(SystemExit) as error:
        runpy.run_module("secretsync_bdd.e2e", run_name="__main__")
    assert error.value.code == 0
