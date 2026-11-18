"""Transport, authentication audience, continuation and ARM operation edge cases."""

import json

import pytest
import requests
from azure.cli.core.mock import DummyCli
from knack.util import CLIError

from azext_secretsync_prototype.api import AzureApi, ApiError, version_for
from azure_boundary import ARM, SUB_B


RESOURCE = "/subscriptions/unit/resourceGroups/rg/providers/Microsoft.SecretSyncController/secretSyncs/probe"
URL = ARM + RESOURCE
POLL = ARM + "/operations/unit?api-version=unit"


@pytest.fixture
def api(world):
    world.http.reset()
    value = AzureApi(DummyCli(), interval=0)
    yield value
    value.close()


@pytest.mark.parametrize("path", ["relative", "https://foreign.invalid/resource"])
def test_refuse_invalid_arm_target(api, path):
    with pytest.raises(CLIError):
        api.get(path)


def test_unsupported_provider(api):
    with pytest.raises(CLIError, match="Unsupported"):
        version_for("/providers/Not.Azure/things/item")


def test_absolute_arm_continuations_keep_version(api):
    assert api._url(POLL) == POLL
    assert api._url(ARM + RESOURCE + "?x=y").endswith("&api-version=2024-08-21-preview")


def test_invalid_json_is_not_an_empty_success(api, world):
    world.http.add("GET", URL, body="invalid-json", status=200)
    with pytest.raises(CLIError, match="invalid JSON"):
        api.get(RESOURCE)


def test_retry_real_transport_failure_then_success(api, world):
    world.http.add("GET", URL, body=requests.ConnectionError("interrupted"))
    world.http.add("GET", URL, json={"id": RESOURCE})
    assert api.get(RESOURCE) == {"id": RESOURCE}
    assert len(world.http.calls) >= 2
    assert world.http.calls[-1].response.status_code == 200


@pytest.mark.parametrize("token", ["", "repeated"])
def test_graph_rejects_bad_continuations(api, world, token):
    endpoint = ARM + "/providers/Microsoft.ResourceGraph/resources"
    world.http.add("POST", endpoint, json={"data": [], "$skipToken": token})
    with pytest.raises(CLIError, match="continuation token"):
        api.graph("Resources")


def test_collection_consumes_pages(api, world):
    world.http.add("GET", URL, json={"value": [{"name": "a"}], "nextLink": POLL})
    world.http.add("GET", POLL, json={"value": [{"name": "b"}]})
    assert api.collection(RESOURCE) == [{"name": "a"}, {"name": "b"}]


def test_collection_repeated_link_fails(api, world):
    world.http.add("GET", URL, json={"value": [], "nextLink": URL})
    with pytest.raises(CLIError, match="repeated collection"):
        api.collection(RESOURCE)


@pytest.mark.parametrize("state", ["Failed", "Canceled", "Cancelled"])
def test_failed_operation_poll_is_not_a_success(api, world, state):
    world.http.add("PUT", URL, json={}, status=202, headers={"Azure-AsyncOperation": POLL})
    world.http.add("GET", POLL, json={"status": state})
    with pytest.raises(CLIError, match="terminal state"):
        api.write(RESOURCE, {"properties": {}})


@pytest.mark.parametrize("header", ["Azure-AsyncOperation", "Location"])
def test_successful_operation_returns_final_resource(api, world, header):
    world.http.add("PUT", URL, json={}, status=202, headers={header: POLL})
    world.http.add("GET", POLL, json={"status": "Running"})
    world.http.add("GET", POLL, json={"status": "Succeeded"})
    final = {"properties": {"provisioningState": "Succeeded"}, "id": RESOURCE}
    world.http.add("GET", URL, json=final)
    assert api.write(RESOURCE, {}) == final


def test_resource_poll_returns_final_not_initial_body(api, world):
    world.http.add("PUT", URL, json={"properties": {"provisioningState": "Creating"}}, status=201)
    final = {"id": RESOURCE, "properties": {"provisioningState": "Succeeded"}}
    world.http.add("GET", URL, json=final)
    assert api.write(RESOURCE, {}) == final


def test_poll_without_status_accepts_completed_response(api, world):
    world.http.add("PUT", URL, json={}, status=202, headers={"Location": POLL})
    world.http.add("GET", POLL, status=204)
    world.http.add("GET", URL, json={"id": RESOURCE})
    assert api.write(RESOURCE, {}) == {"id": RESOURCE}


def test_timeout_is_explicit(api, world, monkeypatch):
    world.http.add("DELETE", URL, status=202, headers={"Location": POLL})
    clock = iter([0, 1000])
    monkeypatch.setattr("azext_secretsync_prototype.api.time.monotonic", lambda: next(clock))
    with pytest.raises(CLIError, match="Timed out"):
        api.delete(RESOURCE)


def test_resource_delete_completion_can_be_not_found(api, world):
    world.http.add("DELETE", URL, status=202)
    world.http.add("GET", URL, status=404)
    assert api.delete(RESOURCE) is None


def test_resource_poll_access_error_preserved(api, world):
    world.http.add("PUT", URL, json={}, status=202)
    world.http.add("GET", URL, status=403)
    with pytest.raises(ApiError) as error:
        api.write(RESOURCE, {})
    assert error.value.status_code == 403


@pytest.mark.parametrize("uri", ["http://vault.vault.azure.net", "https://other.invalid", "https:///empty"])
def test_vault_rejects_wrong_cloud_or_insecure_uri(api, uri):
    with pytest.raises(CLIError, match="active cloud"):
        api.secret(uri, "name", SUB_B)


def test_sovereign_vault_uses_cloud_endpoint_audience_and_subscription(api, world):
    api.vault_suffix = ".vault.usgovcloudapi.net"
    api.vault_audience = "https://vault.usgovcloudapi.net"
    endpoint = "https://authoritative.vault.usgovcloudapi.net/secrets/unit"
    world.http.add("GET", endpoint, json={"tags": {"file-encoding": "hex"}})
    assert api.secret(endpoint.split("/secrets/")[0], "unit", SUB_B) == {"tags": {"file-encoding": "hex"}}
    assert world.token_requests[-1] == {"resource": "https://vault.usgovcloudapi.net", "subscription": SUB_B}


@pytest.mark.parametrize("body,status,verified", [
    (json.dumps({"issuer": "https://issuer.unit.invalid/cluster/"}), 200, True),
    ("invalid-json", 200, False),
    (json.dumps({"issuer": "https://other.invalid"}), 200, False),
    (json.dumps({"issuer": 1}), 200, False),
    ("x" * 65537, 200, False),
    ("{}", 403, False),
], ids=["verified-slash", "invalid-json", "unrelated", "not-string", "oversized", "forbidden"])
def test_issuer_discovery_is_conservative(api, world, body, status, verified):
    issuer = "https://issuer.unit.invalid/cluster"
    world.http.add("GET", issuer + "/.well-known/openid-configuration", body=body, status=status)
    result, actual = api.issuer(issuer)
    assert actual is verified
    assert result == (issuer + "/" if verified else issuer)
    assert "Authorization" not in world.http.calls[0].request.headers


def test_issuer_unavailable_falls_back_without_claiming_verification(api, world):
    issuer = "https://issuer.unit.invalid/cluster"
    world.http.add("GET", issuer + "/.well-known/openid-configuration", body=requests.ConnectionError())
    assert api.issuer(issuer) == (issuer, False)
