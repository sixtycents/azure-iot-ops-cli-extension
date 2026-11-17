# Sources: set_secretsync_secret and test_secretsync_secret_set* at the pinned revision;
# test_secretsync_secret_set_multikey_mapping and the supplied design's additive merge requirement.
# Public CLI help's "target key updated" sentence conflicts with the code and is not the baseline.
@secretsync
Feature: Set Key Vault mappings on a named SecretSync
  Setting mappings preserves distinct existing sourcePath and targetKey pairs.
  Source secrets must already exist in the default SPC's vault.
  Completion of the ARM command is not a guarantee of completed Kubernetes reconciliation.
  Idempotency concerns resulting configuration, not the number of ARM update requests.

  Background:
    Given instance "aio" exists in resource group "rg" on custom location "cl-a"
    And its defaultSecretProviderClassRef resolves to SPC "spc-a" for Key Vault "vault-a"
    And the SPC has valid connection properties and an empty or valid objects YAML array

  @scenario-SS-050 @e2e
  Scenario: Create one Opaque Kubernetes secret from multiple Key Vault secrets
    Given run-owned Key Vault secrets "kv-user" and "kv-pass" already exist
    And SecretSync "creds" does not exist
    When I run "az iot ops secretsync secret set -n aio -g rg --secret-sync-name creds --secret-map kv-user=username --secret-map kv-pass=password"
    Then both source secrets are verified before the first SPC or SecretSync write
    And the SPC contains each source exactly once as objectType "secret"
    And the command returns SecretSync "creds" with both sourcePath and targetKey mappings
    And its secretProviderClassName is "spc-a"
    And its kubernetesSecretType is "Opaque"
    And it uses the SecretSync controller service account and the SPC location and extendedLocation
    And within the reconciliation timeout Kubernetes secret "creds" contains the source bytes under "username" and "password"
    And command output contains mappings but not secret values

  @scenario-SS-051 @e2e
  Scenario: Extend a SecretSync without disrupting an existing mapping
    Given run-owned Key Vault secrets "kv-cert" and "kv-key" already exist
    And SecretSync "tls" already maps "kv-cert" to "certificate" through "spc-a"
    And Kubernetes secret "tls" has reconciled the existing mapping
    When I set mapping "kv-key=privateKey" on SecretSync "tls"
    Then the returned SecretSync contains both mappings
    And existing SecretSync properties unrelated to mappings are preserved
    And existing SPC connection properties and unrelated object entries are preserved
    And within the reconciliation timeout Kubernetes secret "tls" contains both original and newly mapped bytes

  @scenario-SS-052
  Scenario: Repeating an existing source and target pair is state-idempotent
    Given Key Vault secret "kv-pass" exists
    And the SPC already contains "kv-pass" with additional object settings
    And SecretSync "creds" already maps "kv-pass" to "password"
    When I set mapping "kv-pass=password" on SecretSync "creds" twice
    Then that mapping appears exactly once
    And "kv-pass" appears exactly once in the SPC objects
    And its additional object settings remain unchanged

  @scenario-SS-053 @e2e
  Scenario: Map one source to two target keys without replacing the first key
    Given run-owned Key Vault secret "kv-cert" exists
    And SecretSync "tls" already maps "kv-cert" to "certificate" through "spc-a"
    When I set mapping "kv-cert=cert-copy" on SecretSync "tls"
    Then the result contains both "kv-cert=certificate" and "kv-cert=cert-copy"
    And the SPC still contains only one object entry for "kv-cert"
    And within the reconciliation timeout both Kubernetes keys contain the same expected source bytes

  @scenario-SS-054
  Scenario Outline: Reject incomplete mapping syntax without writing resources
    Given the default SPC is readable
    When I set mapping "<mapping>" on SecretSync "creds"
    Then the command exits unsuccessfully
    And the diagnostic identifies the invalid "--secret-map" and the required source=target format
    And no Key Vault value is read and no SPC or SecretSync write is made

    Examples:
      | case          | mapping       |
      | no-equals     | kv-pass       |
      | empty-source  | =password     |
      | empty-target  | kv-pass=      |
      | both-empty    | =             |

  @scenario-SS-055
  Scenario: Require enabled synchronization
    Given the instance has no defaultSecretProviderClassRef
    When I set mapping "kv-pass=password" on SecretSync "creds"
    Then the command exits unsuccessfully with secret sync enablement guidance
    And no Key Vault value is read and no SPC or SecretSync write is made

  @scenario-SS-056
  Scenario Outline: Fail when the referenced default SPC cannot be read
    Given reading the referenced default SPC fails with "<failure>"
    When I set mapping "kv-pass=password" on SecretSync "creds"
    Then the command exits unsuccessfully with the SPC service error
    And no Key Vault value is read and no SPC or SecretSync write is made

    Examples:
      | case    | failure            |
      | missing | resource not found |
      | denied  | access denied      |

  @scenario-SS-057
  Scenario: Discover a vault across subscriptions using its authoritative URI
    Given instance "aio" is in subscription "sub-a"
    And Key Vault "vault-a" is in accessible subscription "sub-b" with its cloud-specific vaultUri
    And Key Vault secret "kv-pass" exists
    When I set mapping "kv-pass=password" on SecretSync "creds"
    Then vault discovery is not restricted to subscription "sub-a"
    And verification uses the discovered vaultUri and the active cloud's Key Vault audience
    And the data-plane client is selected for subscription "sub-b"
    And the command does not construct a public-cloud vault suffix

  @scenario-SS-058
  Scenario: Explain a vault that cannot be discovered
    Given vault discovery returns no accessible match for "vault-a"
    When I set mapping "kv-pass=password" on SecretSync "creds"
    Then the command exits unsuccessfully with vault existence and accessibility guidance
    And no SPC or SecretSync write is made

  @scenario-SS-059
  Scenario: Validate every requested source before applying any mapping
    Given Key Vault secret "kv-user" exists but "kv-missing" does not
    When I set mappings "kv-user=username" and "kv-missing=password" on SecretSync "creds"
    Then the command exits unsuccessfully identifying the missing secret and vault
    And no SPC or SecretSync write is made
    And no missing Key Vault secret is created

  @scenario-SS-060
  Scenario Outline: Preserve access and connectivity errors from secret verification
    Given reading Key Vault secret "kv-pass" fails with "<failure>"
    When I set mapping "kv-pass=password" on SecretSync "creds"
    Then the command exits unsuccessfully with the verification failure
    And it does not misreport the secret as nonexistent
    And no SPC or SecretSync write is made

    Examples:
      | case        | failure                            |
      | forbidden   | an authorization denial            |
      | unavailable | an exhausted service-unavailable retry |
      | transport   | an exhausted transport failure     |

  @scenario-SS-061
  Scenario Outline: Honor secret file-encoding metadata for new SPC objects
    Given Key Vault secret "kv-cert" has "<tag-state>" and content type "<content-type>"
    And "kv-cert" is not yet an SPC object
    When I set mapping "kv-cert=certificate" on SecretSync "tls"
    Then the new SPC object has "<encoding>"
    And the command does not copy secret values into the ARM resource

    Examples:
      | case          | tag-state             | content-type           | encoding                   |
      | hex           | file-encoding=hex     | application/pkix-cert  | objectEncoding set to hex  |
      | upper-hex     | file-encoding=HEX     | application/x-pem-file | objectEncoding set to hex  |
      | no-tag        | no tags               | application/pkix-cert  | no objectEncoding property |
      | unrelated     | purpose=tls           | application/x-pem-file | no objectEncoding property |
      | non-hex       | file-encoding=base64  | application/pkix-cert  | no objectEncoding property |

  @scenario-SS-062
  Scenario: Preserve objects contributed by the operations experience
    Given the SPC contains operations-experience objects with version and encoding settings
    And Key Vault secret "kv-pass" exists and is not yet in the SPC
    When I set mapping "kv-pass=password" on SecretSync "creds"
    Then the original object entries and their settings remain semantically unchanged
    And one new "kv-pass" object of type "secret" is added
    And the objects property remains valid SPC objects YAML

  @scenario-SS-063
  Scenario: Use the latest Key Vault secret version without creating a new version
    Given Key Vault secret "kv-pass" has more than one version
    And "kv-pass" is not yet an SPC object
    When I set mapping "kv-pass=password" on SecretSync "creds"
    Then source verification requests the latest version
    And the SPC object does not pin an objectVersion
    And no Key Vault secret value or version is created, updated, or deleted

  @scenario-SS-064
  Scenario: Leave consumer-specific key naming to the caller
    Given Key Vault secret "kv-pass" exists
    When I set mapping "kv-pass=custom.key" on SecretSync "creds"
    Then the resulting mapping uses targetKey "custom.key"
    And the CLI does not require a fixed credential-key vocabulary or create a consumer

  @scenario-SS-065
  Scenario Outline: Stop after an SPC write or polling failure
    Given Key Vault secret "kv-pass" exists
    And the SPC update "<failure>"
    When I set mapping "kv-pass=password" on SecretSync "creds"
    Then the command exits unsuccessfully
    And no SecretSync create or update is attempted

    Examples:
      | case     | failure                      |
      | rejected | is rejected by ARM           |
      | poller   | reaches a failed terminal state |

  @scenario-SS-066
  Scenario: Do not treat a forbidden SecretSync lookup as a create opportunity
    Given Key Vault secret "kv-pass" exists
    And the SPC update succeeds
    And reading SecretSync "creds" is forbidden
    When I set mapping "kv-pass=password" on SecretSync "creds"
    Then the command exits unsuccessfully with the lookup error
    And no SecretSync create or update is attempted
    And no rollback of the completed SPC update is claimed

  @scenario-SS-067
  Scenario: Surface failure after an SPC update without claiming atomicity
    Given Key Vault secret "kv-pass" exists
    And the SPC update succeeds but the SecretSync write fails
    When I set mapping "kv-pass=password" on SecretSync "creds"
    Then the command exits unsuccessfully with the SecretSync service error
    And no successful mapping result or automatic SPC rollback is claimed

  @scenario-SS-068
  Scenario: Retry after a completed SPC update and failed SecretSync creation
    Given an earlier set attempt added "kv-pass" to the SPC but failed to create SecretSync "creds"
    And Key Vault secret "kv-pass" exists and all required services are now available
    When I set mapping "kv-pass=password" on SecretSync "creds"
    Then SecretSync "creds" is created with exactly that mapping
    And the SPC still contains exactly one object for "kv-pass"
    And unrelated SPC objects remain unchanged
