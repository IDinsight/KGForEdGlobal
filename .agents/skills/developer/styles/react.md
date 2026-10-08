# React Development Style

Apply with `universal.md` and the project's language style for React code.

- Use the repository's established component model and React version.
- Keep render logic pure. Do not mutate props, state, context values, or other
  pre-existing values during render.
- Follow the Rules of Hooks. Call Hooks at the top level of React components or
  custom Hooks, not conditionally or from ordinary functions.
- Prefer derived values over duplicated state. Do not copy props into state
  unless the state is intentionally allowed to diverge.
- Keep state as local as practical. Lift or globalize it only when multiple
  owners genuinely need to coordinate through the same state.
- Use effects for synchronization with external systems, not for ordinary data
  derivation that can happen during render or event handling.
- Clean up effect-owned subscriptions, observers, timers, and other resources.
  Prevent obsolete async work from updating state when a newer request or an
  unmount makes the result stale.
- Put user-triggered side effects in event handlers when they are caused by that
  interaction rather than by rendering itself.
- Keep component responsibilities focused. Extract a component or hook when it
  creates a meaningful boundary, not solely to reduce line count.
- Use stable semantic keys for lists; do not use array indexes when item
  identity can change.
- Preserve controlled versus uncontrolled input behavior intentionally.
- Keep accessibility semantics intact: labels, keyboard behavior, focus,
  landmarks, names, and state must remain usable without relying only on visual
  presentation.
- Avoid memoization by default. Add `memo`, `useMemo`, or `useCallback` when an
  established performance need or identity contract justifies it.
- Keep server, client, and shared concerns separated according to the hosting
  framework's established boundaries.
