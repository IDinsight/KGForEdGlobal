# TypeScript Development Style

Apply with `universal.md` for TypeScript implementation.

- Follow the repository's TypeScript version, compiler options, formatter, and
  linter. Treat configured strictness as binding rather than weakening it around
  a local change.
- Prefer precise domain types. Avoid `any`; use `unknown` plus narrowing when a
  value is genuinely untrusted or not yet typed.
- Define explicit types at module, API, persistence, event, and other material
  boundaries. Let local inference handle obvious implementation details.
- Prefer discriminated unions for meaningful variant states instead of loosely
  related optional fields. Handle closed variants exhaustively when missing a
  case would be a correctness bug.
- Avoid non-null assertions and unsafe casts unless an established invariant
  makes them necessary and the invariant is evident at the use site.
- Represent absence intentionally with the project's established `null` /
  `undefined` convention; do not mix them casually.
- Keep asynchronous control flow explicit. Await promises intentionally and
  propagate or translate errors at the correct boundary.
- Prefer immutable inputs and values when mutation is not required by the local
  design.
- Do not create broad utility types or generic abstractions for a single use.
- Keep runtime validation at untrusted boundaries; static types alone do not
  validate JSON, request data, environment variables, storage records, or other
  external values.
- Do not silence compiler errors with broad suppression. When a suppression is
  unavoidable, prefer `@ts-expect-error` with a reason so the compiler can flag
  it when the underlying error disappears.
