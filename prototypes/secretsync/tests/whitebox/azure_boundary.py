"""In-memory external Azure/controller boundary, reached through real requests serialization."""

import json
import re
from copy import deepcopy
from urllib.parse import unquote, urlsplit


SUB = "11111111-1111-1111-1111-111111111111"
SUB_B = "22222222-2222-2222-2222-222222222222"
TENANT = "33333333-3333-3333-3333-333333333333"
PRINCIPAL = "44444444-4444-4444-4444-444444444444"
CLIENT = "55555555-5555-5555-5555-555555555555"
ARM = "https://management.azure.com"
ISSUER = "https://issuer.unit.invalid/cluster"
SPC_TYPE = "microsoft.secretsynccontroller/azurekeyvaultsecretproviderclasses"
SYNC_TYPE = "microsoft.secretsynccontroller/secretsyncs"


def rid(provider, name, group="rg", subscription=SUB):
    return f"/subscriptions/{subscription}/resourceGroups/{group}/providers/{provider}/{name}"


class AzureBoundary:
    def __init__(self):
        self.reset()

    def reset(self):
        self.resources = {}
        self.secrets = {}
        self.kubernetes = {}
        self.calls = []
        self.faults = []
        self.page_size = 1000
        self.vault_uri = "https://vault-a.vault.azure.net/"
        self.vault_subscription = SUB
        self.vaults_visible = True
        self.discovery_verified = True
        self.issuer_response = ISSUER
        self.add("Microsoft.ExtendedLocation/customLocations", "cl-a", properties={
            "namespace": "aio-ns", "hostResourceId": rid("Microsoft.Kubernetes/connectedClusters", "cluster"),
        })
        self.add("Microsoft.Kubernetes/connectedClusters", "cluster", properties={
            "oidcIssuerProfile": {"enabled": True, "issuerUrl": ISSUER, "selfHostedIssuerUrl": ISSUER + "-self"},
            "securityProfile": {"workloadIdentity": {"enabled": True}},
        })
        self.add("Microsoft.IoTOperations/instances", "aio", properties={})
        self.add("Microsoft.KeyVault/vaults", "vault-a", properties={"vaultUri": self.vault_uri})
        self.add("Microsoft.ManagedIdentity/userAssignedIdentities", "sync-mi",
                 properties={"clientId": CLIENT, "principalId": PRINCIPAL})
        self.spc()
        self.instance["properties"]["defaultSecretProviderClassRef"] = {"resourceId": self.spc_id}
        for name in ("kv-user", "kv-pass", "kv-cert", "kv-key", "probe"):
            self.secrets[name] = {"value": f"unit-{name}-bytes", "tags": {}}

    @property
    def instance(self):
        return self.resources[rid("Microsoft.IoTOperations/instances", "aio").lower()]

    @property
    def spc_id(self):
        return rid("Microsoft.SecretSyncController/azureKeyVaultSecretProviderClasses", "spc-a")

    @property
    def spc_resource(self):
        return self.resources[self.spc_id.lower()]

    @property
    def cluster(self):
        return self.resources[rid("Microsoft.Kubernetes/connectedClusters", "cluster").lower()]

    def add(self, provider, name, group="rg", custom_location="cl-a", properties=None):
        resource = {
            "id": rid(provider, name, group), "name": name, "type": provider,
            "resourceGroup": group, "location": "westus2",
            "extendedLocation": {"name": rid("Microsoft.ExtendedLocation/customLocations", custom_location),
                                 "type": "CustomLocation"},
            "properties": properties or {},
        }
        self.resources[resource["id"].lower()] = resource
        return resource

    def spc(self, name="spc-a", sources=(), group="rg", custom_location="cl-a"):
        import yaml

        value = self.add(
            "Microsoft.SecretSyncController/azureKeyVaultSecretProviderClasses", name, group, custom_location,
            {"keyvaultName": "vault-a", "clientId": CLIENT, "tenantId": TENANT},
        )
        if sources:
            value["properties"]["objects"] = yaml.safe_dump({
                "array": [yaml.safe_dump({"objectName": source, "objectType": "secret"}) for source in sources],
            })
        return value

    def sync(self, name="creds", mappings=(("kv-pass", "password"),), spc="spc-a", group="rg", custom_location="cl-a"):
        value = self.add(
            "Microsoft.SecretSyncController/secretSyncs", name, group, custom_location,
            {"secretProviderClassName": spc, "kubernetesSecretType": "Opaque", "serviceAccountName": "aio-ssc-sa",
             "objectSecretMapping": [{"sourcePath": source, "targetKey": target} for source, target in mappings]},
        )
        self.reconcile()
        return value

    def reconcile(self):
        expected = {}
        for resource in self.resources.values():
            if resource["type"].lower() != SYNC_TYPE:
                continue
            properties = resource["properties"]
            values = {}
            for entry in properties.get("objectSecretMapping") or []:
                source = self.secrets.get(entry["sourcePath"])
                if source:
                    values[entry["targetKey"]] = source["value"].encode()
            expected[resource["name"]] = values
        self.kubernetes = expected

    def fail(self, method, path, status=403, body=None, predicate=None, limit=None):
        self.faults.append({"method": method, "path": path.lower(), "status": status, "body": body,
                            "predicate": predicate, "remaining": limit})

    def callback(self, request):
        parsed = urlsplit(request.url)
        path = unquote(parsed.path)
        lower = path.lower()
        body = json.loads(request.body) if request.body else None
        self.calls.append({"method": request.method, "url": request.url, "path": path, "body": deepcopy(body)})
        for fault in self.faults:
            if (request.method == fault["method"] and fault["path"] in lower
                    and fault["remaining"] != 0
                    and (fault["predicate"] is None or fault["predicate"](body))):
                if fault["remaining"] is not None:
                    fault["remaining"] -= 1
                return fault["status"], {}, json.dumps(fault["body"] or {"error": {"code": "InjectedFailure"}})
        if parsed.hostname == "issuer.unit.invalid":
            return (200 if self.discovery_verified else 503), {}, json.dumps({"issuer": self.issuer_response})
        if "/secrets/" in lower:
            source = path.split("/secrets/")[1]
            secret = self.secrets.get(source)
            return (200, {}, json.dumps(secret)) if secret else (404, {}, '{"error":{"code":"SecretNotFound"}}')
        if lower == "/providers/microsoft.resourcegraph/resources":
            query = body["query"]
            if "microsoft.keyvault/vaults" in query:
                data = [{"vaultUri": self.vault_uri, "subscriptionId": self.vault_subscription}] if self.vaults_visible else []
            else:
                match = re.search(r"extendedLocation.name =~ '([^']+)'", query)
                assert match, f"Discovery must scope a custom location: {query}"
                data = [
                    deepcopy(value) for value in self.resources.values()
                    if value["type"].lower() in (SPC_TYPE, SYNC_TYPE)
                    and value["extendedLocation"]["name"].lower() == match[1].lower()
                ]
            offset = int(body["options"].get("$skipToken", 0))
            page = {"data": data[offset:offset + self.page_size]}
            if len(data) > offset + self.page_size:
                page["$skipToken"] = str(offset + self.page_size)
            return 200, {}, json.dumps(page)
        if request.method == "GET":
            if lower.endswith(("/federatedidentitycredentials", "/roleassignments")):
                data = [value for key, value in self.resources.items() if key.startswith(lower + "/")]
                return 200, {}, json.dumps({"value": data})
            value = self.resources.get(lower)
            return (200, {}, json.dumps(value)) if value else (404, {}, '{"error":{"code":"ResourceNotFound"}}')
        if request.method == "PUT":
            value = deepcopy(body)
            provider = path.split("/providers/")[-1].rsplit("/", 1)[0]
            value.update({"id": path, "name": path.rsplit("/", 1)[-1],
                          "type": provider, "resourceGroup": path.split("/")[4]})
            self.resources[lower] = value
            self.reconcile()
            return 200, {}, json.dumps(value)
        if request.method == "DELETE":
            self.resources.pop(lower, None)
            self.reconcile()
            return 204, {}, ""
        raise AssertionError(f"Unrecognized HTTP boundary: {request.method} {request.url}")

    def selected_calls(self, method=None, contains=None):
        return [
            call for call in self.calls if (method is None or call["method"] == method)
            and (contains is None or contains.lower() in call["path"].lower())
        ]

    @property
    def mutations(self):
        return [call for call in self.calls if call["method"] in {"PUT", "DELETE"}]

    def sync_resources(self):
        return [value for value in self.resources.values() if value["type"].lower() in {SPC_TYPE, SYNC_TYPE}]
