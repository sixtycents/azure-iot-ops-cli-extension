"""Authenticated REST boundary for the isolated prototype."""

import time
from urllib.parse import urlsplit

import requests
from azure.cli.core._profile import Profile
from azure.cli.core.commands.client_factory import get_subscription_id
from knack.log import get_logger
from knack.util import CLIError


VERSIONS = {
    "microsoft.iotoperations": "2026-10-01",
    "microsoft.secretsynccontroller": "2024-08-21-preview",
    "microsoft.extendedlocation": "2021-08-15",
    "microsoft.kubernetes": "2024-01-01",
    "microsoft.managedidentity": "2023-01-31",
    "microsoft.authorization": "2022-04-01",
    "microsoft.keyvault": "2023-07-01",
}
LOGGER = get_logger(__name__)


class ApiError(CLIError):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


def version_for(resource_id):
    providers = resource_id.lower().split("/providers/")
    provider = providers[-1].split("/")[0]
    if provider not in VERSIONS:
        raise CLIError(f"Unsupported resource provider: {provider}")
    return VERSIONS[provider]


class AzureApi:
    def __init__(self, cli_ctx, timeout=600, interval=2):
        LOGGER.debug("SecretSync implementation %s", __file__)
        self.profile = Profile(cli_ctx=cli_ctx)
        self.subscription = self.profile.get_subscription(subscription=get_subscription_id(cli_ctx=cli_ctx))
        self.arm = cli_ctx.cloud.endpoints.resource_manager.rstrip("/")
        self.arm_audience = cli_ctx.cloud.endpoints.active_directory_resource_id
        self.vault_suffix = cli_ctx.cloud.suffixes.keyvault_dns
        self.vault_audience = "https://" + self.vault_suffix.lstrip(".")
        self.timeout = timeout
        self.interval = interval
        self.session = requests.Session()

    def close(self):
        self.session.close()

    def _url(self, path, version=None):
        if path.startswith("https://"):
            if urlsplit(path).netloc.lower() != urlsplit(self.arm).netloc.lower():
                raise CLIError("Refusing to send ARM credentials to a different host.")
            url = path
        else:
            if not path.startswith("/"):
                raise CLIError("Expected an absolute ARM resource ID.")
            url = self.arm + path
        if "api-version=" not in url:
            url += ("&" if "?" in url else "?") + "api-version=" + (version or version_for(path))
        return url

    def _request(self, method, url, body=None, audience=None, subscription=None):
        token = self.profile.get_raw_token(
            resource=audience or self.arm_audience,
            subscription=subscription or self.subscription["id"],
        )[0][1]
        for attempt in range(3):
            try:
                LOGGER.debug("SecretSync HTTP %s %s", method, urlsplit(url).path)
                response = self.session.request(
                    method, url, json=body,
                    headers={"Authorization": f"Bearer {token}", "User-Agent": "secretsync-prototype/0.1.0"},
                    timeout=(10, 60), allow_redirects=False,
                )
            except requests.RequestException as error:
                if attempt == 2:
                    raise CLIError(f"Transport failure during {method} {urlsplit(url).path}.") from error
                time.sleep(self.interval * (attempt + 1))
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(self.interval * (attempt + 1))
                continue
            if response.status_code >= 300:
                raise ApiError(
                    response.status_code,
                    f"Azure {method} {urlsplit(url).path} failed (HTTP {response.status_code}).",
                )
            if not response.content:
                return response, None
            try:
                return response, response.json()
            except ValueError as error:
                raise CLIError(f"Azure returned invalid JSON during {method} {urlsplit(url).path}.") from error

    def get(self, resource_id):
        return self._request("GET", self._url(resource_id))[1]

    def graph(self, query):
        resources = []
        body = {"subscriptions": [], "query": query, "options": {}}
        seen_tokens = set()
        while True:
            page = self._request(
                "POST", self._url("/providers/Microsoft.ResourceGraph/resources", "2022-10-01"), body
            )[1]
            resources.extend(page["data"])
            token = page.get("$skipToken")
            if token is None:
                return resources
            if not token or token in seen_tokens:
                raise CLIError("Resource Graph returned an invalid or repeated continuation token.")
            seen_tokens.add(token)
            body["options"] = {"$skipToken": token}

    def collection(self, resource_id):
        results = []
        url = self._url(resource_id)
        seen = set()
        while url:
            if url in seen:
                raise CLIError("Azure returned a repeated collection continuation URL.")
            seen.add(url)
            page = self._request("GET", url)[1]
            results.extend(page["value"])
            next_link = page.get("nextLink")
            url = self._url(next_link) if next_link else None
        return results

    def write(self, resource_id, body):
        return self._mutate("PUT", resource_id, body)

    def delete(self, resource_id):
        self._mutate("DELETE", resource_id)

    def _mutate(self, method, resource_id, body=None):
        response, result = self._request(method, self._url(resource_id), body)
        polling_url = response.headers.get("Azure-AsyncOperation") or response.headers.get("Location")
        state = (result or {}).get("properties", {}).get("provisioningState", "")
        deadline = time.monotonic() + self.timeout
        while response.status_code == 202 or polling_url or state.lower() in {
            "creating", "updating", "deleting", "accepted", "running", "inprogress",
        }:
            if time.monotonic() >= deadline:
                raise CLIError(f"Timed out waiting for {method} {resource_id}.")
            time.sleep(self.interval)
            poll_target = self._url(polling_url) if polling_url else self._url(resource_id)
            try:
                response, status = self._request("GET", poll_target)
            except ApiError as error:
                if method == "DELETE" and not polling_url and error.status_code == 404:
                    return None
                raise
            if not polling_url:
                result = status
            state = (status or {}).get("status") or (status or {}).get("properties", {}).get("provisioningState", "")
            if state.lower() in {"failed", "canceled", "cancelled"}:
                raise CLIError(f"{method} {resource_id} reached terminal state {state}.")
            if state.lower() == "succeeded" or (response.status_code != 202 and not state):
                break
        if state.lower() in {"failed", "canceled", "cancelled"}:
            raise CLIError(f"{method} {resource_id} reached terminal state {state}.")
        if method == "DELETE":
            return None
        return self.get(resource_id) if polling_url or response.status_code == 202 else result

    def secret(self, vault_uri, name, subscription):
        parsed = urlsplit(vault_uri)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(self.vault_suffix):
            raise CLIError("Discovered Key Vault URI does not belong to the active cloud.")
        return self._request(
            "GET", f"{vault_uri.rstrip('/')}/secrets/{name}?api-version=7.4",
            audience=self.vault_audience, subscription=subscription,
        )[1]

    def issuer(self, arm_issuer):
        try:
            response = self.session.get(
                arm_issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=5, allow_redirects=False
            )
        except requests.RequestException:
            return arm_issuer, False
        if response.status_code != 200 or len(response.content) > 65536:
            return arm_issuer, False
        try:
            issuer = response.json().get("issuer")
        except ValueError:
            return arm_issuer, False
        if not isinstance(issuer, str) or issuer.rstrip("/") != arm_issuer.rstrip("/"):
            return arm_issuer, False
        return issuer, True
