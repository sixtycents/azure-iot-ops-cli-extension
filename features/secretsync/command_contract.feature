# Specification baseline: 0.1.0.
# Human review accepted the feature content, including the six additional safeguards.
# This annotation records the review; it is not an enforceable CI approval mechanism.
# Baseline decision: preserve current implementation/test behavior when sources conflict.
# Code baseline: Azure/azure-iot-ops-cli-extension at a9a705916af6848733c134e87dd8a6ac141a6060.
# Source paths relative to that repository:
#   azext_edge/edge/commands_secretsync.py
#   azext_edge/edge/params.py (SecretSync argument contexts)
#   azext_edge/edge/providers/orchestration/resources/instances.py
#   azext_edge/edge/providers/orchestration/connected_cluster.py
#   azext_edge/edge/util/resource_graph.py
#   azext_edge/tests/edge/orchestration/resources/test_instances_unit.py
#   azext_edge/tests/edge/orchestration/resources/test_secretsync_secret_unit.py
#   azext_edge/tests/edge/orchestration/test_secretsync_secret_int.py
# CLI references:
#   https://learn.microsoft.com/en-us/cli/azure/iot/ops/secretsync?view=azure-cli-latest
#   https://learn.microsoft.com/en-us/cli/azure/iot/ops/secretsync/secret?view=azure-cli-latest
# Runtime references:
#   https://learn.microsoft.com/en-us/azure/iot-operations/secure-iot-ops/howto-enable-secure-settings
#   https://learn.microsoft.com/en-us/azure/iot-operations/secure-iot-ops/howto-manage-secrets
#   https://learn.microsoft.com/en-us/azure/azure-arc/kubernetes/secret-store-extension
# Additional input: supplied "AKV SecretSync Secrets Command Grou.txt" design.
#
# Reconciliations:
# - set uses repeatable --secret-map, not the design's --secret-name or the
#   conceptual guide's --name/--secret target=... source=... syntax.
# - Merge identity is (sourcePath, targetKey), despite replacement wording in CLI help.
# - remove deletes every mapping for the source and deletes an empty SecretSync.
# - ARM uses properties.objectSecretMapping and properties.kubernetesSecretType;
#   the Kubernetes projection uses spec.secretObject.data and spec.secretObject.type.
# - Removal checks other references BEFORE mutating the SecretSync, despite design ordering.
# - The guard considers the same custom location AND the same SPC, regardless of creator.
# - Resource Graph's existing helper follows skip tokens; complete discovery is baseline behavior.
#
# Implementation is an isolated prototype extension under prototypes/secretsync.
# Production commands remain in azext_edge; the prototype registers only in an isolated extension directory.
# Human-approved coverage scope is every Python file beneath prototypes/secretsync/src,
# including command/argument wiring, handlers, REST adapters, and developer/live runners.
# Existing azext_edge code and tests are not part of this prototype's production coverage scope.
#
# All scenarios and all Examples rows require offline execution, including @e2e.
# safety_proposals.feature retains the provenance of the six reviewed additions beyond code.
# Those scenarios are included in this accepted baseline, not optional or skipped cases.
# Logical fixture names represent unique run-owned resources, never literal shared DEV resources.
# Live runs require explicit authorization, real DEV/PPE dependencies and candidate az installation,
# bounded reconciliation waits, and ownership-checked cleanup that reports failures.
# No values fetched from Key Vault or Kubernetes may be printed by fixture/assertion diagnostics.

@secretsync
Feature: SecretSync command surface and common CLI behavior
  Engineers can address an IoT Operations instance consistently across all six commands.
  CLI contract checks do not authorize any additional functionality in consumer commands.

  @scenario-SS-001
  Scenario Outline: Discover every command without contacting Azure
    Given no Azure credentials or service connections are available
    When I run "az iot ops secretsync <command> --help"
    Then the command exits successfully
    And the help describes "<purpose>"
    And no Azure request is made

    Examples:
      | case         | command       | purpose                               |
      | enable        | enable        | instance secret synchronization        |
      | disable       | disable       | disabling instance secret sync         |
      | list          | list          | listing associated sync resources      |
      | secret-set    | secret set    | setting Key Vault secret mappings      |
      | secret-list   | secret list   | listing mappings in a named SecretSync |
      | secret-remove | secret remove | removing a Key Vault secret reference  |

  @scenario-SS-002
  Scenario Outline: Accept each documented instance alias
    Given instance "aio" exists in resource group "rg"
    And instance "aio" has no SecretSync or SecretProviderClass resources
    When I run "az iot ops secretsync list <instance-option> aio -g rg"
    Then the command exits successfully
    And discovery is scoped to the custom location of instance "aio"

    Examples:
      | case       | instance-option |
      | long       | --instance      |
      | short-i    | -i              |
      | short-n    | -n              |

  @scenario-SS-003
  Scenario Outline: Reject omitted required arguments before contacting Azure
    Given no default resource group is configured
    And all required arguments for "az iot ops secretsync <command>" are supplied except "<argument>"
    When I invoke that command
    Then the command exits unsuccessfully
    And the diagnostic identifies the missing "<argument>" argument
    And no Azure request is made

    Examples:
      | case                   | command       | argument          |
      | enable-instance        | enable        | --instance        |
      | enable-group           | enable        | --resource-group  |
      | enable-vault           | enable        | --kv-resource-id  |
      | enable-identity        | enable        | --mi-user-assigned |
      | disable-instance       | disable       | --instance        |
      | disable-group          | disable       | --resource-group  |
      | list-instance          | list          | --instance        |
      | list-group             | list          | --resource-group  |
      | set-instance           | secret set    | --instance        |
      | set-group              | secret set    | --resource-group  |
      | set-sync               | secret set    | --secret-sync-name |
      | set-map                | secret set    | --secret-map      |
      | secret-list-instance   | secret list   | --instance        |
      | secret-list-group      | secret list   | --resource-group  |
      | secret-list-sync       | secret list   | --secret-sync-name |
      | remove-instance        | secret remove | --instance        |
      | remove-group           | secret remove | --resource-group  |
      | remove-sync            | secret remove | --secret-sync-name |
      | remove-secret          | secret remove | --secret-name     |

  @scenario-SS-004
  Scenario: Use a configured default resource group
    Given resource group "rg" is configured as the Azure CLI default group
    And instance "aio" exists in resource group "rg" with no sync resources
    When I run "az iot ops secretsync list -n aio"
    Then discovery uses instance "aio" in resource group "rg"
    And the command exits successfully

  @scenario-SS-005
  Scenario Outline: Report a missing instance instead of an empty successful result
    Given instance "missing" does not exist in resource group "rg"
    And all other required arguments for "<command>" are valid
    And any destructive confirmation is supplied with "--yes"
    When I run "az iot ops secretsync <command>" targeting instance "missing" in resource group "rg"
    Then the command exits unsuccessfully with an instance-not-found diagnostic
    And no resource is created, updated, or deleted

    Examples:
      | case    | command     |
      | enable  | enable      |
      | list    | list        |
      | set     | secret set  |
      | maps    | secret list |
      | disable | disable     |

  @scenario-SS-006
  Scenario: Apply subscription selection and JSON querying to mapping output
    Given instance "aio" and SecretSync "creds" exist in resource group "rg" of subscription "sub-b"
    And SecretSync "creds" maps "kv-user" to "username"
    When I run "az iot ops secretsync secret list -n aio -g rg --secret-sync-name creds --subscription sub-b --query [0].sourcePath -o tsv"
    Then the command exits successfully
    And standard output is "kv-user"
    And instance and SecretSync requests target subscription "sub-b"
