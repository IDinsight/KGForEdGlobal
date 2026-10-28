# GitHub Actions for Next.js Applications

Read when the work creates or changes GitHub Actions workflows for a Next.js
application. It supplements `../styles/github-actions.md` and never overrides
the protocol, the approved design, repository-enforced configuration, or
established project conventions. When the existing workflows, installed tool
versions, or official documentation differ from this file, follow them.

## Using These Examples

- Examples use major version tags (`@v4`) for readability, and those tags may be
  behind each action's current release. Check the current major version and pin
  per project policy. When no policy is established, pin third-party actions to
  a full commit SHA with a version comment
  (`uses: owner/action@<full-sha> # vX.Y.Z`), verified against the action's
  release tag.
- Workflows run the project's established lint, type-check, test, and build
  scripts. The tests, fixtures, and test configuration they run (including
  Playwright configuration) belong to Tester; the workflow only invokes them.
- Deployment targets, environments and promotion, production approval gates,
  branch protection and required checks, new scanning tools or external
  services, and OIDC trust are architecture when not already established.
  Sections that depend on one of these say so.
- Secrets appear only as `${{ secrets.NAME }}`; non-secret configuration uses
  `${{ vars.NAME }}`. Report each new secret or variable by name and purpose.

## Workflow Layout

Organize workflows by concern. One `ci.yml` is enough until the design adds
deployment or scheduled scanning; then split by purpose.

| Workflow                | Trigger                      | Purpose                                     |
| ----------------------- | ---------------------------- | ------------------------------------------- |
| `ci.yml`                | `push`, `pull_request`       | Lint, type check, test, build               |
| `deploy-staging.yml`    | `push` to the default branch | When the design adds a staging deploy       |
| `deploy-production.yml` | `release` or manual          | When the design adds production deploy      |
| `security.yml`          | `schedule`, `pull_request`   | When the design adds audit or SAST scanning |

Starting timeouts before you have observed run times: 10 minutes for lint and
type check, 15 for tests and build, 20 for end-to-end tests, 30 for deployment.
Adjust from real durations.

In a monorepo, filter triggers to the app and the shared packages it depends on,
and run commands from the app directory:

```yaml
on:
  push:
    paths:
      - "apps/web/**"
      - "packages/ui/**"
      - "pnpm-lock.yaml"

defaults:
  run:
    working-directory: apps/web
```

`defaults.run` applies only to `run:` steps. Point `setup-node` at the right
lockfile with `cache-dependency-path` when it is not at the repository root.

## Runtime Setup

Use the package manager the lockfile names: `pnpm-lock.yaml` means pnpm,
`package-lock.json` means npm, `yarn.lock` means Yarn, and `bun.lock` or
`bun.lockb` means Bun. pnpm is the default-stack choice for new projects.

### pnpm

```yaml
- uses: actions/checkout@v4
# Reads the pnpm version from package.json "packageManager".
- uses: pnpm/action-setup@v4
- uses: actions/setup-node@v4
  with:
    node-version-file: ".nvmrc" # or the project's Node version file
    cache: "pnpm" # caches the pnpm store, keyed on the lockfile
- run: pnpm install --frozen-lockfile
```

Every pnpm example in this file assumes `package.json` sets `packageManager`,
such as `"packageManager": "pnpm@<version>"`. Without it, `pnpm/action-setup`
fails with "No pnpm version is specified". Set the version in one place only:
either in `package.json` or with `with: version:` on the action. Setting both
fails when the versions differ.

`pnpm/action-setup` must run before `setup-node` so the `pnpm` cache option can
find pnpm.

### npm

```yaml
- uses: actions/checkout@v4
- uses: actions/setup-node@v4
  with:
    node-version-file: ".nvmrc"
    cache: "npm"
- run: npm ci
```

`npm ci` installs exactly what `package-lock.json` records and fails if it is
out of date. `npm install` may rewrite the lockfile and produce non-reproducible
builds.

## Baseline CI: Lint and Test

Every workflow declares read-only permissions and cancels superseded runs on the
same ref. Jobs without `needs:` run in parallel.

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read

concurrency:
  # Cancel superseded CI runs on the same ref. Never reuse this for deploys.
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version-file: ".nvmrc"
          cache: "pnpm"
      - run: pnpm install --frozen-lockfile
      - run: pnpm lint

  test:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version-file: ".nvmrc"
          cache: "pnpm"
      - run: pnpm install --frozen-lockfile
      - run: pnpm test
```

## Type Check and Build

```yaml
type-check:
  runs-on: ubuntu-latest
  timeout-minutes: 10
  steps:
    - uses: actions/checkout@v4
    - uses: pnpm/action-setup@v4
    - uses: actions/setup-node@v4
      with:
        node-version-file: ".nvmrc"
        cache: "pnpm"
    - run: pnpm install --frozen-lockfile
    - run: pnpm type-check

build:
  runs-on: ubuntu-latest
  timeout-minutes: 15
  needs: [lint, type-check, test]
  steps:
    - uses: actions/checkout@v4
    - uses: pnpm/action-setup@v4
    - uses: actions/setup-node@v4
      with:
        node-version-file: ".nvmrc"
        cache: "pnpm"
    - run: pnpm install --frozen-lockfile
    - run: pnpm build
```

If the project has no `type-check` script, add one rather than inlining the
command in the workflow, so developers run the same check locally:

```json
{
  "scripts": {
    "type-check": "tsc --noEmit"
  }
}
```

When the project uses typed routes, generated route types must exist before
`tsc` runs. Run `next typegen` first on Next.js versions that provide it, or
type-check after `next build`.

When branch protection is part of the established setup or the approved design,
the required status checks must name these job names exactly; renaming a job
breaks the requirement silently.

### Next.js build cache

Cache build outputs only when builds are genuinely slow (more than about two
minutes). For Next.js, cache `.next/cache` in the build job. Place the step
after checkout and before installing dependencies, so `hashFiles` does not walk
`node_modules`:

```yaml
- uses: actions/cache@v4
  with:
    path: .next/cache
    # Exact hit when lockfile and sources match; restore-keys falls
    # back to the latest cache for the same lockfile.
    key:
      nextjs-${{ runner.os }}-${{ hashFiles('**/pnpm-lock.yaml') }}-${{
      hashFiles('**/*.ts', '**/*.tsx') }}
    restore-keys: |
      nextjs-${{ runner.os }}-${{ hashFiles('**/pnpm-lock.yaml') }}-
```

## Coverage

Generate a coverage report and keep it as an artifact or a pull request comment.
Fail the build on a threshold only when the project has established one, and set
that threshold in the test runner's configuration rather than in the workflow.

```yaml
test:
  runs-on: ubuntu-latest
  timeout-minutes: 15
  steps:
    - uses: actions/checkout@v4
    - uses: pnpm/action-setup@v4
    - uses: actions/setup-node@v4
      with:
        node-version-file: ".nvmrc"
        cache: "pnpm"
    - run: pnpm install --frozen-lockfile
    # pnpm forwards arguments after the script name to the script.
    - run: pnpm test --coverage
    - uses: actions/upload-artifact@v4
      if: always() # keep the report even when tests fail
      with:
        name: coverage-report
        path: coverage/
        retention-days: 7
```

With npm, pass runner arguments after a separator: `npm test -- --coverage`.

## End-to-End Tests

When the project uses Playwright for end-to-end tests:

```yaml
e2e:
  runs-on: ubuntu-latest
  timeout-minutes: 20
  needs: [build]
  steps:
    - uses: actions/checkout@v4
    - uses: pnpm/action-setup@v4
    - uses: actions/setup-node@v4
      with:
        node-version-file: ".nvmrc"
        cache: "pnpm"
    - run: pnpm install --frozen-lockfile
    # --with-deps also installs the OS libraries the browser needs.
    - run: pnpm exec playwright install --with-deps chromium
    - run: pnpm build
    - run: pnpm exec playwright test
    - uses: actions/upload-artifact@v4
      if: failure() # traces and screenshots for diagnosing failures
      with:
        name: playwright-report
        path: playwright-report/
        retention-days: 7
```

The Playwright configuration's `webServer` setting usually starts the built app;
keep the workflow's build command consistent with what that configuration
expects.

## Environment Variables

Next.js has two categories of environment variables:

- Build-time (`NEXT_PUBLIC_*`): inlined into the client bundle during
  `next build`. Set them in the build job's `env:`. Anything with this prefix is
  public; never put a secret in it.
- Runtime (server-only values such as `DATABASE_URL`): read by the server at
  runtime. Set them in the deployment environment, not in CI.

```yaml
build:
  runs-on: ubuntu-latest
  timeout-minutes: 15
  env:
    # Non-secret configuration variable, baked into the client bundle.
    NEXT_PUBLIC_API_URL: ${{ vars.NEXT_PUBLIC_API_URL }}
  steps:
    # ...setup and install as above
    - run: pnpm build
```

When the design has separate staging and production deployments, scope these
variables with GitHub environments so each build gets the right values.

## Dependency Audit

When the approved design adds dependency vulnerability auditing, run it on pull
requests and on a weekly schedule:

```yaml
audit:
  runs-on: ubuntu-latest
  timeout-minutes: 10
  steps:
    - uses: actions/checkout@v4
    - uses: pnpm/action-setup@v4
    - uses: actions/setup-node@v4
      with:
        node-version-file: ".nvmrc"
        cache: "pnpm"
    - run: pnpm install --frozen-lockfile
    - run: pnpm audit --audit-level=high
```

Start with dependency auditing before heavier scanners, and do not add tools
whose findings nobody will triage.

## Static Analysis

When the approved design adds SAST with Semgrep, run the Semgrep CLI from its
container image. The older `returntocorp/semgrep-action` wrapper is deprecated.

```yaml
sast:
  runs-on: ubuntu-latest
  timeout-minutes: 10
  permissions:
    contents: read
    actions: read # required by upload-sarif in private repositories
    security-events: write # upload SARIF to code scanning
  container:
    image: semgrep/semgrep # pin per project policy, e.g. by digest
  steps:
    - uses: actions/checkout@v4
    - run: >-
        semgrep scan --config p/owasp-top-ten --config p/security-audit --sarif
        --output semgrep.sarif
    - uses: github/codeql-action/upload-sarif@v3
      with:
        sarif_file: semgrep.sarif
```

Choose rulesets with `--config`; `p/owasp-top-ten` targets common web
vulnerabilities. SARIF upload puts results in the repository's Security tab,
which requires code scanning to be available for the repository.

## Failure Notifications

When the design adds failure notifications for the default branch:

<!-- markdownlint-disable MD013 -->

```yaml
notify:
  runs-on: ubuntu-latest
  timeout-minutes: 5
  needs: [lint, type-check, test, build]
  # Runs only when a needed job failed on main. Pull requests from forks
  # cannot read secrets, so never notify from pull_request runs.
  if: failure() && github.ref == 'refs/heads/main'
  steps:
    - uses: slackapi/slack-github-action@v2
      with:
        webhook: ${{ secrets.SLACK_WEBHOOK_URL }}
        webhook-type: incoming-webhook
        payload: |
          {
            "text": "Next.js CI failed on main: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
          }
```

<!-- markdownlint-enable MD013 -->

## Update Automation

When the repository already uses Dependabot, or the design adds it, configure it
to update both npm dependencies and pinned actions:

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: "npm" # also covers pnpm and Yarn lockfiles
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```

## Common Pitfalls

- `npm install` instead of `npm ci`, or pnpm without `--frozen-lockfile`. Both
  can install different versions than the lockfile records.
- Caching `node_modules` directly. Cache the package manager's store instead;
  `setup-node` does this with `cache: "npm"` or `cache: "pnpm"`.
- `NEXT_PUBLIC_*` values missing from the client bundle. They are inlined at
  build time, so values set only in the deployment environment are `undefined`
  in the browser. Set them in the build job.
- Playwright browsers failing to launch. Install with `--with-deps` so the OS
  libraries are present.
- A lint script that calls `next lint`. Recent Next.js versions deprecated and
  then removed it; the script should call ESLint (or the project's linter)
  directly. Check the installed Next.js version.
- `hashFiles` globs that include `node_modules`, which slow every run. Keep
  cache steps before install or narrow the globs to source directories.
- Cache size limits. Repository caches have a size cap and evict
  least-recently-used entries; several lockfiles or large monorepos can thrash
  the cache.

## Authoritative References

- [GitHub Actions workflow syntax](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions)
- [Security hardening for GitHub Actions](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions)
- [actions/setup-node](https://github.com/actions/setup-node)
- [pnpm/action-setup](https://github.com/pnpm/action-setup)
- [actions/cache](https://github.com/actions/cache)
- [Next.js documentation](https://nextjs.org/docs)
- [Playwright: continuous integration](https://playwright.dev/docs/ci)
- [Semgrep sample CI configurations](https://semgrep.dev/docs/semgrep-ci/sample-ci-configs/)
- [Keeping actions up to date with Dependabot](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/keeping-your-actions-up-to-date-with-dependabot)
