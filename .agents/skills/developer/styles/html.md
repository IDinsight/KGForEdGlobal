# HTML Development Style

Apply with `universal.md` for HTML or rendered markup.

- Prefer semantic elements that match the content and interaction before adding
  generic containers or ARIA roles.
- Keep DOM source order aligned with reading and keyboard order. Do not use CSS
  positioning or positive `tabindex` values to repair a confusing source order.
- Maintain a logical heading hierarchy and meaningful landmark structure.
- Associate form controls with visible, accessible names and labels. Surface
  instructions, validation, and error relationships in markup, not only
  visually.
- Group related form controls with semantic structure such as `fieldset` and
  `legend` when the relationship matters to understanding the form.
- Use appropriate input types and established `autocomplete` / `inputmode`
  values when they improve input behavior without changing the data contract.
- Give buttons an explicit `type` when default submit behavior could be
  ambiguous.
- Provide meaningful alternative text for informative images and empty alt text
  for decorative images where appropriate.
- Use links for navigation and buttons for actions. Do not nest interactive
  controls inside other interactive controls.
- Use table elements for tabular data, with headers and captions or header
  associations when users need them to understand row and column relationships.
- Do not rely on color, placeholder text, hover state, or visual position as the
  only way to communicate meaning.
- Keep document metadata, language, and encoding correct when the application
  controls the document shell.
- Avoid invalid nesting and unnecessary wrapper markup.
