# Sources: remove_secretsync_secret, test_secretsync_secret_remove*, and multikey integration
# tests at the pinned revision. Public CLI help documents deletion of the last mapping.
# Pagination is implemented by azext_edge/edge/util/resource_graph.py at the same revision.
# Reference checks happen before the target write to avoid counting the target's stale ARG record.
# This is not a guarantee of ARG freshness or atomic protection against concurrent writers.
@secretsync
Feature: Remove a source secret from a SecretSync without breaking other consumers
  Removal affects every mapping for a sourcePath in the named SecretSync, never the source in Key Vault.
  Shared SPC cleanup depends on other SecretSyncs in the same custom location using the same SPC.

  Background:
    Given instance "aio" exists in resource group "rg" on custom location "cl-a"
    And SecretSync "creds" belongs to "cl-a" and references SPC "spc-a"
    And SPC "spc-a" has valid objects YAML containing the mapped sources

  @scenario-SS-080
  Scenario Outline: Confirm removal before accessing or changing resources
    Given SecretSync "creds" maps "kv-pass" to "password"
    When I remove source "kv-pass" with "<confirmation>"
    Then prompt behavior is "<prompt>"
    And resource access behavior is "<access>"

    Examples:
      | case    | confirmation       | prompt        | access                             |
      | decline | a declined prompt  | prompted once | no Azure reads or writes           |
      | accept  | an accepted prompt | prompted once | perform the confirmed removal      |
      | short   | -y                 | not prompted  | perform the confirmed removal      |
      | long    | --yes              | not prompted  | perform the confirmed removal      |

  @scenario-SS-081 @e2e
  Scenario: Remove one source and preserve the remaining Kubernetes key
    Given run-owned SecretSync "creds" maps "kv-user" to "username" and "kv-pass" to "password"
    And its Kubernetes secret has reconciled both mappings
    And no other SecretSync references "kv-pass" through "spc-a"
    When I run "az iot ops secretsync secret remove -n aio -g rg --secret-sync-name creds --secret-name kv-pass -y"
    Then the command returns the updated SecretSync with only "kv-user=username"
    And the SPC no longer includes "kv-pass" but retains "kv-user" and unrelated object settings
    And within the reconciliation timeout Kubernetes secret "creds" retains "username" with its original bytes and has no "password" key
    And Key Vault secret "kv-pass" and all its versions remain unchanged

  @scenario-SS-082 @e2e
  Scenario: Delete a SecretSync when the removed source owns its last mappings
    Given run-owned SecretSync "creds" maps only "kv-pass" to "password"
    And its Kubernetes secret has reconciled that mapping with standard controller ownership
    And no other SecretSync references "kv-pass" through "spc-a"
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then the SecretSync ARM resource is deleted instead of written with an empty mapping array
    And the command exits successfully without a resource result body
    And the final "kv-pass" object is removed from the SPC
    And an SPC with no remaining objects has no objects property
    And within the reconciliation timeout the SecretSync custom resource and its controller-owned Kubernetes secret are absent
    And the Key Vault source secret still exists

  @scenario-SS-083 @e2e
  Scenario: Preserve a source shared with an operations-experience consumer
    Given run-owned SecretSync "creds" maps only "kv-pass" to "password"
    And another run-owned SecretSync in "cl-a" created through the operations-experience resource contract references "kv-pass" through "spc-a"
    And both Kubernetes secrets have reconciled
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then SecretSync "creds" is deleted
    And the shared SPC object for "kv-pass" remains unchanged
    And the other SecretSync and its Kubernetes secret remain functional
    And a new run-owned Key Vault version subsequently reconciles to the other consumer within the rotation timeout
    And no operations-experience UI automation or mocked service is required for this resource-level compatibility check

  @scenario-SS-084
  Scenario: Remove every target mapping for the selected source
    Given SecretSync "creds" maps "kv-pass" to "password" and "password-copy"
    And it also maps "kv-user" to "username"
    And no other SecretSync references "kv-pass" through "spc-a"
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then both "kv-pass" mappings are removed
    And the returned SecretSync retains "kv-user=username"
    And no Key Vault deletion is attempted

  @scenario-SS-085
  Scenario Outline: Count only other consumers of the same source and SPC in the custom location
    Given SecretSync "creds" maps "kv-pass" to "password" and "kv-user" to "username"
    And the only other source reference is "<other-reference>"
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then shared SPC object "kv-pass" is "<spc-outcome>"
    And all other SecretSyncs are unchanged

    Examples:
      | case          | other-reference                                                    | spc-outcome |
      | same-spc      | another SecretSync in cl-a using spc-a and source kv-pass           | retained    |
      | other-key     | another SecretSync in cl-a using spc-a and source kv-pass under another key | retained |
      | different-spc | another SecretSync in cl-a using spc-b and source kv-pass           | removed     |
      | different-cl  | another SecretSync in cl-b using its own spc-a and source kv-pass   | removed     |
      | other-source  | another SecretSync in cl-a using spc-a and source kv-unrelated      | removed     |

  @scenario-SS-086
  Scenario: Do not count the target's stale Resource Graph record as another consumer
    Given SecretSync "creds" maps only "kv-pass" to "password"
    And Resource Graph returns "creds" with its pre-removal mapping and no other consumers
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then reference discovery occurs before the SecretSync is mutated
    And the target SecretSync is excluded from the reference count
    And the SecretSync is deleted before its unshared SPC object is removed

  @scenario-SS-087
  Scenario: Fail safely when the reference discovery query fails
    Given SecretSync "creds" maps "kv-pass" to "password"
    And discovering other consumers fails
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then the command exits unsuccessfully with the discovery error
    And neither the target SecretSync nor the SPC is changed

  @scenario-SS-088
  Scenario: Explain a source that is not mapped in the named SecretSync
    Given SecretSync "creds" maps only "kv-user" to "username"
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then the command exits unsuccessfully identifying source "kv-pass" and SecretSync "creds"
    And no reference discovery query, resource write, or resource deletion is made

  @scenario-SS-089
  Scenario: Do not infer Key Vault access is required for removal
    Given SecretSync "creds" maps "kv-pass" to "password"
    And the caller can manage sync resources but cannot read or delete Key Vault secrets
    And no other SecretSync references "kv-pass" through "spc-a"
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then removal succeeds without any Key Vault data-plane request

  @scenario-SS-090
  Scenario Outline: Use the target SecretSync's SPC rather than the instance default
    Given the instance default SPC association is "<default-state>"
    And SecretSync "creds" references nondefault SPC "spc-a" and maps "kv-pass" to "password"
    And no other SecretSync references "kv-pass" through "spc-a"
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then cleanup targets only SPC "spc-a"
    And it does not require or modify the instance default SPC association

    Examples:
      | case      | default-state                 |
      | different | configured to spc-default     |
      | absent    | not configured                |

  @scenario-SS-091
  Scenario Outline: Avoid unnecessary SPC writes when its object is already absent
    Given SecretSync "creds" maps "kv-pass" to "password"
    And no other SecretSync references "kv-pass" through "spc-a"
    And the SPC objects state is "<state>"
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then the target SecretSync is deleted
    And no SPC update is made

    Examples:
      | case       | state                                |
      | no-objects | no objects property                  |
      | empty-text | an empty objects string              |
      | other-only | valid objects YAML without kv-pass   |

  @scenario-SS-092
  Scenario Outline: Do not clean the SPC after a failed target mutation
    Given SecretSync "creds" maps "<mappings>"
    And no other SecretSync references "kv-pass" through "spc-a"
    And the target SecretSync "<operation>" fails
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then the command exits unsuccessfully with the target service error
    And the SPC object list is not updated

    Examples:
      | case    | mappings                              | operation |
      | partial | kv-pass=password, kv-user=username    | update    |
      | final   | kv-pass=password                      | delete    |

  @scenario-SS-093
  Scenario Outline: Report a failed SPC cleanup after a completed target mutation
    Given SecretSync "creds" maps "kv-pass" to "password"
    And no other SecretSync references "kv-pass" through "spc-a"
    And target deletion succeeds but SPC "<operation>" fails
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then the command exits unsuccessfully with the SPC error
    And it does not claim a completed removal or restoration of the deleted SecretSync
    And no Key Vault secret is deleted

    Examples:
      | case  | operation |
      | read  | read      |
      | write | update    |

  @scenario-SS-094
  Scenario Outline: Surface a missing or inaccessible target before any mutation
    Given reading SecretSync "creds" fails with "<failure>"
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then the command exits unsuccessfully with that failure
    And no SecretSync or SPC write or deletion is made

    Examples:
      | case      | failure            |
      | missing   | resource not found |
      | forbidden | access denied      |

  @scenario-SS-114
  Scenario: Preserve a shared SPC object referenced on a later discovery page
    Given source "kv-pass" is referenced by multiple SecretSyncs in "cl-a" using "spc-a"
    And reference discovery has more than one result page
    And an additional consumer appears only on a later page
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then all discovery pages are consumed before deciding whether the SPC object is unshared
    And the shared SPC object is retained

  @scenario-SS-117
  Scenario: Abort removal when a later reference-discovery page cannot be retrieved
    Given source "kv-pass" is mapped in SecretSync "creds"
    And the first reference-discovery page does not include another consumer
    And fetching the next required page fails
    When I remove source "kv-pass" from SecretSync "creds" with "--yes"
    Then the command exits unsuccessfully with the discovery failure
    And neither the target SecretSync nor the SPC is changed
