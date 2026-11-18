"""Real candidate CLI, Azure and Kubernetes operations. Never imported by offline tests."""

import base64
from copy import deepcopy
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import uuid

import pytest


SPC = "Microsoft.SecretSyncController/azureKeyVaultSecretProviderClasses"
SYNC = "Microsoft.SecretSyncController/secretSyncs"
OWNER_TAG = "secretsync-bdd-owner"
VERSION = "2024-08-21-preview"


def require(condition, message):
    if not condition:
        pytest.fail(message, pytrace=False)


class LiveError(RuntimeError):
    """Deliberately omits subprocess arguments, stdout and stderr."""


class LiveWorld:
    def __init__(self, tmp_path):
        self.profile = json.loads(os.environ["SECRETSYNC_E2E_PROFILE"])
        self.tmp = tmp_path
        self.prefix = "bdd-" + uuid.uuid4().hex[:12]
        self.names = {}
        self.values = {}
        self.versions = {}
        self.created = []
        self.access_created = []
        self.output = ""
        self.result = None
        self.rc = None
        self.http_events = []
        self.timeout = self.profile.get("timeout_seconds", 1800)
        p = self.profile
        self.instance_id = (f"/subscriptions/{p['subscription_id']}/resourceGroups/{p['resource_group']}"
                            f"/providers/Microsoft.IoTOperations/instances/{p['instance_name']}")
        self.instance = self.rest("GET", self.instance_id, version="2026-10-01")
        require(self.instance.get("tags", {}).get(OWNER_TAG) == p["ownership_token"],
                "Instance ownership tag does not match the selected environment.")
        require(p["dedicated_instance"] is True, "A dedicated instance is required.")
        self.location_id = self.instance["extendedLocation"]["name"]
        location = self.rest("GET", self.location_id, version="2021-08-15")
        expected_cluster = (f"/subscriptions/{p['subscription_id']}/resourceGroups/{p['cluster_resource_group']}"
                            f"/providers/Microsoft.Kubernetes/connectedClusters/{p['cluster_name']}")
        require(location["properties"]["hostResourceId"].lower() == expected_cluster.lower()
                and location["properties"]["namespace"] == p["namespace"],
                "The profile cluster/namespace does not match the instance custom location.")
        self.cluster = self.rest("GET", expected_cluster, version="2024-01-01")
        self.identity = self.rest("GET", p["identity_resource_id"], version="2023-01-31")
        self.vault = self.rest("GET", p["vault_resource_id"], version="2023-07-01")
        self.vault_name = p["vault_resource_id"].rsplit("/", 1)[-1]
        self.vault_uri = self.vault["properties"]["vaultUri"]
        self.kubectl(["get", "namespace", p["namespace"], "-o", "json"])
        require(not self.resources(), "The dedicated instance must start with no associated sync resources.")
        require(not self.instance["properties"].get("defaultSecretProviderClassRef"),
                "The dedicated instance must start without a default SPC reference.")
        self.initial_roles = self.collection(
            p["vault_resource_id"] + "/providers/Microsoft.Authorization/roleAssignments", "2022-04-01")
        self.initial_federation = self.collection(p["identity_resource_id"] + "/federatedIdentityCredentials",
                                                  "2023-01-31")

    def name(self, logical):
        return self.names.setdefault(logical, self.prefix + "-" + logical)

    def execute(self, args, label, executable=None, json_output=True):
        try:
            env = dict(os.environ)
            env["PYTHONPATH"] = os.environ["SECRETSYNC_E2E_CANDIDATE_DIR"]
            completed = subprocess.run(
                [*(executable or [sys.executable, "-m", "azure.cli"]), *args],
                capture_output=True, text=True, timeout=self.timeout, check=False, env=env,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise LiveError(f"{label}: process failed ({type(error).__name__}).") from None
        self.http_events = re.findall(r"SecretSync HTTP (GET|POST|PUT|DELETE) (\S+)", completed.stderr)
        if label == "Candidate SecretSync command" and not completed.returncode:
            paths = re.findall(r"SecretSync implementation ([^\r\n]+)", completed.stderr)
            expected = Path(os.environ["SECRETSYNC_E2E_CANDIDATE_DIR"]) / "azext_secretsync_prototype" / "api.py"
            require(bool(paths) and all(Path(p).resolve() == expected.resolve() for p in paths),
                    "The invoked command did not load the installed candidate implementation.")
        if completed.returncode:
            raise LiveError(
                f"{label}: exited with code {completed.returncode}; inspect the target environment.") from None
        if not json_output:
            return completed.stdout
        try:
            return json.loads(completed.stdout) if completed.stdout.strip() else None
        except ValueError:
            raise LiveError(f"{label}: invalid JSON response.") from None

    def az(self, args, label, json_output=True):
        quiet = [] if "--debug" in args else ["--only-show-errors"]
        return self.execute([*args, "--subscription", self.profile["subscription_id"], *quiet,
                             "--output", "json" if json_output else "none"], label, json_output=json_output)

    def rest(self, method, resource, body=None, version=VERSION):
        # az rest resolves relative ARM URLs against the active cloud.
        args = ["rest", "--method", method, "--url", resource]
        if "api-version=" not in resource:
            args += ["--url-parameters", "api-version=" + version]
        if body is not None:
            path = self.tmp / ("resource-" + uuid.uuid4().hex + ".json")
            path.write_text(json.dumps(body), encoding="utf-8")
            args += ["--body", "@" + str(path)]
        result = self.az(args, "Azure " + method + " configuration")
        if method in ("PUT", "DELETE") and "/providers/microsoft.secretsynccontroller/" in resource.lower():
            self.az(["resource", "wait", "--ids", resource, "--api-version", version,
                     "--deleted" if method == "DELETE" else "--created", "--interval", "5",
                     "--timeout", str(self.timeout)], "Wait for owned ARM resource", json_output=False)
        return result

    def collection(self, resource, version):
        values = []
        while resource:
            page = self.rest("GET", resource, version=version)
            values.extend(page["value"])
            resource = page.get("nextLink")
        return values

    def graph(self, query):
        rows, options = [], {}
        while True:
            page = self.rest("POST", "/providers/Microsoft.ResourceGraph/resources",
                             {"subscriptions": [self.profile["subscription_id"]], "query": query, "options": options},
                             version="2022-10-01")
            rows.extend(page["data"])
            if not page.get("$skipToken"):
                return rows
            options["$skipToken"] = page["$skipToken"]

    def resources(self):
        return self.graph(
            "Resources | where type in~ ('" + SPC + "', '" + SYNC + "') "
            "| where extendedLocation.name =~ '" + self.location_id.replace("'", "''") + "'")

    def kubectl(self, args):
        return self.execute(["--context", self.profile["cluster_name"],
                             "--namespace", self.profile["namespace"], *args],
                            "Kubernetes request", executable=["kubectl"])

    def wait(self, check, message):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(5)
        require(False, message)

    def command(self, command, options=()):
        args = ["iot", "ops", "secretsync", *command.split(), "-n", self.profile["instance_name"],
                "-g", self.profile["resource_group"], *options]
        # Capture output only in memory; no raw live subprocess log artifacts.
        result = self.az([*args, "--debug"], "Candidate SecretSync command")
        self.command_events = list(self.http_events)
        self.result, self.rc = result, 0
        self.output = json.dumps(result)
        return result

    def track(self, kind, logical):
        name = self.name(logical)
        base = self.instance_id.split("/providers/")[0]
        resource = f"{base}/providers/{kind}/{name}"
        if resource not in self.created:
            self.created.append(resource)
        return resource

    def ensure_enabled(self):
        if hasattr(self, "spc"):
            return
        self.track(SPC, "spc-a")
        try:
            self.spc = self.command(
                "enable", ["--mi-user-assigned", self.profile["identity_resource_id"],
                           "--kv-resource-id", self.profile["vault_resource_id"],
                           "--spc", self.name("spc-a"), "--tags", OWNER_TAG + "=" + self.prefix])
        finally:
            self.access_attempts = {path.lower() for method, path in self.http_events if method == "PUT"}
            self.remember_access()

    def remember_access(self):
        for resource, version, original in (
            (self.profile["vault_resource_id"] + "/providers/Microsoft.Authorization/roleAssignments",
             "2022-04-01", self.initial_roles),
            (self.profile["identity_resource_id"] + "/federatedIdentityCredentials",
             "2023-01-31", self.initial_federation),
        ):
            known = {r["id"].lower() for r in original}
            for value in self.collection(resource, version):
                if (value["id"].lower() not in known and value["id"].lower() in self.access_attempts
                        and (value["id"], version) not in self.access_created):
                    props = value["properties"]
                    owned = (props.get("principalId") == self.identity["properties"]["principalId"]
                             if "/roleAssignments/" in value["id"]
                             else props.get("subject")
                             == f"system:serviceaccount:{self.profile['namespace']}:aio-ssc-sa")
                    require(owned, "An unexpected access resource appeared; cleanup ownership is uncertain.")
                    self.access_created.append((value["id"], version))

    def source(self, logical, rotate=False):
        name = self.name(logical)
        if logical in self.values and not rotate:
            return
        value = secrets.token_urlsafe(32).encode("ascii")
        path = self.tmp / (name + ".secret")
        path.write_bytes(value)
        # Track before sending: even an ambiguous timeout needs owned-resource cleanup.
        self.values[logical] = value
        try:
            self.az(["keyvault", "secret", "set", "--vault-name", self.vault_name, "--name", name,
                     "--file", str(path)], "Create owned source secret", json_output=False)
        finally:
            path.unlink()
        self.versions[logical] = self.az(
            ["keyvault", "secret", "list-versions", "--vault-name", self.vault_name, "--name", name],
            "Read owned source version metadata")

    def set(self, mappings, logical="creds"):
        self.ensure_enabled()
        self.track(SYNC, logical)
        options = ["--secret-sync-name", self.name(logical)]
        for source, target in mappings:
            self.source(source)
            options += ["--secret-map", self.name(source) + "=" + target]
        return self.command("secret set", options)

    def remove(self):
        return self.command("secret remove", ["--secret-sync-name", self.name("creds"),
                                              "--secret-name", self.name("kv-pass"), "--yes"])

    def sync_body(self, logical="creds"):
        return self.rest("GET", self.track(SYNC, logical))

    def spc_body(self):
        return self.rest("GET", self.track(SPC, "spc-a"))

    def data(self, logical):
        page = self.kubectl(["get", "secret", self.name(logical), "--ignore-not-found", "-o", "json"])
        return None if page is None else {key: base64.b64decode(value) for key, value in page.get("data", {}).items()}

    def expect_data(self, logical, mapping):
        def matches():
            actual = self.data(logical)
            return (actual is not None and set(actual) == set(mapping)
                    and all(hmac.compare_digest(actual[key], self.values[source]) for key, source in mapping.items()))
        self.wait(matches, "The expected Kubernetes secret data did not reconcile (values redacted).")

    def assert_no_values(self):
        require(not any(value.decode() in self.output for value in self.values.values()),
                "Candidate output disclosed secret data (values redacted).")

    def config_digest(self):
        resources = [self.spc_body(), self.sync_body()]
        return hashlib.sha256(json.dumps(resources, sort_keys=True).encode()).hexdigest()

    def _cleanup_sync_resources(self, errors):
        try:
            base = self.instance_id.split("/providers/")[0]
            existing = {r["id"].lower() for kind in (SYNC, SPC)
                        for r in self.collection(base + "/providers/" + kind, VERSION)}
            for kind in (SYNC, SPC):
                for resource in reversed(self.created):
                    if f"/providers/{kind}/" in resource and resource.lower() in existing:
                        try:
                            self.rest("DELETE", resource)
                        except LiveError:
                            errors.append("An owned sync resource could not be deleted.")
        except LiveError:
            errors.append("Could not inventory owned sync resources for cleanup.")

    def cleanup(self):
        errors = []
        if hasattr(self, "access_attempts"):
            try:
                self.remember_access()
            except LiveError:
                errors.append("Could not verify access resources created during enablement.")
        # Disable may have already deleted resources; query first, never infer a failed GET is absence.
        try:
            instance = self.rest("GET", self.instance_id, version="2026-10-01")
            reference = instance["properties"].get("defaultSecretProviderClassRef", {}).get("resourceId")
            if reference and reference.lower() in {r.lower() for r in self.created}:
                body = deepcopy(instance)
                body["properties"].pop("defaultSecretProviderClassRef")
                self.rest("PUT", self.instance_id, body, version="2026-10-01")
            elif reference:
                errors.append("Unexpected default reference; not modified.")
        except LiveError:
            errors.append("Could not verify or clear the owned default reference.")
        self._cleanup_sync_resources(errors)
        for logical in self.values:
            try:
                self.az(["keyvault", "secret", "delete", "--vault-name", self.vault_name, "--name", self.name(logical)],
                        "Delete owned source secret", json_output=False)
            except LiveError:
                errors.append("An owned source secret could not be deleted.")
        for resource, version in reversed(self.access_created):
            try:
                self.rest("DELETE", resource, version=version)
            except LiveError:
                errors.append("An owned access resource could not be deleted.")
        require(not errors, "Live cleanup failed: " + " ".join(errors))
