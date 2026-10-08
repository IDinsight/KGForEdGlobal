# TypeScript Documentation Style

- Follow established TSDoc/JSDoc conventions and supported tags. Document public
  contracts, parameter meaning, defaults, return values, and relevant generics
  without duplicating obvious type declarations.
- Explain async behavior, promise rejection/errors, side effects, cancellation,
  and optional/null values when relevant to consumers.
- Keep examples consistent with exported APIs, imports, runtime behavior, and
  the project's documented execution environment. Show required setup and avoid
  invented results or claims that typechecking proves runtime behavior.
- Respect existing API documentation generation. These rules do not choose
  implementation types, architecture, or error-handling strategies.
