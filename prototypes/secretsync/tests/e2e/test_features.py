"""Only @e2e scenarios are selected by the dedicated live runner."""

from copy import deepcopy
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when
import yaml

from live_world import OWNER_TAG, SPC, SYNC, require


scenarios(str(Path(__file__).resolve().parents[4] / "features" / "secretsync"))


def g(pattern):
    return given(parsers.re(pattern))


def w(pattern):
    return when(parsers.re(pattern))


def t(pattern):
    return then(parsers.re(pattern))


def entries(spc):
    raw = spc["properties"].get("objects")
    return [yaml.safe_load(e) if isinstance(e, str) else e for e in yaml.safe_load(raw)["array"]] if raw else []


def mapping(live, pairs):
    return [{"sourcePath": live.name(source), "targetKey": target} for source, target in pairs]


@g(r'instance "aio" exists in resource group "rg"(?: on custom location "cl-a")?')
@g(r'its connected cluster and namespace are discoverable')
@g(r'Key Vault "vault-a" and user-assigned managed identity "sync-mi" exist')
@g(r'the cluster has OIDC issuer and workload identity enabled with a usable issuer URL')
def prerequisites(live):
    props = live.cluster["properties"]
    require(props.get("oidcIssuerProfile", {}).get("enabled")
            and props.get("securityProfile", {}).get("workloadIdentity", {}).get("enabled"),
            "The cluster requires OIDC issuer and workload identity.")


@g(r'the instance has no associated sync resources')
def no_resources(live):
    require(not live.resources(), "The dedicated instance unexpectedly has sync resources.")


@g(r'its defaultSecretProviderClassRef resolves to (?:readable )?SPC "spc-a"(?: for Key Vault "vault-a")?')
@g(r'the SPC has valid connection properties and an empty or valid objects YAML array')
def default_spc(live):
    live.ensure_enabled()
    require(live.spc_body()["properties"]["keyvaultName"] == live.vault_name, "The default SPC vault differs.")


@g(r'(?:a )?run-owned Key Vault secret "(?P<source>[^"]+)" (?:already )?exists')
def source(live, source):
    live.source(source)


@g(r'run-owned Key Vault secrets "(?P<first>[^"]+)" and "(?P<second>[^"]+)" already exist')
def sources(live, first, second):
    live.source(first)
    live.source(second)


@g(r'SecretSync "creds" does not exist')
def no_creds(live):
    require(not any(r["name"] == live.name("creds") for r in live.resources()), "The owned SecretSync already exists.")


@g(r'SecretSync "tls" already maps "kv-cert" to "certificate" through "spc-a"')
def existing_tls(live):
    live.set([("kv-cert", "certificate")], "tls")
    live.old_sync = deepcopy(live.result)
    live.old_spc = live.spc_body()


@g(r'Kubernetes secret "tls" has reconciled the existing mapping')
def tls_reconciled(live):
    live.expect_data("tls", {"certificate": "kv-cert"})


@g(r'SecretSync "creds" belongs to "cl-a" and references SPC "spc-a"')
def owned_creds(live):
    live.set([("kv-pass", "password")])


@g(r'SPC "spc-a" has valid objects YAML containing the mapped sources')
def mapped_spc(live):
    require(any(e["objectName"] == live.name("kv-pass") for e in entries(live.spc_body())), "SPC source missing.")


@g(r'run-owned SecretSync "creds" maps "kv-user" to "username" and "kv-pass" to "password"')
def two_mappings(live):
    live.set([("kv-user", "username"), ("kv-pass", "password")])
    live.source_versions = deepcopy(live.versions)
    live.old_spc = live.spc_body()


@g(r'run-owned SecretSync "creds" maps only "kv-pass" to "password"')
def one_mapping(live):
    live.set([("kv-pass", "password")])
    live.old_spc = live.spc_body()
    live.source_versions = deepcopy(live.versions)


@g(r'its Kubernetes secret has reconciled both mappings')
def two_reconciled(live):
    live.expect_data("creds", {"username": "kv-user", "password": "kv-pass"})
    live.config_before = live.config_digest()


@g(r'its Kubernetes secret has reconciled that mapping with standard controller ownership')
def one_reconciled(live):
    live.expect_data("creds", {"password": "kv-pass"})
    value = live.kubectl(["get", "secret", live.name("creds"), "-o", "json"])
    require(any(r["kind"].lower() == "secretsync" for r in value["metadata"].get("ownerReferences", [])),
            "The controller did not establish SecretSync ownership.")


@g(r'no other SecretSync references "kv-pass" through "spc-a"')
def unshared(live):
    rows = [r for r in live.resources() if r["type"].lower() == SYNC.lower()]
    require(all(r["name"] == live.name("creds") for r in rows), "Another consumer makes this fixture unsafe.")


@g(r'another run-owned SecretSync in "cl-a" created through the operations-experience resource contract '
   r'references "kv-pass" through "spc-a"')
def experience_consumer(live):
    body = {"location": live.spc["location"], "extendedLocation": live.spc["extendedLocation"],
            "tags": {OWNER_TAG: live.prefix},
            "properties": {"secretProviderClassName": live.name("spc-a"), "kubernetesSecretType": "Opaque",
                           "serviceAccountName": "aio-ssc-sa", "objectSecretMapping": mapping(live, [("kv-pass", "custom-key")])}}
    live.rest("PUT", live.track(SYNC, "experience"), body)
    live.other_before = live.sync_body("experience")
    live.wait(lambda: any(r["id"].lower() == live.other_before["id"].lower() for r in live.resources()),
              "The other consumer did not become visible to reference discovery.")


@g(r'both Kubernetes secrets have reconciled')
def both_reconciled(live):
    live.expect_data("creds", {"password": "kv-pass"})
    live.expect_data("experience", {"custom-key": "kv-pass"})


@g(r'the instance has a run-owned default SPC and a SecretSync with a reconciled Kubernetes secret')
def list_setup(live):
    live.set([("kv-pass", "password")])
    live.expect_data("creds", {"password": "kv-pass"})
    live.config_before = live.config_digest()


@g(r'the entire instance and its sync configuration are dedicated to this test run')
def dedicated(live):
    require(live.instance["tags"][OWNER_TAG] == live.profile["ownership_token"], "Dedicated ownership mismatch.")


@g(r'it has a default SPC, an additional SPC, and SecretSyncs created by more than one client')
def disable_setup(live):
    live.set([("kv-pass", "password")])
    body = deepcopy(live.spc)
    for key in ("id", "name", "type", "systemData"):
        body.pop(key, None)
    live.rest("PUT", live.track(SPC, "additional"), body)
    body = {"location": live.spc["location"], "extendedLocation": live.spc["extendedLocation"],
            "properties": {"secretProviderClassName": live.name("additional"), "kubernetesSecretType": "Opaque",
                           "serviceAccountName": "aio-ssc-sa", "objectSecretMapping": mapping(live, [("kv-pass", "password")])}}
    live.rest("PUT", live.track(SYNC, "experience"), body)
    live.owned_ids = list(live.created)


@g(r'a separate custom location has unrelated sync resources')
def unrelated(live):
    rows = live.graph("Resources | where type in~ ('" + SPC + "', '" + SYNC + "') "
                      "| where extendedLocation.name !~ '" + live.location_id.replace("'", "''") + "'")
    require(bool(rows), "SS-031 requires predeployed unrelated sync resources in a separate custom location.")
    live.unrelated_before = {r["id"]: live.rest("GET", r["id"]) for r in rows}


@g(r'the vault, managed identity, role assignments, and federation already exist')
def access_snapshot(live):
    live.access_before = [
        live.collection(live.profile["vault_resource_id"] + "/providers/Microsoft.Authorization/roleAssignments",
                        "2022-04-01"),
        live.collection(live.profile["identity_resource_id"] + "/federatedIdentityCredentials", "2023-01-31")]
    live.source_versions = deepcopy(live.versions)


@w(r'I enable secret sync for "aio" using "sync-mi" and "vault-a"')
def enable(live):
    live.ensure_enabled()


@w(r'I set mapping "(?P<source>[^=]+)=(?P<target>[^"]+)" on (?:run-owned )?SecretSync "(?P<name>[^"]+)"')
def set_mapping(live, source, target, name):
    live.set([(source, target)], name)


@w(r'I run "az iot ops secretsync secret set -n aio -g rg --secret-sync-name creds '
   r'--secret-map kv-user=username --secret-map kv-pass=password"')
def create(live):
    live.set([("kv-user", "username"), ("kv-pass", "password")])


@w(r'I run "az iot ops secretsync list -n aio -g rg"')
def list_resources(live):
    live.command("list")


@w(r'I run "az iot ops secretsync secret list -n aio -g rg --secret-sync-name creds"')
def list_mappings(live):
    live.command("secret list", ["--secret-sync-name", live.name("creds")])


@w(r'I run "az iot ops secretsync secret remove -n aio -g rg --secret-sync-name creds --secret-name kv-pass -y"')
@w(r'I remove source "kv-pass" from SecretSync "creds" with "--yes"')
def remove(live):
    live.remove()


@w(r'I run "az iot ops secretsync disable -n aio -g rg --yes"')
def disable(live):
    live.command("disable", ["--yes"])


@t(r'the command exits successfully')
def success(live):
    require(live.rc == 0, "Candidate command did not succeed.")


@t(r'the command returns the created default SPC')
def spc_result(live):
    require(live.result["id"].lower() == live.track(SPC, "spc-a").lower(), "Candidate returned the wrong SPC.")


@t(r'the SPC uses the identity client ID, vault name, tenant ID, cluster region, and instance custom location')
def spc_fields(live):
    p = live.spc["properties"]
    require(p["clientId"] == live.identity["properties"]["clientId"] and p["keyvaultName"] == live.vault_name
            and p["tenantId"] == live.identity["properties"]["tenantId"]
            and live.spc["location"] == live.cluster["location"]
            and live.spc["extendedLocation"] == live.instance["extendedLocation"], "SPC connection fields differ.")


@t(r'the managed identity has Key Vault Reader and Key Vault Secrets User at the vault scope')
def roles(live):
    assigned = live.collection(live.profile["vault_resource_id"] + "/providers/Microsoft.Authorization/roleAssignments",
                               "2022-04-01")
    actual = {v["properties"]["roleDefinitionId"].rsplit("/", 1)[-1] for v in assigned
              if v["properties"]["principalId"] == live.identity["properties"]["principalId"]}
    require({"21090545-7ca7-4776-b22c-e363652d74d2", "4633458b-17de-408a-b874-0445c86b69e6"} <= actual,
            "The identity lacks the required vault-scope roles.")


@t(r'a federated credential allows the SecretSync service account in the instance namespace')
@t(r'its audience is "api://AzureADTokenExchange"')
def federation(live):
    rows = live.collection(live.profile["identity_resource_id"] + "/federatedIdentityCredentials", "2023-01-31")
    subject = f"system:serviceaccount:{live.profile['namespace']}:aio-ssc-sa"
    require(any(r["properties"]["subject"] == subject
                and r["properties"]["audiences"] == ["api://AzureADTokenExchange"] for r in rows),
            "SecretSync federation subject/audience missing.")


@t(r'the instance defaultSecretProviderClassRef identifies the created SPC')
def default_reference(live):
    instance = live.rest("GET", live.instance_id, version="2026-10-01")
    require(instance["properties"]["defaultSecretProviderClassRef"]["resourceId"].lower()
            == live.spc["id"].lower(), "Instance default SPC reference differs.")


@t(r'within the reconciliation timeout Kubernetes secret "(?P<name>[^"]+)" contains the Key Vault bytes under "(?P<key>[^"]+)"')
def probe(live, name, key):
    live.expect_data(name, {key: "probe"})


@t(r'(?:the secret bytes are not included in command output or test diagnostics|'
   r'command output contains mappings but not secret values|'
   r'output contains configuration and secret references but not Key Vault secret values|no secret values appear in output)')
def values_redacted(live):
    live.assert_no_values()


@t(r'both source secrets are verified before the first SPC or SecretSync write')
def sources_verified(live):
    reads = [i for i, (method, path) in enumerate(live.command_events) if method == "GET" and "/secrets/" in path]
    writes = [i for i, (method, _) in enumerate(live.command_events) if method == "PUT"]
    require(len(reads) == 2 and bool(writes) and max(reads) < min(writes),
            "Source verification did not precede the configuration writes.")


@t(r'the SPC contains each source exactly once as objectType "secret"')
def spc_objects(live):
    actual = entries(live.spc_body())
    require(sorted(e["objectName"] for e in actual) == sorted([live.name("kv-user"), live.name("kv-pass")])
            and all(e["objectType"] == "secret" for e in actual), "SPC source entries differ.")


@t(r'the command returns SecretSync "creds" with both sourcePath and targetKey mappings')
def result_mappings(live):
    require(live.result["properties"]["objectSecretMapping"] == mapping(
        live, [("kv-user", "username"), ("kv-pass", "password")]), "Returned mappings differ.")


@t(r'its secretProviderClassName is "spc-a"')
@t(r'its kubernetesSecretType is "Opaque"')
@t(r'it uses the SecretSync controller service account and the SPC location and extendedLocation')
def sync_fields(live):
    p = live.result["properties"]
    require(p["secretProviderClassName"] == live.name("spc-a") and p["kubernetesSecretType"] == "Opaque"
            and p["serviceAccountName"] == "aio-ssc-sa" and live.result["location"] == live.spc["location"]
            and live.result["extendedLocation"] == live.spc["extendedLocation"], "SecretSync fields differ.")


@t(r'within the reconciliation timeout Kubernetes secret "creds" contains the source bytes under "username" and "password"')
def userpass(live):
    live.expect_data("creds", {"username": "kv-user", "password": "kv-pass"})


@t(r'the returned SecretSync contains both mappings')
def additive(live):
    require(live.result["properties"]["objectSecretMapping"] == mapping(
        live, [("kv-cert", "certificate"), ("kv-key", "privateKey")]), "Additive mappings differ.")


@t(r'existing SecretSync properties unrelated to mappings are preserved')
def other_properties(live):
    require({k: v for k, v in live.result["properties"].items() if k != "objectSecretMapping"} ==
            {k: v for k, v in live.old_sync["properties"].items() if k != "objectSecretMapping"},
            "Unrelated SecretSync properties changed.")


@t(r'existing SPC connection properties and unrelated object entries are preserved')
def other_objects(live):
    actual = live.spc_body()
    require(all(e in entries(actual) for e in entries(live.old_spc)), "Existing SPC objects changed.")
    require({k: v for k, v in actual["properties"].items() if k not in ("objects", "provisioningState")} ==
            {k: v for k, v in live.old_spc["properties"].items() if k not in ("objects", "provisioningState")},
            "Existing SPC connection properties changed.")


@t(r'within the reconciliation timeout Kubernetes secret "tls" contains both original and newly mapped bytes')
def tls(live):
    live.expect_data("tls", {"certificate": "kv-cert", "privateKey": "kv-key"})


@t(r'the result contains both "kv-cert=certificate" and "kv-cert=cert-copy"')
def multikey(live):
    require(live.result["properties"]["objectSecretMapping"] == mapping(
        live, [("kv-cert", "certificate"), ("kv-cert", "cert-copy")]), "Multikey mappings differ.")


@t(r'the SPC still contains only one object entry for "kv-cert"')
def one_object(live):
    require(sum(e["objectName"] == live.name("kv-cert") for e in entries(live.spc_body())) == 1, "Duplicate SPC source.")


@t(r'within the reconciliation timeout both Kubernetes keys contain the same expected source bytes')
def multikey_bytes(live):
    live.expect_data("tls", {"certificate": "kv-cert", "cert-copy": "kv-cert"})


@t(r'the output contains the associated SPC and SecretSync ARM resources with their properties')
@t(r'each resource identifies custom location "cl-a"')
def resource_list(live):
    require({r["id"].lower() for r in live.result} == {live.track(SPC, "spc-a").lower(), live.track(SYNC, "creds").lower()}
            and all(r["extendedLocation"]["name"].lower() == live.location_id.lower() and r["properties"]
                    for r in live.result), "Associated configuration output differs.")


@t(r'no Azure or Kubernetes resource is changed')
def configuration_unchanged(live):
    require(live.config_digest() == live.config_before, "The read-only command changed configuration.")
    live.expect_data("creds", {"password": "kv-pass"})


@t(r'the result is the two sourcePath and targetKey objects from SecretSync "creds"')
@t(r'no other SecretSync\'s mappings are included')
def mapping_list(live):
    require(live.result == mapping(live, [("kv-user", "username"), ("kv-pass", "password")]), "Mapping list differs.")


@t(r'no Key Vault data-plane read or resource mutation is performed by the list command')
def list_unchanged(live):
    events = list(live.command_events)
    require(live.config_digest() == live.config_before, "Mapping list changed configuration.")
    require(bool(events) and all(method not in ("PUT", "DELETE") and "/secrets/" not in path
                                 for method, path in events), "Mapping list accessed values or mutated configuration.")


@t(r'the command returns the updated SecretSync with only "kv-user=username"')
def retained_mapping(live):
    require(live.result["properties"]["objectSecretMapping"] == mapping(live, [("kv-user", "username")]),
            "Remaining mapping differs.")


@t(r'the SPC no longer includes "kv-pass" but retains "kv-user" and unrelated object settings')
def retained_objects(live):
    require(entries(live.spc_body()) == [e for e in entries(live.old_spc) if e["objectName"] != live.name("kv-pass")],
            "SPC cleanup changed unrelated objects.")


@t(r'within the reconciliation timeout Kubernetes secret "creds" retains "username" '
   r'with its original bytes and has no "password" key')
def retained_bytes(live):
    live.expect_data("creds", {"username": "kv-user"})


@t(r'Key Vault secret "kv-pass" and all its versions remain unchanged')
@t(r'the Key Vault source secret still exists')
def source_intact(live):
    actual = live.az(["keyvault", "secret", "list-versions", "--vault-name", live.vault_name,
                      "--name", live.name("kv-pass")], "Check source versions")
    require(actual == live.source_versions["kv-pass"], "Source versions changed.")


@t(r'the SecretSync ARM resource is deleted instead of written with an empty mapping array')
@t(r'SecretSync "creds" is deleted')
def target_absent(live):
    live.wait(lambda: all(r["id"].lower() != live.track(SYNC, "creds").lower() for r in live.resources()),
              "Deleted SecretSync remained discoverable.")


@t(r'the command exits successfully without a resource result body')
def no_result(live):
    require(live.rc == 0 and live.result is None, "Deletion returned an unexpected resource body.")


@t(r'the final "kv-pass" object is removed from the SPC')
@t(r'an SPC with no remaining objects has no objects property')
def empty_objects(live):
    require("objects" not in live.spc_body()["properties"], "Empty SPC retained an objects property.")


@t(r'within the reconciliation timeout the SecretSync custom resource and its controller-owned Kubernetes secret are absent')
def runtime_absent(live):
    def absent():
        sync = live.kubectl(["get", "secretsync", live.name("creds"), "--ignore-not-found", "-o", "json"])
        return sync is None and live.data("creds") is None
    live.wait(absent, "The deleted sync or its controller-owned Kubernetes secret remained.")


@t(r'the shared SPC object for "kv-pass" remains unchanged')
def shared_object(live):
    require(entries(live.spc_body()) == entries(live.old_spc), "Shared SPC object changed.")


@t(r'the other SecretSync and its Kubernetes secret remain functional')
def shared_functional(live):
    require(live.sync_body("experience")["properties"] == live.other_before["properties"],
            "The other consumer configuration changed.")
    live.expect_data("experience", {"custom-key": "kv-pass"})


@t(r'a new run-owned Key Vault version subsequently reconciles to the other consumer within the rotation timeout')
def rotate(live):
    live.source("kv-pass", rotate=True)
    live.expect_data("experience", {"custom-key": "kv-pass"})


@t(r'no operations-experience UI automation or mocked service is required for this resource-level compatibility check')
def real_resource(live):
    require(live.other_before["id"].lower() == live.track(SYNC, "experience").lower(),
            "The operations-experience contract resource is missing.")


@t(r'the instance no longer has a defaultSecretProviderClassRef')
def disabled_reference(live):
    require(not live.rest("GET", live.instance_id, version="2026-10-01")["properties"].get("defaultSecretProviderClassRef"),
            "Disable left the instance default SPC reference.")


@t(r'all SecretSyncs in "cl-a" are deleted before any SPC in "cl-a" is deleted')
@t(r'all SPCs in "cl-a" are deleted')
def disabled_resources(live):
    if not hasattr(live, "disable_events"):
        live.disable_events = list(live.command_events)
    deletes = [path for method, path in live.disable_events if method == "DELETE"]
    first_spc = next((i for i, path in enumerate(deletes) if "/azureKeyVaultSecretProviderClasses/" in path), len(deletes))
    require(bool(deletes) and not any("/secretSyncs/" in path for path in deletes[first_spc:]),
            "SecretSync deletions did not precede SPC deletions.")
    live.wait(lambda: not live.resources(), "Disable left associated sync resources.")


@t(r'unrelated custom-location resources remain unchanged')
def unrelated_unchanged(live):
    for resource, original in live.unrelated_before.items():
        require(live.rest("GET", resource) == original, "Disable changed an unrelated custom-location resource.")


@t(r'Key Vault secrets, the vault, managed identity, role assignments, and federation remain')
def dependencies_remain(live):
    require(live.rest("GET", live.profile["vault_resource_id"], version="2023-07-01") == live.vault,
            "Vault configuration changed.")
    require(live.rest("GET", live.profile["identity_resource_id"], version="2023-01-31") == live.identity,
            "Identity configuration changed.")
    after = [
        live.collection(live.profile["vault_resource_id"] + "/providers/Microsoft.Authorization/roleAssignments",
                        "2022-04-01"),
        live.collection(live.profile["identity_resource_id"] + "/federatedIdentityCredentials", "2023-01-31")]
    require(after == live.access_before, "Disable changed role assignments or federation.")
    source_intact(live)


@t(r'within the reconciliation timeout the run-owned sync custom resources are absent from Kubernetes')
def disabled_runtime(live):
    def absent():
        for kind, logical in (("secretsync", "creds"), ("secretsync", "experience"),
                              ("secretproviderclass", "spc-a"), ("secretproviderclass", "additional")):
            if live.kubectl(["get", kind, live.name(logical), "--ignore-not-found", "-o", "json"]) is not None:
                return False
        return True
    live.wait(absent, "Disable left run-owned Kubernetes custom resources.")
