# Deployment: Google Cloud Platform

Read when the technical design must choose, configure, or change deployment to
Google Cloud, with Cloud Run as the primary compute target. Record the resulting
decisions in the technical design; Developer implements the workflows once the
design selects this option. It never overrides the protocol, the completed
scope, established project constraints, or official documentation for the
platform.

Deployment targets, environments, promotion paths, and production approval gates
are Architect decisions. Railway is the default target for new projects and
Google Cloud is supported when the organization or infrastructure requires it;
see `default-stack.md`. The maturity levels in
`../../scoper/references/maturity-levels.md` indicate when staging, approval
gates, and automated rollback are commonly expected, but the completed scope
decides what the design needs. Workflow examples use major-version action tags
for readability; Developer pins them per the project's policy, or to full commit
SHAs when none is established.

## Overview

The standard GCP deployment flow from GitHub Actions:

1. Authenticate with GCP using Workload Identity Federation (preferred) or a
   service account key.
2. Build a container image and push it to Artifact Registry (or GCR).
3. Deploy the image to Cloud Run.
4. Run a health check against the deployed service.

Workload Identity Federation is the preferred auth method — it avoids storing
long-lived service account keys as GitHub secrets.

## Secrets Required

### Workload Identity Federation (preferred)

| Secret/Variable       | Purpose                                                                                                                                |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `GCP_PROJECT_ID`      | GCP project ID                                                                                                                         |
| `WIF_PROVIDER`        | Workload Identity Provider resource name (e.g., `projects/123/locations/global/workloadIdentityPools/github/providers/github-actions`) |
| `WIF_SERVICE_ACCOUNT` | Service account email (e.g., `deploy@project-id.iam.gserviceaccount.com`)                                                              |

### Service account key (fallback)

| Secret           | Purpose                                 |
| ---------------- | --------------------------------------- |
| `GCP_PROJECT_ID` | GCP project ID                          |
| `GCP_SA_KEY`     | Base64-encoded service account key JSON |

Use service account keys only when Workload Identity Federation cannot be
configured (e.g., the GCP org restricts WIF). Rotate keys regularly and scope
the service account to the minimum required permissions.

Both deploy workflows below read the same secret names. Store them as
environment secrets on the `staging` and `production` GitHub environments, with
a separate project for each. With one repository-level `GCP_PROJECT_ID`, both
workflows deploy the same Cloud Run service, so every merge to the default
branch reaches production without the approval gate.

---

## Authentication

### Workload Identity Federation

```yaml
- uses: google-github-actions/auth@v2
  with:
    workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
    service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}
```

One-time setup in GCP:

1. Create a Workload Identity Pool and Provider linked to the GitHub OIDC
   issuer.
2. Grant the provider's principal set the `roles/iam.workloadIdentityUser` role
   on the deploy service account.
3. Grant the service account the minimum roles: `roles/run.developer`,
   `roles/artifactregistry.writer`, and `roles/iam.serviceAccountUser`. The
   workflows below build with Cloud Build, which also needs
   `roles/cloudbuild.builds.editor` and project-level Cloud Storage permissions
   to create, get, and list buckets and upload objects, because
   `gcloud builds submit` stages the source in `gs://<project>_cloudbuild`.

The `google-github-actions/auth` action documentation has the full setup steps.

### Service account key

```yaml
- uses: google-github-actions/auth@v2
  with:
    credentials_json: ${{ secrets.GCP_SA_KEY }}
```

---

## Build and Push Container Image

### Using Cloud Build (remote build)

<!-- markdownlint-disable MD013 -->

```yaml
- uses: google-github-actions/setup-gcloud@v2

- name: Build and push
  env:
    GCP_PROJECT_ID: ${{ secrets.GCP_PROJECT_ID }}
    IMAGE:
      ${{ vars.GCP_REGION }}-docker.pkg.dev/${{ secrets.GCP_PROJECT_ID
      }}/app/${{ github.event.repository.name }}:${{ github.sha }}
  run: |
    gcloud builds submit \
      --tag "$IMAGE" \
      --project "$GCP_PROJECT_ID"
```

<!-- markdownlint-enable MD013 -->

Cloud Build runs the Docker build on GCP infrastructure. Useful when the build
requires GCP-internal resources or when GitHub runners are too slow.

### Using Docker (local build)

<!-- markdownlint-disable MD013 -->

```yaml
- name: Configure Docker for Artifact Registry
  run:
    gcloud auth configure-docker ${{ vars.GCP_REGION }}-docker.pkg.dev --quiet

- name: Build and push
  env:
    IMAGE:
      ${{ vars.GCP_REGION }}-docker.pkg.dev/${{ secrets.GCP_PROJECT_ID
      }}/app/${{ github.event.repository.name }}:${{ github.sha }}
  run: |
    docker build -t "$IMAGE" .
    docker push "$IMAGE"
```

<!-- markdownlint-enable MD013 -->

---

## Deploy to Cloud Run

### Staging Deployment

Commonly expected from maturity Level 4, when merges to the default branch
deploy to staging.

<!-- markdownlint-disable MD013 -->

```yaml
name: Deploy Staging
on:
  push:
    branches: [main]

permissions:
  contents: read
  id-token: write

concurrency:
  group: deploy-staging
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    environment: staging
    env:
      # Pass secrets and context values through env, never ${{ }} in run.
      GCP_PROJECT_ID: ${{ secrets.GCP_PROJECT_ID }}
      GCP_REGION: ${{ vars.GCP_REGION }}
      SERVICE: ${{ github.event.repository.name }}
      IMAGE:
        ${{ vars.GCP_REGION }}-docker.pkg.dev/${{ secrets.GCP_PROJECT_ID
        }}/app/${{ github.event.repository.name }}:${{ github.sha }}
    steps:
      - uses: actions/checkout@v4

      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}

      - uses: google-github-actions/setup-gcloud@v2

      - name: Build and push
        run: |
          gcloud builds submit \
            --tag "$IMAGE" \
            --project "$GCP_PROJECT_ID"

      - name: Deploy to Cloud Run
        run: |
          gcloud run deploy "$SERVICE" \
            --image "$IMAGE" \
            --port 8000 \
            --region "$GCP_REGION" \
            --platform managed \
            --project "$GCP_PROJECT_ID"

      - name: Health check
        run: |
          SERVICE_URL=$(gcloud run services describe "$SERVICE" \
            --region "$GCP_REGION" \
            --project "$GCP_PROJECT_ID" \
            --format 'value(status.url)')
          for i in $(seq 1 12); do
            STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$SERVICE_URL/health" 2>/dev/null || echo "000")
            if [ "$STATUS" = "200" ]; then
              echo "Health check passed: $SERVICE_URL"
              exit 0
            fi
            echo "Attempt $i: got $STATUS, waiting..."
            sleep 10
          done
          echo "Health check failed after 2 minutes"
          exit 1
```

<!-- markdownlint-enable MD013 -->

### Production Deployment

Commonly expected from maturity Level 4, behind an approval gate.

<!-- markdownlint-disable MD013 -->

```yaml
name: Deploy Production
on:
  workflow_dispatch:
  release:
    types: [published]

permissions:
  contents: read
  id-token: write

concurrency:
  group: deploy-production
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    environment: production
    env:
      # Pass secrets and context values through env, never ${{ }} in run.
      GCP_PROJECT_ID: ${{ secrets.GCP_PROJECT_ID }}
      GCP_REGION: ${{ vars.GCP_REGION }}
      SERVICE: ${{ github.event.repository.name }}
      IMAGE:
        ${{ vars.GCP_REGION }}-docker.pkg.dev/${{ secrets.GCP_PROJECT_ID
        }}/app/${{ github.event.repository.name }}:${{ github.sha }}
    steps:
      - uses: actions/checkout@v4

      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}

      - uses: google-github-actions/setup-gcloud@v2

      - name: Build and push
        run: |
          gcloud builds submit \
            --tag "$IMAGE" \
            --project "$GCP_PROJECT_ID"

      - name: Deploy to Cloud Run
        run: |
          gcloud run deploy "$SERVICE" \
            --image "$IMAGE" \
            --port 8000 \
            --region "$GCP_REGION" \
            --platform managed \
            --project "$GCP_PROJECT_ID" \
            --no-traffic \
            --tag candidate

      - name: Verify new revision
        run: |
          # Revisions have no URL of their own. The candidate tag gives the new
          # revision a dedicated URL without sending it production traffic.
          CANDIDATE_URL=$(gcloud run services describe "$SERVICE" \
            --region "$GCP_REGION" \
            --project "$GCP_PROJECT_ID" \
            --format json |
            jq -r '.status.traffic[] | select(.tag == "candidate") | .url')
          STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$CANDIDATE_URL/health" 2>/dev/null || echo "000")
          if [ "$STATUS" != "200" ]; then
            echo "New revision health check failed: $STATUS"
            exit 1
          fi

      - name: Route traffic to new revision
        run: |
          gcloud run services update-traffic "$SERVICE" \
            --region "$GCP_REGION" \
            --project "$GCP_PROJECT_ID" \
            --to-latest

      - name: Notify on failure
        if: failure()
        uses: slackapi/slack-github-action@v2
        with:
          webhook: ${{ secrets.SLACK_WEBHOOK_URL }}
          webhook-type: incoming-webhook
          payload: |
            {
              "text": "Production deploy FAILED: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
            }
```

<!-- markdownlint-enable MD013 -->

The `--no-traffic` and `--tag` pattern deploys the new revision without routing
traffic to it, verifies it at the tag's URL, then shifts traffic. This enables
zero-downtime deployments. `--no-traffic` fails when the service does not exist
yet, so create the production service once with a `gcloud run deploy` that omits
it before the first release.

---

## Rollback

Automated rollback is commonly expected at maturity Level 5.

Cloud Run keeps previous revisions. To roll back, route traffic to the previous
revision:

```yaml
- name: Rollback on failure
  if: failure()
  env:
    GCP_PROJECT_ID: ${{ secrets.GCP_PROJECT_ID }}
    GCP_REGION: ${{ vars.GCP_REGION }}
    SERVICE: ${{ github.event.repository.name }}
  run: |
    PREV_REVISION=$(gcloud run revisions list \
      --service "$SERVICE" \
      --region "$GCP_REGION" \
      --project "$GCP_PROJECT_ID" \
      --sort-by '~metadata.creationTimestamp' \
      --limit 2 \
      --format 'value(metadata.name)' | tail -1)
    gcloud run services update-traffic "$SERVICE" \
      --region "$GCP_REGION" \
      --project "$GCP_PROJECT_ID" \
      --to-revisions "$PREV_REVISION=100"
    echo "Rolled back to revision: $PREV_REVISION"
```

---

## Other GCP Targets

### App Engine

Replace the Cloud Run deploy step:

```yaml
- name: Deploy to App Engine
  env:
    GCP_PROJECT_ID: ${{ secrets.GCP_PROJECT_ID }}
  run: gcloud app deploy app.yaml --project "$GCP_PROJECT_ID" --quiet
```

App Engine deployments are slower (2–5 minutes) and don't support the
`--no-traffic` canary pattern as cleanly. Use `--no-promote` and
`gcloud app services set-traffic` for traffic splitting.

### GKE (Google Kubernetes Engine)

GKE deployments use `kubectl` and are significantly more complex. The typical
flow:

1. Authenticate with `google-github-actions/auth`.
2. Get GKE credentials: `gcloud container clusters get-credentials`.
3. Update the image in the Kubernetes manifest or Helm chart.
4. Apply with `kubectl apply` or `helm upgrade`.
5. Wait for rollout: `kubectl rollout status`.

GKE deployments are outside the scope of this reference's standard patterns.
Consult the project's Kubernetes documentation.

---

## Gotchas

- **Workload Identity Federation setup.** WIF requires one-time setup in GCP
  (pool, provider, IAM bindings). If the auth step fails with "Unable to
  generate access token," the WIF configuration is incomplete. Check the
  pool/provider resource names and the IAM binding on the service account.
- **Artifact Registry vs Container Registry.** GCR (`gcr.io`) is legacy. New
  projects should use Artifact Registry (`<region>-docker.pkg.dev`). The auth
  and push commands differ slightly.
- **Cloud Build timeout.** Cloud Build has a default timeout of 10 minutes.
  Large Docker builds may exceed this. Set `--timeout=20m` on
  `gcloud builds submit` if needed.
- **Cloud Run cold starts.** Cloud Run scales to zero by default. The first
  request after idle incurs a cold start (1–10 seconds depending on the image
  size and startup time). Set `--min-instances=1` for production services that
  need consistent latency.
- **Health checks need invoker access.** Both workflows call the service with
  plain `curl`, but neither passes `--allow-unauthenticated`, so a service they
  create requires authentication and the check fails with `403`. Allow
  unauthenticated invocations for a public service. For a private service, send
  an identity token from an account with invoker access, or run the check
  through `gcloud run services proxy`. The production check must proxy with
  `--tag candidate`; without it, the proxy reaches the revision that is still
  serving traffic.
- **`id-token: write` permission.** Workload Identity Federation requires the
  `id-token: write` permission in the workflow. Without it, the auth step
  silently falls back to looking for a credentials JSON and fails.

---

## Authoritative References

- [Google Cloud — Cloud Run documentation](https://cloud.google.com/run/docs)
- [Google Cloud — Artifact Registry](https://cloud.google.com/artifact-registry/docs)
- [Google Cloud — Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation)
- [google-github-actions/auth](https://github.com/google-github-actions/auth)
- [google-github-actions/setup-gcloud](https://github.com/google-github-actions/setup-gcloud)
- [GitHub Actions — OpenID Connect for cloud deployments](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/about-security-hardening-with-openid-connect)
- [GitHub Actions workflow syntax](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions)
