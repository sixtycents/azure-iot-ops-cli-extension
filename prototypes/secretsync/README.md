# Specification-first SecretSync prototype

This is a **separate, experimental Azure CLI extension**, not a change to `azure-iot`.
It implements `enable`, `disable`, `list`, `secret set`, `secret list`, and `secret remove`
under `az iot ops secretsync`. Do not install it alongside `azure-iot-ops` in the same
extension directory: both own those command names.

The contract is [the nine accepted feature files](../../features/secretsync), baseline
`0.1.0`: 85 scenarios, 179 expanded offline cases, and 10 additionally selected live
cases. SS-066 incorporates the separately accepted correction: a forbidden SecretSync
lookup fails during preflight, before any SPC or SecretSync mutation.

## Setup

Run these PowerShell commands from the repository root with Python 3.10 or later:

```powershell
python -m venv .\prototypes\secretsync\.venv
& .\prototypes\secretsync\.venv\Scripts\python.exe -m pip install -e ".\prototypes\secretsync[test]"
```

The test extra includes the pinned, public owning-extension wheel for exercising its
unchanged consumer authentication helpers. It is not registered in the embedded test
CLI. Actual device/dataflow deployment is not part of this prototype.

## White-box developer tool

```powershell
& .\prototypes\secretsync\.venv\Scripts\python.exe -m secretsync_bdd.whitebox --output .\prototypes\secretsync\reports
```

This command runs **only offline tests**, including offline counterparts of every
`@e2e` scenario. No Azure login or deployed environment is needed. It has no test-filter
option: use direct pytest for focused debugging, not qualification.

Each invocation creates a fresh `run-*` directory and prints the HTML report location:

| Artifact | Contents |
| --- | --- |
| `result.html`, `result.json` | Overall status, separate gates, source/spec SHA256 digests, Git revision and dirty state |
| `cases.html`, `cases.json` | Stable scenario IDs, Examples row identities, steps, collected test nodes, outcomes |
| `junit.xml` | Every BDD and supporting unit-test result |
| `coverage-html`, `coverage.json`, `.coverage` | Fresh per-file line coverage and executable missing lines |
| `pytest.stdout.txt`, `pytest.stderr.txt` | Offline diagnostic output |

Qualification exits nonzero for failing tests, missing/duplicate/unbound cases,
unexpected deselection, skips or xfails in required cases, incomplete collection, or
coverage gaps. The official Gherkin parser expands the inventory independently of
pytest. Bindings must match its source path, scenario location and expanded text,
not merely carry the right tag.

**Coverage scope is every Python file beneath `prototypes\secretsync\src`.**
That includes registration, handlers, service behavior, real REST adapters and both
runners. A new production file in that tree is automatically required, even if never
imported. Each file must have zero missing executable lines and no excluded lines;
a rounded total of `100%` is insufficient. Existing `azext_iot`, generated dependencies
and test code are outside this agreed scope. Coverage is not branch coverage and is
not proof that live Azure behavior is correct.

Offline tests exercise actual Azure CLI argument parsing/dispatch and HTTP serialization.
`responses` simulates Azure and the external synchronization controller. Authentication,
configuration, prompts, transport failures and waits are isolated; sockets are disabled.
Tooling unit tests simulate their external subprocess boundary. No live coverage is
merged into offline coverage.

Focused debugging and repository-style lint:

```powershell
& .\prototypes\secretsync\.venv\Scripts\python.exe -m pytest -q -p secretsync_bdd.plugin .\prototypes\secretsync\tests\whitebox --disable-socket
& .\prototypes\secretsync\.venv\Scripts\python.exe -m flake8 .\prototypes\secretsync\src .\prototypes\secretsync\tests --config=setup.cfg
```

## Candidate build and isolation

```powershell
& .\prototypes\secretsync\.venv\Scripts\python.exe -m build --wheel --no-isolation .\prototypes\secretsync
```

The wheel is written to `prototypes\secretsync\dist`. Keep the runners in the editable
repository checkout: their feature and test paths deliberately refer to that checkout.
The live runner installs the candidate wheel into a **fresh private extension directory**;
it does not replace the engineer's normal extensions. DEV/PPE tests verify the candidate
name, version, SHA256, installation path and the actual implementation path loaded by
each command. CLI debug HTTP metadata is inspected in memory; raw live subprocess
output is not persisted.

## Live DEV/PPE tool

Live tests require an explicitly selected, predeployed **dedicated** IoT Operations
instance, working SecretSync controller, OIDC/workload identity, Key Vault and managed
identity. The caller needs configuration, federation, vault-role and source-secret
management permissions. Do not select a shared instance or production environment.

Before running:

1. Authenticate Azure CLI using the intended automation or engineering identity.
2. Set the dedicated instance's `secretsync-bdd-owner` tag to the profile's ownership token.
3. Ensure the instance has **no default SPC reference and no associated sync resources**.
4. Configure `kubectl` with a context named exactly as `cluster_name`, pointing at that
   cluster, with access to the profile's namespace. Use `KUBECONFIG` for a private config.
5. For SS-031, predeploy unrelated sync resources in another custom location in the
   same subscription. They are read and compared, never created, modified or deleted.

Store environment profiles outside source control. Their shape is:

```json
{
  "dev-secretsync": {
    "environment_type": "dev",
    "subscription_id": "<subscription-guid>",
    "resource_group": "<dedicated-resource-group>",
    "instance_name": "<dedicated-instance>",
    "vault_resource_id": "/subscriptions/<guid>/resourceGroups/<rg>/providers/Microsoft.KeyVault/vaults/<vault>",
    "identity_resource_id": "/subscriptions/<guid>/resourceGroups/<rg>/providers/Microsoft.ManagedIdentity/userAssignedIdentities/<identity>",
    "cluster_name": "<connected-cluster-and-kubectl-context-name>",
    "cluster_resource_group": "<cluster-resource-group>",
    "namespace": "<instance-namespace>",
    "ownership_token": "<instance-ownership-tag-value-not-a-credential>",
    "dedicated_instance": true,
    "timeout_seconds": 1800
  }
}
```

`environment_type` is `dev` or `ppe`. Timeout is optional, in `1..7200` seconds, and
bounds each subprocess/reconciliation wait as well as the live pytest process.
Profile files must not contain credentials; other keys are rejected.

```powershell
& .\prototypes\secretsync\.venv\Scripts\python.exe -m secretsync_bdd.e2e `
  --profiles C:\private\secretsync-environments.json `
  --environment dev-secretsync `
  --candidate .\prototypes\secretsync\dist\iot_secretsync_prototype-0.1.0-py3-none-any.whl `
  --output .\prototypes\secretsync\reports `
  --allow-live --allow-destructive
```

Both opt-ins are mandatory. The runner selects inherited/direct `@e2e` tags from the
same accepted features, with separate fixtures/process/plugin configuration and **no
mocking, recorded responses or fake credentials**. The live path uses real candidate
CLI subprocesses, `az rest`, Key Vault and `kubectl`. It checks actual materialization,
preservation, deletion and rotation using bounded waits and redacted comparisons.
Unavailable prerequisites fail/block the run, never skip it into a pass.

Live reports contain result/case HTML and JSON plus JUnit (with each case's owned
resource prefix), but no coverage or raw
subprocess logs. Secret values are generated per run, passed through temporary files,
compared in memory and never included in assertion diagnostics. Cleanup removes only
tracked run-owned sync resources, source secrets and newly created access resources;
it never deletes the predeployed instance, cluster, vault or identity. Cleanup failures
fail qualification. Key Vault soft-delete retention still applies.

Do not terminate a live job casually: a killed process cannot guarantee cleanup. Use
the reported target and `bdd-*` resource prefix to investigate interrupted runs before
reusing the dedicated instance. A CI integration should reserve the environment and
provide an ownership-aware recovery job.

## Engineer workflow and approval boundary

Use the repository's `bdd-specify`, `bdd-implement` and `bdd-verify` skills. Start with
human-reviewed features; keep stable `@scenario-SS-*` IDs and a unique `case` per
Examples row. Every case needs meaningful offline assertions. Add `@e2e` only when
additional real-environment execution is intended, then add its separate live binding.

Implement production behavior inside the scoped `src` tree. Use focused unit tests for
internal branches, not coverage exclusions. If implementation reveals a new observable
behavior, propose the feature change and obtain human acceptance before implementing
it. Passing the local tools does not grant human approval.

These are local tools ready for later pipeline invocation, **not installed repository
branch protections or a CI approval policy**. No live pass is implied by implemented
bindings, collection or successful offline qualification.
