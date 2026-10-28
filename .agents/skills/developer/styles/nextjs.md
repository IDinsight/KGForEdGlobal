# Next.js Development Style

Apply with `universal.md` and the applicable language style for Next.js code.
Also apply `react.md` and `html.md` when the affected work materially changes
rendered React UI, and `css.md` when it materially changes styling.

- Inspect the installed Next.js version and the project's existing router and
  conventions before using framework APIs. Do not assume App Router or Pages
  Router behavior from memory.
- When the project uses App Router, prefer Server Components for components that
  do not require browser-only APIs, interactive state, or client hooks. Add
  `"use client"` only at the smallest necessary boundary.
- Keep App Router route files thin: a page or layout fetches initial data and
  renders a feature component. Place feature components, data access, and
  actions where the project keeps them, outside route segments or in private
  `_folders`, rather than growing route files.
- Keep secrets, privileged data access, and server-only dependencies out of
  client bundles. Use the project's server-only boundary mechanism where it
  exists, such as `import "server-only"` at the top of server data-access
  modules, so accidental client imports fail at build time.
- Never put secrets or personal data in `NEXT_PUBLIC_` variables; Next.js
  inlines them into the client bundle.
- Pass only serializable values across Server Component to Client Component
  boundaries unless the framework API explicitly supports another value type.
- Treat Server Actions, Route Handlers, webhooks, and similar entry points as
  public server endpoints. Validate input and enforce authentication and
  authorization before reading protected data or performing mutations.
- Request interception (`middleware.ts`, or `proxy.ts` in Next.js 16 and later;
  check the installed version) is defense in depth, not access control: Server
  Actions are reachable by direct POST. After moving or renaming a server
  function, recheck that the interception matcher still covers it.
- Make caching, revalidation, and request-time behavior explicit when freshness
  affects correctness. Follow the installed version's actual semantics rather
  than relying on defaults remembered from another Next.js release.
- After a mutation, update or invalidate the affected cached data using the
  project's established Next.js cache APIs so the UI does not keep serving stale
  state.
- Do not fetch independent server data one request after another when the work
  can start in parallel. Use the project's streaming or Suspense patterns for
  independently slow UI regions when they improve the user-visible result.
- Preserve the project's established error, loading, not-found, and redirect
  patterns. When adding a data-driven route, cover its loading, empty, error,
  and not-found states, never a blank screen; `error.tsx` must be a Client
  Component.
- Surface pending and validation states for form submissions through the
  project's form pattern, such as `useActionState`.
- For client-side fetching, use the project's data-fetching library, such as SWR
  or TanStack Query, rather than ad hoc `useEffect` fetching.
- When the project uses shadcn/ui, keep `components/ui/` as the primitive layer:
  import primitives from it, compose feature-specific wrappers elsewhere, and
  keep domain vocabulary and behavior out of it.
- Use framework-native navigation, asset, metadata, and route primitives when
  the project has standardized on them; do not introduce parallel mechanisms
  without an established reason.
- Avoid duplicating business logic between server and client layers. Put shared
  domain logic in an appropriate non-framework boundary when the architecture
  supports it.
- Do not change deployment, runtime, middleware, edge/server placement, or
  persistence strategy as an incidental implementation choice when those are
  consequential architectural decisions.

For authentication, authorization, Server Action, Route Handler, and sensitive
data patterns, read `../references/security-nextjs.md` when the work needs that
depth.
