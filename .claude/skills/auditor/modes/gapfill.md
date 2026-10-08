# Gap-Fill Audit

<!-- standards:invocation -->

```json
{
  "schemaVersion": 1,
  "kind": "mode",
  "group": "inspection-mode",
  "id": "GAPFILL",
  "label": "Gap fill",
  "description": "Refresh a usable baseline without a narrower target.",
  "selection": "assessment"
}
```

Use when a usable `.standards/CONTEXT.md` exists and needs verification or
refresh for the active cycle, with no narrower subtree target. This includes
cases where the baseline may be incomplete, stale, incorrect, or insufficiently
grounded.

The goal is to reconcile the existing baseline against current authoritative
evidence rather than rebuild it indiscriminately.

## Procedure

1. Read the existing `.standards/CONTEXT.md` as claims to verify, not as
   authoritative truth.
2. Identify which claims or omissions matter to the active request or
   `PROJECT_CONTEXT` defect.
3. Verify those areas against primary repository evidence, project instructions,
   completed upstream artifacts, and explicit user input where applicable.
4. Preserve claims that remain correct and relevant.
5. Replace stale or incorrect claims; add missing material facts; remove details
   that are no longer useful to downstream roles.
6. Rewrite `.standards/CONTEXT.md` as a coherent current baseline using
   `../template.md`; do not append change history or an audit diary.

## Mode-specific completion condition

Every material baseline claim needed for the active cycle is either grounded in
current evidence or explicitly represented as a non-blocking unknown, and known
stale or incorrect context has been removed.
