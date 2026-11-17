---
name: bdd-implement
description: Implement human-accepted Gherkin features with complete offline scenario tests, 100 percent scoped line coverage, and selected mock-free live tests.
---

# Implement accepted behavior

Use this skill after a human has accepted Gherkin feature specifications and their coverage scope.

Read [the shared workflow contract](../bdd-specify/references/workflow.md) before proceeding.

## 1. Check the acceptance boundary

1. Inspect repository instructions and Git status. Preserve unrelated changes.
2. Read the feature files, accepted coverage inventory, acceptance evidence, and relevant existing code/tests.
3. Compare the current specification and coverage scope with the exact accepted revision.
4. If acceptance is absent or stale, use `bdd-specify` to request it. Do not begin unaccepted behavior.
5. Confirm command ownership before registration changes. A prototype request does not authorize importing
   another extension's command group into this repository.

## 2. Establish the executable contract

1. Reuse existing pytest tooling and patterns. Add development dependencies only when needed for this phase.
2. Build the canonical feature/example inventory and scenario-to-test mapping defined in the shared contract.
3. Add offline BDD bindings and external-boundary fixtures. Include every scenario, including `@e2e` scenarios.
4. Implement a completeness check comparing expected executions with collection and actual outcomes.
   Reject undefined steps, missing/duplicate bindings, skips, xfails, and incomplete qualifying runs.
5. Run the new acceptance tests before implementation. Confirm failure is for the missing behavior,
   not a broken fixture, missing import, unavailable dependency, or test-collection error.

## 3. Implement and measure

1. Make minimal production changes satisfying the accepted contract, preserving unrelated behavior.
2. Keep CLI parsing, application logic, and relevant SDK serialization real in offline tests.
   Simulate API and environmental boundaries according to the shared contract.
3. Assert output, diagnostics, outbound requests, and side effects as required by the features.
4. Add focused unit tests for internal edge cases not usefully expressed in Gherkin.
5. Run the complete offline acceptance suite with fresh, separately scoped coverage. Require zero missing
   executable lines in every accepted first-party production file, including newly discovered owned files.
6. If coverage reveals missing observable behavior, propose a feature change and pause that behavior for
   renewed human acceptance. Do not weaken assertions, change the coverage scope, or invent behavior to pass.
7. Run the smallest relevant existing regression tests and repository lint checks for the changed code.

## 4. Add selected live bindings

1. Implement separate live steps for inherited or directly tagged `@e2e` scenarios using the same features.
2. Keep live execution outside offline fixtures and recording/playback infrastructure.
3. Invoke the installed candidate extension through real `az` subprocesses; verify real dependencies and
   specified runtime effects. Use no mocked boundaries in the live test path.
4. Require explicit DEV/PPE configuration and live opt-in. Do not provision resources or run destructive
   scenarios without approval. Use owned resources, bounded waits, and explicit cleanup reporting.
5. If live execution is not authorized or prerequisites are unavailable, leave it clearly not run or blocked.
   Do not substitute offline results.

## 5. Integrate and hand off

When CI wiring is in scope, add credential-free offline validation with separate completeness and coverage gates.
Keep authorized live jobs separate; do not give PR validation Azure credentials.
Configure review enforcement only when requested and accessible, otherwise report it as pending.

Use `bdd-verify` to evaluate the accepted revision, tests, coverage evidence, live selection, and remaining gaps.
Do not commit, push, or create a pull request unless requested.
