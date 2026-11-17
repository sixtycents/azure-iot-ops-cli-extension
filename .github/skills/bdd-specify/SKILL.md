---
name: bdd-specify
description: Draft and review Gherkin feature specifications before implementing CLI functionality, including scenario IDs, E2E selection, and human acceptance.
---

# Specify behavior before implementation

Use this skill when an engineer wants to specify new functionality or reimplement existing commands using
behavior-driven development. This phase produces a proposed contract, not production code.

Read [the shared workflow contract](references/workflow.md) before proceeding.

## 1. Establish scope

1. Inspect repository instructions, Git status, command registrations, help, relevant implementation, and tests.
2. Confirm that this repository owns the requested command group. Do not copy or register commands owned by
   another extension without an explicit decision from the engineer.
3. Inventory all in-scope subcommands and their arguments, defaults, outputs, errors, API interactions, and
   compatibility requirements. Treat existing behavior as evidence requiring review, not automatic acceptance.
4. Identify external dependencies and a proposed list of first-party production files for the coverage gate.
   Include command registration, argument handling, validators, providers, and directly dependent changed logic.
5. Ask the engineer to resolve ambiguities that materially affect observable behavior or scope.

## 2. Draft executable features

1. Use existing feature locations if present; otherwise propose a dedicated feature directory.
2. Write Gherkin describing observable outcomes, not internal method calls or implementation structure.
3. Follow the shared contract for stable scenario IDs, outline examples, and inherited `@e2e` selection.
4. Cover each subcommand's successful behavior and applicable validation errors, authorization failures,
   missing resources, idempotency, pagination, polling, partial failure, and cleanup behavior.
   Include only cases relevant to the command; do not invent requirements to fill a checklist.
5. Give errors concrete expectations: exit outcome, useful diagnostic content, and side effects that must
   or must not have occurred. Avoid brittle assertions against incidental wording or generated identifiers.
6. Make each scenario independently executable with owned data. State the real dependencies and observable
   runtime result for every proposed E2E scenario.
7. Keep unresolved behavior clearly identified as a proposal. Do not hide pending decisions in passing steps,
   skipped scenarios, or vague statements such as "the operation works."

## 3. Request human acceptance and stop

Present the exact feature diff, command inventory, coverage scope, E2E selections, assumptions, and open questions.
Ask for explicit acceptance of the specification and coverage scope.

Record acceptance against the exact specification revision as described in the shared contract. A local
acceptance record is traceability, not an enforceable authorization mechanism.

Do not install test dependencies, generate implementation tests, or change production behavior in this phase.
Do not treat permission to create skills or draft features as permission to accept features.
After acceptance, use `bdd-implement` in a subsequent phase.
