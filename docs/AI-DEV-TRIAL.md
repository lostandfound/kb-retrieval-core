# AI-assisted development trial

This protocol is the small, persistent trial for using this repository's
existing development records as an AI-assisted engineering harness. It does
not change the package boundary: `kb-retrieval-core` remains retrieval and
Evidence Packet assembly only. Do not add an LLM, prompt runtime, session
store, or general-purpose logging feature to the package for this trial.

## Use the existing records

| Need | Record |
| --- | --- |
| A concrete implementation task and its acceptance checks | [`ISSUES.md`](ISSUES.md) |
| An unresolved concern that is not ready to become an issue | Add a short dated note to the relevant issue or a focused concern document only when needed; do not create a standing inbox preemptively |
| A durable choice that affects architecture, compatibility, or future work | `docs/adr/ADR-NNNN-*.md` (create only for a consequential decision) |
| What changed and when | Git diff, commit history, and release audit |
| What the next session needs to know | This file plus the concise session handoff in `AGENTS.md` |

`ISSUES.md` is the task/acceptance ledger, not a chat transcript. Keep one
reviewable change tied to its issue and mark it complete only when its checks
pass. A question or uncertainty belongs in the issue while it is being
resolved; promote it to an ADR only if the answer should constrain later work.
Use Git as the change log. Do not copy full prompts, chain-of-thought, or
routine command output into repository documents.

## Per-task loop

1. Read `AGENTS.md`, check `git status --short --branch` and recent commits,
   then read `docs/ARCHITECTURE.md` before any retrieval behavior change.
2. Select an existing issue or write a narrow issue with observable acceptance
   checks before implementation. State the consumer impact and keep lexical
   retrieval as the baseline.
3. If a consequential design choice arises, record options, decision, reason,
   consequences, and reconsideration trigger in an ADR. Otherwise keep the
   rationale in the issue/diff; avoid an ADR for routine implementation detail.
4. Implement the smallest change within this package's boundary. Use the
   consumer-owned profile and real consumer data for retrieval quality claims;
   synthetic fixtures establish mechanics only. Vector/hybrid remains opt-in
   until its consumer admission gate passes.
5. Run the relevant checks from `AGENTS.md` and the issue. Report exact
   commands/results, changed paths, and any unresolved concern. Update the
   issue and this handoff only when the next session's starting facts changed.

## Reusable session prompt

Paste this at the start of an AI-assisted task when the repository instructions
are not loaded automatically:

> Work in `kb-retrieval-core` under `AGENTS.md` and `docs/AI-DEV-TRIAL.md`.
> First inspect the working tree and recent commits, then read the relevant
> issue; read `docs/ARCHITECTURE.md` completely before changing retrieval
> behavior. Keep this package domain-independent and preserve lexical search
> as the default. Use `docs/ISSUES.md` for scoped work and acceptance checks,
> add an ADR only for a consequential durable decision, and use Git for the
> change history. Do not add LLM, prompting, conversation, or logging features
> to this package. Implement the smallest complete change, run the relevant
> documented checks, and leave a concise handoff with results and remaining
> uncertainty. Do not claim vector/hybrid quality from synthetic fixtures.

## Trial review

After several substantive tasks, review whether issues had clear acceptance
checks, decisions were recoverable without chat history, and handoffs prevented
repeated discovery. Add automation or a new record type only if a repeated,
concrete failure shows the current files and Git history are insufficient.


## Development harness installation

`apm.yml` pins the development dependency to GitHub's
`lostandfound/kb-harness-core#v0.2.1`; `apm.lock.yaml` records the resolved
commit. Restore the tools from the repository root:

```bash
apm install --dev --target codex --https
python3 -m venv .venv-kb-harness
.venv-kb-harness/bin/python -m pip install apm_modules/lostandfound/kb-harness-core
.venv-kb-harness/bin/kb --help
```

APM deploys the package to `apm_modules/lostandfound/kb-harness-core/`,
skills to `.agents/skills/`, and agents to `.codex/agents/`.
The CLI lives in `.venv-kb-harness/`, separate from retrieval runtime
dependencies. Change skills upstream and reinstall rather than editing
generated files.

This installs development tools only. Commands such as `kb validate` need
a configured KB with `kb-domain.yml`; this package's root is not itself
a KB.


## First trial: diagnostic codes

The explicitly scoped first experiment is [Issue #16](ISSUES.md#issue-16-trial-a-development-kb-with-one-sourced-decision).
Its record is [Stable CLI diagnostic codes](../dev-kb/content/decisions/stable-cli-diagnostic-codes.md).
`dev-kb/` is a separate development KB with its own configuration; the outer
repository remains a retrieval package. See [the KB rules](../dev-kb/CONTRIBUTING.md).
Issue #15 and commit `02d413c` remain the sources of truth.

```bash
.venv-kb-harness/bin/kb validate --start dev-kb
.venv-kb-harness/bin/kb sync --start dev-kb --check
```

Read the record without chat history and answer: why were diagnostic codes
added, and which change implemented them? Follow its links to the issue,
implementation, and tests. This checks navigation and provenance only;
it does not establish retrieval quality or prove reduced handoff effort
across sessions. Keep the trial to this one record until a later session
has evaluated its usefulness.
