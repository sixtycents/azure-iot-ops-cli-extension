---
name: bdd-verify
description: Verify accepted Gherkin specifications against complete offline execution, exact scoped line coverage, human approval, and explicitly selected live E2E evidence.
---

# Verify the BDD contract

Use this skill to qualify a BDD implementation or inspect its readiness for review. Verification must expose
missing tooling and missing evidence instead of treating workflow instructions as implemented checks.

Read [the shared workflow contract](../bdd-specify/references/workflow.md) before proceeding.

## 1. Establish what is being verified

1. Inspect repository instructions, Git status, the accepted specification revision, coverage scope,
   implementation diff, test bindings, fixtures, and validation configuration.
2. Verify human acceptance matches the current feature files and coverage inventory. Report pending proposals
   or stale acceptance as blockers. Do not approve specifications or edit them to match the implementation.
3. Rebuild the expected scenario/example inventory from all in-scope features, independent of test selection.
4. Identify exact commands for offline acceptance, coverage, relevant regressions/lint, and optional live tests.
   If a required gate is absent, report it as not implemented; do not manufacture a passing result.

## 2. Verify offline completeness and isolation

1. Run the complete offline qualification path, not a developer-selected subset.
2. Reconcile expected cases against collected and executed cases. Require one binding per expected execution,
   every outline example, no undefined steps, and no skipped, xfailed, unexpectedly deselected, or failed cases.
3. Inspect assertions against the accepted observable outcomes, including relevant API requests and failures.
   Executing a Then step without a meaningful assertion is not behavioral coverage.
4. Inspect boundary fixtures and outbound-network prevention. Ensure external credentials, configuration,
   files, subprocesses, clocks, and service availability cannot make the offline result environment-dependent.
5. Treat isolation defects as failures; do not rerun against real Azure to obtain an offline pass.

## 3. Verify exact line coverage

1. Run coverage fresh and separately from existing unit or live results with explicit production source scope.
2. Compare measured files with the accepted coverage inventory and actual owned production files.
   Fail on omitted or unmeasured owned files, including files never imported.
3. Require zero missing executable lines per expected file, not just a displayed rounded total of 100%.
4. Check for scope reductions, no-cover pragmas, stale/combined data, or exclusions masking missing tests.
5. Run the relevant existing regression and lint commands. Keep scenario completeness, behavior assertions,
   coverage, and regression results distinct.

## 4. Verify live selection and evidence

1. Derive the E2E inventory from inherited and direct `@e2e` tags, expanding all selected outline examples.
2. Inspect live steps, inherited fixtures, plugin configuration, subprocess environment, and candidate artifact
   selection for mocks, fake transports/credentials, request interception, or recording/playback.
3. Execute only with explicit live authorization and DEV/PPE configuration. Confirm dependencies,
   resource ownership, bounded waits, real runtime assertions, and cleanup before approving execution.
4. Reconcile live results with the selected inventory. Missing cases, skips, failures, unavailable dependencies,
   or cleanup failures cannot become a pass. Report an empty selection explicitly.
5. If not authorized or not runnable, state "not run" or "blocked" with the reason. Do not claim E2E coverage
   from collected tests, implemented bindings, or offline success alone.

## 5. Report evidence

Provide a concise report containing:

- The accepted specification/coverage revision and any pending human decisions.
- Offline expected, executed, and passed case counts, with missing/duplicate/unbound cases identified.
- Per-file missing executable lines and any missing production files.
- Live selected, executed, and passed case counts, plus dependency and cleanup failures.
- Exact validation commands and their outcomes; distinguish checks not implemented from checks not run.
- Scenario ID to offline/live test-result mapping, including outline case identities.

Do not alter features, production code, coverage exclusions, or approval configuration during verification
unless separately requested. Report concrete failures for the implementation phase to address.
