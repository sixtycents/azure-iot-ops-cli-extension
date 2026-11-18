"""Isolated prototype extension; it is never registered by azure-iot-ops."""

from azure.cli.core import AzCommandsLoader
from azure.cli.core.commands import CliCommandType
from azure.cli.core.commands.parameters import get_three_state_flag, resource_group_name_type, tags_type
from knack.help_files import helps


PURPOSES = {
    "enable": "Enable instance secret synchronization.",
    "disable": "Disabling instance secret sync deletes associated sync resources.",
    "list": "Listing associated sync resources for an instance.",
    "secret set": "Setting Key Vault secret mappings on a named SecretSync.",
    "secret list": "Listing mappings in a named SecretSync.",
    "secret remove": "Removing a Key Vault secret reference without deleting it from Key Vault.",
}


class PrototypeCommandsLoader(AzCommandsLoader):
    def __init__(self, cli_ctx=None):
        super().__init__(
            cli_ctx=cli_ctx,
            custom_command_type=CliCommandType(operations_tmpl="azext_secretsync_prototype.commands#{}"),
        )

    def load_command_table(self, args):
        for command, purpose in PURPOSES.items():
            helps["iot ops secretsync " + command] = f"type: command\nshort-summary: {purpose}\n"
        for group in ("iot ops secretsync", "iot ops secretsync secret"):
            helps[group] = "type: group\nshort-summary: Isolated SecretSync prototype.\n"
        with self.command_group("iot ops secretsync", is_preview=True) as group:
            for command in ("enable", "disable", "list"):
                group.custom_command(command, "sync_" + command)
        with self.command_group("iot ops secretsync secret", is_preview=True) as group:
            for command in ("set", "list", "remove"):
                group.custom_command(command, "secret_" + command)
        return self.command_table

    def load_arguments(self, command):
        with self.argument_context("iot ops secretsync") as context:
            context.argument("instance_name", options_list=["--instance", "-i", "-n"],
                             help="IoT Operations instance name.")
            context.argument("resource_group_name", arg_type=resource_group_name_type)
            context.argument("confirm_yes", options_list=["--yes", "-y"],
                             arg_type=get_three_state_flag(),
                             help="Confirm destructive configuration removal without a prompt.")
            context.argument("secret_sync_name", options_list=["--secret-sync-name"], help="SecretSync resource name.")
        with self.argument_context("iot ops secretsync enable") as context:
            context.argument("mi_user_assigned", options_list=["--mi-user-assigned"],
                             help="Managed identity resource ID.")
            context.argument("keyvault_resource_id", options_list=["--kv-resource-id"], help="Key Vault resource ID.")
            context.argument("spc_name", options_list=["--spc"], help="Default SPC name; default spc-ops-{hash}.")
            context.argument("skip_role_assignments", options_list=["--skip-ra"], arg_type=get_three_state_flag(),
                             help="Skip vault role assignment reads and writes.")
            context.argument("use_self_hosted_issuer", options_list=["--self-hosted-issuer"],
                             arg_type=get_three_state_flag(),
                             help="Select the cluster self-hosted issuer.")
            context.argument("custom_role_id", options_list=["--custom-role-id"],
                             help="Custom role definition resource ID.")
            context.argument("tags", arg_type=tags_type)
        with self.argument_context("iot ops secretsync secret set") as context:
            context.argument("secret_map", options_list=["--secret-map"], action="append",
                             help="Repeatable <akv-secret-name>=<target-key> mapping.")
        with self.argument_context("iot ops secretsync secret remove") as context:
            context.argument("secret_name", options_list=["--secret-name"],
                             help="Source Key Vault secret name to remove.")


COMMAND_LOADER_CLS = PrototypeCommandsLoader
