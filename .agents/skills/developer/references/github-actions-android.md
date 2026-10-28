# GitHub Actions for Android Apps

Read when the work creates or changes GitHub Actions workflows for an Android
app built with Kotlin, Jetpack Compose, and Gradle. It supplements
`../styles/github-actions.md` and never overrides the protocol, the approved
design, repository-enforced configuration, or established project conventions.
When the existing workflows, installed tool versions, or official documentation
differ from this file, follow them.

## Using These Examples

- Examples use major version tags (`@v4`) for readability, and those tags may be
  behind each action's current release. Check the current major version and pin
  per project policy. When no policy is established, pin third-party actions to
  a full commit SHA with a version comment
  (`uses: owner/action@<full-sha> # vX.Y.Z`), verified against the action's
  release tag.
- Workflows run the project's established Gradle tasks. The tests, fixtures, and
  test configuration they run belong to Tester; the workflow only invokes them.
- Deployment and distribution targets (Firebase App Distribution, Play Store
  tracks), environments and promotion, production approval gates, branch
  protection and required checks, new scanning tools or external services, and
  OIDC trust are architecture when not already established. Sections that depend
  on one of these say so.
- Secrets appear only as `${{ secrets.NAME }}`. Name them `<SERVICE>_<PURPOSE>`
  and report each new secret by name and purpose. Never commit keystores or
  service-account files.

## Workflow Layout

Organize workflows by concern. One `ci.yml` is enough until the design adds
distribution or release; then split by purpose.

| Workflow         | Trigger                      | Purpose                                          |
| ---------------- | ---------------------------- | ------------------------------------------------ |
| `ci.yml`         | `push`, `pull_request`, call | Lint, static analysis, unit tests, debug build   |
| `distribute.yml` | `push` to the default branch | When the design adds pre-release tester builds   |
| `release.yml`    | `release` or manual          | When the design adds Play Store release          |
| `security.yml`   | `schedule`, `pull_request`   | When the design adds dependency or SAST scanning |

Starting timeouts before you have observed run times: 10 to 15 minutes for lint,
static analysis, and unit tests; 20 to 25 for builds; 30 for emulator tests and
deployment. Gradle builds vary widely, so adjust from real durations.

## Runtime Setup

```yaml
- uses: actions/checkout@v4
- uses: actions/setup-java@v4
  with:
    distribution: "temurin"
    java-version: "17" # the JDK the Android Gradle Plugin requires
- uses: gradle/actions/setup-gradle@v4
```

Use the JDK version the project's Android Gradle Plugin requires (JDK 17 for AGP
8.x) or its configured Gradle toolchain. `setup-gradle` caches Gradle
distributions, dependencies, and build outputs; do not add a separate cache step
for Gradle. By default it writes cache entries only from the default branch and
restores them read-only elsewhere, so the first pull request run after a
dependency change can be slow.

## Baseline CI: Parallel Verification

Every workflow declares read-only permissions and cancels superseded runs on the
same ref. Keep independent verification jobs, such as lint, static analysis,
unit tests, and dependency checks, without `needs:` so they run in parallel.
Only build or deployment jobs need to wait for the checks whose success they
require.

Declare `workflow_call` when release or distribution workflows reuse `ci.yml`.
Use a fixed `ci-` concurrency prefix rather than `github.workflow`: a called
workflow receives the caller's workflow name, which can otherwise collide with
the caller's concurrency group.

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_call:

permissions:
  contents: read

concurrency:
  # Cancel superseded CI runs on the same ref. Never reuse this for releases.
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: "temurin"
          java-version: "17"
      - uses: gradle/actions/setup-gradle@v4
      - run: ./gradlew lint # lintDebug limits the run to one variant

  test:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: "temurin"
          java-version: "17"
      - uses: gradle/actions/setup-gradle@v4
      - run: ./gradlew testDebugUnitTest
```

## Static Analysis and Debug Build

Run the static analysis the project has configured, such as detekt or ktlint
(`./gradlew ktlintCheck`). Adding a new analyzer is a tooling decision for the
design.

```yaml
detekt:
  runs-on: ubuntu-latest
  timeout-minutes: 10
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-java@v4
      with:
        distribution: "temurin"
        java-version: "17"
    - uses: gradle/actions/setup-gradle@v4
    - run: ./gradlew detekt

build:
  runs-on: ubuntu-latest
  timeout-minutes: 20
  needs: [lint, detekt, test]
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-java@v4
      with:
        distribution: "temurin"
        java-version: "17"
    - uses: gradle/actions/setup-gradle@v4
    - run: ./gradlew assembleDebug
    - uses: actions/upload-artifact@v4
      with:
        name: debug-apk
        path: app/build/outputs/apk/debug/app-debug.apk
        retention-days: 7
```

When branch protection is part of the established setup or the approved design,
the required status checks must name these job names exactly; renaming a job
breaks the requirement silently.

## Coverage

When the project has Kover configured, generate its report with the unit tests
and keep it as an artifact. Fail the build on a threshold only when the project
has established one, and set it in the Kover configuration rather than in the
workflow.

```yaml
test:
  runs-on: ubuntu-latest
  timeout-minutes: 15
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-java@v4
      with:
        distribution: "temurin"
        java-version: "17"
    - uses: gradle/actions/setup-gradle@v4
    # Variant report task; names vary by Kover version.
    - run: ./gradlew testDebugUnitTest koverXmlReportDebug
    - uses: actions/upload-artifact@v4
      if: always() # keep the report even when tests fail
      with:
        name: coverage-report
        path: app/build/reports/kover/
        retention-days: 7
```

Projects that use JaCoCo instead run their configured report task, such as
`./gradlew jacocoTestReport`.

## Dependency Audit

When the approved design adds dependency vulnerability scanning with the OWASP
Dependency-Check Gradle plugin (`org.owasp.dependencycheck`), run it on pull
requests and on a weekly schedule:

```yaml
dependency-check:
  runs-on: ubuntu-latest
  timeout-minutes: 20
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-java@v4
      with:
        distribution: "temurin"
        java-version: "17"
    - uses: gradle/actions/setup-gradle@v4
    - run: ./gradlew dependencyCheckAnalyze
      env:
        # Read by the plugin's nvd settings; without a key, NVD updates
        # are very slow or rate-limited.
        NVD_API_KEY: ${{ secrets.NVD_API_KEY }}
```

Wire the key into the plugin's NVD settings as its documentation describes. When
the design prefers GitHub-native alerts instead, Dependabot (see
[Update Automation](#update-automation)) covers Gradle dependencies without a
plugin.

## Release Signing

When the design includes signed release builds in CI, keep the keystore out of
the repository. Store it as a base64-encoded secret, decode it at run time, and
delete it when the build finishes. With Play App Signing, this is the upload
key; Google re-signs the app with the app signing key.

The module reads signing values from the environment so local debug builds still
work without them:

```kotlin
// app/build.gradle.kts
android {
    signingConfigs {
        create("release") {
            // CI writes this file from a secret; it is never committed.
            storeFile = file("release.keystore")
            storePassword = System.getenv("KEYSTORE_PASSWORD")
            keyAlias = System.getenv("KEY_ALIAS")
            keyPassword = System.getenv("KEY_PASSWORD")
        }
    }
    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
        }
    }
}
```

```yaml
build-release:
  runs-on: ubuntu-latest
  timeout-minutes: 25
  needs: [lint, detekt, test]
  # Pushes to main only: pull requests from forks cannot read secrets.
  if: github.ref == 'refs/heads/main' && github.event_name == 'push'
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-java@v4
      with:
        distribution: "temurin"
        java-version: "17"
    - uses: gradle/actions/setup-gradle@v4
    - name: Decode keystore
      env:
        # Pass through env, never ${{ }} inside the script.
        KEYSTORE_BASE64: ${{ secrets.KEYSTORE_BASE64 }}
      run: echo "$KEYSTORE_BASE64" | base64 -d > app/release.keystore
    - run: ./gradlew assembleRelease
      env:
        KEYSTORE_PASSWORD: ${{ secrets.KEYSTORE_PASSWORD }}
        KEY_ALIAS: ${{ secrets.KEY_ALIAS }}
        KEY_PASSWORD: ${{ secrets.KEY_PASSWORD }}
    - name: Remove keystore
      if: always()
      run: rm -f app/release.keystore
    - uses: actions/upload-artifact@v4
      with:
        name: release-apk
        path: app/build/outputs/apk/release/app-release.apk
        retention-days: 30 # release artifacts outlive debug ones
```

## Pre-Release Distribution

When the approved design selects Firebase App Distribution for pre-release
testers, add a job after the signed release build. Authenticate with a service
account through `GOOGLE_APPLICATION_CREDENTIALS`; the older `firebase login:ci`
token flow is deprecated. When the design establishes OIDC with Workload
Identity Federation, use that instead of a long-lived key.

```yaml
distribute-firebase:
  runs-on: ubuntu-latest
  timeout-minutes: 10
  needs: [build-release]
  steps:
    - uses: actions/download-artifact@v4
      with:
        name: release-apk
    - uses: actions/setup-node@v4
      with:
        node-version: "lts/*"
    # Pin firebase-tools to a version per project policy.
    - run: npm install -g firebase-tools
    - name: Write service account credentials
      env:
        FIREBASE_SERVICE_ACCOUNT: ${{ secrets.FIREBASE_SERVICE_ACCOUNT }}
      run: printf '%s' "$FIREBASE_SERVICE_ACCOUNT" > "$RUNNER_TEMP/sa.json"
    - name: Distribute to testers
      env:
        GOOGLE_APPLICATION_CREDENTIALS: ${{ runner.temp }}/sa.json
        FIREBASE_APP_ID: ${{ secrets.FIREBASE_APP_ID }}
      run: |
        firebase appdistribution:distribute app-release.apk \
          --app "$FIREBASE_APP_ID" \
          --release-notes "Build $GITHUB_RUN_NUMBER from $GITHUB_SHA" \
          --groups "qa-testers"
    - name: Remove credentials
      if: always()
      run: rm -f "$RUNNER_TEMP/sa.json"
```

Grant the service account only the Firebase App Distribution role it needs. The
tester group name is a placeholder for the group the design names.

## Play Store Release

When the approved design selects Play Store release with an approval gate, build
an Android App Bundle (AAB) in a release workflow and upload it from a job bound
to a GitHub environment. Required reviewers on that environment provide the
approval gate; configuring them is part of the design, not the workflow.

```yaml
name: Release

on:
  release:
    types: [published]

permissions:
  contents: read

concurrency:
  # Never cancel a release in progress; it can leave a partial upload.
  group: release-${{ github.ref }}
  cancel-in-progress: false

jobs:
  verify:
    # The lint, detekt, test, and other selected CI jobs run in parallel inside
    # the called workflow. This caller can depend only on `verify`, not on those
    # internal job IDs.
    uses: ./.github/workflows/ci.yml

  build-aab:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    needs: [verify]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: "temurin"
          java-version: "17"
      - uses: gradle/actions/setup-gradle@v4
      - name: Decode keystore
        env:
          KEYSTORE_BASE64: ${{ secrets.KEYSTORE_BASE64 }}
        run: echo "$KEYSTORE_BASE64" | base64 -d > app/release.keystore
      - run: ./gradlew bundleRelease
        env:
          KEYSTORE_PASSWORD: ${{ secrets.KEYSTORE_PASSWORD }}
          KEY_ALIAS: ${{ secrets.KEY_ALIAS }}
          KEY_PASSWORD: ${{ secrets.KEY_PASSWORD }}
      - name: Remove keystore
        if: always()
        run: rm -f app/release.keystore
      - uses: actions/upload-artifact@v4
        with:
          name: release-aab
          path: app/build/outputs/bundle/release/app-release.aab
          retention-days: 30

  deploy-play-store:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    needs: [build-aab]
    environment: play-store # required reviewers gate this job
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: release-aab
      - uses: r0adkll/upload-google-play@v1
        with:
          serviceAccountJsonPlainText: ${{ secrets.PLAY_STORE_SERVICE_ACCOUNT }}
          packageName: com.example.app # the app's applicationId
          releaseFiles: app-release.aab
          track: internal # the track the design names
```

## Instrumented Tests on an Emulator

Instrumented tests, including Compose UI tests and most Room tests, need an
Android emulator. They are slow and more prone to flakiness in CI, so run them
when the project or design calls for them in CI, often on merges to the default
branch rather than every pull request.

<!-- markdownlint-disable MD013 -->

```yaml
instrumented-test:
  runs-on: ubuntu-latest
  timeout-minutes: 30
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-java@v4
      with:
        distribution: "temurin"
        java-version: "17"
    - uses: gradle/actions/setup-gradle@v4
    # Hardware acceleration: without KVM the emulator is too slow to use.
    - name: Enable KVM
      run: |
        echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' | sudo tee /etc/udev/rules.d/99-kvm4all.rules
        sudo udevadm control --reload-rules
        sudo udevadm trigger --name-match=kvm
    - uses: reactivecircus/android-emulator-runner@v2
      with:
        api-level: 34 # an API level the app supports
        arch: x86_64
        script: ./gradlew connectedDebugAndroidTest
```

<!-- markdownlint-enable MD013 -->

Do not add automatic retries to hide emulator flakiness; report the failure so
its cause can be found. With a matrix of API levels, set `fail-fast: false` so
every failing level is visible.

## Failure Notifications

When the design adds failure notifications for the default branch:

<!-- markdownlint-disable MD013 -->

```yaml
notify:
  runs-on: ubuntu-latest
  timeout-minutes: 5
  needs: [lint, detekt, test, build]
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
            "text": "Android CI failed on main: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
          }
```

<!-- markdownlint-enable MD013 -->

## Update Automation

When the repository already uses Dependabot, or the design adds it, configure it
to update both Gradle dependencies (including the version catalog) and pinned
actions:

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: "gradle"
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

- A non-executable `gradlew`, common after a commit from Windows. Fix the file
  mode in the repository (`git update-index --chmod=+x gradlew`) rather than
  adding `chmod` to every workflow.
- Gradle daemon memory. CI should not reuse daemons across builds.
  `setup-gradle` handles this; with raw Gradle, pass `--no-daemon` to avoid
  out-of-memory failures, and set JVM heap in `gradle.properties` rather than in
  the workflow.
- Keystore or service-account files left on the runner. Delete them in an
  `if: always()` step before unrelated later steps run.
- Interpolating secrets directly into `run:` scripts with `${{ }}`. Pass them
  through `env:` and quote the shell variable.
- Play Store rejecting an upload because its `versionCode` was already used.
  Derive `versionCode` by the project's convention, such as from the release tag
  or run number, and keep it increasing.
- Emulator flakiness. Raise timeouts for emulator jobs and consider running them
  on merges to the default branch only.
- Large APK and AAB artifacts accumulating. Set `retention-days`: short for
  debug builds, longer for release builds.

## Authoritative References

- [GitHub Actions workflow syntax](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions)
- [Security hardening for GitHub Actions](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions)
- [Using environments for deployment](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)
- [actions/setup-java](https://github.com/actions/setup-java)
- [gradle/actions (setup-gradle)](https://github.com/gradle/actions)
- [reactivecircus/android-emulator-runner](https://github.com/ReactiveCircus/android-emulator-runner)
- [r0adkll/upload-google-play](https://github.com/r0adkll/upload-google-play)
- [Sign your app](https://developer.android.com/studio/publish/app-signing)
- [Firebase CLI reference](https://firebase.google.com/docs/cli)
- [Firebase App Distribution](https://firebase.google.com/docs/app-distribution)
- [Kover: Kotlin code coverage](https://github.com/Kotlin/kotlinx-kover)
- [detekt](https://detekt.dev/docs/intro/)
- [OWASP Dependency-Check Gradle plugin](https://github.com/dependency-check/dependency-check-gradle)
- [Keeping actions up to date with Dependabot](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/keeping-your-actions-up-to-date-with-dependabot)
