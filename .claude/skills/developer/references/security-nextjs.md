# Next.js Security Reference

Read when the project uses Next.js and the work touches authentication or
sessions, Server Functions or Actions, Route Handlers, Proxy or middleware, data
access, the server and client boundary, environment variables, headers or CSP,
caching, redirects, or server-side fetches. It supplements `../styles/nextjs.md`
and never overrides the protocol, the approved design, repository-enforced
configuration, or established project conventions. When the installed version or
official documentation differs from this file, follow the installed version.

App Router patterns come first, but inspect Pages Router and legacy API routes
when present. Treat Client Components and anything serialized to them as
browser-visible, and treat Server Functions, Server Actions, and Route Handlers
as public entry points. Recent Next.js releases call the request interceptor
Proxy (`proxy.*`); older projects use `middleware.*`.

## Principles

- Separate authentication from authorization.
- Treat browsers, Client Components, request metadata, hidden form fields, and
  every client-supplied identifier as untrusted.
- Keep secrets, credentials, signing operations, and privileged provider calls
  on the server and out of client bundles, URLs, logs, analytics, and errors.
- Choose session, cookie, token, or identity-provider architecture from
  revocation and client needs. That choice belongs to the design.
- Minimize sensitive inputs, storage, and outputs. Fail closed when a security
  dependency fails.
- Treat Proxy, layouts, hidden UI, CORS, security headers, and obscurity as
  defense in depth, not primary access control.
- Do not claim code is "secure" or OWASP-compliant without evidence. Record what
  remains unverified.

## Security-Relevant Surfaces

Before changing security-relevant code, locate the repository equivalents of:

- `app/**/page.*`, dynamic segments such as `app/[id]`, `layout.*`, `route.*`,
  every exported Server Function or Action, `proxy.*` or legacy `middleware.*`,
  Pages Router API routes, and data-fetching functions;
- authentication and session modules, server-side cookie writes, callbacks,
  logout, recovery, role and permission checks, the Data Access Layer (DAL),
  DTOs, direct database calls, and API clients;
- every `'use client'` and `'use server'` boundary, `server-only` guards, props
  passed to Client Components, React Taint use and configuration, environment
  variables, generated HTML, MDX, third-party scripts, and redirects;
- `next.config.*`, response headers, CSP, image remote patterns, rewrites,
  source maps, cache directives, logging and instrumentation, and deployment
  configuration;
- `package.json`, lockfiles, CI security checks, and incident or runbook
  documentation.

Look for alternate entry points and bypasses near the change: actions without
authorization or schema validation, client-only guards, layout-only checks,
dynamic route IDs used without object authorization, broad Proxy matcher
exclusions, direct database access outside the chosen boundary, raw HTML,
secrets prefixed `NEXT_PUBLIC_`, browser token storage, user-controlled
redirects or fetch URLs, shared caches holding personalized data, state-changing
`GET` requests, and sensitive objects serialized to the client.

## App Router Security Model

- Every used or exported Server Function or Action is a public HTTP entry point
  reachable through a direct `POST`, not a private function. Encrypted action
  IDs and dead-code elimination are defense in depth, not authorization.
- Each Server Action authenticates the current session, authorizes the exact
  action and resource or tenant against authoritative server data, validates all
  client input with a server-side schema, enforces the current workflow state,
  and fails closed. Use the project's schema library, such as Zod; adding one is
  a dependency choice for the design.
- When the design includes a Data Access Layer, which Next.js recommends for new
  projects, route security-sensitive data access through it. Put
  `import 'server-only'` in DAL, auth, and database modules, centralize
  authorization there, and return explicit minimal DTOs.
- Direct database queries in Server Components may be acceptable in a throwaway
  spike, but they make inconsistent policy and accidental serialization to the
  client more likely in maintained code.
- React Taint is an optional tripwire when the project has deliberately adopted
  the experimental React channel. Keep minimizing DTOs and guarding server
  modules even when taint is enabled.
- Keep browser sessions in cookies that trusted server code sets and deletes,
  such as an authentication library, a Server Action, or a Route Handler. Use
  `HttpOnly`, `Secure`, an intentional `SameSite`, `Path`, and `Max-Age` or
  `Expires`. Never put bearer credentials in browser storage.
- Use Proxy only for optional optimistic redirects or coarse checks. Perform the
  secure check near the data source, and repeat it there even when Proxy, a
  layout, or the UI has already checked.

## Inputs, Outputs, and Caching

- Validate all `FormData`, JSON, dynamic route params, query values, headers,
  and cookies on the server.
- Pass explicit DTOs to Client Components. Never pass full database, auth, or
  provider objects, tokens, or internal authorization state.
- Prevent mass assignment by allowlisting writable fields and rechecking current
  authoritative state before committing.
- Use state-changing HTTP methods for mutations, with deliberate CSRF or origin
  protection for cookie-authenticated mutations.
- Where the app controls HTTP responses, use `401` for missing or invalid
  authentication and `403` for insufficient permission.
- Keep personalized data out of shared or public caches. Review static
  generation, revalidation, fetch caching, route output, streaming, and
  prefetching for user-data leakage.
- Return generic browser errors. Never serialize stacks, internal endpoints,
  provider responses, tokens, or secrets.
- Avoid `dangerouslySetInnerHTML`, user-controlled MDX or SVG, and unsafe URL
  schemes. Sanitize any rich HTML the design requires.
- Accept only relative, allowlisted redirect targets. Reject absolute and
  protocol-relative URLs from user input.
- Restrict `next/image` remote patterns and any server-side fetch or proxy
  feature to intended schemes, hosts, ports, paths, response sizes, and redirect
  behavior, so they cannot reach internal or metadata services.

## Controls by Delivery Stage

These lists show what is commonly expected at each stage. Implement a control
when the approved design or acceptance conditions call for it; the stage label
alone does not authorize new architecture or dependencies.

### Throwaway Prototypes

- Mock or static data and mock auth only. Never connect to production secrets,
  production identity, or real sensitive data.
- Direct data fetching or simple local state only where the route is clearly a
  spike.
- Basic form-shape validation and safe error rendering.
- No `NEXT_PUBLIC_*` secrets and no browser token storage, even in prototypes.
- Prototype status, mock data, and missing authorization noted in code comments
  or the README.

### Any Real Data

- A maintained authentication library or provider integration where auth is
  needed; choosing it is architecture.
- Sessions set from trusted server code in cookies, as described above.
- Authentication and authorization on every Server Action and Route Handler that
  reads or mutates protected data.
- Server-side input validation, explicit DTOs, and CSRF or origin protection for
  cookie-authenticated mutations.

### Internal Layered Applications

- Security-sensitive data access centralized in server-only modules or a DAL,
  with authorization repeated close to the data.
- Mass-assignment protection, cache review for personalized data, and generic
  browser errors.
- Authentication, authorization, validation, privileged-action, and
  unexpected-error events logged for investigation, with sensitive values
  redacted.
- Client bundles, HTML, React payloads, logs, and analytics free of secrets and
  sensitive DTO fields.

### Pre-Production Hardening

- Short idle and absolute session lifetimes, rotation, server-side revocation,
  recent-auth checks, and a session-management view where appropriate.
- MFA for administrative and materially privileged users.
- Rate limits and monitoring on login, registration, recovery, verification,
  Server Actions, Route Handlers, search, export, uploads, invitations, and
  expensive rendering or API paths.
- A Content Security Policy, rolled out in report-only mode, cleaned of
  violations, and then enforced.
- HSTS at the HTTPS edge after validation, `X-Content-Type-Options: nosniff`, a
  deliberate `Referrer-Policy`, a minimal `Permissions-Policy`, and frame
  protections.
- Centralized audit events for login, logout, session renewal and revocation,
  recovery, permission changes, denials, exports, sensitive reads,
  impersonation, admin actions, and security configuration changes.
- Secret scanning, dependency scanning, and static analysis in CI where
  available.

These controls usually come with operational procedures for staging deployment,
rollback, key and session rotation, dependency patching, backup and restore, and
incident response. Writing those procedures is documentation and operations work
outside Developer; report missing ones rather than inventing them.

### Public Production

Most production controls are architecture or operations decisions: a versioned
threat model and abuse cases covering browser compromise, identity and recovery,
Server Actions, authorization, caches, tenants, third parties, supply chain,
insiders, availability, and downstream APIs; requirements traced to OWASP ASVS;
a hardened identity provider with phishing-resistant MFA; admin interfaces
isolated by origin, identity policy, session namespace, gateway, and deployment
where justified; a strict CSP with nonces or hashes, minimal third-party code,
and Trusted Types where supported; an origin locked to trusted edge traffic,
authenticated service calls, restricted egress, and WAF, bot, adaptive
rate-limit, and DDoS controls; SBOM and provenance for critical dependencies and
build artifacts; tamper-resistant events in a separately controlled SIEM; and
exercised incident playbooks. Implement only the parts the approved design
assigns to code. Production security cannot be established from a Next.js
repository alone; evidence about identity providers, edge, cloud, CI, SIEM, and
incident exercises stays unverified until someone supplies it.

## OWASP Top 10:2025 Map

Use this map to check which categories a change touches. It is an index, not
evidence.

| Category                                   | Next.js evidence to inspect                                                                                                                                                |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A01 Broken Access Control                  | DAL, Server Function or Action and Route Handler policy, object and tenant checks for dynamic route params, CSRF and CORS, paths and redirects, cache and SSRF boundaries. |
| A02 Security Misconfiguration              | `next.config`, environment and preview settings, cookies, CSP and headers, Proxy matchers, source maps, image remote patterns, cloud and CDN permissions, errors.          |
| A03 Software Supply Chain Failures         | npm lockfile and transitive inventory, supported Next.js, React, and Node versions, SCA and patch process, script and package registries, CI identities, SBOM.             |
| A04 Cryptographic Failures                 | HTTPS and HSTS, session and OAuth keys, cookie protection, randomness, algorithms, provider certificate validation, key storage and rotation, sensitive-data caching.      |
| A05 Injection                              | HTML, JS, MDX, CSS, and URL sinks, SQL and NoSQL, commands, paths, headers, logs, templates, deserialization, and third-party script and data boundaries.                  |
| A06 Insecure Design                        | Server and client trust boundaries, business invariants, tenant and cache model, uploads, server fetches, quotas, failure states, abuse cases, threat model.               |
| A07 Authentication Failures                | Login, recovery, and MFA, enumeration and automation controls, OAuth validation, session fixation, rotation, and revocation, logout, audience and scope.                   |
| A08 Software or Data Integrity Failures    | Client-controlled state, mass assignment, webhook signatures and replay, unsafe serialized data, third-party code and artifacts, CI/CD trust and promotion.                |
| A09 Security Logging and Alerting Failures | Auth, access-control, validation, admin, data, CSP, and error events, redaction and encoding, monitoring, alert ownership, retention, tamper resistance.                   |
| A10 Mishandling of Exceptional Conditions  | Action, handler, and global error paths, fail-closed auth and downstreams, transaction rollback, stale state and races, idempotency, cleanup, limits, repeated errors.     |

## Common Failure Patterns

- A page hides controls or redirects in the client, but its Server Action or
  Route Handler has no authorization check.
- Proxy, middleware, or a layout is the only authorization layer.
- `'use server'` is treated as access control.
- A Server Component or DAL passes a full user, database, or auth object to a
  Client Component.
- Direct database calls are scattered across Server Components and Actions,
  bypassing consistent DAL authorization.
- A secret is prefixed `NEXT_PUBLIC_`, included in public env, logged during
  build, embedded in source maps, or returned by an action.
- JWT, access, or refresh tokens live in local storage, session storage,
  browser-readable cookies, client state, query strings, or analytics.
- Cookie authentication lacks CSRF protection, or broad allowed origins make
  Server Action or Route Handler assumptions unsafe.
- A layout caches a stale role, a shared cache omits user or tenant identity, or
  statically generated output contains personalized data.
- `dangerouslySetInnerHTML`, user-controlled MDX or SVG, URL schemes, or
  third-party scripts create an XSS path.
- User-controlled redirects accept absolute or protocol-relative URLs.
- Server-side `fetch`, image optimization, webhooks, previews, or proxy routes
  accept arbitrary URLs and can reach internal or metadata services.
- A mutation uses `GET`, lacks origin or recent-auth checks, or trusts hidden
  form fields for user, role, price, tenant, or ownership.
- Dependencies or Next.js stay on a release with a relevant security advisory
  because nobody owns or checks upgrades.
- An Action catches and suppresses an auth, downstream, or write failure,
  commits partial work, retries a non-idempotent operation, trusts stale
  authorization, or fails open.

## Before Handoff

Run the project's existing tests, linters, type checks, build, and any
dependency or secret scans as self-checks, then confirm that:

- every changed Server Action, Route Handler, page, and legacy API route
  authenticates and authorizes as the design requires, including object and
  tenant access for dynamic route params;
- client bundles, HTML, React payloads, source maps, errors, logs, and analytics
  do not carry secrets or sensitive DTO fields;
- `server-only` guards are in place on DAL, auth, and database modules;
- session, cookie, CSRF, CORS, and cache behavior match the chosen architecture;
- secrets and production settings fail closed with no unsafe defaults;
- exceptional conditions preserve authorization and transaction integrity and
  return safe errors;
- security events are observable as the design requires without recording
  credentials or sensitive content.

Do not add or rewrite formal security tests. Tester owns them.

## Authoritative References

- [Next.js authentication guide](https://nextjs.org/docs/app/guides/authentication)
- [Next.js data security guide](https://nextjs.org/docs/app/guides/data-security)
- [Next.js React Taint configuration](https://nextjs.org/docs/app/api-reference/config/next-config-js/taint)
- [Next.js `use server` security considerations](https://nextjs.org/docs/app/api-reference/directives/use-server)
- [Next.js Content Security Policy guide](https://nextjs.org/docs/app/guides/content-security-policy)
- [Next.js environment variables guide](https://nextjs.org/docs/app/guides/environment-variables)
- [OWASP Top 10: 2025 introduction](https://owasp.org/Top10/2025/0x00_2025-Introduction/)
- [OWASP Application Security Verification Standard](https://owasp.org/www-project-application-security-verification-standard/)
- [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [OWASP Cross-Site Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [OWASP Cross Site Scripting Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html)
- [OWASP Content Security Policy Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
- [OWASP Server-Side Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
