"""Semantic bindings: real CLI parsing and HTTP serialization; fake external services only."""

import json
import re
from copy import deepcopy

import requests
import yaml
from pytest_bdd import given, parsers, then, when

from azext_secretsync_prototype.service import AUDIENCE, ROLES, SERVICE_ACCOUNT, objects, store_objects
from azure_boundary import CLIENT, ISSUER, PRINCIPAL, SPC_TYPE, SUB, SUB_B, SYNC_TYPE, TENANT, rid


VAULT = rid("Microsoft.KeyVault/vaults", "vault-a")
IDENTITY = rid("Microsoft.ManagedIdentity/userAssignedIdentities", "sync-mi")
CUSTOM_ROLE = f"/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/custom"


def g(pattern):
    return given(parsers.re(pattern))


def w(pattern):
    return when(parsers.re(pattern))


def t(pattern):
    return then(parsers.re(pattern))


def sync(world, name="creds", pairs=(("kv-pass", "password"),), **kwargs):
    value = world.azure.sync(name, pairs, **kwargs)
    if kwargs.get("spc", "spc-a") == "spc-a":
        names = {entry["objectName"] for entry in objects(world.azure.spc_resource)}
        entries = objects(world.azure.spc_resource)
        entries.extend({"objectName": source, "objectType": "secret"} for source, _ in pairs if source not in names)
        store_objects(world.azure.spc_resource, entries)
    return value


def clear_sync(world):
    for key in [key for key, value in world.azure.resources.items()
                if value["type"].lower() in (SPC_TYPE, SYNC_TYPE)]:
        del world.azure.resources[key]


def enable(world, options=""):
    return world.invoke("enable", f"--mi-user-assigned {IDENTITY} --kv-resource-id {VAULT} {options}")


def snapshot(world):
    world.before = deepcopy(world.azure.resources)
    world.source_before = deepcopy(world.azure.secrets)


@g(r'instance "aio" exists in resource group "rg"(?: on custom location "cl-a")?')
@g(r'instance "aio" uses custom location "cl-a" and default SPC "spc-a"')
@g(r'its defaultSecretProviderClassRef resolves to (?:readable )?SPC "spc-a"(?: for Key Vault "vault-a")?')
@g(r'the default SPC(?: and requested Key Vault sources exist| and Key Vault secret "kv-pass" exist|'
   r' and source Key Vault secret exist| is readable)')
@g(r'the SPC has valid connection properties and an empty or valid objects YAML array')
def valid_base(world):
    assert world.azure.instance["extendedLocation"]["name"].endswith("/cl-a")
    assert world.azure.spc_resource["properties"]["keyvaultName"] == "vault-a"
    assert isinstance(objects(world.azure.spc_resource), list)


@g(r'its connected cluster and namespace are discoverable')
@g(r'Key Vault "vault-a" and user-assigned managed identity "sync-mi" exist')
@g(r'the cluster has OIDC issuer and workload identity enabled with a usable issuer URL')
@g(r'the cluster advertises a usable selfHostedIssuerUrl')
@g(r'the vault, managed identity, role assignments, and federation already exist')
def connected_dependencies(world):
    assert world.azure.cluster["properties"]["oidcIssuerProfile"]["enabled"]
    assert world.azure.resources[VAULT.lower()]
    assert world.azure.resources[IDENTITY.lower()]


@g(r'(?:the instance|instance "aio") has no associated sync resources')
@g(r'instance "aio" has no SecretSync or SecretProviderClass resources')
@g(r'instance "aio" exists in resource group "rg" with no sync resources')
@g(r'the instance has no (?:default SPC reference and no associated sync resources|associated sync resources)')
def empty_resources(world):
    clear_sync(world)
    world.azure.instance["properties"].pop("defaultSecretProviderClassRef", None)
    snapshot(world)


@g(r'the instance has no defaultSecretProviderClassRef')
def no_default(world):
    world.azure.instance["properties"].pop("defaultSecretProviderClassRef", None)


@g(r'(?:its custom location (?:still has|contains)|the custom location already contains a populated) '
   r'(?:an? SPC and a SecretSync|SPC and SecretSync)')
@g(r'the instance (?:already has sync resources|has associated sync resources and a default SPC reference|'
   r'has a default SPC reference and associated sync resources)')
@g(r'the instance has a run-owned default SPC and a SecretSync with a reconciled Kubernetes secret')
def populated(world):
    sync(world)
    for role in ROLES:
        value = {"id": VAULT + "/providers/Microsoft.Authorization/roleAssignments/" + role,
                 "name": role, "type": "Microsoft.Authorization/roleAssignments",
                 "properties": {"principalId": PRINCIPAL,
                                "roleDefinitionId": (
                                    f"/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/{role}")}}
        world.azure.resources[value["id"].lower()] = value
    snapshot(world)


@g(r'SecretSync "creds" belongs to "cl-a" and references SPC "spc-a"')
def target_exists(world):
    sync(world)


@g(r'SPC "spc-a" has valid objects YAML containing the mapped sources')
def mapped_objects(world):
    assert objects(world.azure.spc_resource)[0]["objectName"] == "kv-pass"


@g(r'(?:run-owned )?Key Vault secrets? "(?P<source>[^"]+)" (?:already )?exists?(?: and all required services are now available)?')
@g(r'a run-owned Key Vault secret "(?P<source>[^"]+)" already exists')
def source_exists(world, source):
    world.azure.secrets.setdefault(source, {"value": "unit-" + source + "-bytes", "tags": {}})


@g(r'run-owned Key Vault secrets "(?P<first>[^"]+)" and "(?P<second>[^"]+)" already exist')
def two_sources(world, first, second):
    source_exists(world, first)
    source_exists(world, second)


@g(r'SecretSync "(?P<name>[^"]+)" does not exist')
def missing_sync(world, name):
    world.azure.resources.pop(rid("Microsoft.SecretSyncController/secretSyncs", name).lower(), None)


@g(r'(?:run-owned )?SecretSync "(?P<name>[^"]+)" (?:already )?maps(?: only)? '
   r'"(?P<source>[^"]+)" to "(?P<key>[^"]+)"(?: through "spc-a")?')
def one_mapping(world, name, source, key):
    sync(world, name, [(source, key)])
    snapshot(world)


@g(r'(?:run-owned )?SecretSync "(?P<name>[^"]+)" maps "(?P<source>[^"]+)" to "(?P<key>[^"]+)" '
   r'and "(?P<second_source>[^"]+)" to "(?P<second_key>[^"]+)"')
def two_mappings(world, name, source, key, second_source, second_key):
    sync(world, name, [(source, key), (second_source, second_key)])
    snapshot(world)


@g(r'SecretSync "(?P<name>[^"]+)" maps "(?P<source>[^"]+)" to (?:both )?"(?P<key>[^"]+)" and "(?P<second>[^"]+)"')
def multikey(world, name, source, key, second):
    sync(world, name, [(source, key), (source, second)])
    snapshot(world)


@g(r'it also maps "kv-user" to "username"')
def append_mapping(world):
    pairs = [(m["sourcePath"], m["targetKey"]) for m in
             world.azure.resources[rid("Microsoft.SecretSyncController/secretSyncs", "creds").lower()]
             ["properties"]["objectSecretMapping"]]
    sync(world, pairs=pairs + [("kv-user", "username")])
    snapshot(world)


@g(r'(?:its Kubernetes secret has reconciled (?:both mappings|that mapping with standard controller ownership)|'
   r'Kubernetes secret "tls" has reconciled the existing mapping|both Kubernetes secrets have reconciled)')
def reconcile(world):
    world.azure.reconcile()
    assert world.azure.kubernetes
    snapshot(world)


@g(r'no other SecretSync references "kv-pass" through "spc-a"')
@g(r'Resource Graph returns "creds" with its pre-removal mapping and no other consumers')
def no_other_consumer(world):
    assert len([r for r in world.azure.resources.values() if r["type"].lower() == SYNC_TYPE]) == 1
    snapshot(world)


@g(r'no Azure credentials or service connections are available')
@g(r'no default resource group is configured')
def offline(world):
    assert not (world.config / "config").exists()
    assert not world.azure.calls


@g(r'resource group "rg" is configured as the Azure CLI default group')
def configured_group(world):
    (world.config / "config").write_text("[defaults]\ngroup = rg\n", encoding="utf-8")


@g(r'all required arguments for "az iot ops secretsync (?P<command>[^"]+)" are supplied except "(?P<argument>[^"]+)"')
def omit_argument(world, command, argument):
    args = {"--instance": "aio", "--resource-group": "rg"}
    if command == "enable":
        args.update({"--mi-user-assigned": IDENTITY, "--kv-resource-id": VAULT})
    if command.startswith("secret"):
        args["--secret-sync-name"] = "creds"
    if command == "secret set":
        args["--secret-map"] = "kv-pass=password"
    if command == "secret remove":
        args["--secret-name"] = "kv-pass"
    del args[argument]
    world.pending = "az iot ops secretsync " + command + " " + " ".join(
        option + " " + value for option, value in args.items())


@g(r'instance "missing" does not exist in resource group "rg"')
def missing_instance(world):
    assert rid("Microsoft.IoTOperations/instances", "missing").lower() not in world.azure.resources


@g(r'all other required arguments for "(?P<command>[^"]+)" are valid')
def valid_arguments(world, command):
    world.pending_options = {
        "enable": f"--mi-user-assigned {IDENTITY} --kv-resource-id {VAULT}",
        "list": "", "secret set": "--secret-sync-name creds --secret-map kv-pass=password",
        "secret list": "--secret-sync-name creds", "disable": "-y",
    }[command]


@g(r'any destructive confirmation is supplied with "--yes"')
def confirmed_arguments(world):
    assert "-y" in world.pending_options or not world.pending_options.startswith("disable")


@g(r'instance "aio" and SecretSync "creds" exist in resource group "rg" of subscription "sub-b"')
def subscription_b(world):
    sync(world, pairs=[("kv-user", "username")])
    for key, value in list(world.azure.resources.items()):
        changed = json.loads(json.dumps(value).replace(SUB, SUB_B))
        del world.azure.resources[key]
        world.azure.resources[changed["id"].lower()] = changed


@g(r'SecretSync "creds" maps "kv-user" to "username"')
def username_mapping(world):
    key = rid("Microsoft.SecretSyncController/secretSyncs", "creds", subscription=SUB_B).lower()
    if key in world.azure.resources:
        assert world.azure.resources[key]["properties"]["objectSecretMapping"] == [
            {"sourcePath": "kv-user", "targetKey": "username"}]
    else:
        one_mapping(world, "creds", "kv-user", "username")


@g(r'the custom role ID is a valid fully qualified role definition ID')
def custom_role(world):
    world.custom_role = CUSTOM_ROLE


@g(r'the identity already has a credential with the selected issuer, SecretSync subject, and token audience')
@g(r'no issuer repair is necessary')
def exact_credential(world):
    credential(world, ISSUER)
    snapshot(world)


def credential(world, issuer, name="existing", subject="system:serviceaccount:aio-ns:aio-ssc-sa"):
    value = {"id": IDENTITY + "/federatedIdentityCredentials/" + name, "name": name,
             "type": "Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials",
             "properties": {"issuer": issuer, "subject": subject, "audiences": AUDIENCE}}
    world.azure.resources[value["id"].lower()] = value
    return value


@g(r'(?:discovery verifies an issuer differing only by a trailing slash from a matching SecretSync credential|'
   r'a verified issuer repair is needed)')
def slash_credential(world):
    credential(world, ISSUER + "/")
    snapshot(world)


@g(r'no exact matching credential already exists')
def no_exact(world):
    assert all(r["properties"].get("issuer") != ISSUER for r in world.azure.resources.values())


@g(r'the credential discovery condition is "(?P<condition>[^"]+)"')
def credential_condition(world, condition):
    if condition.startswith("an exact"):
        credential(world, ISSUER)
    elif condition.startswith("both"):
        credential(world, ISSUER)
        credential(world, ISSUER + "/", "slash")
    elif condition.startswith("only"):
        credential(world, ISSUER, subject="other-service-account")
    else:
        assert condition == "issuer discovery cannot verify the ARM issuer"
        credential(world, ISSUER + "/")
        world.azure.discovery_verified = False
    snapshot(world)


@g(r'the cluster prerequisite state is "(?P<state>[^"]+)"')
def prerequisites(world, state):
    props = world.azure.cluster["properties"]
    if "OIDC" in state:
        props["oidcIssuerProfile"]["enabled"] = False
    if "workload identity" in state:
        props["securityProfile"]["workloadIdentity"]["enabled"] = False
    if "URL absent" in state:
        props["oidcIssuerProfile"].pop("selfHostedIssuerUrl" if "self-hosted" in state else "issuerUrl")


@g(r'Azure refuses a required vault role assignment')
def role_failure(world):
    world.azure.fail("PUT", "/roleAssignments/")


@g(r'Azure refuses the credential "(?P<operation>[^"]+)" operation')
def credential_failure(world, operation):
    world.azure.fail("GET" if operation == "list" else "PUT", "/federatedIdentityCredentials")


@g(r'enablement fails at "(?P<stage>[^"]+)"')
def enable_failure(world, stage):
    method, path = {
        "reading the vault": ("GET", VAULT), "reading the identity": ("GET", IDENTITY),
        "writing federation": ("PUT", "/federatedIdentityCredentials/"),
        "waiting for SPC creation": ("PUT", "/azureKeyVaultSecretProviderClasses/"),
        "updating the instance reference": ("PUT", "/Microsoft.IoTOperations/instances/"),
    }[stage]
    world.azure.fail(method, path, status=403)


@g(r'the entire instance and its sync configuration are dedicated to this test run')
def dedicated(world):
    world.azure.instance["tags"] = {"bdd-owned": "offline-test"}
    snapshot(world)


@g(r'it has a default SPC, an additional SPC, and SecretSyncs created by more than one client')
def extra_resources(world):
    sync(world)
    world.azure.spc("spc-extra")
    sync(world, "experience", [("kv-user", "username")], spc="spc-extra")
    snapshot(world)


@g(r'a separate custom location has unrelated sync resources')
@g(r'both custom locations contain sync resources')
def other_location(world):
    sync(world)
    world.azure.spc("unrelated", custom_location="cl-b")
    world.azure.sync("unrelated", custom_location="cl-b")
    snapshot(world)


@g(r'another instance in the same resource group uses custom location "cl-b"')
def other_instance(world):
    world.azure.add("Microsoft.IoTOperations/instances", "other", custom_location="cl-b")


@g(r'(?:the instance custom location contains|the custom location contains) "(?P<resources>[^"]+)"')
def resource_mix(world, resources):
    clear_sync(world)
    if "SPC" in resources and "no SPC" not in resources:
        world.azure.spc()
    if "two SPC" in resources:
        world.azure.spc("spc-extra")
    if "SecretSync" in resources and "no SecretSync" not in resources:
        sync(world) if world.azure.spc_id.lower() in world.azure.resources else world.azure.sync()
    if "two SecretSync" in resources:
        world.azure.sync("extra")
    snapshot(world)


@g(r'one discovered resource is in another resource group')
def other_group(world):
    resource = next(r for r in world.azure.sync_resources())
    del world.azure.resources[resource["id"].lower()]
    resource["id"] = resource["id"].replace("/resourceGroups/rg/", "/resourceGroups/other-rg/")
    resource["resourceGroup"] = "other-rg"
    world.azure.resources[resource["id"].lower()] = resource
    snapshot(world)


@g(r'the instance has a default SPC reference but discovery finds no sync resources')
def stale_reference(world):
    clear_sync(world)
    snapshot(world)


@g(r'Azure refuses the instance update')
def instance_write_failure(world):
    world.azure.fail("PUT", "/Microsoft.IoTOperations/instances/")


@g(r'a SecretSync deletion fails or reaches a failed terminal state')
def delete_failure(world):
    world.azure.fail("DELETE", "/secretSyncs/")


@g(r'"(?P<operation>[^"]+)" fails(?: with an Azure service error)?')
def operation_failure(world, operation):
    method, path = {
        "discovering sync resources": ("POST", "/Microsoft.ResourceGraph/"),
        "deleting a discovered SPC": ("DELETE", "/azureKeyVaultSecretProviderClasses/"),
        "resolving the connected cluster": ("GET", "/Microsoft.Kubernetes/connectedClusters/"),
        "querying associated sync resources": ("POST", "/Microsoft.ResourceGraph/"),
    }[operation]
    world.azure.fail(method, path)


@g(r'(?:the instance custom location has sync resources on multiple Resource Graph pages|'
   r'Resource Graph returns the instance sync resources over multiple skip-token pages)')
def many_pages(world):
    extra_resources(world)
    world.azure.page_size = 1


@g(r'the SPC already contains "kv-pass" with additional object settings')
def additional_settings(world):
    store_objects(world.azure.spc_resource, [
        {"objectName": "kv-pass", "objectType": "secret", "objectVersion": "pinned", "objectEncoding": "base64"}])
    snapshot(world)


@g(r'reading the referenced default SPC fails with "(?P<failure>[^"]+)"')
def default_read_failure(world, failure):
    world.azure.fail("GET", world.azure.spc_id, 404 if failure == "resource not found" else 403)


@g(r'instance "aio" is in subscription "sub-a"')
def subscription_a(world):
    assert world.azure.instance["id"].startswith("/subscriptions/" + SUB)


@g(r'Key Vault "vault-a" is in accessible subscription "sub-b" with its cloud-specific vaultUri')
def vault_subscription(world):
    world.azure.vault_subscription = SUB_B
    world.azure.vault_uri = "https://authoritative-name.vault.azure.net/"


@g(r'vault discovery returns no accessible match for "vault-a"')
def invisible_vault(world):
    world.azure.vaults_visible = False


@g(r'Key Vault secret "kv-user" exists but "kv-missing" does not')
def missing_source(world):
    assert "kv-user" in world.azure.secrets
    assert "kv-missing" not in world.azure.secrets


@g(r'reading Key Vault secret "kv-pass" fails with "(?P<failure>[^"]+)"')
def secret_failure(world, failure):
    if "transport" in failure:
        world.http.add("GET", re.compile(r"https://.*/secrets/kv-pass.*"),
                       body=requests.ConnectionError("offline transport fault"))
    else:
        world.azure.fail("GET", "/secrets/kv-pass", 403 if "authorization" in failure else 503)


@g(r'Key Vault secret "kv-cert" has "(?P<tag_state>[^"]+)" and content type "(?P<content_type>[^"]+)"')
def encoding_metadata(world, tag_state, content_type):
    world.azure.secrets["kv-cert"].update({
        "contentType": content_type,
        "tags": dict([tag_state.split("=", 1)]) if "=" in tag_state else {},
    })


@g(r'"(?P<source>[^"]+)" is not yet an SPC object')
def no_object(world, source):
    assert source not in [e["objectName"] for e in objects(world.azure.spc_resource)]


@g(r'the SPC contains operations-experience objects with version and encoding settings')
def experience_objects(world):
    store_objects(world.azure.spc_resource, [
        {"objectName": "experience", "objectType": "secret", "objectVersion": "version", "objectEncoding": "hex"}])
    snapshot(world)


@g(r'Key Vault secret "kv-pass" exists and is not yet in the SPC')
@g(r'Key Vault secret "kv-pass" has more than one version')
def latest_source(world):
    world.azure.secrets["kv-pass"]["versions"] = ["old", "latest"]
    assert not any(e["objectName"] == "kv-pass" for e in objects(world.azure.spc_resource))


@g(r'the SPC update "(?P<failure>[^"]+)"')
def spc_write_failure(world, failure):
    body = {"properties": {"provisioningState": "Failed"}} if failure.startswith("reaches") else None
    world.azure.fail("PUT", "/azureKeyVaultSecretProviderClasses/", 200 if body else 403, body=body)


@g(r'reading SecretSync "creds" is forbidden')
def forbidden_sync(world):
    world.azure.fail("GET", "/secretSyncs/creds")


@g(r'the SPC update succeeds but the SecretSync write fails')
@g(r'the subsequent SecretSync write fails')
def sync_write_failure(world):
    world.azure.fail("PUT", "/secretSyncs/")


@g(r'an earlier set attempt added "kv-pass" to the SPC but failed to create SecretSync "creds"')
def partial_set(world):
    sync_write_failure(world)
    world.set(["kv-pass=password"])
    assert world.rc != 0
    assert objects(world.azure.spc_resource)[0]["objectName"] == "kv-pass"
    world.azure.faults.clear()
    world.azure.calls.clear()
    snapshot(world)


@g(r'setting a mapping successfully updates the SPC')
def source_ready(world):
    assert "kv-pass" in world.azure.secrets


@g(r'SecretSync "creds" exists with objectSecretMapping "(?P<state>[^"]+)"')
def empty_mappings(world, state):
    value = sync(world)
    if state == "absent":
        value["properties"].pop("objectSecretMapping")
    else:
        value["properties"]["objectSecretMapping"] = None if state == "null" else []


@g(r'"(?P<operation>[^"]+)" fails with "(?P<failure>[^"]+)"')
def read_error(world, operation, failure):
    path = world.azure.spc_id if operation == "reading the default SPC" else "/secretSyncs/creds"
    world.azure.fail("GET", path, 404 if failure == "resource not found" else 403)


@g(r'another run-owned SecretSync in "cl-a" created through the operations-experience resource contract '
   r'references "kv-pass" through "spc-a"')
def shared_consumer(world):
    world.azure.sync("experience", [("kv-pass", "custom-key")])
    snapshot(world)


@g(r'the only other source reference is "(?P<other_reference>[^"]+)"')
def other_reference(world, other_reference):
    world.azure.sync("other", [("kv-unrelated" if "kv-unrelated" in other_reference else "kv-pass", "other-key")],
                     spc="spc-b" if "spc-b" in other_reference else "spc-a",
                     custom_location="cl-b" if "cl-b" in other_reference else "cl-a")
    snapshot(world)


@g(r'discovering other consumers fails')
def discovery_failure(world):
    world.azure.fail("POST", "/Microsoft.ResourceGraph/")
    snapshot(world)


@g(r'the caller can manage sync resources but cannot read or delete Key Vault secrets')
def no_vault_access(world):
    world.azure.fail("GET", "/secrets/")
    world.azure.fail("DELETE", "/secrets/")


@g(r'the instance default SPC association is "(?P<default_state>[^"]+)"')
def nondefault(world, default_state):
    if default_state == "not configured":
        no_default(world)
    else:
        world.azure.spc("spc-default")
        world.azure.instance["properties"]["defaultSecretProviderClassRef"] = {
            "resourceId": rid("Microsoft.SecretSyncController/azureKeyVaultSecretProviderClasses", "spc-default")}
    snapshot(world)


@g(r'SecretSync "creds" references nondefault SPC "spc-a" and maps "kv-pass" to "password"')
def nondefault_sync(world):
    sync(world)
    snapshot(world)


@g(r'the SPC objects state is "(?P<state>[^"]+)"')
def absent_object(world, state):
    if state == "no objects property":
        world.azure.spc_resource["properties"].pop("objects", None)
    elif state == "an empty objects string":
        world.azure.spc_resource["properties"]["objects"] = ""
    else:
        store_objects(world.azure.spc_resource, [{"objectName": "other", "objectType": "secret"}])
    snapshot(world)


@g(r'SecretSync "creds" maps "(?P<mappings>[^"]+)"')
def mapping_string(world, mappings):
    sync(world, pairs=[tuple(pair.strip().split("=")) for pair in mappings.split(",")])
    snapshot(world)


@g(r'the target SecretSync "(?P<operation>[^"]+)" fails')
def target_mutation_error(world, operation):
    world.azure.fail("PUT" if operation == "update" else "DELETE", "/secretSyncs/")


@g(r'target deletion succeeds but SPC "(?P<operation>[^"]+)" fails')
def cleanup_error(world, operation):
    world.azure.fail("GET" if operation == "read" else "PUT", "/azureKeyVaultSecretProviderClasses/")


@g(r'reading SecretSync "creds" fails with "(?P<failure>[^"]+)"')
def target_read_error(world, failure):
    world.azure.fail("GET", "/secretSyncs/creds", 404 if failure == "resource not found" else 403)


@g(r'source "kv-pass" is referenced by multiple SecretSyncs in "cl-a" using "spc-a"')
def multiple_consumers(world):
    sync(world)
    shared_consumer(world)


@g(r'reference discovery has more than one result page')
def reference_pages(world):
    world.azure.page_size = 1


@g(r'an additional consumer appears only on a later page')
def later_consumer(world):
    assert list(world.azure.resources).index(rid("Microsoft.SecretSyncController/secretSyncs", "experience").lower()) > 0


@g(r'source "kv-pass" is mapped in SecretSync "creds"')
def source_mapped(world):
    sync(world)
    snapshot(world)


@g(r'the first reference-discovery page does not include another consumer')
def first_page(world):
    world.azure.page_size = 1
    assert world.azure.sync_resources()[0]["type"].lower() == SPC_TYPE


@g(r'fetching the next required page fails')
def next_page_error(world):
    world.azure.fail("POST", "/Microsoft.ResourceGraph/", predicate=lambda body: "$skipToken" in body["options"])


@g(r'the named SecretSync "creds" belongs to "(?P<ownership>[^"]+)"')
def wrong_owner(world, ownership):
    world.azure.sync(custom_location="cl-b" if ownership == "custom location cl-b" else "cl-a",
                     spc="other-spc" if "different SPC" in ownership else "spc-a")
    snapshot(world)


@g(r'the mapping conflict is "(?P<conflict>[^"]+)"')
def target_conflict(world, conflict):
    world.requested = ["kv-user=password", "kv-pass=password"]
    if conflict.startswith("a requested"):
        sync(world)
        world.requested = ["kv-user=password"]
    snapshot(world)


@g(r'the default SPC has objects containing "(?P<invalid_state>[^"]+)"')
def malformed_objects(world, invalid_state):
    world.azure.spc_resource["properties"]["objects"] = {
        "invalid YAML syntax": "array: [", "a scalar rather than an objects map": "scalar",
        "a non-array value for array": "array: value", "a non-map decoded object entry": "array:\n- scalar",
    }[invalid_state]
    snapshot(world)


@g(r'the requested source exists')
def requested_exists(world):
    assert "kv-pass" in world.azure.secrets


@g(r'two SecretSyncs in different resource groups share a name, custom location, SPC, and source')
def same_name(world):
    sync(world)
    world.azure.sync("creds", group="other-rg")
    snapshot(world)


@g(r'no endpoint references SecretSync "custom-creds"')
def no_endpoint(world):
    assert not any("endpoint" in r["type"].lower() for r in world.azure.resources.values())


@w(r'I run "(?P<command>[^"]+)"')
def run(world, command):
    snapshot(world)
    world.run(command)


@w(r'I invoke that command')
def invoke_pending(world):
    world.run(world.pending)


@w(r'I run "az iot ops secretsync (?P<command>[^"]+)" targeting instance "missing" in resource group "rg"')
def run_missing(world, command):
    world.invoke(command, world.pending_options, instance="missing")


@w(r'I enable secret sync for "aio" using "sync-mi" and "vault-a"')
@w(r'I enable secret sync without "--skip-ra"')
def normal_enable(world):
    snapshot(world)
    enable(world)


@w(r'I enable secret sync with "(?P<options>[^"]+)"')
def enable_option(world, options):
    if "--self-hosted-issuer" in options:
        world.azure.issuer_response = ISSUER + "-self"
    enable(world, "" if options == "none" else options.replace("{custom-role-id}", CUSTOM_ROLE))


@w(r'I enable secret sync with "(?P<spc_option>[^"]+)" and "(?P<tags_option>[^"]+)"')
def enable_creation(world, spc_option, tags_option):
    enable(world, " ".join(o for o in (spc_option, tags_option) if o != "none"))


@w(r'I enable secret sync with "--skip-ra" and issuer option "(?P<issuer_option>[^"]+)"')
def enable_prerequisite(world, issuer_option):
    enable(world, "--skip-ra " + (issuer_option if issuer_option != "none" else ""))


def confirmation(world, value):
    world.prompt_answer = value != "a declined prompt"
    return "" if "prompt" in value else value


@w(r'I disable secret sync with "(?P<value>[^"]+)"')
def disable(world, value):
    snapshot(world)
    world.invoke("disable", confirmation(world, value))


@w(r'I set mapping "(?P<mapping>[^"]+)" on (?:run-owned )?SecretSync "(?P<name>[^"]+)"(?P<twice> twice)?')
def set_mapping(world, mapping, name, twice):
    snapshot(world)
    world.set([mapping], name)
    if twice:
        world.set([mapping], name)


@w(r'I set mappings "(?P<first>[^"]+)" and "(?P<second>[^"]+)" (?:on SecretSync "creds"|in the same invocation)')
def set_two(world, first, second):
    snapshot(world)
    world.set([first, second])


@w(r'I remove source "kv-pass"(?: from SecretSync "creds")? with "(?P<value>[^"]+)"')
def remove_source(world, value):
    snapshot(world)
    world.remove(confirmation=confirmation(world, value))


@w(r'I remove the source from one of them with "--yes"')
def remove_duplicate_name(world):
    world.remove()


@w(r'I set the requested mappings on SecretSync "creds"')
def set_requested(world):
    world.set(world.requested)


@w(r'I invoke "az iot ops secretsync secret (?P<command>[^"]+)" for "aio" and "creds" with otherwise valid arguments')
def ownership_action(world, command):
    options = "--secret-sync-name creds"
    options += {"set": " --secret-map kv-pass=password", "list": "", "remove": " --secret-name kv-pass -y"}[command]
    world.invoke("secret " + command, options)


@w(r'the set command reports that failure')
def report_partial(world):
    world.set(["kv-pass=password"])


@t(r'the command exits successfully')
def success(world):
    assert world.rc == 0, world.messages


@t(r'the command exits unsuccessfully(?: with (?P<diagnostic>.+))?')
def failure(world, diagnostic=None):
    assert world.rc not in (0, None)
    assert "unexpected error" not in world.messages and "Traceback" not in world.messages
    assert re.search(r"ERROR|failed|not found|Invalid|Enable|Missing|mismatch|Duplicate|unrecognized|required", world.messages,
                     re.IGNORECASE)
    if diagnostic is None:
        return
    expected = {
        "an instance-not-found diagnostic": ("HTTP 404", "/instances/missing"),
        "secret sync enablement guidance": ("Secret sync not enabled", "az iot ops secretsync enable"),
        "the SPC service error": ("/azureKeyVaultSecretProviderClasses/", "HTTP"),
        "vault existence and accessibility guidance": ("Key Vault", "accessible"),
        "the verification failure": ("GET", "failed"),
        "the lookup error": ("/secretSyncs/creds", "HTTP 403"),
        "the SecretSync service error": ("SecretSync write failed", "HTTP"),
        "the discovery error": ("Microsoft.ResourceGraph", "HTTP"),
        "the discovery failure": ("Microsoft.ResourceGraph", "HTTP"),
        "the target service error": ("/secretSyncs/creds", "HTTP"),
        "the SPC error": ("/azureKeyVaultSecretProviderClasses/", "HTTP"),
        "actionable credential repair guidance": ("Federated credential repair failed", "permissions", "issuer"),
        "a resource ownership mismatch diagnostic": ("ownership mismatch",),
        "a diagnostic identifying invalid SPC objects": ("Invalid SPC objects",),
        "that failure": ("HTTP",),
        "that error": ("HTTP",),
        "the service error": ("HTTP",),
    }
    if diagnostic == "the verification failure" and "Transport failure" in world.messages:
        assert "/secrets/kv-pass" in world.messages
    else:
        assert all(text in world.messages for text in expected[diagnostic]), world.messages


@t(r'the command exits unsuccessfully identifying source "kv-pass" and SecretSync "creds"')
def unknown_mapping(world):
    failure(world)
    assert "kv-pass" in world.messages and "creds" in world.messages and "not found" in world.messages


@t(r'the command exits unsuccessfully identifying the duplicate target key and conflicting sources')
def conflicting_diagnostic(world):
    failure(world)
    assert all(value in world.messages for value in ("Duplicate target key", "password", "kv-pass", "kv-user"))


@t(r'the command exits unsuccessfully identifying the missing secret and vault')
def missing_source_diagnostic(world):
    failure(world)
    assert all(value in world.messages for value in ("kv-missing", "vault-a", "not found"))


@t(r'no Azure request is made')
def no_requests(world):
    assert not world.azure.calls
    assert not world.token_requests


@t(r'the help describes "(?P<purpose>[^"]+)"')
def help_purpose(world, purpose):
    assert purpose.lower() in " ".join(world.output.lower().split())
    if purpose == "setting Key Vault secret mappings":
        assert "--secret-map" in world.output


@t(r'the diagnostic identifies the missing "(?P<argument>[^"]+)" argument')
def missing_argument(world, argument):
    assert argument in world.messages
    assert "required" in world.messages


@t(r'discovery is scoped to the custom location of instance "aio"')
@t(r'discovery uses instance "aio" in resource group "rg"')
def scoped_discovery(world):
    query = world.azure.selected_calls("POST")[0]["body"]["query"]
    assert rid("Microsoft.ExtendedLocation/customLocations", "cl-a") in query
    assert any("/resourceGroups/rg/providers/Microsoft.IoTOperations/instances/aio" in c["path"]
               for c in world.azure.calls)


@t(r'standard output is "kv-user"')
def stdout_query(world):
    assert world.output.strip() == "kv-user"


@t(r'instance and SecretSync requests target subscription "sub-b"')
def selected_subscription(world):
    calls = [c for c in world.azure.calls if "/instances/" in c["path"] or "/secretSyncs/" in c["path"]]
    assert len(calls) == 2
    assert all(c["path"].startswith("/subscriptions/" + SUB_B) for c in calls)


@t(r'(?:no resource is created, updated, or deleted|no Azure or Kubernetes resource is changed|'
   r'no SPC or SecretSync write is (?:made|attempted)|'
   r'no SecretSync or SPC (?:is mutated|write or deletion is made)|'
   r'neither (?:the SPC nor the SecretSync|the target SecretSync nor the SPC) is changed)')
def no_mutations(world):
    assert not world.azure.mutations
    assert world.azure.resources == world.before


@t(r'the existing configuration remains unchanged')
@t(r'existing sync resources remain unchanged')
def unchanged(world):
    assert world.azure.resources == world.before


@t(r'no Key Vault value is read and no SPC or SecretSync write is made')
def no_value_or_write(world):
    assert not world.azure.selected_calls(contains="/secrets/")
    assert not world.azure.mutations


@t(r'no other instance\'s mappings are returned')
def no_mapping_result(world):
    assert world.result is None


@t(r'the diagnostic identifies (?:the invalid "--secret-map" and the required source=target format|invalid SPC objects)')
def invalid_diagnostic(world):
    assert "Invalid" in world.messages
    assert ("--secret-map" in world.messages and "<akv-name>=<target-key>" in world.messages
            or "SPC objects" in world.messages)


@t(r'the command returns the created default SPC')
def created_spc(world):
    success(world)
    assert world.result["type"].lower() == SPC_TYPE
    world.created_spc = deepcopy(world.result)


@t(r'the SPC uses the identity client ID, vault name, tenant ID, cluster region, and instance custom location')
def spc_properties(world):
    value = world.created_spc
    assert value["properties"] == {"clientId": CLIENT, "keyvaultName": "vault-a", "tenantId": TENANT}
    assert value["location"] == world.azure.cluster["location"]
    assert value["extendedLocation"] == world.azure.instance["extendedLocation"]


@t(r'the managed identity has Key Vault Reader and Key Vault Secrets User at the vault scope')
def builtin_roles(world):
    calls = world.azure.selected_calls("PUT", "/roleAssignments/")
    assert {c["body"]["properties"]["roleDefinitionId"].rsplit("/", 1)[-1] for c in calls} == set(ROLES)
    assert all(c["path"].startswith(VAULT + "/") and c["body"]["properties"]["principalId"] == PRINCIPAL for c in calls)


@t(r'a federated credential allows the SecretSync service account in the instance namespace')
def federated_subject(world):
    values = world.azure.selected_calls("PUT", "/federatedIdentityCredentials/")
    assert len(values) == 1
    assert values[0]["body"]["properties"]["subject"] == "system:serviceaccount:aio-ns:" + SERVICE_ACCOUNT


@t(r'its audience is "api://AzureADTokenExchange"')
def audience(world):
    value = world.azure.selected_calls("PUT", "/federatedIdentityCredentials/")[0]
    assert value["body"]["properties"]["audiences"] == AUDIENCE


@t(r'(?:the instance defaultSecretProviderClassRef identifies the created SPC|the instance references that SPC|'
   r'a default SPC is created and associated with the instance)')
def default_reference(world):
    success(world)
    assert world.azure.instance["properties"]["defaultSecretProviderClassRef"]["resourceId"] == world.result["id"]


@t(r'the created SPC name satisfies "(?P<rule>[^"]+)"')
def spc_name(world, rule):
    assert world.result["name"] == "custom-spc" if rule.startswith("exactly") else re.fullmatch(
        r"spc-ops-[0-9a-f]{7}", world.result["name"])


@t(r'its tags satisfy "(?P<rule>[^"]+)"')
def spc_tags(world, rule):
    assert world.result.get("tags", {}) == ({"team": "cli", "stage": "dev"} if rule.startswith("team=") else {})


@t(r'vault role assignment behavior is "(?P<expected>[^"]+)"')
def role_options(world, expected):
    calls = world.azure.selected_calls(contains="/roleAssignments")
    if expected.startswith("no "):
        assert not calls
    elif "custom" in expected:
        assert [c["body"]["properties"]["roleDefinitionId"] for c in calls if c["method"] == "PUT"] == [CUSTOM_ROLE]
    else:
        builtin_roles(world)


@t(r'enablement still configures federation and the default SPC')
def complete_enable(world):
    default_reference(world)
    federated_subject(world)


@t(r'no duplicate federated credential is created')
@t(r'no new federated credential is created')
@t(r'no federated credential is created or updated')
def no_federation_write(world):
    assert not world.azure.selected_calls("PUT", "/federatedIdentityCredentials/")


@t(r'federation uses the selected self-hosted issuer after issuer discovery')
@t(r'it does not silently select the system issuer instead')
def self_hosted(world):
    value = world.azure.selected_calls("PUT", "/federatedIdentityCredentials/")[0]
    assert value["body"]["properties"]["issuer"] == ISSUER + "-self"


@t(r'the diagnostic identifies "(?P<problem>[^"]+)" and the applicable cluster configuration guidance')
def prerequisite_diagnostic(world, problem):
    if problem == "both disabled prerequisites":
        assert "OIDC issuer" in world.messages and "workload identity federation" in world.messages
    else:
        assert problem.lower() in world.messages.lower()
    assert "cluster" in world.messages.lower() or "connectedk8s" in world.messages


@t(r'no federation, SPC creation, or default reference update is performed')
def no_enable_mutations(world):
    assert not world.azure.selected_calls("PUT", "/federatedIdentityCredentials/")
    assert not world.azure.selected_calls("PUT", "/azureKeyVaultSecretProviderClasses/")
    assert not world.azure.selected_calls("PUT", "/instances/")


@t(r'the diagnostic identifies the assignment failure, identity principal, and vault scope')
def role_diagnostic(world):
    assert "Role assignment failure" in world.messages and PRINCIPAL in world.messages and VAULT in world.messages


@t(r'a warning says associated secret sync resources already exist and points to the list command')
def already_enabled(world):
    assert "already" in world.messages and "az iot ops secretsync list" in world.messages


@t(r'existing SPCs, mappings, and the instance default reference are not rewritten')
@t(r'existing SPCs, SecretSync mappings, and the instance default reference are not rewritten')
@t(r'existing SPCs and SecretSync mappings are not overwritten')
def no_configuration_write(world):
    assert not any(c for c in world.azure.mutations if "/federatedIdentityCredentials/" not in c["path"]
                   and "/roleAssignments/" not in c["path"])


@t(r'that credential is updated to the verified issuer while preserving its name, subject, and audience')
def repaired_credential(world):
    writes = world.azure.selected_calls("PUT", "/federatedIdentityCredentials/")
    assert len(writes) == 1 and writes[0]["path"].endswith("/existing")
    old = world.before[(IDENTITY + "/federatedIdentityCredentials/existing").lower()]["properties"]
    assert writes[0]["body"]["properties"] == dict(old, issuer=ISSUER)


@t(r'"(?P<effect>[^"]+)" is not performed')
def no_later_enable_stage(world, effect):
    if "federation" in effect:
        assert not world.azure.selected_calls("PUT", "/federatedIdentityCredentials/")
    if "SPC" in effect and "reference" not in effect:
        assert not world.azure.selected_calls("PUT", "/azureKeyVaultSecretProviderClasses/")
    if "reference" in effect:
        assert not world.azure.selected_calls("PUT", "/instances/")
    if "reporting" in effect:
        assert world.rc != 0 and world.result is None


@t(r'(?:no successful rollback of earlier role assignments is claimed|'
   r'the command does not claim that existing mappings were replaced|no transaction-wide rollback is claimed|'
   r'no restoration of the already removed instance association is claimed|'
   r'no complete successful disablement or transaction-wide rollback is claimed|'
   r'no successful mapping result or automatic SPC rollback is claimed|'
   r'it does not claim a completed removal or restoration of the deleted SecretSync)')
def no_false_success(world):
    assert not re.search(r"(?:rollback|restor\w+|replac\w+|disablement|removal) (?:succeeded|completed|successful)",
                         world.messages, re.IGNORECASE)
    if world.rc != 0:
        assert world.result is None


@t(r'(?:the confirmation prompt behavior|prompt behavior) is "(?P<prompt>[^"]+)"')
def prompt_count(world, prompt):
    assert len(world.prompts) == (1 if prompt == "prompted once" else 0)


@t(r'the default association and sync resources are "(?P<outcome>[^"]+)"')
def disable_outcome(world, outcome):
    if outcome == "unchanged":
        unchanged(world)
        no_requests(world)
    else:
        assert not world.azure.sync_resources()
        assert "defaultSecretProviderClassRef" not in world.azure.instance["properties"]


@t(r'resource access behavior is "(?P<access>[^"]+)"')
def removal_access(world, access):
    if access.startswith("no "):
        no_requests(world)
    else:
        success(world)
        assert world.azure.selected_calls("DELETE", "/secretSyncs/")


@t(r'(?:the instance no longer has a defaultSecretProviderClassRef|the instance default SPC reference is removed)')
def reference_removed(world):
    assert "defaultSecretProviderClassRef" not in world.azure.instance["properties"]


@t(r'(?:all SecretSyncs in "cl-a" are deleted before any SPC in "cl-a" is deleted|'
   r'every discovered SecretSync is deleted before any discovered SPC is deleted|'
   r'no SecretSync deletion is attempted after SPC deletions have started)')
def deletion_order(world):
    calls = world.azure.selected_calls("DELETE")
    first_spc = next((i for i, c in enumerate(calls) if "/azureKeyVaultSecretProviderClasses/" in c["path"]), len(calls))
    assert not any("/secretSyncs/" in c["path"] for c in calls[first_spc:])


@t(r'(?:all SPCs in "cl-a" are deleted|both sync resources are deleted|'
   r'every discovered sync resource is deleted from its own resource group)')
def resources_deleted(world):
    expected = [r for r in world.before.values() if r["type"].lower() in (SPC_TYPE, SYNC_TYPE)
                and r["extendedLocation"]["name"].endswith("/cl-a")]
    assert expected
    assert {r["id"].lower() for r in expected} == {c["path"].lower() for c in world.azure.selected_calls("DELETE")}


@t(r'unrelated custom-location resources remain unchanged')
def other_location_unchanged(world):
    for key, resource in world.before.items():
        if resource.get("extendedLocation", {}).get("name", "").endswith("/cl-b"):
            assert world.azure.resources[key] == resource


@t(r'Key Vault secrets, the vault, managed identity, role assignments, and federation remain')
def dependencies_preserved(world):
    assert world.azure.secrets == world.source_before
    for key, resource in world.before.items():
        if resource["type"].lower() not in (SPC_TYPE, SYNC_TYPE) and "/instances/" not in key:
            assert world.azure.resources[key] == resource


@t(r'within the reconciliation timeout the run-owned sync custom resources are absent from Kubernetes')
def kubernetes_empty(world):
    names = {r["name"] for r in world.before.values() if r["type"].lower() == SYNC_TYPE
             and r["extendedLocation"]["name"].endswith("/cl-a")}
    assert names.isdisjoint(world.azure.kubernetes)


@t(r'no instance update is made solely to remove an already absent reference')
def no_instance_write(world):
    assert not world.azure.selected_calls("PUT", "/instances/")


@t(r'the command exits successfully without resource writes or deletes')
@t(r'the command exits successfully without resource writes')
def read_only_success(world):
    success(world)
    assert not world.azure.mutations


@t(r'a warning says no (?:secret )?sync resources were found(?: and includes enablement guidance)?')
def empty_warning(world):
    assert "No secret sync resources found" in world.messages
    assert "enable" in world.messages


@t(r'the warning contains enablement syntax for instance "aio" and resource group "rg"')
def enable_guidance(world):
    assert "az iot ops secretsync enable --instance aio -g rg" in world.messages


@t(r'no resource deletion is attempted')
@t(r'no SecretSync or SPC deletion is attempted')
def no_deletion(world):
    assert not world.azure.selected_calls("DELETE")


@t(r'no SPC deletion is started')
def no_spc_delete(world):
    assert not world.azure.selected_calls("DELETE", "/azureKeyVaultSecretProviderClasses/")


@t(r'every discovery page is retrieved before any sync resource deletion is started')
@t(r'all discovery pages are consumed before deciding whether the SPC object is unshared')
def pages_before_mutation(world):
    graph_calls = [i for i, c in enumerate(world.azure.calls) if c["method"] == "POST"]
    assert len(graph_calls) > 1
    first = next(i for i, c in enumerate(world.azure.calls) if c["method"] == "DELETE")
    assert max(graph_calls) < first


@t(r'the output contains the associated SPC and SecretSync ARM resources with their properties')
@t(r'output contains the associated resources from every page')
@t(r'both resources are returned')
def all_resources(world):
    expected = [r for r in world.azure.resources.values() if r["type"].lower() in (SPC_TYPE, SYNC_TYPE)
                and r["extendedLocation"]["name"].endswith("/cl-a")]
    assert world.result == expected


@t(r'each resource identifies custom location "cl-a"')
@t(r'output contains only sync resources associated with "cl-a"')
def returned_scope(world):
    assert world.result and all(r["extendedLocation"]["name"].endswith("/cl-a") for r in world.result)


@t(r'it includes matching resources regardless of whether the CLI or operations experience created them')
def all_creators(world):
    all_resources(world)


@t(r'the result contains exactly "(?P<result>[^"]+)"')
def exact_resource_count(world, result):
    assert len(world.result) == (4 if result == "all four sync resources" else 1)
    if result != "all four sync resources":
        assert world.result[0]["type"].lower() == (SPC_TYPE if result == "the SPC" else SYNC_TYPE)


@t(r'the command exits successfully without a resource result body')
def empty_result(world):
    success(world)
    assert world.result is None


@t(r'listing does not require or create a default SPC reference')
def no_default_created(world):
    assert "defaultSecretProviderClassRef" not in world.azure.instance["properties"]
    assert not world.azure.mutations


@t(r'no successful empty-resource result is reported')
@t(r'it does not report a successful empty mapping array')
def no_empty_success(world):
    assert world.rc != 0 and world.result is None


@t(r'no next-page token is mistaken for a resource or ignored')
def next_tokens(world):
    calls = world.azure.selected_calls("POST")
    assert len(calls) > 1 and "$skipToken" in calls[1]["body"]["options"]
    assert all("properties" in r for r in world.result)


@t(r'both source secrets are verified before the first SPC or SecretSync write')
def verify_before_write(world):
    indices = [i for i, c in enumerate(world.azure.calls) if "/secrets/" in c["path"]]
    assert len(indices) == 2
    assert max(indices) < next(i for i, c in enumerate(world.azure.calls) if c["method"] == "PUT")


@t(r'the SPC contains each source exactly once as objectType "secret"')
def source_objects(world):
    entries = objects(world.azure.spc_resource)
    assert sorted(e["objectName"] for e in entries) == ["kv-pass", "kv-user"]
    assert all(e["objectType"] == "secret" for e in entries)


@t(r'the command returns SecretSync "creds" with both sourcePath and targetKey mappings')
def created_mappings(world):
    success(world)
    assert world.result["name"] == "creds"
    assert world.result["properties"]["objectSecretMapping"] == [
        {"sourcePath": "kv-user", "targetKey": "username"}, {"sourcePath": "kv-pass", "targetKey": "password"}]


@t(r'its secretProviderClassName is "spc-a"')
@t(r'its kubernetesSecretType is "Opaque"')
@t(r'it uses the SecretSync controller service account and the SPC location and extendedLocation')
def sync_properties(world):
    assert world.result["properties"]["secretProviderClassName"] == "spc-a"
    assert world.result["properties"]["kubernetesSecretType"] == "Opaque"
    assert world.result["properties"]["serviceAccountName"] == SERVICE_ACCOUNT
    assert world.result["location"] == world.azure.spc_resource["location"]
    assert world.result["extendedLocation"] == world.azure.spc_resource["extendedLocation"]


@t(r'within the reconciliation timeout Kubernetes secret "(?P<name>[^"]+)" contains the Key Vault bytes under "(?P<key>[^"]+)"')
def reconciled_probe(world, name, key):
    assert world.azure.kubernetes[name][key] == world.azure.secrets["probe"]["value"].encode()


@t(r'within the reconciliation timeout Kubernetes secret "creds" contains the source bytes under "username" and "password"')
def reconciled_multiple(world):
    assert world.azure.kubernetes["creds"] == {
        "username": world.azure.secrets["kv-user"]["value"].encode(),
        "password": world.azure.secrets["kv-pass"]["value"].encode(),
    }


@t(r'within the reconciliation timeout Kubernetes secret "tls" contains both original and newly mapped bytes')
def reconciled_tls(world):
    assert world.azure.kubernetes["tls"] == {
        "certificate": world.azure.secrets["kv-cert"]["value"].encode(),
        "privateKey": world.azure.secrets["kv-key"]["value"].encode(),
    }


@t(r'within the reconciliation timeout both Kubernetes keys contain the same expected source bytes')
def reconciled_multikey(world):
    data = world.azure.kubernetes["tls"]
    assert set(data) == {"certificate", "cert-copy"}
    assert data["certificate"] == data["cert-copy"] == world.azure.secrets["kv-cert"]["value"].encode()


@t(r'(?:the secret bytes are not included in command output or test diagnostics|'
   r'command output contains mappings but not secret values|'
   r'output contains configuration and secret references but not Key Vault secret values|'
   r'no secret values appear in output|no secret bytes are disclosed|'
   r'the command does not copy secret values into the ARM resource)')
def no_secret_disclosure(world):
    serialized = world.messages + json.dumps(world.result) + json.dumps(world.azure.mutations)
    assert not any(secret["value"] in serialized for secret in world.azure.secrets.values())


@t(r'the returned SecretSync contains both mappings')
def additive_mappings(world):
    assert world.result["properties"]["objectSecretMapping"] == [
        {"sourcePath": "kv-cert", "targetKey": "certificate"}, {"sourcePath": "kv-key", "targetKey": "privateKey"}]


@t(r'existing SecretSync properties unrelated to mappings are preserved')
def preserve_sync_properties(world):
    old = world.before[world.result["id"].lower()]["properties"]
    assert {k: v for k, v in world.result["properties"].items() if k != "objectSecretMapping"} == {
        k: v for k, v in old.items() if k != "objectSecretMapping"}


@t(r'existing SPC connection properties and unrelated object entries are preserved')
@t(r'the original object entries and their settings remain semantically unchanged')
@t(r'unrelated SPC objects remain unchanged')
def preserve_spc(world):
    previous = world.before[world.azure.spc_id.lower()]
    assert {k: v for k, v in previous["properties"].items() if k != "objects"} == {
        k: v for k, v in world.azure.spc_resource["properties"].items() if k != "objects"}
    assert all(entry in objects(world.azure.spc_resource) for entry in objects(previous))


@t(r'that mapping appears exactly once')
def one_pair(world):
    assert world.result["properties"]["objectSecretMapping"].count({"sourcePath": "kv-pass", "targetKey": "password"}) == 1


@t(r'"kv-pass" appears exactly once in the SPC objects')
@t(r'the SPC contains exactly one "kv-pass" object')
@t(r'the SPC still contains exactly one object for "kv-pass"')
def single_source_object(world):
    assert [entry["objectName"] for entry in objects(world.azure.spc_resource)].count("kv-pass") == 1


@t(r'its additional object settings remain unchanged')
def preserve_object_settings(world):
    assert objects(world.azure.spc_resource) == objects(world.before[world.azure.spc_id.lower()])


@t(r'the result contains both "kv-cert=certificate" and "kv-cert=cert-copy"')
@t(r'the result contains both mappings without collapsing them by sourcePath')
def multikey_result(world):
    value = world.result["properties"]["objectSecretMapping"] if isinstance(world.result, dict) else world.result
    assert value == [{"sourcePath": "kv-cert", "targetKey": "certificate"}, {"sourcePath": "kv-cert", "targetKey": "cert-copy"}]


@t(r'the SPC still contains only one object entry for "kv-cert"')
def one_certificate(world):
    assert [entry["objectName"] for entry in objects(world.azure.spc_resource)].count("kv-cert") == 1


@t(r'vault discovery is not restricted to subscription "sub-a"')
def discovery_subscriptions(world):
    assert world.azure.selected_calls("POST")[0]["body"]["subscriptions"] == []


@t(r'verification uses the discovered vaultUri and the active cloud\'s Key Vault audience')
@t(r'the command does not construct a public-cloud vault suffix')
def authoritative_vault(world):
    assert world.azure.selected_calls(contains="/secrets/")[0]["url"].startswith(world.azure.vault_uri)
    assert any(request["resource"] == "https://vault.azure.net" for request in world.token_requests)


@t(r'the data-plane client is selected for subscription "sub-b"')
def vault_token_subscription(world):
    assert any(r.get("subscription") == SUB_B and r.get("resource") == "https://vault.azure.net"
               for r in world.token_requests)


@t(r'no missing Key Vault secret is created')
@t(r'no Key Vault secret value or version is created, updated, or deleted')
@t(r'no Key Vault deletion is attempted')
@t(r'no Key Vault secret is deleted')
def vault_readonly(world):
    assert not any(c["method"] != "GET" for c in world.azure.selected_calls(contains="/secrets/"))


@t(r'it does not misreport the secret as nonexistent')
def not_missing(world):
    assert "not found in Key Vault" not in world.messages


@t(r'the new SPC object has "(?P<encoding>[^"]+)"')
def encoded_object(world, encoding):
    entry = next(e for e in objects(world.azure.spc_resource) if e["objectName"] == "kv-cert")
    assert entry.get("objectEncoding") == ("hex" if encoding == "objectEncoding set to hex" else None)


@t(r'one new "kv-pass" object of type "secret" is added')
def new_object(world):
    assert objects(world.azure.spc_resource)[-1] == {"objectName": "kv-pass", "objectType": "secret"}


@t(r'the objects property remains valid SPC objects YAML')
def valid_objects(world):
    assert isinstance(yaml.safe_load(world.azure.spc_resource["properties"]["objects"])["array"], list)
    assert objects(world.azure.spc_resource)


@t(r'source verification requests the latest version')
def latest_version(world):
    assert "/secrets/kv-pass?" in world.azure.selected_calls(contains="/secrets/")[0]["url"]


@t(r'the SPC object does not pin an objectVersion')
def unpinned(world):
    assert "objectVersion" not in objects(world.azure.spc_resource)[0]


@t(r'the resulting mapping uses targetKey "custom.key"')
@t(r'the SecretSync is created with the caller-selected key')
@t(r'no consumer-specific key requirement is inferred')
def arbitrary_key(world):
    success(world)
    assert world.result["properties"]["objectSecretMapping"] == [{"sourcePath": "kv-pass", "targetKey": "custom.key"}]


@t(r'the CLI does not require a fixed credential-key vocabulary or create a consumer')
@t(r'no endpoint is created or changed')
def no_consumer_mutation(world):
    assert all("/secretSyncs/" in c["path"] or "/azureKeyVaultSecretProviderClasses/" in c["path"]
               for c in world.azure.mutations)
    assert not any("endpoint" in c["path"].lower() for c in world.azure.calls)


@t(r'no SecretSync create or update is attempted')
def no_sync_write(world):
    assert not world.azure.selected_calls("PUT", "/secretSyncs/")


@t(r'SecretSync "creds" is created with exactly that mapping')
@t(r'the new SecretSync contains exactly one "kv-pass=password" mapping')
def exact_new_mapping(world):
    success(world)
    assert world.result["properties"]["objectSecretMapping"] == [{"sourcePath": "kv-pass", "targetKey": "password"}]


@t(r'the result is the two sourcePath and targetKey objects from SecretSync "creds"')
@t(r'no other SecretSync\'s mappings are included')
def mapping_list(world):
    assert world.result == [
        {"sourcePath": "kv-user", "targetKey": "username"}, {"sourcePath": "kv-pass", "targetKey": "password"}]


@t(r'no Key Vault data-plane read or resource mutation is performed by the list command')
def mapping_readonly(world):
    assert not world.azure.selected_calls(contains="/secrets/")
    assert not world.azure.mutations


@t(r'the JSON result is an empty array')
def empty_mapping_list(world):
    success(world)
    assert world.result == [] and json.loads(world.output) == []


@t(r'a warning identifies that SecretSync "creds" contains no secrets')
def empty_mapping_warning(world):
    assert "No secrets found in SecretSync 'creds'" in world.messages


@t(r'no SecretSync lookup or resource mutation is performed')
def no_lookup(world):
    assert not world.azure.selected_calls(contains="/secretSyncs/")
    assert not world.azure.mutations


@t(r'the command returns the updated SecretSync with only "kv-user=username"')
@t(r'the returned SecretSync retains "kv-user=username"')
def remaining_username(world):
    success(world)
    assert world.result["properties"]["objectSecretMapping"] == [{"sourcePath": "kv-user", "targetKey": "username"}]


@t(r'the SPC no longer includes "kv-pass" but retains "kv-user" and unrelated object settings')
def remaining_objects(world):
    entries = objects(world.azure.spc_resource)
    assert entries == [e for e in objects(world.before[world.azure.spc_id.lower()]) if e["objectName"] != "kv-pass"]


@t(r'within the reconciliation timeout Kubernetes secret "creds" retains "username" '
   r'with its original bytes and has no "password" key')
def remaining_kubernetes(world):
    assert world.azure.kubernetes["creds"] == {"username": world.azure.secrets["kv-user"]["value"].encode()}


@t(r'Key Vault secret "kv-pass" and all its versions remain unchanged')
@t(r'the Key Vault source secret still exists')
def source_preserved(world):
    assert world.azure.secrets["kv-pass"] == world.source_before["kv-pass"]
    vault_readonly(world)


@t(r'the SecretSync ARM resource is deleted instead of written with an empty mapping array')
@t(r'SecretSync "creds" is deleted')
@t(r'the target SecretSync is deleted')
def sync_deleted(world):
    assert rid("Microsoft.SecretSyncController/secretSyncs", "creds").lower() not in world.azure.resources
    assert world.azure.selected_calls("DELETE", "/secretSyncs/creds")
    assert not world.azure.selected_calls("PUT", "/secretSyncs/creds")


@t(r'the final "kv-pass" object is removed from the SPC')
def removed_object(world):
    assert "kv-pass" not in [e["objectName"] for e in objects(world.azure.spc_resource)]


@t(r'an SPC with no remaining objects has no objects property')
def no_objects_property(world):
    assert "objects" not in world.azure.spc_resource["properties"]


@t(r'within the reconciliation timeout the SecretSync custom resource and its controller-owned Kubernetes secret are absent')
def kubernetes_deleted(world):
    sync_deleted(world)
    assert "creds" not in world.azure.kubernetes


@t(r'the shared SPC object for "kv-pass" remains unchanged')
@t(r'the shared SPC object is retained')
@t(r'the SPC object is preserved for the other SecretSync')
def shared_object(world):
    assert objects(world.azure.spc_resource) == objects(world.before[world.azure.spc_id.lower()])
    assert "kv-pass" in [e["objectName"] for e in objects(world.azure.spc_resource)]


@t(r'the other SecretSync and its Kubernetes secret remain functional')
def other_functional(world):
    key = rid("Microsoft.SecretSyncController/secretSyncs", "experience").lower()
    assert world.azure.resources[key] == world.before[key]
    assert world.azure.kubernetes["experience"]["custom-key"] == world.azure.secrets["kv-pass"]["value"].encode()


@t(r'a new run-owned Key Vault version subsequently reconciles to the other consumer within the rotation timeout')
def rotation(world):
    world.azure.secrets["kv-pass"]["value"] = "unit-new-version-bytes"
    world.azure.reconcile()
    assert world.azure.kubernetes["experience"]["custom-key"] == b"unit-new-version-bytes"


@t(r'no operations-experience UI automation or mocked service is required for this resource-level compatibility check')
def resource_contract(world):
    # Offline controller emulation tests the contract, not the live environment's reconciliation.
    key = rid("Microsoft.SecretSyncController/secretSyncs", "experience").lower()
    assert set(world.azure.resources[key]["properties"]) >= {
        "secretProviderClassName", "objectSecretMapping", "kubernetesSecretType", "serviceAccountName"}


@t(r'both "kv-pass" mappings are removed')
def all_source_mappings_removed(world):
    assert all(m["sourcePath"] != "kv-pass" for m in world.result["properties"]["objectSecretMapping"])


@t(r'shared SPC object "kv-pass" is "(?P<spc_outcome>[^"]+)"')
def reference_outcome(world, spc_outcome):
    assert ("kv-pass" in [e["objectName"] for e in objects(world.azure.spc_resource)]) == (spc_outcome == "retained")


@t(r'all other SecretSyncs are unchanged')
def others_unchanged(world):
    for key, value in world.before.items():
        if value["type"].lower() == SYNC_TYPE and value["name"] != "creds":
            assert world.azure.resources[key] == value


@t(r'reference discovery occurs before the SecretSync is mutated')
def discovery_before_target(world):
    graph_index = next(i for i, c in enumerate(world.azure.calls) if c["method"] == "POST")
    mutation_index = next(i for i, c in enumerate(world.azure.calls) if c["method"] in ("PUT", "DELETE"))
    assert graph_index < mutation_index


@t(r'the target SecretSync is excluded from the reference count')
@t(r'the SecretSync is deleted before its unshared SPC object is removed')
def target_excluded(world):
    removed_object(world)
    calls = world.azure.mutations
    assert calls[0]["method"] == "DELETE" and "/secretSyncs/" in calls[0]["path"]
    assert calls[1]["method"] == "PUT" and "/azureKeyVaultSecretProviderClasses/" in calls[1]["path"]


@t(r'no reference discovery query, resource write, or resource deletion is made')
def no_discovery_write(world):
    assert not world.azure.selected_calls("POST") and not world.azure.mutations


@t(r'removal succeeds without any Key Vault data-plane request')
def removal_no_vault(world):
    success(world)
    assert not world.azure.selected_calls(contains="/secrets/")


@t(r'cleanup targets only SPC "spc-a"')
def target_spc(world):
    assert all("/spc-a" in c["path"] for c in world.azure.selected_calls(contains="/azureKeyVaultSecretProviderClasses/"))


@t(r'it does not require or modify the instance default SPC association')
def preserve_default(world):
    assert world.azure.instance == world.before[world.azure.instance["id"].lower()]
    no_instance_write(world)


@t(r'no SPC update is made')
@t(r'the SPC object list is not updated')
def no_spc_write(world):
    assert not world.azure.selected_calls("PUT", "/azureKeyVaultSecretProviderClasses/")


@t(r'only the targeted full ARM resource ID is excluded from the reference count')
def full_identity(world):
    assert rid("Microsoft.SecretSyncController/secretSyncs", "creds", group="other-rg").lower() in world.azure.resources
    shared_object(world)


@t(r'its diagnostic identifies the completed SPC update and the failed SecretSync stage')
def partial_diagnostic(world):
    failure(world)
    assert any(e["objectName"] == "kv-pass" for e in objects(world.azure.spc_resource))
    assert "SPC update completed; SecretSync write failed" in world.messages


@t(r'it gives retry or inspection guidance without claiming rollback')
def partial_guidance(world):
    assert "Inspect the SPC and retry" in world.messages
    assert "No rollback was performed" in world.messages


@g(r'a valid existing device and inbound endpoint configuration')
def device_configuration(world):
    world.consumer_args = {}


@g(r'the command includes the valid companion arguments "(?P<companions>[^"]+)"')
def companion_args(world, companions):
    if companions != "none":
        option, value = companions.split()
        world.consumer_args[option] = value


@g(r'the caller supplies "(?P<option>[^"]+)" as "(?P<reference>[^"]+)"')
def reference_arg(world, option, reference):
    world.consumer_args[option] = reference


@w(r'the existing device inbound endpoint add command builds the endpoint')
def device_auth(world):
    from azext_edge.edge.providers.adr.helpers import process_authentication

    names = {"--cert-ref": "certificate_reference", "--key-ref": "key_reference",
             "--icr": "intermediate_certificate_reference", "--user-ref": "username_reference",
             "--pass-ref": "password_reference"}
    world.consumer_result = process_authentication(**{names[k]: v for k, v in world.consumer_args.items()})


@t(r'"(?P<reference>[^"]+)" is stored in the corresponding credential reference without rewriting')
def unchanged_reference(world, reference):
    names = {"--cert-ref": "certificateSecretName", "--key-ref": "keySecretName",
             "--icr": "intermediateCertificatesSecretName", "--user-ref": "usernameSecretName",
             "--pass-ref": "passwordSecretName"}
    expected = {names[k]: v for k, v in world.consumer_args.items()}
    section = "x509Credentials" if "--cert-ref" in world.consumer_args else "usernamePasswordCredentials"
    assert world.consumer_result[section] == expected
    assert reference in expected.values()


@t(r'that command does not create an SPC, a SecretSync, or a Key Vault secret')
@t(r'no new "--akv" argument is required')
def consumer_no_azure(world):
    assert not world.azure.calls and world.consumer_result


@g(r'run-owned SecretSync "eventhub-sasl" maps existing Key Vault sources to "username" and "password"')
def sasl_mapping(world):
    sync(world, "eventhub-sasl", [("kv-user", "username"), ("kv-pass", "password")])
    snapshot(world)


@g(r'otherwise valid Event Hubs endpoint creation arguments with SASL plain authentication are supplied')
def sasl_args(world):
    world.consumer_settings = {}


@w(r'the existing Event Hubs dataflow endpoint command uses "--secret-name eventhub-sasl" with SASL authentication')
def dataflow_auth(world):
    from azext_edge.edge.providers.orchestration.resources.dataflows import DataFlowEndpoints

    world.consumer_result = DataFlowEndpoints._process_authentication_type(
        None, "EventHub", world.consumer_settings, authentication_type="Sasl",
        sasl_secret_name="eventhub-sasl", sasl_type="Plain")


@t(r'its authentication secretRef is "eventhub-sasl" without a target-key suffix')
def sasl_reference(world):
    assert world.consumer_settings == {"authentication": {
        "method": "Sasl", "saslSettings": {"secretRef": "eventhub-sasl", "saslType": "Plain"}}}


@t(r'the endpoint command does not recreate or rewrite the SecretSync')
def no_sync_rewrite(world):
    unchanged(world)
    assert not world.azure.calls
