"""Embedded Azure CLI host using real parsing/dispatch and isolated external boundaries."""

import io
import json
import re
import shlex
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy

import responses
from azure.cli.core import AzCommandsLoader
from azure.cli.core._profile import Profile
from azure.cli.core.mock import DummyCli

from azext_secretsync_prototype import PrototypeCommandsLoader
from azext_secretsync_prototype import api, commands

from azure_boundary import AzureBoundary, SUB, SUB_B, TENANT


class EmbeddedLoader(PrototypeCommandsLoader):
    """Supply the main host's help-map contract without loading unrelated extensions."""

    def __init__(self, cli_ctx=None):
        super().__init__(cli_ctx)
        self.cmd_to_loader_map = {}

    def load_command_table(self, args):
        table = super().load_command_table(args)
        self.cmd_to_loader_map = {name: [self] for name in table}
        return table

    def load_arguments(self, command):
        super().load_arguments(command)
        AzCommandsLoader.load_arguments(self, command)


class World:
    def __init__(self, monkeypatch, tmp_path, caplog):
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.caplog = caplog
        self.azure = AzureBoundary()
        self.prompt_answer = True
        self.prompts = []
        self.results = []
        self.token_requests = []
        self.config = tmp_path / "azure"
        self.config.mkdir()
        self.extensions = tmp_path / "extensions"
        self.extensions.mkdir()
        monkeypatch.setenv("AZURE_CONFIG_DIR", str(self.config))
        monkeypatch.setenv("AZURE_EXTENSION_DIR", str(self.extensions))
        monkeypatch.setenv("AZURE_CORE_COLLECT_TELEMETRY", "false")
        monkeypatch.setenv("AZURE_EXTENSION_USE_DYNAMIC_INSTALL", "no")
        monkeypatch.setattr("azure.cli.core._config.GLOBAL_CONFIG_DIR", str(self.config))
        (self.config / "azureProfile.json").write_text(json.dumps({
            "subscriptions": [
                {"id": sid, "name": name, "state": "Enabled", "tenantId": TENANT,
                 "isDefault": default, "environmentName": "AzureCloud",
                 "user": {"name": "offline-principal", "type": "servicePrincipal"}}
                for sid, name, default in ((SUB, "sub-a", True), (SUB_B, "sub-b", False))
            ],
        }), encoding="utf-8")
        monkeypatch.setattr(Profile, "get_raw_token", self.token)
        monkeypatch.setattr(commands, "prompt_y_n", self.prompt)
        monkeypatch.setattr(api.time, "sleep", lambda delay: None)
        self.http = responses.RequestsMock(assert_all_requests_are_fired=False)
        self.http.start()
        for method in (responses.GET, responses.PUT, responses.POST, responses.DELETE):
            self.http.add_callback(method, re.compile(r"https://.*"), callback=self.azure.callback,
                                   content_type="application/json")
        self.before = deepcopy(self.azure.resources)
        self.rc = None
        self.result = None
        self.output = ""

    def close(self):
        self.http.stop()
        self.http.reset()

    def prompt(self, message):
        self.prompts.append(message)
        return self.prompt_answer

    def token(self, *args, **kwargs):
        self.token_requests.append(kwargs)
        return (("Bearer", "offline-token", {"expiresOn": "2099-01-01 00:00:00"}), SUB, TENANT)

    def run(self, command):
        cli = DummyCli(commands_loader_cls=EmbeddedLoader)
        stdout, stderr = io.StringIO(), io.StringIO()
        args = shlex.split(command)
        assert args[0] == "az"
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                self.rc = cli.invoke(args[1:], out_file=stdout)
            except SystemExit as error:
                self.rc = error.code
        self.result = cli.result.result if cli.result else None
        self.output = stdout.getvalue()
        self.stderr = stderr.getvalue()
        self.results.append((self.rc, deepcopy(self.result), self.output))
        return self.result

    def invoke(self, command, options="", instance="aio", group="rg"):
        return self.run(f"az iot ops secretsync {command} -n {instance} -g {group} {options}".strip())

    def set(self, mappings, name="creds"):
        return self.invoke("secret set", f"--secret-sync-name {name} " + " ".join(
            "--secret-map " + pair for pair in mappings
        ))

    def remove(self, source="kv-pass", name="creds", confirmation="-y"):
        return self.invoke("secret remove", f"--secret-sync-name {name} --secret-name {source} {confirmation}")

    @property
    def messages(self):
        return self.output + "\n" + getattr(self, "stderr", "") + "\n" + self.caplog.text
