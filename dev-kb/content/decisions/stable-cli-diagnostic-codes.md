---
type: Decision
title: Stable CLI diagnostic codes
description: Why the CLI exposes stable error codes independently of human-readable
  messages.
tags:
- cli
timestamp: 2026-09-23T17:52:11Z
sources:
- ref:diagnostic-issue
- ref:diagnostic-commit
---

## Problem

Consumers previously had to match human-readable error messages to distinguish failure categories. This made wording an accidental contract. Issue #15 introduced machine-readable codes to remove that dependency. （出典: diagnostic-issue, diagnostic-commit）

## Decision

Commit `02d413c6241098a591d92c53bc3775be91ed606d` added a stable `code` to failure envelopes and `command` when argument parsing has identified it. The existing schema version and error message remain. Exception lookup follows the MRO so a package error keeps its specific code before builtin fallbacks are considered. Argument failures use `usage_error`. The package adopts the harness convention without importing its private implementation. （出典: diagnostic-commit）

## Evidence

The original task is [Issue #15](../../../docs/ISSUES.md#issue-15-add-stable-diagnostic-codes-to-failure-envelopes). Inspect the immutable change with `git show 02d413c6241098a591d92c53bc3775be91ed606d`. The implementation is in [diagnostics.py](../../../src/kb_retrieval_core/diagnostics.py) and [cli.py](../../../src/kb_retrieval_core/cli.py); existing checks are in [test_diagnostics.py](../../../tests/test_diagnostics.py). These tests cover package-specific codes, builtin fallbacks, public exports, and CLI envelopes. Run them with `PYTHONPATH=src python3 -m pytest tests/test_diagnostics.py`. Links point to the working tree; the commit identifies the historical evidence. （出典: diagnostic-issue, diagnostic-commit）
