# Selecting Test Techniques

Choose by the behavior and contract, using the least expensive boundary that
provides the required evidence. Mix techniques when distinct risks require it;
these are not workflow modes or a requirement to use every layer.

- **Unit:** transformations, rules, state transitions, and edge cases. Replaced
  dependencies do not establish real wiring or external behavior.
- **Component:** public interfaces, interactions, events, or rendered states.
  Simplified hosts may omit real routing, persistence, or browser semantics.
- **Integration:** contracts across actual modules, storage adapters, handlers,
  or services. Record replaced boundaries; an in-memory fake may not model real
  storage semantics.
- **CLI:** arguments, exit codes, stdout/stderr, file effects, and interruption.
  Invoke through the established command boundary; isolate cwd and environment.
- **System:** user journeys and runtime boundaries. Use a representative
  environment, control data and services, and state limitations.

Reuse coverage at the layer that already proves the obligation. Do not duplicate
it at every layer or select techniques solely by filename. Performance and
resource criteria require measurements under the defined conditions; lint and
functional success do not establish those properties. For inspection-based
contracts, record the exact inspected evidence and limits without inventing a
runtime test requirement.
