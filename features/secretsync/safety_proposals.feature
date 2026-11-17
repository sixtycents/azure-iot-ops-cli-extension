# REVIEWED ADDITIONS in specification baseline 0.1.0: not claims about the pinned implementation.
# These gaps were identified while reconciling resource isolation, additive merge, and shared-SPC safety.
# Human review accepted all six safeguards; every scenario requires offline execution.
# Further behavioral changes require renewed acceptance before implementation.
# Concurrent create/remove atomicity across ARG and ARM is NOT promised here; a broader concurrency
# design and ownership decision would be required before making that guarantee.
@secretsync
Feature: Reviewed safeguards for shared SecretSync configuration
  These are explicitly accepted additions beyond compatibility with the pinned implementation.
  Additional requirements must not be silently introduced to satisfy a coverage threshold.

  @scenario-SS-110
  Scenario Outline: Reject a named resource outside the instance boundary
    Given instance "aio" uses custom location "cl-a" and default SPC "spc-a"
    And the named SecretSync "creds" belongs to "<ownership>"
    When I invoke "az iot ops secretsync secret <command>" for "aio" and "creds" with otherwise valid arguments
    Then the command exits unsuccessfully with a resource ownership mismatch diagnostic
    And no SecretSync or SPC is mutated
    And no other instance's mappings are returned

    Examples:
      | case          | command | ownership                           |
      | set-location  | set     | custom location cl-b                |
      | list-location | list    | custom location cl-b                |
      | remove-location | remove | custom location cl-b               |
      | set-spc       | set     | cl-a but a different SPC than spc-a |

  @scenario-SS-111
  Scenario Outline: Reject ambiguous target keys before making any change
    Given the default SPC and requested Key Vault sources exist
    And the mapping conflict is "<conflict>"
    When I set the requested mappings on SecretSync "creds"
    Then the command exits unsuccessfully identifying the duplicate target key and conflicting sources
    And neither the SPC nor the SecretSync is changed

    Examples:
      | case     | conflict                                                           |
      | in-call  | two different requested sources both use targetKey password         |
      | existing | a requested source uses password already mapped from another source |

  @scenario-SS-112
  Scenario: Deduplicate identical pairs while creating a new SecretSync
    Given the default SPC and Key Vault secret "kv-pass" exist
    And SecretSync "creds" does not exist
    When I set mappings "kv-pass=password" and "kv-pass=password" in the same invocation
    Then the new SecretSync contains exactly one "kv-pass=password" mapping
    And the SPC contains exactly one "kv-pass" object

  @scenario-SS-113
  Scenario Outline: Reject malformed SPC objects without replacing configuration
    Given the default SPC has objects containing "<invalid-state>"
    And the requested source exists
    When I set mapping "kv-pass=password" on SecretSync "creds"
    Then the command exits unsuccessfully with a diagnostic identifying invalid SPC objects
    And neither the SPC nor the SecretSync is changed

    Examples:
      | case       | invalid-state                         |
      | syntax     | invalid YAML syntax                   |
      | root       | a scalar rather than an objects map   |
      | array      | a non-array value for array            |
      | entry      | a non-map decoded object entry        |

  @scenario-SS-115
  Scenario: Distinguish same-named resources by full ARM identity during reference counting
    Given two SecretSyncs in different resource groups share a name, custom location, SPC, and source
    When I remove the source from one of them with "--yes"
    Then only the targeted full ARM resource ID is excluded from the reference count
    And the SPC object is preserved for the other SecretSync

  @scenario-SS-116
  Scenario: Report completed work when a later mutation fails
    Given setting a mapping successfully updates the SPC
    And the subsequent SecretSync write fails
    When the set command reports that failure
    Then its diagnostic identifies the completed SPC update and the failed SecretSync stage
    And it gives retry or inspection guidance without claiming rollback
    And no secret bytes are disclosed
