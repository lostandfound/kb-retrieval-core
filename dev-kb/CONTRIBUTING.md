# Development KB

This KB is a navigation aid for engineering decisions. `../docs/ISSUES.md`
and Git remain canonical. Record only facts supported by those sources;
do not infer rejected alternatives or copy chat transcripts.

The schema is `content/vocabulary.yml`. A Decision has Problem, Decision,
and Evidence sections. Cite registered sources using `ref:<id>` in
frontmatter and `（出典: <id>）` in prose. Repository reference paths are
relative to the outer repository root; revisions identify the source snapshot.
Use relative Markdown links to make the current implementation accessible.

From the outer repository root:

```bash
.venv-kb-harness/bin/kb entity create --start dev-kb --from /path/to/spec.yml --dry-run
.venv-kb-harness/bin/kb entity create --start dev-kb --from /path/to/spec.yml
.venv-kb-harness/bin/kb validate --start dev-kb
.venv-kb-harness/bin/kb sync --start dev-kb --check
```

Commit generated indexes and `graph.json` with the record. After manually
editing a record, run `kb sync --start dev-kb` before the check.
