# Sources: disable_secretsync and test_secretsync_disable at the pinned code revision.
# Public CLI help explicitly deletes all associated SPCs and SecretSyncs, not just the default SPC.
@secretsync
Feature: Disable synchronization for an instance
  Disabling removes the default SPC association and all sync resources in the instance custom location.
  It is destructive configuration removal, not deletion of Key Vault secrets or the managed identity.

  Background:
    Given instance "aio" exists in resource group "rg" on custom location "cl-a"

  @scenario-SS-030
  Scenario Outline: Require confirmation unless explicitly bypassed
    Given the instance has associated sync resources and a default SPC reference
    When I disable secret sync with "<confirmation>"
    Then the confirmation prompt behavior is "<prompt>"
    And the default association and sync resources are "<outcome>"

    Examples:
      | case    | confirmation          | prompt          | outcome   |
      | decline | a declined prompt     | prompted once   | unchanged |
      | accept  | an accepted prompt    | prompted once   | removed   |
      | short   | -y                    | not prompted    | removed   |
      | long    | --yes                 | not prompted    | removed   |

  @scenario-SS-031 @e2e
  Scenario: Disable only a dedicated run-owned instance's synchronization resources
    Given the entire instance and its sync configuration are dedicated to this test run
    And it has a default SPC, an additional SPC, and SecretSyncs created by more than one client
    And a separate custom location has unrelated sync resources
    And the vault, managed identity, role assignments, and federation already exist
    When I run "az iot ops secretsync disable -n aio -g rg --yes"
    Then the instance no longer has a defaultSecretProviderClassRef
    And all SecretSyncs in "cl-a" are deleted before any SPC in "cl-a" is deleted
    And all SPCs in "cl-a" are deleted
    And unrelated custom-location resources remain unchanged
    And Key Vault secrets, the vault, managed identity, role assignments, and federation remain
    And within the reconciliation timeout the run-owned sync custom resources are absent from Kubernetes

  @scenario-SS-032
  Scenario: Discover and remove sync resources without a default association
    Given the instance has no defaultSecretProviderClassRef
    And its custom location still has an SPC and a SecretSync
    When I run "az iot ops secretsync disable -n aio -g rg -y"
    Then both sync resources are deleted
    And no instance update is made solely to remove an already absent reference

  @scenario-SS-033
  Scenario Outline: Remove every discovered resource using its resource group
    Given the instance custom location contains "<resources>"
    And one discovered resource is in another resource group
    When I run "az iot ops secretsync disable -n aio -g rg -y"
    Then every discovered sync resource is deleted from its own resource group
    And no SecretSync deletion is attempted after SPC deletions have started

    Examples:
      | case      | resources                          |
      | spcs-only | two SPCs and no SecretSyncs         |
      | mixed     | two SPCs and two SecretSyncs        |
      | sync-only | a SecretSync and no SPCs            |

  @scenario-SS-034
  Scenario: Disable an already disabled instance
    Given the instance has no default SPC reference and no associated sync resources
    When I run "az iot ops secretsync disable -n aio -g rg -y"
    Then the command exits successfully without resource writes or deletes
    And a warning says no sync resources were found and includes enablement guidance

  @scenario-SS-035
  Scenario: Clear a stale default association even when discovery finds no sync resources
    Given the instance has a default SPC reference but discovery finds no sync resources
    When I run "az iot ops secretsync disable -n aio -g rg -y"
    Then the instance default SPC reference is removed
    And a warning says no sync resources were found
    And no resource deletion is attempted

  @scenario-SS-036
  Scenario: Stop if the default association cannot be cleared
    Given the instance has a default SPC reference and associated sync resources
    And Azure refuses the instance update
    When I run "az iot ops secretsync disable -n aio -g rg -y"
    Then the command exits unsuccessfully
    And no SecretSync or SPC deletion is attempted

  @scenario-SS-037
  Scenario: Do not delete SPCs when SecretSync deletion fails
    Given the instance has a default SPC reference and associated sync resources
    And a SecretSync deletion fails or reaches a failed terminal state
    When I run "az iot ops secretsync disable -n aio -g rg -y"
    Then the command exits unsuccessfully
    And no SPC deletion is started
    And no restoration of the already removed instance association is claimed

  @scenario-SS-038
  Scenario Outline: Surface discovery or SPC deletion failure after disassociation
    Given the instance has a default SPC reference and associated sync resources
    And "<operation>" fails
    When I run "az iot ops secretsync disable -n aio -g rg -y"
    Then the command exits unsuccessfully with the service error
    And no complete successful disablement or transaction-wide rollback is claimed

    Examples:
      | case      | operation                     |
      | discovery | discovering sync resources    |
      | spc       | deleting a discovered SPC     |

  @scenario-SS-039
  Scenario: Delete resources from every discovery page
    Given the instance custom location has sync resources on multiple Resource Graph pages
    When I run "az iot ops secretsync disable -n aio -g rg -y"
    Then every discovery page is retrieved before any sync resource deletion is started
    And every discovered SecretSync is deleted before any discovered SPC is deleted
