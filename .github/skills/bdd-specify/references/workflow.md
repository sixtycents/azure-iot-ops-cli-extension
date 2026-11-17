# Shared BDD workflow contract

Read this contract when using any of the BDD skills. It defines requirements for future implementation;
the presence of these instructions alone does not install tooling or enforce a CI gate.

## Specification and approval

- Feature files are the human-reviewed behavioral contract. Existing code, tests, and service documentation
  inform proposals but do not authorize new behavior.
- Before implementation, a human must explicitly accept the exact feature revision and first-party coverage
  scope. Identify the revision with a commit plus a clean feature diff, or a deterministic digest of the
  sorted feature paths and contents and the coverage inventory when files are uncommitted.
- Record what was accepted and the human's explicit response in the session or an existing review system.
  Never invent acceptance, impersonate a reviewer, or use an agent-editable `approved: true` flag as proof.
- If an uncovered edge case changes observable behavior, propose a feature diff and obtain renewed acceptance
  before implementing that behavior. Unaffected accepted work may continue. A proposed revision is not accepted
  merely because it would make tests or coverage pass.
- Internal refactoring and focused tests within the accepted contract do not require new behavioral features.
- For team enforcement, propose CODEOWNERS and protected-branch review rules that invalidate stale approval,
  or a trusted check binding human acceptance to the exact specification and coverage-scope revision.
  Skills and local records are not a security boundary. Do not claim enforcement unless it is configured.

## Gherkin and scenario identity

- Use `pytest-bdd` with the repository's pytest conventions. Do not introduce a second BDD runner.
- Give each Scenario or Scenario Outline a globally unique, stable tag such as `@scenario-SS-001`.
  Keep the identifier when wording or file location changes; never reuse it for different behavior.
- Use concrete Given/When/Then outcomes. Scenario Outlines must have nonempty Examples; each example row is
  a separate required execution. Use an explicit unique case column where it helps distinguish examples.
- `@e2e` on a Feature selects all its scenarios; `@e2e` on a scenario selects that scenario.
  Start with feature/scenario tagging, not example-level tagging. Expand every example of a selected outline.
- All scenarios, including those tagged `@e2e`, must run in the offline white-box suite.
  E2E tags select additional live execution, never a replacement for offline execution.
- Build one canonical inventory from all in-scope feature files, independent of pytest collection filters.
  Use the Gherkin parser or pytest-bdd's supported parser interface rather than regular-expression parsing.
  Assign each outline row an execution identity using its scenario ID, Examples block, and row location.
  Row identities may change with the accepted revision; scenario IDs remain stable.
- Bind scenarios explicitly or use carefully scoped automatic bindings. Verify the resulting collected
  executions against the inventory, including outline rows; do not assume bindings cover every feature.

## White-box execution

- Exercise real command parsing, first-party logic, and SDK serialization where applicable. Mock external
  boundaries, not the behavior under test or the entire provider implementing the command.
- Prefer existing `responses` HTTP interception and Azure SDK fake transports over a separate fake server.
  Match the actual transport used; a requests interceptor alone does not isolate every SDK or subprocess.
- Assert relevant HTTP methods, URLs, API versions, bodies, response handling, and side-effect sequencing.
  Include accepted error and polling behavior rather than success-only fixtures.
- Isolate credentials, Azure CLI configuration, environment variables, filesystem state, subprocesses,
  clocks, sleeps, randomness, and discovery when they could introduce external instability.
  Use temporary directories and deterministic boundary fakes without masking first-party defects.
- Deny unexpected outbound network access, for example with `pytest-socket`. In-process socket guards do
  not protect child processes: fake external child-process boundaries and use credential-free,
  network-restricted CI for stronger isolation. Never permit unrecognized requests to reach Azure.
- A dedicated acceptance run must match the complete expected inventory. Fail on missing or duplicate
  bindings, undefined steps, unexpected deselection, skips, xfails, empty collection, or failed executions.
  Focused developer runs are useful but cannot qualify the complete suite.

## Coverage

- Require zero missing executable lines in every file of the accepted first-party production inventory.
  This is 100% line coverage, not branch coverage and not 100% of the entire existing repository.
- Include all owned production files, including unimported files, and directly dependent changed first-party
  logic. Check source discovery against the inventory so new files cannot escape measurement.
- Exclude tests, third-party packages, and generated SDKs. Do not narrow the inventory, add no-cover pragmas,
  alter coverage exclusions, or remove required behavior simply to reach the threshold.
- Use `pytest-cov` / `coverage.py` with explicit source scope and a fresh dedicated coverage file.
  Do not append existing coverage or combine live-test coverage with the white-box result.
- Enforce a 100% threshold and inspect missing-line counts, not a rounded percentage. A passing result requires
  every expected file to be represented with zero missing executable lines.
- Scenario completeness, assertions about behavior, and line coverage are separate gates.
  Add focused unit tests for internal paths; if a gap exposes a new observable requirement, request acceptance.

## Live E2E execution

- Use the same accepted feature files with separate live step definitions and fixtures. Share only helpers
  that cannot introduce fake behavior, such as pure assertions and scenario inventory parsing.
- Run the installed candidate extension through real `az` subprocesses. Record its artifact/revision and
  verify the loaded extension path/version so another local installation cannot satisfy the test.
- Use a separate test root and process outside the offline fixture tree. Audit inherited conftest files,
  auto-loaded plugins, and SDK recording/playback behavior; a separate process alone does not disable them.
- No service mocks, fake credentials, request interception, fake transports, or recorded-response playback.
  Inspect live test code and fixture/plugin configuration; do not claim that a skill or a package blacklist
  can technically prove the complete absence of mocks.
- Run only inherited or directly tagged `@e2e` scenarios, with every selected outline example. Compare live
  results against this inventory. Untagged scenarios are intentionally not selected, not skipped tests.
- Require explicit live opt-in and DEV/PPE target configuration. Verify prerequisites and permissions before
  side effects. Missing configuration or unavailable dependencies block or fail the requested run, not pass it.
- If no scenarios are tagged, report "no E2E scenarios selected" without claiming live qualification.
  If a qualification run requires live coverage, empty selection must fail.
- Assert real service effects and the specified runtime outcome, not just HTTP success or process exit.
  For secret synchronization, assert the agreed synchronization result without logging secret values.
- Use unique run-owned resources, bounded readiness polling, and cleanup in teardown/finally.
  Never delete shared dependencies or resources whose ownership is uncertain. Report cleanup errors explicitly
  without hiding the original failure. Obtain approval before potentially destructive live execution.

## Repository integration and reporting

- Inspect existing dependency manifests, test conventions, CI workflows, and shared fixtures before wiring
  the workflow. Preserve unrelated work and existing validation.
- This repository uses pytest, pytest-mock, responses, pytest-cov, and tox. Unit test files use `_unit.py`;
  integration test files use `_int.py`. Ensure new BDD bindings are actually collected by the intended jobs.
- Its existing coverage configuration omits generated SDKs and tests. Its unit tox job appends coverage.
  Add an isolated BDD qualification path rather than relying on that accumulated coverage.
- Add `pytest-bdd` and, where needed, `pytest-socket` as development dependencies only during the approved
  implementation phase. Do not install tools merely to author these skills.
- Present results by accepted revision: expected and executed offline cases, selected and executed live cases,
  missing lines by production file, pending proposals, exact commands and outcomes, and blockers.
- Distinguish passed, failed, blocked, and not run. Never infer a live pass from offline success or represent
  a documented but unimplemented check as an active gate.
