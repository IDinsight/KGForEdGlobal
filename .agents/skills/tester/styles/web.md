# Web Testing Style

- Exercise user-visible behavior using the repository's established rendering
  and interaction tools. Prefer semantic roles and accessible names over DOM
  depth, CSS classes, or internal state.
- Cover relevant loading, empty, success, and failure states under controlled
  boundary responses. Select high-value cases under the shared budget.
- For changed interactions, check relevant keyboard navigation, accessible
  names, focus movement, and error announcements. Automated accessibility checks
  cover only their detected rules; record manual or browser gaps.
- Use a real browser when layout, navigation, hydration, browser APIs, or actual
  focus behavior is the contract. A simulated DOM cannot prove those properties.
- For security behavior, test the server or data boundary as well as any
  relevant UI outcome. Hidden controls cannot prove authorization or data
  isolation.
- Reuse project viewport and visual-test conventions. Review intentional visual
  changes; do not approve all snapshots to remove failures.
