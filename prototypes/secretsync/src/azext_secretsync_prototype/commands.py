"""Thin command dispatch; destructive confirmation precedes API construction."""

from knack.prompting import prompt_y_n

from .api import AzureApi
from .service import SecretSyncService


def _run(cmd, operation, **kwargs):
    api = AzureApi(cmd.cli_ctx)
    try:
        return getattr(SecretSyncService(api, api.subscription["id"]), operation)(**kwargs)
    finally:
        api.close()


def sync_enable(cmd, instance_name, resource_group_name, mi_user_assigned, keyvault_resource_id,
                spc_name=None, skip_role_assignments=None, use_self_hosted_issuer=None, custom_role_id=None, tags=None):
    return _run(
        cmd, "enable", instance_name=instance_name, resource_group_name=resource_group_name,
        mi_user_assigned=mi_user_assigned, keyvault_resource_id=keyvault_resource_id, spc_name=spc_name,
        skip_role_assignments=skip_role_assignments, use_self_hosted_issuer=use_self_hosted_issuer,
        custom_role_id=custom_role_id, tags=tags,
    )


def sync_list(cmd, instance_name, resource_group_name):
    return _run(cmd, "list", instance_name=instance_name, resource_group_name=resource_group_name)


def sync_disable(cmd, instance_name, resource_group_name, confirm_yes=None):
    if not confirm_yes and not prompt_y_n("Disable all secret synchronization for this instance?"):
        return None
    return _run(cmd, "disable", instance_name=instance_name, resource_group_name=resource_group_name)


def secret_set(cmd, instance_name, resource_group_name, secret_sync_name, secret_map):
    return _run(cmd, "set", instance_name=instance_name, resource_group_name=resource_group_name,
                secret_sync_name=secret_sync_name, secret_map=secret_map)


def secret_list(cmd, instance_name, resource_group_name, secret_sync_name):
    return _run(cmd, "list_secrets", instance_name=instance_name, resource_group_name=resource_group_name,
                secret_sync_name=secret_sync_name)


def secret_remove(cmd, instance_name, resource_group_name, secret_sync_name, secret_name, confirm_yes=None):
    if not confirm_yes and not prompt_y_n(f"Remove source '{secret_name}' from SecretSync '{secret_sync_name}'?"):
        return None
    return _run(cmd, "remove", instance_name=instance_name, resource_group_name=resource_group_name,
                secret_sync_name=secret_sync_name, secret_name=secret_name)
