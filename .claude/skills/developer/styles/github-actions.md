# GitHub Actions Development Style

Apply with `universal.md` when creating or changing GitHub Actions workflows,
composite actions, or reusable workflows.

- Follow the repository's existing workflows, action pinning policy, runners,
  job naming, and shared actions before introducing new patterns.
- Treat new environments, deployment targets, promotion paths, production
  approval gates, branch protection or required checks, new scanning tools or
  external services, and cloud trust setup such as OIDC as architecture when
  they are not already established.
- Declare `permissions:` explicitly. Default to read-only access and grant write
  scopes, including `id-token: write`, only to the jobs that need them.
- When no pinning policy is established, pin third-party actions to a full
  commit SHA with a version comment. Never reference `@main`, `@master`, or
  `@latest`.
- Set `timeout-minutes` on every job, sized from observed run times.
- Use concurrency groups to cancel superseded CI runs on the same ref. Never
  cancel in-progress deployment or release jobs; give them a separate group with
  `cancel-in-progress: false`.
- Run the same lint, type-check, test, and build commands developers run
  locally. Add CI-only settings only when the CI environment requires them.
- Install dependencies from the lockfile in frozen mode, such as `npm ci`,
  `pnpm install --frozen-lockfile`, or `uv sync --frozen`.
- Prefer the setup action's built-in dependency caching. When a separate cache
  step is needed, key it on lockfile hashes and cache the package manager's
  store, not `node_modules` or a virtual environment.
- When tests need a database or similar service, use a service container with a
  pinned image tag and a health check rather than a mock.
- Do not add automatic retries or reruns to hide flaky tests. Investigate the
  cause, such as timing, resource contention, or missing cleanup.
- Read credentials only from the `secrets` context and never echo them or expose
  them in job outputs. Write file-based credentials such as signing keys from
  secrets at run time and delete them before unrelated later steps.
- Use `pull_request` for checks that build or run contributed code. Use
  `pull_request_target` only for metadata tasks such as labeling, and never
  check out or run the pull request's code in it.
- Do not interpolate untrusted event fields such as titles, branch names, or
  comment bodies into `run:` scripts with `${{ }}`. Pass them through `env:` and
  quote the shell variable.
- In monorepos, add `paths` filters that also cover shared packages the target
  depends on.
- Use `needs:` only for real dependencies so independent jobs run in parallel.
- Set `fail-fast: false` on a matrix when each combination's result matters.
- Use a composite action to share steps within a job and a reusable workflow
  when the shared unit needs its own job, runner, or permissions.
- Set `retention-days` on uploaded artifacts to match their purpose.
- Validate changed workflow files with the project's configured workflow or YAML
  linter. Report new secrets or variables by name and purpose, and report a
  workflow you could not run as unverified.

For stack-specific workflow examples, read the matching reference:
`../references/github-actions-python.md`,
`../references/github-actions-nextjs.md`, or
`../references/github-actions-android.md`.
