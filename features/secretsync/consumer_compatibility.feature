# Sources: supplied design's consumer reference matrix; public manage-secrets guidance;
# azext_edge/edge/providers/adr/helpers.py process_authentication and
# azext_edge/edge/providers/orchestration/resources/dataflows.py authentication handling
# at the pinned revision.
# This specifies compatibility only: no new endpoint arguments or automatic consumer creation.
@secretsync
Feature: Preserve existing consumer secret-reference contracts
  SecretSync names are Kubernetes secret names and target keys are caller-selected.
  Device and dataflow endpoint commands keep their existing reference syntax.

  @scenario-SS-100
  Scenario Outline: Keep device inbound references unchanged
    Given a valid existing device and inbound endpoint configuration
    And the command includes the valid companion arguments "<companions>"
    And the caller supplies "<option>" as "<reference>"
    When the existing device inbound endpoint add command builds the endpoint
    Then "<reference>" is stored in the corresponding credential reference without rewriting
    And that command does not create an SPC, a SecretSync, or a Key Vault secret
    And no new "--akv" argument is required

    Examples:
      | case          | option     | reference            | companions                 |
      | certificate   | --cert-ref | tls/certificate      | none                       |
      | key           | --key-ref  | tls/privateKey       | --cert-ref tls/certificate |
      | intermediates | --icr      | tls/intermediateCerts | --cert-ref tls/certificate |
      | username      | --user-ref | creds/username       | --pass-ref creds/password  |
      | password      | --pass-ref | creds/password       | --user-ref creds/username  |

  @scenario-SS-101
  Scenario: Keep a dataflow endpoint's reference at Kubernetes secret granularity
    Given run-owned SecretSync "eventhub-sasl" maps existing Key Vault sources to "username" and "password"
    And otherwise valid Event Hubs endpoint creation arguments with SASL plain authentication are supplied
    When the existing Event Hubs dataflow endpoint command uses "--secret-name eventhub-sasl" with SASL authentication
    Then its authentication secretRef is "eventhub-sasl" without a target-key suffix
    And the endpoint command does not recreate or rewrite the SecretSync

  @scenario-SS-102
  Scenario: Setting mappings does not create or validate a particular consumer
    Given the default SPC and source Key Vault secret exist
    And no endpoint references SecretSync "custom-creds"
    When I set mapping "kv-pass=custom.key" on SecretSync "custom-creds"
    Then the SecretSync is created with the caller-selected key
    And no endpoint is created or changed
    And no consumer-specific key requirement is inferred
