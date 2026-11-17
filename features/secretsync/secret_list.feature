# Sources: list_secretsync_secrets and test_secretsync_secret_list* at the pinned revision.
@secretsync
Feature: List mappings in one SecretSync
  Listing returns sourcePath and targetKey mappings, never the materialized secret values.
  Unlike the top-level list, this command requires the named SecretSync and enabled synchronization.

  Background:
    Given instance "aio" exists in resource group "rg"
    And its defaultSecretProviderClassRef resolves to readable SPC "spc-a"

  @scenario-SS-070 @e2e
  Scenario: List mappings for a named SecretSync without reading source values
    Given run-owned SecretSync "creds" maps "kv-user" to "username" and "kv-pass" to "password"
    And its Kubernetes secret has reconciled both mappings
    When I run "az iot ops secretsync secret list -n aio -g rg --secret-sync-name creds"
    Then the result is the two sourcePath and targetKey objects from SecretSync "creds"
    And no other SecretSync's mappings are included
    And no Key Vault data-plane read or resource mutation is performed by the list command
    And no secret values appear in output

  @scenario-SS-071
  Scenario: Preserve distinct target keys for the same source
    Given SecretSync "tls" maps "kv-cert" to both "certificate" and "cert-copy"
    When I run "az iot ops secretsync secret list -n aio -g rg --secret-sync-name tls"
    Then the result contains both mappings without collapsing them by sourcePath

  @scenario-SS-072
  Scenario Outline: Return an empty array for an existing resource without mappings
    Given SecretSync "creds" exists with objectSecretMapping "<state>"
    When I run "az iot ops secretsync secret list -n aio -g rg --secret-sync-name creds -o json"
    Then the JSON result is an empty array
    And a warning identifies that SecretSync "creds" contains no secrets

    Examples:
      | case    | state           |
      | empty   | an empty array  |
      | absent  | absent          |
      | null    | null            |

  @scenario-SS-073
  Scenario: Require a default SPC association
    Given the instance has no defaultSecretProviderClassRef
    When I run "az iot ops secretsync secret list -n aio -g rg --secret-sync-name creds"
    Then the command exits unsuccessfully with secret sync enablement guidance
    And no SecretSync lookup or resource mutation is performed

  @scenario-SS-074
  Scenario Outline: Do not replace read failures with an empty mapping list
    Given "<operation>" fails with "<failure>"
    When I run "az iot ops secretsync secret list -n aio -g rg --secret-sync-name creds"
    Then the command exits unsuccessfully with that failure
    And it does not report a successful empty mapping array

    Examples:
      | case       | operation                 | failure              |
      | stale-spc  | reading the default SPC   | resource not found   |
      | denied-spc | reading the default SPC   | access denied        |
      | absent-ss  | reading SecretSync creds  | resource not found   |
      | denied-ss  | reading SecretSync creds  | access denied        |
