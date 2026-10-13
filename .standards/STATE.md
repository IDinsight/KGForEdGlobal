# S.T.A.N.D.A.R.D.S. Workflow State

`WorkflowState`: `ARCHITECTING` `CycleMode`: `STANDARD` `PendingCycleMode`:
`UNSET` `PendingCycleRequest`: `UNSET` `PendingCycleBlockedOn`: `NONE`

## Active Work

`CompletionPolicy`: `FULL_DELIVERABLE` `Id`:
`make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5` `Request`:
`Make the backend test suite fast again. (1) Measure test timings on the user's local machine and rank tests and test files from slowest to fastest. (2) Using that ranking, delete the tests and test files that take far too long, and speed up how the rest of the suite runs. (3) Rework the backend Makefile test targets: remove the TEST_ARGS variable and add make test-all to run every test. Workflow instruction: the user explicitly selected a STANDARD cycle with completion policy FULL_DELIVERABLE. Background from a Navigator investigation of CI run 36025148809 on main (Sept 24): suite grew from 828 tests in ~7 s (Sept 1) to 3,819 tests in ~86 min after the LP work merged, plus 928 slow-marked tests deselected unless --run-slow (backend/tests/pytest_slow.py); CI time ~65% tests/kgfeg/evals/lp_eval/, ~34% tests/kgfeg/kgs/ (mostly test_lp_*); slowest CI files test_automatic_recovery.py, test_critique_citation_remediation.py, test_presentation.py, test_lp_checkpoint_formats.py; in test_automatic_recovery.py time goes to build_snapshot and repeated prepare_evaluation calls; CI timings came from log timestamps, so part 1 needs fresh local measurements. Open scoping decisions named by the user: what counts as way too long (threshold, share of total, or user approval of a list after ranking); whether deleted LP generation/evaluation behavior gets cheaper replacement tests or plain removal; what make test and make test-all run (fast default, --run-slow, alembic-marked tests); whether CI calls the Makefile target and runs in parallel; measurement conditions (coverage, fixed order -p no:randomly, serial vs -n auto, slow-marked tests); where the timing ranking is saved. Audit target: backend/tests/, backend/Makefile, pytest and coverage settings in backend/pyproject.toml, and .github/workflows/tests.yml.`
`Scope`: `.standards/docs/scope/make-the-backend-test-suite-fast-again-20261013T155516Z-dfdae6e5.md`
`Architecture`: `NONE` `Development`: `NONE` `PromotionReason`: `NONE`
`AuditTarget`: `NONE` `BlockedOn`: `NONE` `PendingVerificationCadence`: `NONE`

`BaselineReconciliation`: `NONE`

<!-- When non-NONE, use one Markdown list entry per source cycle:
- `SourceCycle`: `<cancelled-cycle-id>`
  `Request`: `<source-cycle request summary>`
Preserve existing entries; see PROTOCOL.md, Baseline Reconciliation Format.
-->

## Handoff

`Kind`: `FORWARD` `From`: `SCOPING` `FailureType`: `NONE` `Reason`:
`Scope completed with AC-001 through AC-017 after the user settled the open scoping decisions.`

## Recovery

`Active`: `false`

## Outstanding Obligations

`Active`: `false`
