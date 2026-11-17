# Sources: enable_secretsync, _attempt_keyvault_role_assignments, _ensure_oidc_issuer,
# federation helpers, and test_secretsync_enable* at the revision in command_contract.feature.
# Also: public "Enable secure settings" and "az iot ops secretsync enable" references.
@secretsync
Feature: Enable instance secret synchronization
  Enablement configures managed identity federation, Key Vault access, and a default SPC.
  It does not create missing vaults, identities, or user secret values.

  Background:
    Given instance "aio" exists in resource group "rg" on custom location "cl-a"
    And its connected cluster and namespace are discoverable
    And Key Vault "vault-a" and user-assigned managed identity "sync-mi" exist
    And the cluster has OIDC issuer and workload identity enabled with a usable issuer URL

  @scenario-SS-010 @e2e
  Scenario: Enable synchronization and establish a working identity path
    Given the instance has no associated sync resources
    And a run-owned Key Vault secret "probe" already exists
    When I enable secret sync for "aio" using "sync-mi" and "vault-a"
    Then the command returns the created default SPC
    And the SPC uses the identity client ID, vault name, tenant ID, cluster region, and instance custom location
    And the managed identity has Key Vault Reader and Key Vault Secrets User at the vault scope
    And a federated credential allows the SecretSync service account in the instance namespace
    And its audience is "api://AzureADTokenExchange"
    And the instance defaultSecretProviderClassRef identifies the created SPC
    When I set mapping "probe=password" on run-owned SecretSync "probe-sync"
    Then within the reconciliation timeout Kubernetes secret "probe-sync" contains the Key Vault bytes under "password"
    And the secret bytes are not included in command output or test diagnostics

  @scenario-SS-011
  Scenario Outline: Select the default SPC name or an explicit name and creation tags
    Given the instance has no associated sync resources
    When I enable secret sync with "<spc-option>" and "<tags-option>"
    Then the created SPC name satisfies "<name-rule>"
    And its tags satisfy "<tags-rule>"
    And the instance references that SPC

    Examples:
      | case           | spc-option        | tags-option                | name-rule                         | tags-rule                  |
      | generated      | none              | none                       | deterministic spc-ops-{hash} name | no user tags required      |
      | explicit       | --spc custom-spc  | --tags team=cli stage=dev  | exactly custom-spc                | team=cli and stage=dev     |

  @scenario-SS-012
  Scenario Outline: Honor role assignment options
    Given the instance has no associated sync resources
    And the custom role ID is a valid fully qualified role definition ID
    When I enable secret sync with "<role-options>"
    Then vault role assignment behavior is "<expected>"
    And enablement still configures federation and the default SPC

    Examples:
      | case        | role-options                         | expected                                 |
      | defaults    | none                                 | ensure both documented built-in roles    |
      | custom      | --custom-role-id {custom-role-id}     | ensure only the supplied custom role     |
      | skip        | --skip-ra                            | no role assignment reads or writes       |
      | skip-custom | --skip-ra --custom-role-id {custom-role-id} | no role assignment reads or writes |

  @scenario-SS-013
  Scenario: Reuse an existing matching federated identity credential
    Given the instance has no associated sync resources
    And the identity already has a credential with the selected issuer, SecretSync subject, and token audience
    When I enable secret sync for "aio" using "sync-mi" and "vault-a"
    Then no duplicate federated credential is created
    And a default SPC is created and associated with the instance

  @scenario-SS-014
  Scenario: Use the self-hosted issuer when requested
    Given the instance has no associated sync resources
    And the cluster advertises a usable selfHostedIssuerUrl
    When I enable secret sync with "--self-hosted-issuer"
    Then federation uses the selected self-hosted issuer after issuer discovery
    And it does not silently select the system issuer instead

  @scenario-SS-015
  Scenario Outline: Explain missing cluster identity prerequisites
    Given the cluster prerequisite state is "<state>"
    When I enable secret sync with "--skip-ra" and issuer option "<issuer-option>"
    Then the command exits unsuccessfully
    And the diagnostic identifies "<problem>" and the applicable cluster configuration guidance
    And no federation, SPC creation, or default reference update is performed

    Examples:
      | case       | state                                  | issuer-option        | problem                      |
      | oidc       | OIDC disabled                          | none                 | OIDC issuer                  |
      | workload   | workload identity disabled             | none                 | workload identity federation |
      | both       | OIDC and workload identity disabled    | none                 | both disabled prerequisites  |
      | system-url | selected system issuer URL absent      | none                 | missing issuerUrl            |
      | self-url   | selected self-hosted issuer URL absent | --self-hosted-issuer | missing selfHostedIssuerUrl  |

  @scenario-SS-016
  Scenario: Stop enablement when a required vault role assignment fails
    Given the instance has no associated sync resources
    And Azure refuses a required vault role assignment
    When I enable secret sync without "--skip-ra"
    Then the command exits unsuccessfully
    And the diagnostic identifies the assignment failure, identity principal, and vault scope
    And no federation, SPC creation, or default reference update is performed
    And no successful rollback of earlier role assignments is claimed

  @scenario-SS-017
  Scenario: Preserve existing sync resources on repeated enablement
    Given the custom location already contains a populated SPC and SecretSync
    And no issuer repair is necessary
    When I enable secret sync for "aio" using "sync-mi" and "vault-a"
    Then a warning says associated secret sync resources already exist and points to the list command
    And existing SPCs, mappings, and the instance default reference are not rewritten
    And no new federated credential is created
    And the command does not claim that existing mappings were replaced

  @scenario-SS-018
  Scenario: Repair a verified issuer mismatch without replacing existing resources
    Given the instance already has sync resources
    And discovery verifies an issuer differing only by a trailing slash from a matching SecretSync credential
    And no exact matching credential already exists
    When I enable secret sync for "aio" using "sync-mi" and "vault-a"
    Then that credential is updated to the verified issuer while preserving its name, subject, and audience
    And existing SPCs, SecretSync mappings, and the instance default reference are not rewritten

  @scenario-SS-019
  Scenario Outline: Avoid speculative or unrelated credential repair
    Given the instance already has sync resources
    And the credential discovery condition is "<condition>"
    When I enable secret sync for "aio" using "sync-mi" and "vault-a"
    Then no federated credential is created or updated
    And existing sync resources remain unchanged

    Examples:
      | case       | condition                                             |
      | exact      | an exact issuer, subject, and audience match exists    |
      | preferred  | both an exact match and a slash-mismatched match exist |
      | unrelated  | only a credential for another subject exists          |
      | unverified | issuer discovery cannot verify the ARM issuer         |

  @scenario-SS-020
  Scenario Outline: Surface a credential repair failure
    Given the instance already has sync resources
    And a verified issuer repair is needed
    And Azure refuses the credential "<operation>" operation
    When I enable secret sync for "aio" using "sync-mi" and "vault-a"
    Then the command exits unsuccessfully with actionable credential repair guidance
    And existing SPCs and SecretSync mappings are not overwritten

    Examples:
      | case   | operation |
      | read   | list      |
      | write  | update    |

  @scenario-SS-021
  Scenario Outline: Stop at a failed enablement stage
    Given the instance has no associated sync resources
    And enablement fails at "<stage>"
    When I enable secret sync for "aio" using "sync-mi" and "vault-a"
    Then the command exits unsuccessfully with the service error
    And "<later-effect>" is not performed
    And no transaction-wide rollback is claimed

    Examples:
      | case      | stage                         | later-effect                    |
      | vault     | reading the vault             | federation or SPC creation      |
      | identity  | reading the identity          | federation or SPC creation      |
      | federation| writing federation            | SPC creation                    |
      | spc       | waiting for SPC creation      | setting the instance SPC reference |
      | instance  | updating the instance reference | reporting completed enablement |
