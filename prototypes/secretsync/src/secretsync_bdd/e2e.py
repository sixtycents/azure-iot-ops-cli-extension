"""Run the actual isolated wheel; DEV/PPE opt-ins are mandatory. Never supplies fake service behavior."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from zipfile import BadZipFile, ZipFile
from email.parser import Parser

from . import runner


PROFILE_FIELDS = {
    "environment_type", "subscription_id", "resource_group", "instance_name", "vault_resource_id",
    "identity_resource_id", "cluster_name", "cluster_resource_group", "namespace", "ownership_token",
    "dedicated_instance",
}


def load_profile(path: Path, name: str) -> dict:
    profiles = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(profiles, dict):
        raise ValueError("Profiles must be a JSON object keyed by environment name")
    profile = profiles.get(name)
    if not isinstance(profile, dict) or set(profile) - (PROFILE_FIELDS | {"timeout_seconds"}):
        raise ValueError("Unknown environment or unsupported profile fields (credentials forbidden)")
    if PROFILE_FIELDS - set(profile):
        raise ValueError("Missing required profile fields")
    if profile["environment_type"] not in ("dev", "ppe") or profile["dedicated_instance"] is not True:
        raise ValueError("Require DEV/PPE and dedicated_instance=true")
    if any(not isinstance(profile[k], str) or not profile[k].strip()
           for k in PROFILE_FIELDS - {"dedicated_instance"}):
        raise ValueError("Profile identifiers must be nonempty strings")
    timeout = profile.get("timeout_seconds", 1800)
    if type(timeout) is not int or not 1 <= timeout <= 7200:
        raise ValueError("timeout_seconds must be an integer in 1..7200")
    return profile


def candidate_metadata(wheel: Path) -> dict:
    if wheel.suffix != ".whl":
        raise ValueError("Candidate must be a wheel")
    with ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise ValueError("Candidate must have exactly one package metadata record")
        metadata = Parser().parsestr(archive.read(names[0]).decode("utf-8"))
    if metadata["Name"] != "iot-secretsync-prototype" or not metadata["Version"]:
        raise ValueError("Unexpected candidate extension name or missing version")
    return {"name": metadata["Name"], "version": metadata["Version"],
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--allow-destructive", action="store_true")
    args = parser.parse_args(argv)
    run = runner.new_run(args.output)
    report = {"mode": "e2e", "specification_baseline": "0.1.0",
              "status": "blocked", "errors": [], "cases": None,
              "gates": {"tests": "not_run", "completeness": "not_run"}}
    stage = "revision metadata"
    try:
        report.update(runner.metadata())
        stage = "live/destructive authorization"
        if not (args.allow_live and args.allow_destructive):
            raise ValueError("Both --allow-live and --allow-destructive are required")
        stage = "named DEV/PPE profile validation"
        profile = load_profile(args.profiles, args.environment)
        stage = "candidate wheel metadata validation"
        candidate = candidate_metadata(args.candidate)
        report.update(candidate=candidate, environment=args.environment,
                      target={k: profile[k] for k in ("environment_type", "subscription_id",
                                                      "resource_group", "instance_name", "namespace")})
        env = runner.environment(run)
        extensions = run / "extensions"
        extensions.mkdir()
        env["AZURE_EXTENSION_DIR"] = str(extensions)
        env["AZURE_EXTENSION_USE_DYNAMIC_INSTALL"] = "no"
        timeout = profile.get("timeout_seconds", 1800)
        stage = "isolated candidate installation; check Azure CLI and wheel dependencies"
        installed = runner.execute(["-m", "azure.cli", "extension", "add", "--source",
                                    str(args.candidate.resolve()), "--yes"], env, timeout)
        if installed.returncode:
            raise ValueError("Candidate installation failed")
        stage = "installed candidate name/version/path verification"
        verification = runner.execute(["-m", "azure.cli", "extension", "show", "--name",
                                       candidate["name"], "--output", "json"], env, timeout)
        if verification.returncode:
            raise ValueError("Candidate verification failed")
        actual = json.loads(verification.stdout)
        expected = extensions / candidate["name"]
        if (not isinstance(actual, dict) or actual.get("name") != candidate["name"]
                or actual.get("version") != candidate["version"] or not isinstance(actual.get("path"), str)
                or Path(actual["path"]).resolve() != expected.resolve() or not expected.is_dir()):
            raise ValueError("Installed candidate identity/path mismatch")
        report["installed_path"] = str(expected)
        env["SECRETSYNC_E2E_PROFILE"] = json.dumps(profile)
        env["SECRETSYNC_E2E_CANDIDATE_DIR"] = str(expected)
        report["status"] = "failed"
        stage = "live pytest execution; check live prerequisites and bindings"
        result = runner.execute(runner.pytest_arguments(run, live=True), env, timeout)
        report["pytest_exit_code"] = result.returncode
        report["gates"]["tests"] = "passed" if result.returncode == 0 else "failed"
        report["gates"]["completeness"] = "failed"
        stage = "E2E inventory reconciliation; inspect cases.json"
        report["cases"] = json.loads((run / "cases.json").read_text(encoding="utf-8"))
        report["cases"] = runner.completeness(run)
        report["gates"]["completeness"] = "passed"
        stage = "JUnit artifact validation"
        if not (run / "junit.xml").is_file():
            raise ValueError("Missing JUnit artifact")
        report["tests"] = runner.junit_results(run)
        if result.returncode == 0:
            report["status"] = "passed"
    except (OSError, ValueError, KeyError, BadZipFile, subprocess.SubprocessError) as error:
        # Do not echo subprocess output or caller-supplied profile values (possibly secret).
        error_type = type(error).__name__
        report["blocked_or_failed_stage"] = stage
        report["errors"].append(f"{error_type}: {stage} failed (raw live diagnostics redacted)")
    return runner.finish(run, report)


if __name__ == "__main__":
    raise SystemExit(main())
