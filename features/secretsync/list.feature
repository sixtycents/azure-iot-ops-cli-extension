# Sources: list_secretsync at the pinned code revision and public CLI group reference.
# Preserve its warning/no-resource-body behavior rather than inventing an empty JSON array.
@secretsync
Feature: List instance synchronization configuration
  The top-level list returns ARM SPC and SecretSync configurations, not individual secret values.

  Background:
    Given instance "aio" exists in resource group "rg" on custom location "cl-a"

  @scenario-SS-040 @e2e
  Scenario: List both kinds of associated resources with properties
    Given the instance has a run-owned default SPC and a SecretSync with a reconciled Kubernetes secret
    When I run "az iot ops secretsync list -n aio -g rg"
    Then the command exits successfully
    And the output contains the associated SPC and SecretSync ARM resources with their properties
    And each resource identifies custom location "cl-a"
    And output contains configuration and secret references but not Key Vault secret values
    And no Azure or Kubernetes resource is changed

  @scenario-SS-041
  Scenario Outline: Return resources when only one resource type is present
    Given the custom location contains "<resources>"
    When I run "az iot ops secretsync list -n aio -g rg"
    Then the result contains exactly "<result>"
    And the command exits successfully without resource writes

    Examples:
      | case      | resources                   | result                   |
      | spc-only  | an SPC and no SecretSyncs    | the SPC                  |
      | sync-only | a SecretSync and no SPCs    | the SecretSync           |
      | multiple  | two SPCs and two SecretSyncs | all four sync resources  |

  @scenario-SS-042
  Scenario: Restrict discovery to the instance custom location
    Given another instance in the same resource group uses custom location "cl-b"
    And both custom locations contain sync resources
    When I run "az iot ops secretsync list -n aio -g rg"
    Then output contains only sync resources associated with "cl-a"
    And it includes matching resources regardless of whether the CLI or operations experience created them

  @scenario-SS-043
  Scenario: Warn when no sync resources are found
    Given the instance has no associated sync resources
    When I run "az iot ops secretsync list -n aio -g rg"
    Then the command exits successfully without a resource result body
    And a warning says no secret sync resources were found
    And the warning contains enablement syntax for instance "aio" and resource group "rg"

  @scenario-SS-044
  Scenario: Discover resources even if the default association is missing
    Given the instance has no defaultSecretProviderClassRef
    And its custom location contains an SPC and a SecretSync
    When I run "az iot ops secretsync list -n aio -g rg"
    Then both resources are returned
    And listing does not require or create a default SPC reference

  @scenario-SS-045
  Scenario Outline: Do not hide discovery failures as empty results
    Given "<operation>" fails with an Azure service error
    When I run "az iot ops secretsync list -n aio -g rg"
    Then the command exits unsuccessfully with that error
    And no successful empty-resource result is reported

    Examples:
      | case      | operation                         |
      | cluster   | resolving the connected cluster    |
      | graph     | querying associated sync resources |

  @scenario-SS-046
  Scenario: Include every page of associated resources
    Given Resource Graph returns the instance sync resources over multiple skip-token pages
    When I run "az iot ops secretsync list -n aio -g rg"
    Then output contains the associated resources from every page
    And no next-page token is mistaken for a resource or ignored
