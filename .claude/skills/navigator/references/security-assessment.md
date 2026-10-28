# Security Assessment

Read in Navigator's ASSESS mode when the user asks for a security review, audit,
posture check, or OWASP assessment outside a workflow cycle. The result stays in
the conversation. It is not a Reviewer finding, a penetration test, or a
compliance certification, and it never claims an application is secure.

For stack-specific checks, also read the Developer references for the affected
stack: `../../developer/references/security-fastapi.md` and
`../../developer/references/security-nextjs.md`. When the user names a target
maturity level, the Security section of
`../../scoper/references/maturity-levels.md` lists the cumulative controls
commonly expected at each level.

## Procedure

1. Set the scope, the target level when one is named, and your assumptions.
2. Map the attack surface: actors, assets, entry points, trust boundaries,
   sessions and tokens, privileged operations, tenants, secrets, uploads,
   outbound requests, third parties, data stores, caches, queues, and deployment
   edges.
3. Inspect code, configuration, manifests, lockfiles, infrastructure, tests, and
   documentation. Use only non-mutating diagnostics under Navigator's shared
   procedure.
4. Evaluate all ten OWASP Top 10:2025 categories below. Do not silently omit
   one: mark each pass, fail, unverified, or not applicable, with evidence and
   rationale.
5. For each gap, show the security consequence: file and line evidence,
   preconditions, a plausible abuse path, the affected asset, and impact.
6. Report scope, target and assessed level when relevant, confidence, gaps
   ordered by severity, the OWASP coverage summary, quick wins, blockers, a
   suggested path, what was verified, and limitations.

## OWASP Top 10:2025 Coverage

| Category                                   | Review focus                                                                                                                                         |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| A01 Broken Access Control                  | Deny by default; server-side function, object, tenant, CSRF, CORS, path, redirect, and SSRF boundaries; authorization tests.                         |
| A02 Security Misconfiguration              | Secure defaults; minimal features and privileges; safe headers, errors, cloud permissions, proxy, and framework settings.                            |
| A03 Software Supply Chain Failures         | Inventory and supported versions; automated dependency scanning, SBOM, provenance, signing, and separation of duties as maturity increases.          |
| A04 Cryptographic Failures                 | Data classification, TLS, password hashing, approved primitives, secure randomness, key lifecycle, and crypto agility.                               |
| A05 Injection                              | Context-safe APIs and parameterization for SQL and NoSQL, commands, templates, logs, headers, HTML and JavaScript, paths, and other interpreters.    |
| A06 Insecure Design                        | Explicit security requirements, trust boundaries, business invariants, abuse and resource limits, failure states, and proportionate threat modeling. |
| A07 Authentication Failures                | Maintained identity and session mechanisms, credential defenses, enumeration resistance, rotation and revocation, audience and scope checks, MFA.    |
| A08 Software or Data Integrity Failures    | Reject untrusted serialized state, code, or updates; prevent mass assignment; verify webhooks, artifacts, and critical data; protect CI/CD trust.    |
| A09 Security Logging and Alerting Failures | Safe logging at lower maturity; monitored, owned, tamper-resistant evidence and exercised detections at higher maturity.                             |
| A10 Mishandling of Exceptional Conditions  | Validate early, fail closed, roll back partial transactions, bound resources, and test dependency, privilege, race, and recovery failures.           |

The Top 10 is an awareness baseline, not proof of security. Use OWASP ASVS for
detailed high-assurance verification.

## Severity Labels

These labels rank gaps in the conversation. They are not Reviewer findings.

- Critical: a practical path to broad account, tenant, sensitive-data,
  signing-key, infrastructure, or privileged compromise; unauthenticated remote
  code execution; or catastrophic integrity or availability impact.
- High: practical unauthorized access or action with significant
  confidentiality, integrity, or availability impact.
- Medium: an exploitable weakness requiring meaningful preconditions or with
  limited impact, or a substantial defense-in-depth failure.
- Low: a limited-impact hardening issue, information exposure, or control
  quality concern.

Raise severity for low complexity, no authentication requirement, broad blast
radius, sensitive data, privileged victims, or evidence of exposure. Lower it
only with verified mitigating controls. Keep severity separate from maturity
level: a missing Level 2 authorization check can still be Critical.

## Reasoning Rules

- Separate authentication from authorization.
- Do not recommend JWT merely to "add security"; session, cookie, token, or
  provider architecture follows from revocation and client needs.
- Treat browsers, client components, mobile clients, request metadata, and all
  client-supplied identifiers as untrusted.
- Secrets, credentials, signing operations, and privileged provider calls stay
  server-side and out of URLs, logs, analytics, errors, and client bundles.
- Treat middleware, hidden UI, CORS, security headers, API documentation
  settings, and obscurity as defense in depth, not primary access control.
- Never claim "secure", a maturity level, OWASP compliance, or penetration-test
  coverage without evidence. Record uncertainty as unverified.

## Gap Format

For each gap, give a short title, severity, status (confirmed, likely, or
unverified), evidence, abuse path, impact and affected assets, the smallest safe
fix with a stronger option where useful, how a fix could be verified, and a
relevant CWE, OWASP, ASVS, or framework reference when useful. Name the likely
owner of a fix under the protocol without starting that work.

## Authoritative References

- [OWASP Top 10:2025](https://owasp.org/Top10/2025/0x00_2025-Introduction/)
- [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/)
- [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/)
- [FastAPI security](https://fastapi.tiangolo.com/tutorial/security/)
- [Next.js authentication](https://nextjs.org/docs/app/guides/authentication)
  and [data security](https://nextjs.org/docs/app/guides/data-security)
