# GitHub Actions for Python Services

Read when the work creates or changes GitHub Actions workflows for a Python API
or service, such as FastAPI or Django. It supplements
`../styles/github-actions.md` and never overrides the protocol, the approved
design, repository-enforced configuration, or established project conventions.
When the existing workflows, installed tool versions, or official documentation
differ from this file, follow them.

## Using These Examples

- Examples use major version tags (`@v4`) for readability, and those tags may be
  behind each action's current release. Check the current major version and pin
  per project policy. When no policy is established, pin third-party actions to
  a full commit SHA with a version comment, as shown under
  [Action Pinning and Update Automation](#action-pinning-and-update-automation).
- Workflows run the project's established lint, type-check, test, and build
  commands. The tests, fixtures, and test configuration they run belong to
  Tester; the workflow only invokes them.
- Deployment targets, environments and promotion, production approval gates,
  branch protection and required checks, new scanning tools or external
  services, and OIDC trust are architecture when not already established.
  Sections that depend on one of these say so.
- Secrets appear only as `${{ secrets.NAME }}`. Name them `<SERVICE>_<PURPOSE>`,
  for example `SLACK_WEBHOOK_URL`, and report each new secret by name and
  purpose.

## Workflow Layout

Organize workflows by concern. One `ci.yml` is enough until the design adds
deployment or scheduled scanning; then split by purpose.

| Workflow                | Trigger                      | Purpose                                     |
| ----------------------- | ---------------------------- | ------------------------------------------- |
| `ci.yml`                | `push`, `pull_request`       | Lint, type check, test, build               |
| `deploy-staging.yml`    | `push` to the default branch | When the design adds a staging deploy       |
| `deploy-production.yml` | `release` or manual          | When the design adds production deploy      |
| `security.yml`          | `schedule`, `pull_request`   | When the design adds audit or SAST scanning |

Starting timeouts before you have observed run times: 10 minutes for lint, type
check, and build; 15 for tests; 30 for deployment. Adjust from real durations.

In a monorepo, filter triggers to the service and the shared packages it depends
on, and run commands from the service directory:

```yaml
on:
  push:
    paths:
      - "apps/api/**"
      - "packages/shared/**"

defaults:
  run:
    working-directory: apps/api
```

`defaults.run` applies only to `run:` steps. Point setup actions at the service
explicitly, for example with `setup-uv`'s `working-directory` or
`cache-dependency-glob` inputs.

## Runtime Setup

Use the project's established package manager. `uv` is the default-stack choice
for new projects; projects that install from `requirements.txt` use `pip`.

### uv

```yaml
- uses: actions/checkout@v4
- uses: astral-sh/setup-uv@v4
  with:
    enable-cache: true # caches uv's package store, keyed on uv.lock
- run: uv sync --frozen
```

`--frozen` installs exactly what `uv.lock` records and fails rather than
updating it. If the project has no lockfile, `uv sync` still works, but report
the missing lockfile as a gap rather than generating one silently. `uv` installs
the Python version that `.python-version` or `requires-python` selects, so a
separate `setup-python` step is usually unnecessary.

### pip

```yaml
- uses: actions/checkout@v4
- uses: actions/setup-python@v5
  with:
    python-version-file: ".python-version"
    cache: "pip"
- run: pip install -r requirements.txt
```

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
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .

  test:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      - run: uv sync --frozen
      - run: uv run pytest -v --tb=short
```

Use the linter, formatter, and test command the project already runs locally;
Ruff and pytest are shown because they are the common defaults.

## Type Check and Build

```yaml
type-check:
  runs-on: ubuntu-latest
  timeout-minutes: 10
  steps:
    - uses: actions/checkout@v4
    - uses: astral-sh/setup-uv@v4
      with:
        enable-cache: true
    - run: uv sync --frozen
    - run: uv run mypy app # the project's package path

build:
  runs-on: ubuntu-latest
  timeout-minutes: 10
  needs: [lint, type-check, test]
  steps:
    - uses: actions/checkout@v4
    - uses: astral-sh/setup-uv@v4
      with:
        enable-cache: true
    - run: uv build # sdist and wheel; needs a [build-system] table
```

For a containerized service, build the image instead:

```yaml
build:
  runs-on: ubuntu-latest
  timeout-minutes: 15
  needs: [lint, type-check, test]
  steps:
    - uses: actions/checkout@v4
    - run: docker build -t app:ci .
```

When branch protection is part of the established setup or the approved design,
the required status checks must name these job names exactly; renaming a job
breaks the requirement silently.

## Coverage

Generate a coverage report and keep it as an artifact or a pull request comment.
Fail the build on a threshold only when the project has established one.

```yaml
test:
  runs-on: ubuntu-latest
  timeout-minutes: 15
  steps:
    - uses: actions/checkout@v4
    - uses: astral-sh/setup-uv@v4
      with:
        enable-cache: true
    - run: uv sync --frozen
    # Add --cov-fail-under=<threshold> only when one is established.
    - run: >-
        uv run pytest -v --tb=short --cov=app --cov-report=xml
        --cov-report=term-missing
    - uses: actions/upload-artifact@v4
      if: always() # keep the report even when tests fail
      with:
        name: coverage-report
        path: coverage.xml
        retention-days: 7
```

## Database Service Containers

When tests need a real database, run it as a service container with a pinned
image tag and a health check rather than mocking it:

```yaml
test:
  runs-on: ubuntu-latest
  timeout-minutes: 15
  services:
    postgres:
      image: postgres:16 # pin the major version the service runs
      env:
        POSTGRES_USER: test
        POSTGRES_DB: test_db
        # No password: acceptable only for an ephemeral CI container.
        POSTGRES_HOST_AUTH_METHOD: trust
      ports:
        - 5432:5432
      # Jobs wait for the health check before steps start.
      options: >-
        --health-cmd="pg_isready -U test" --health-interval=10s
        --health-timeout=5s --health-retries=5
  env:
    DATABASE_URL: postgresql://test@localhost:5432/test_db
  steps:
    - uses: actions/checkout@v4
    - uses: astral-sh/setup-uv@v4
      with:
        enable-cache: true
    - run: uv sync --frozen
    - run: uv run pytest -v --tb=short
```

Match the image's major version to the one the service runs in its deployed
environment.

## Matrix Builds

When the project supports several Python versions:

```yaml
test:
  runs-on: ubuntu-latest
  timeout-minutes: 15
  strategy:
    fail-fast: false # report every failing version, not just the first
    matrix:
      python-version: ["3.11", "3.12", "3.13"]
  env:
    # Overrides .python-version so each leg really uses its version.
    UV_PYTHON: ${{ matrix.python-version }}
  steps:
    - uses: actions/checkout@v4
    - uses: astral-sh/setup-uv@v4
      with:
        enable-cache: true
    - run: uv sync --frozen
    - run: uv run pytest -v --tb=short
```

Installing an interpreter with `uv python install` alone does not make `uv sync`
use it when `.python-version` pins another version; set `UV_PYTHON` or pass
`--python`.

## Dependency Audit

When the approved design adds dependency vulnerability auditing, run it on pull
requests and on a weekly schedule:

```yaml
audit:
  runs-on: ubuntu-latest
  timeout-minutes: 10
  steps:
    - uses: actions/checkout@v4
    - uses: astral-sh/setup-uv@v4
      with:
        enable-cache: true
    - run: uv sync --frozen
    - run: uv run pip-audit # audits the synced project environment
```

`pip-audit` must be in the project's development dependency group for `uv run`
to find it. Start with dependency auditing before heavier scanners, and do not
add tools whose findings nobody will triage.

## Static Analysis

When the approved design adds SAST for Python code, Bandit is a common choice:

```yaml
sast:
  runs-on: ubuntu-latest
  timeout-minutes: 10
  steps:
    - uses: actions/checkout@v4
    - uses: astral-sh/setup-uv@v4
      with:
        enable-cache: true
    - run: uv sync --frozen
    - run: uv run bandit -r app -ll # -ll: medium and high severity
```

Use `-lll` to report only high severity. Configure exclusions in the project's
Bandit configuration rather than on the command line.

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
            "text": "CI failed on main: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
          }
```

<!-- markdownlint-enable MD013 -->

## Action Pinning and Update Automation

A full commit SHA is the only immutable reference to an action version. Keep the
human-readable version in a trailing comment so updates stay reviewable:

```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
- uses: astral-sh/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86 # v5.4.2
```

Verify each SHA against the action's release tag before committing it. When the
repository already uses Dependabot, or the design adds it, configure it to
update both Python dependencies and pinned actions:

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: "pip" # also reads pyproject.toml
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```

Check Dependabot's current ecosystem list for direct `uv.lock` support before
relying on the `pip` ecosystem to update a uv lockfile.

## Common Pitfalls

- Installing without the lockfile. Bare `pip install <package>` or `uv sync`
  without `--frozen` can install different versions in CI than in development.
- Caching `.venv` directly. Cache the package manager's store instead;
  `setup-uv` does this with `enable-cache: true`, and `setup-python` with
  `cache: "pip"`.
- A service-container health check that names a user the container never
  created. The default user is `postgres`; when the check runs
  `pg_isready -U test`, set `POSTGRES_USER: test`.
- `mypy` failing on missing stubs. Add stub packages such as `types-requests` to
  the development dependencies. Treat `--ignore-missing-imports` as a temporary
  workaround and report it as a known limitation.
- Matrix legs that silently test one version. See
  [Matrix Builds](#matrix-builds).
- Cache size limits. Repository caches have a size cap and evict
  least-recently-used entries; several lockfiles or large monorepos can thrash
  the cache.

## Authoritative References

- [GitHub Actions workflow syntax](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions)
- [Building and testing Python](https://docs.github.com/en/actions/guides/building-and-testing-python)
- [Using uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/)
- [actions/setup-python](https://github.com/actions/setup-python)
- [actions/upload-artifact](https://github.com/actions/upload-artifact)
- [Security hardening for GitHub Actions](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions)
- [Keeping actions up to date with Dependabot](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/keeping-your-actions-up-to-date-with-dependabot)
- [pip-audit](https://github.com/pypa/pip-audit)
- [Bandit documentation](https://bandit.readthedocs.io/)
