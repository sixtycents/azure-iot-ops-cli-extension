"""Specification-owned SecretSync behavior, independent from Azure authentication."""

from copy import deepcopy
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

import yaml
from knack.log import get_logger
from knack.util import CLIError

from .api import ApiError


LOGGER = get_logger(__name__)
SPC_TYPE = "microsoft.secretsynccontroller/azurekeyvaultsecretproviderclasses"
SYNC_TYPE = "microsoft.secretsynccontroller/secretsyncs"
SERVICE_ACCOUNT = "aio-ssc-sa"
AUDIENCE = ["api://AzureADTokenExchange"]
ROLES = ("21090545-7ca7-4776-b22c-e363652d74d2", "4633458b-17de-408a-b874-0445c86b69e6")


def resource_id(subscription, group, provider, name):
    return f"/subscriptions/{subscription}/resourceGroups/{group}/providers/{provider}/{name}"


def sync_id(instance, name, kind="secretSyncs"):
    base = instance["id"].split("/providers/")[0]
    return f"{base}/providers/Microsoft.SecretSyncController/{kind}/{name}"


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def objects(spc):
    raw = spc["properties"].get("objects")
    try:
        document = yaml.safe_load(raw) if raw else {"array": []}
        if not isinstance(document, dict) or not isinstance(document.get("array"), list):
            raise ValueError("Expected an objects map containing an array.")
        entries = [yaml.safe_load(entry) if isinstance(entry, str) else entry for entry in document["array"]]
        if any(not isinstance(entry, dict) or not isinstance(entry.get("objectName"), str) for entry in entries):
            raise ValueError("Expected an objectName in each decoded map.")
    except (yaml.YAMLError, ValueError, TypeError) as error:
        raise CLIError(f"Invalid SPC objects on '{spc['name']}'. Expected valid objects YAML.") from error
    return entries


def store_objects(spc, entries):
    if entries:
        spc["properties"]["objects"] = yaml.safe_dump({"array": [yaml.safe_dump(entry) for entry in entries]})
    else:
        spc["properties"].pop("objects", None)


def parse_mappings(secret_map):
    mappings = []
    for item in secret_map:
        source, sep, target = item.partition("=")
        if not sep or not source or not target:
            raise CLIError("Invalid --secret-map. Expected <akv-name>=<target-key>; both parts are required.")
        pair = {"sourcePath": source, "targetKey": target}
        if pair not in mappings:
            mappings.append(pair)
    return mappings


def ensure_unique_targets(mappings):
    targets = {}
    for mapping in mappings:
        source, target = mapping["sourcePath"], mapping["targetKey"]
        if target in targets and targets[target] != source:
            raise CLIError(f"Duplicate target key '{target}' for sources '{targets[target]}' and '{source}'.")
        targets[target] = source


class SecretSyncService:
    def __init__(self, api, subscription_id):
        self.api = api
        self.subscription_id = subscription_id

    def instance(self, name, group):
        return self.api.get(resource_id(self.subscription_id, group, "Microsoft.IoTOperations/instances", name))

    def associated(self, instance):
        cl = instance["extendedLocation"]["name"]
        query = (
            "Resources | where extendedLocation.name =~ " + literal(cl)
            + f" | where type in~ ('{SPC_TYPE}', '{SYNC_TYPE}')"
            + " | project id, name, type, resourceGroup, extendedLocation, properties, tags, location"
        )
        return self.api.graph(query)

    def default_spc(self, instance):
        reference = instance["properties"].get("defaultSecretProviderClassRef", {}).get("resourceId")
        if not reference:
            raise CLIError("Secret sync not enabled. Use 'az iot ops secretsync enable --instance "
                           f"{instance['name']} -g {instance['id'].split('/')[4]}'.")
        return self.api.get(reference)

    def owned_sync(self, instance, name, spc=None):
        target = self.api.get(sync_id(instance, name))
        if target["extendedLocation"]["name"].lower() != instance["extendedLocation"]["name"].lower():
            raise CLIError(f"Resource ownership mismatch for SecretSync '{name}': custom location differs.")
        if spc and target["properties"]["secretProviderClassName"].lower() != spc["name"].lower():
            raise CLIError(f"Resource ownership mismatch for SecretSync '{name}': SPC differs.")
        return target

    def list(self, instance_name, resource_group_name):
        instance = self.instance(instance_name, resource_group_name)
        custom_location = self.api.get(instance["extendedLocation"]["name"])
        self.api.get(custom_location["properties"]["hostResourceId"])
        resources = self.associated(instance)
        if resources:
            return resources
        LOGGER.warning(
            "No secret sync resources found. Use 'az iot ops secretsync enable --instance %s -g %s'.",
            instance_name, resource_group_name,
        )
        return None

    def list_secrets(self, instance_name, resource_group_name, secret_sync_name):
        instance = self.instance(instance_name, resource_group_name)
        self.default_spc(instance)
        target = self.owned_sync(instance, secret_sync_name)
        mappings = target["properties"].get("objectSecretMapping") or []
        if not mappings:
            LOGGER.warning("No secrets found in SecretSync '%s'.", secret_sync_name)
        return mappings

    def set(self, instance_name, resource_group_name, secret_sync_name, secret_map):
        mappings = parse_mappings(secret_map)
        instance = self.instance(instance_name, resource_group_name)
        spc = self.default_spc(instance)
        entries = objects(spc)
        try:
            target = self.owned_sync(instance, secret_sync_name, spc)
        except ApiError as error:
            if error.status_code != 404:
                raise
            target = None
        existing = target["properties"].get("objectSecretMapping", []) if target else []
        merged = existing + [mapping for mapping in mappings if mapping not in existing]
        ensure_unique_targets(merged)
        vault_name = spc["properties"]["keyvaultName"]
        vaults = self.api.graph(
            "Resources | where type =~ 'microsoft.keyvault/vaults' | where name =~ "
            + literal(vault_name) + " | project vaultUri=properties.vaultUri, subscriptionId"
        )
        if len(vaults) != 1:
            raise CLIError(f"Key Vault '{vault_name}' not found or ambiguous. Ensure it exists and is accessible.")
        vault = vaults[0]
        names = {entry["objectName"] for entry in entries}
        for mapping in mappings:
            source = mapping["sourcePath"]
            try:
                secret = self.api.secret(vault["vaultUri"], source, vault["subscriptionId"])
            except ApiError as error:
                if error.status_code != 404:
                    raise
                raise CLIError(f"AKV secret '{source}' not found in Key Vault '{vault_name}'.") from error
            if source not in names:
                entry = {"objectName": source, "objectType": "secret"}
                if (secret.get("tags") or {}).get("file-encoding", "").lower() == "hex":
                    entry["objectEncoding"] = "hex"
                entries.append(entry)
                names.add(source)
        store_objects(spc, entries)
        self.api.write(spc["id"], spc)
        if target is None:
            target = {
                "location": spc["location"], "extendedLocation": deepcopy(spc["extendedLocation"]),
                "properties": {"kubernetesSecretType": "Opaque", "secretProviderClassName": spc["name"],
                               "serviceAccountName": SERVICE_ACCOUNT},
            }
        target["properties"]["objectSecretMapping"] = merged
        try:
            return self.api.write(sync_id(instance, secret_sync_name), target)
        except CLIError as error:
            raise CLIError(
                "SPC update completed; SecretSync write failed. Inspect the SPC and retry the set command. "
                f"No rollback was performed. {error}"
            ) from error

    def remove(self, instance_name, resource_group_name, secret_sync_name, secret_name):
        instance = self.instance(instance_name, resource_group_name)
        target = self.owned_sync(instance, secret_sync_name)
        properties = target["properties"]
        old = properties.get("objectSecretMapping", [])
        remaining = [entry for entry in old if entry["sourcePath"] != secret_name]
        if len(old) == len(remaining):
            raise CLIError(f"Secret '{secret_name}' not found in SecretSync '{secret_sync_name}'.")
        spc_name = properties["secretProviderClassName"]
        resources = self.associated(instance)
        shared = any(
            other["type"].lower() == SYNC_TYPE
            and other["id"].lower() != target["id"].lower()
            and other.get("properties", {}).get("secretProviderClassName", "").lower() == spc_name.lower()
            and any(entry.get("sourcePath") == secret_name
                    for entry in other.get("properties", {}).get("objectSecretMapping", []))
            for other in resources
        )
        result = None
        if remaining:
            properties["objectSecretMapping"] = remaining
            result = self.api.write(target["id"], target)
        else:
            self.api.delete(target["id"])
        if not shared:
            spc = self.api.get(sync_id(instance, spc_name, "azureKeyVaultSecretProviderClasses"))
            entries = objects(spc)
            kept = [entry for entry in entries if entry["objectName"] != secret_name]
            if len(kept) != len(entries):
                store_objects(spc, kept)
                self.api.write(spc["id"], spc)
        return result

    def disable(self, instance_name, resource_group_name):
        instance = self.instance(instance_name, resource_group_name)
        if instance["properties"].pop("defaultSecretProviderClassRef", None):
            self.api.write(instance["id"], instance)
        resources = self.associated(instance)
        if not resources:
            LOGGER.warning("No secret sync resources found. Use 'az iot ops secretsync enable'.")
        for kind in (SYNC_TYPE, SPC_TYPE):
            for target in resources:
                if target["type"].lower() == kind:
                    self.api.delete(target["id"])

    def enable(self, instance_name, resource_group_name, mi_user_assigned, keyvault_resource_id,
               spc_name=None, skip_role_assignments=False, use_self_hosted_issuer=False,
               custom_role_id=None, tags=None):
        instance = self.instance(instance_name, resource_group_name)
        self.api.get(keyvault_resource_id)
        identity = self.api.get(mi_user_assigned)
        principal = identity["properties"]["principalId"]
        vault_sub = keyvault_resource_id.split("/")[2]
        if not skip_role_assignments:
            roles = [custom_role_id] if custom_role_id else [
                f"/subscriptions/{vault_sub}/providers/Microsoft.Authorization/roleDefinitions/{role}" for role in ROLES
            ]
            try:
                self._roles(keyvault_resource_id, principal, roles)
            except CLIError as error:
                raise CLIError(f"Role assignment failure for principal '{principal}' at '{keyvault_resource_id}'. "
                               "Assign Key Vault Reader and Key Vault Secrets User or the custom role. "
                               f"{error}") from error
        custom_location = self.api.get(instance["extendedLocation"]["name"])
        cluster = self.api.get(custom_location["properties"]["hostResourceId"])
        cluster_properties = cluster["properties"]
        missing = []
        if not cluster_properties.get("oidcIssuerProfile", {}).get("enabled"):
            missing.append("OIDC issuer (--enable-oidc-issuer)")
        if not cluster_properties.get("securityProfile", {}).get("workloadIdentity", {}).get("enabled"):
            missing.append("workload identity federation (--enable-workload-identity)")
        if missing:
            raise CLIError("Enable " + " and ".join(missing) + " with 'az connectedk8s update'.")
        issuer_key = "selfHostedIssuerUrl" if use_self_hosted_issuer else "issuerUrl"
        arm_issuer = cluster_properties["oidcIssuerProfile"].get(issuer_key)
        if not arm_issuer:
            raise CLIError(f"Missing {issuer_key}; check cluster configuration.")
        issuer, verified = self.api.issuer(arm_issuer)
        subject = f"system:serviceaccount:{custom_location['properties']['namespace']}:{SERVICE_ACCOUNT}"
        resources = self.associated(instance)
        if resources:
            if verified:
                self._federate(mi_user_assigned, issuer, subject, repair_only=True)
            LOGGER.warning("Instance already has associated secret sync resources. Use 'az iot ops secretsync list'.")
            return None
        self._federate(mi_user_assigned, issuer, subject)
        name = spc_name or "spc-ops-" + sha256(
            f"{cluster['name']}-{resource_group_name}-{instance_name}".encode()
        ).hexdigest()[:7]
        target = {
            "location": cluster["location"], "extendedLocation": deepcopy(instance["extendedLocation"]),
            "properties": {"clientId": identity["properties"]["clientId"],
                           "keyvaultName": keyvault_resource_id.rsplit("/", 1)[-1],
                           "tenantId": self.api.subscription["tenantId"]},
        }
        if tags:
            target["tags"] = tags
        spc = self.api.write(sync_id(instance, name, "azureKeyVaultSecretProviderClasses"), target)
        instance["properties"]["defaultSecretProviderClassRef"] = {"resourceId": spc["id"]}
        self.api.write(instance["id"], instance)
        return spc

    def _roles(self, vault_id, principal, role_ids):
        collection = vault_id + "/providers/Microsoft.Authorization/roleAssignments"
        assigned = self.api.collection(collection)
        for role_id in role_ids:
            if any(row["properties"]["principalId"].lower() == principal.lower()
                   and row["properties"]["roleDefinitionId"].lower() == role_id.lower() for row in assigned):
                continue
            assignment = str(uuid5(NAMESPACE_URL, f"{vault_id.lower()}:{principal}:{role_id.lower()}"))
            self.api.write(
                collection + "/" + assignment,
                {"properties": {"principalId": principal, "principalType": "ServicePrincipal",
                                "roleDefinitionId": role_id}},
            )

    def _federate(self, identity_id, issuer, subject, repair_only=False):
        collection = identity_id + "/federatedIdentityCredentials"
        try:
            credentials = self.api.collection(collection)
            matches = [
                cred for cred in credentials if cred["properties"].get("subject") == subject
                and cred["properties"].get("audiences") == AUDIENCE
            ]
            if any(cred["properties"]["issuer"] == issuer for cred in matches):
                return
            candidates = [cred for cred in matches if cred["properties"]["issuer"].rstrip("/") == issuer.rstrip("/")]
            if repair_only and not candidates:
                return
            name = candidates[0]["name"] if repair_only else sha256(f"{issuer}:{subject}".encode()).hexdigest()[:12]
            self.api.write(
                collection + "/" + name,
                {"properties": {"issuer": issuer, "subject": subject, "audiences": AUDIENCE}},
            )
        except CLIError as error:
            raise CLIError(
                f"Federated credential {'repair' if repair_only else 'configuration'} failed for "
                f"'{identity_id}'. Check credential list/write permissions and issuer configuration. {error}"
            ) from error
