## Preserve the baseline

When scoping changes to an existing system, use its established behavior and
limitations as the baseline. Require the proposed change to achieve its intended
outcomes without introducing defects or worsening existing limitations.

Existing defects, omissions, and technical debt do not automatically become
requirements for this work. Include remediation only when it is explicitly requested,
necessary to achieve the agreed outcomes, or necessary to prevent the change from
introducing or worsening a problem.

Record relevant inherited limitations as context and, where useful, explicit
exclusions. Do not turn them into acceptance conditions merely because they were
discovered. If an existing limitation makes the requested outcome infeasible, explain
the dependency and resolve the scope rather than silently expanding it.

For example, an existing limitation in OpenTelemetry metrics tracking does not by
itself require remediation. It becomes relevant to this scope if the proposed change
worsens it or requires capabilities that the limitation prevents.
