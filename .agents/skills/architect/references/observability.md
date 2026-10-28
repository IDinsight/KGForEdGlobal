# Observability

Read when the technical design must decide logging, metrics, error tracking,
tracing, alerting, or service objectives for a service. Record the resulting
decisions in the technical design; Developer implements them. Navigator's ASSESS
mode can also use the checklist below for an informal maturity check. This
reference never overrides the protocol, the completed scope, established project
constraints, or official documentation for the installed tooling.

Observability answers three questions: what is happening right now (metrics),
why did something fail (logs + traces), and did something break before users
noticed (alerts). A production service needs all three pillars. Structured
logging alone is not observability.

This is an evaluation guide, not a tooling prescription. The maturity levels in
`../../scoper/references/maturity-levels.md` indicate when each signal is
commonly expected, but the completed scope decides what the design needs.

---

## Signals by Maturity

### Structured Logging (commonly from Level 4)

**What to check:**

- Does the service use one structured logger consistently? In the default stack
  this is Loguru with `serialize=True`; an established project may use another.
- Are logs emitted as structured JSON rather than mixed text?
- Does every log line include a request ID, timestamp, level, and service name?
- Are sensitive fields (passwords, tokens, PII) absent from log output?
- Is there a global error handler that logs unhandled exceptions with a stack
  trace?
- Are **requests** logged (method, path, status, duration, request ID)?
- Are **errors** logged with full exception context and request ID?
- Are **key transformations** logged (business decisions, state transitions,
  important data changes)?

**What good looks like:**

```json
{
  "text": "Request completed",
  "record": {
    "level": { "name": "INFO" },
    "time": "2025-06-30T10:00:00.000000+0000",
    "extra": {
      "request_id": "abc123",
      "method": "POST",
      "path": "/orders",
      "status": 201,
      "duration_ms": 45
    }
  }
}
```

Request IDs are generated at the middleware layer (UUID per request), attached
to the response as `X-Request-ID`, and propagated to every log line for that
request via a context variable, such as Loguru's patcher in the default stack.

**Logging categories commonly expected at this stage:**

- **Requests**: every HTTP request/response with method, path, status code,
  duration, and request ID.
- **Errors**: unhandled exceptions with full traceback, request context, and
  request ID.
- **Key transformations**: business logic decisions, state transitions, and
  important data operations that aid post-incident debugging.

**Common gaps at this stage:** two logging libraries in one service; log lines
that mix JSON and plain text; missing request ID on background tasks; stack
traces exposed in API responses; no logs for business-critical operations;
logging only requests but not errors or transformations.

---

### Metrics, Error Tracking, and Tracing (commonly from Level 4)

**Metrics — what to check:**

- Are the RED metrics exposed? Rate (requests/s), Errors (4xx/5xx rate),
  Duration (p50/p95/p99 latency).
- Is there a `/metrics` endpoint (or equivalent) for scraping?
- Is the metrics endpoint protected from public access?
- Are custom business metrics present for critical operations (e.g. orders
  created, payments processed)?

**Metrics — what good looks like:** an instrumentation library auto-instruments
the HTTP layer. Custom counters and histograms are added for business-critical
operations. Cardinality is kept low — no user IDs or full request paths as label
values.

**Error tracking — what to check:**

- Is an error tracking service configured (e.g. Sentry, Rollbar, Honeybadger)?
- Are 4xx errors filtered out (client mistakes, not service bugs)?
- Are 5xx errors captured with full context: request, user, release version?
- Is the DSN stored as an environment variable, not hardcoded?

**Error tracking — what good looks like:** unhandled exceptions captured
automatically. Before-send filters drop 4xx and health check noise. User context
attached where auth is present. Release tracking enabled to correlate errors
with deploys.

**Distributed tracing — what to check:**

- Is tracing configured (e.g. OpenTelemetry with an OTLP exporter)?
- Are database queries and outbound HTTP calls included in spans?
- Is tracing disabled by default and enabled explicitly in staging/production?

**Alerting — what to check:**

- Is there an alert for sustained error rate above threshold (e.g. >1% over 5
  min)?
- Is there an alert for p95 latency above SLO?
- Is there a health check alert for the service going down?
- Are alerts actionable? Do they link to a runbook or dashboard?

**Common gaps at this stage:** metrics exposed but no dashboards; alerts
configured but never tested; traces collected but no sampling policy; error
tracking DSN committed to the repo.

---

### Service Objectives and Operational Readiness (commonly at Level 5)

**What to check:**

- Are SLIs (service-level indicators) defined and measured? Examples:
  availability, latency p99, error rate.
- Are SLOs (targets) documented and monitored with an error budget?
- Has a representative load test been run? Do results match the SLO under
  expected peak traffic?
- Are runbooks attached to every alert?
- Is there an on-call rotation with documented escalation paths?
- Are dashboards used by the team, not just created and forgotten?

**What good looks like:** a single dashboard showing the three or four signals
that matter most. Alerts page on sustained budget burn, not individual spikes.
Load test results are committed to the repo alongside the test scripts. Runbooks
are linked from alerts and reviewed quarterly.

**Common gaps at this stage:** SLOs defined but error budgets not tracked; load
tests run once and never repeated; runbooks out of date with the current
architecture.

---

## Assessment Checklist

For each dimension, mark as: **present**, **partial**, or **absent**. Note
evidence found.

| Signal                      | Commonly expected from | Evidence to look for                              |
| --------------------------- | ---------------------- | ------------------------------------------------- |
| Structured JSON logs        | 4                      | Log format in middleware or logging config        |
| Request ID on every line    | 4                      | Middleware generating and propagating UUID        |
| No secrets in logs          | 3                      | Log statements reviewed for token/password fields |
| Global error handler        | 3                      | Exception handler registered at app level         |
| RED metrics exposed         | 4                      | Metrics endpoint + instrumentation library        |
| Error tracking configured   | 4                      | SDK initialization in app entry point             |
| Tracing configured          | 4                      | OTel or equivalent, even if disabled by default   |
| Alerts defined              | 4                      | Alert rules in monitoring config or IaC           |
| SLIs/SLOs documented        | 5                      | SLO definitions in docs or monitoring config      |
| Load test results           | 5                      | Test scripts and results in repo                  |
| Runbooks linked from alerts | 5                      | Alert annotations reference runbook URL           |

---

## Design Guidance

When designing observability, consult the following in order:

1. **Stack reference**, such as `../../developer/references/fastapi.md`, for
   framework-specific instrumentation libraries and wiring patterns.
2. **Platform documentation** — your deployment target (AWS CloudWatch, GCP
   Cloud Monitoring, Datadog, Grafana Cloud) dictates export format and agent
   configuration.
3. **OpenTelemetry documentation** — for portable tracing and metrics across
   platforms.

Do not bake a specific monitoring vendor into the application code where a
standard exporter (OTLP, StatsD, Prometheus) achieves the same result with less
coupling.
