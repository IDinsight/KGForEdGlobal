# LLM Provider API Testing Patterns

Read when the active change calls an LLM or embedding provider API, such as
OpenAI, Anthropic, or Gemini. It supplements Tester's styles and never overrides
the protocol, the active contract, the test budget, repository-enforced
configuration, or the project's established fixtures and tools. It never
authorizes a provider, SDK, client design, or test tool that the design or the
project's established tooling has not already chosen. When the installed SDK or
its official documentation differs from this file, follow the installed version.

The examples assume the design gives the code a thin, project-owned provider
client: `get_llm_client` in a FastAPI service and `@/lib/llm/client` in a
Next.js app. Map them to the project's actual modules, types, and fixtures.

## Selecting Cases

The budget allows at most five new high-value scenarios per changed source file
for the active change. The failure cases below are candidates, not a checklist.
Choose by the acceptance conditions, the technical criteria, and the riskiest
failure boundaries. Each selected case is a scenario, and so is each row of a
parameterized table. When the contract needs more than the budget allows, record
the uncovered obligations and ask for a targeted increase.

## The Seam

- Test against the thin provider client that the code receives as a dependency.
  In FastAPI, replace it through `app.dependency_overrides`. In Next.js, mock
  the client module with a factory. Without one, Vitest loads the real module,
  which can import the SDK, read settings, and require a key. Elsewhere, pass
  the fake where the code receives its client.
- Do not patch SDK internals, such as the SDK's resource classes or private
  methods.
- Browser-level routing, such as Playwright's `page.route`, sees only the
  requests the page makes, not the provider calls the server makes. Test server
  functions, Server Actions, and Route Handlers directly, in isolation, with the
  mocked client. Run a browser test of that path only against a server the
  design lets run with a fake provider. Otherwise record what stays unverified
  as a gap under `../SKILL.md`; a required obligation it leaves uncovered stays
  open.
- If the code under test imports `server-only`, which throws outside a React
  Server environment, use the project's existing Vitest handling for it, such as
  a mock in the test or its setup file.
- If the code builds its SDK client inline and offers no seam, do not refactor
  application code to add one. Use another controlled boundary the project
  already relies on, after confirming that it intercepts the SDK's requests.
  Otherwise route the gap under Defects and Routing in `../SKILL.md`. Only the
  application change goes to Developer; the fakes and tests stay with Tester.

## Fakes

- A fake keeps the contract of the boundary it replaces: it returns, raises, or
  streams what the real client does there, in the same sync or async form.
  Record the requests it receives so tests can check them.
- Use the provider's real response shape where provider data enters project
  code. When the client passes provider responses through, the fake at the seam
  returns that shape. When the client maps them to project types, the fake
  returns those types, and the client's own tests supply provider-shaped
  payloads through what the client exposes: an injected SDK client, the SDK's
  documented HTTP hook, or a mapping function. The OpenAI and Anthropic SDKs
  accept a custom HTTP client in Python and a custom `fetch` in TypeScript.
- Before relying on an HTTP mocking tool the project already uses, confirm that
  it sees the SDK's requests. The Anthropic Python SDK, for example, sends them
  through `httpx2`, which tools that patch `httpx` do not see by default.
- Build provider-shaped payloads from the installed SDK's response types or the
  provider's documented examples, not from memory. Model the details that break
  parsing:
  - Tool calls: OpenAI returns a function call's `arguments` as a JSON string in
    both Chat Completions and the Responses API, and Chat Completions warns that
    it is not always valid JSON. Anthropic returns a `tool_use` block whose
    `input` is an object.
  - Streaming: chunks arrive in order, a tool call's input can arrive in
    fragments, such as Anthropic's `input_json_delta` events, and the stream
    ends with a stop signal.
  - Stop signals: each API reports the same outcome differently, so model the
    one the real API sends:
    - truncation: Chat Completions `length`, a Responses API `incomplete` status
      with reason `max_output_tokens`, Anthropic `max_tokens`, or Gemini
      `MAX_TOKENS`;
    - refusal: the Chat Completions `refusal` message field, a Responses API
      output part of type `refusal`, or the Anthropic `refusal` stop reason;
    - content filter: Chat Completions `content_filter`, a Responses API
      `incomplete` status with reason `content_filter`, Gemini `SAFETY`, or a
      Gemini prompt blocked with no candidates.
  - Embeddings: return vectors with the configured dimension, and fixed values
    when a test depends on similarity order.

## Failure Cases

Choose among these as the contract requires:

- a timeout;
- a `429` rate limit with `Retry-After`;
- a `5xx` error;
- malformed or truncated output: tool arguments that are not valid JSON, a
  response cut off at the token limit, or a stream that sends an `error` event
  or stops early after it has started;
- a refusal;
- a content-filter block.

Use the stop signals under Fakes for the provider's shape of each outcome.

Know which layer retries. The OpenAI and Anthropic SDKs retry connection errors,
timeouts, `408`, `409`, `429`, and `5xx` responses twice by default, and the
application may add its own retries. Assert the retry behavior the contract
specifies. Keep waits short and bounded with the project's clock or the retry
settings the design exposes; a fake at the seam never runs the SDK's retries.

## Assertions

- Assert parsed or structured results and side effects: stored records, emitted
  events, retry counts, and no writes or partial results after a failure.
- Check what the code sends only where the contract constrains it, such as
  personal data removed before the call. Do not snapshot whole prompts.
- Do not assert the wording of a real or recorded model response. Text your own
  fake returns may be checked where the code passes it through.

## Recorded Responses

Use recordings only when the project already does. Then:

- scrub API keys, auth headers, account identifiers, and personal data from both
  the recorded requests and the responses;
- note how to refresh them in a test-owned place, such as the recording fixture
  or its helper, and in the report when you refresh them;
- treat a refresh as a live run under the rules below, and review the refreshed
  recording's diff like a snapshot change.

## Live Calls

- Except where a contract criterion below requires otherwise, default test runs
  and CI use no real API keys and make no network calls to the provider.
- In default runs, test setup assigns a placeholder key before the settings
  load. It overrides any real key from the environment or `.env`, so a request
  that escapes the fakes fails instead of reaching a real account. With
  pydantic-settings, environment variables take priority over `.env` values.
- Add a live smoke test only when the user asks for one. Mark it explicitly,
  with a registered pytest marker or the runner's equivalent, and run it only
  when an environment variable enables it. That run keeps the real key, and
  running it needs the user's permission.
- A live smoke run never counts as acceptance evidence. Record it under
  Execution Evidence as supplemental, with environment variable names and never
  their values.
- When a contract criterion itself requires evidence from the real provider,
  verify it only by the method and conditions the contract defines, as with
  quality criteria under Scope. Unless the contract says otherwise, that run is
  also opt-in and stays out of default runs and CI.

## Scope

Model-quality evaluation, such as golden datasets, accuracy thresholds, or
LLM-as-judge scoring, is separate from these functional tests and outside this
reference. A passing test with a fake says nothing about output quality, and a
quality score is not a functional result. Verify a quality criterion only by the
method and conditions the contract defines; if it defines none, route the gap
under Defects and Routing in `../SKILL.md`.

## FastAPI Example

Set the placeholder before the application is imported:

```python
# tests/conftest.py: before any `app` import
import os
import secrets

# Replaces any real key from the shell or `.env`, except in the opt-in live run.
if os.environ.get("RUN_LIVE_LLM_TESTS") != "1":
    os.environ["LLM_API_KEY"] = secrets.token_urlsafe(16)
```

Replace the client through the dependency override, and assert the stored result
and the failure outcome rather than model wording:

<!-- markdownlint-disable MD013 -->

```python
import pytest
from sqlmodel import select

from app.cases.model import Summary
from app.llm.client import Completion, RateLimitedError, ToolCall, get_llm_client
from app.main import app


class FakeLLMClient:
    """Stands in for the project's client: same methods, fixed results."""

    def __init__(self):
        self.requests = []
        self.result = None
        self.error = None

    async def complete(self, **request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture(name="fake_llm")
def fake_llm_fixture():
    fake = FakeLLMClient()
    app.dependency_overrides[get_llm_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_llm_client, None)


def test_create_summary_stores_tool_call_result(client, session, fake_llm, owner_headers, case_id):
    # The client's own result type; `arguments` stays the JSON string the
    # provider sent, so the route's parsing is exercised.
    fake_llm.result = Completion(
        tool_calls=[ToolCall(arguments='{"summary": "Two visits."}', name="record_summary")],
    )
    response = client.post(f"/cases/{case_id}/summary", headers=owner_headers)
    assert response.status_code == 201
    assert response.json()["summary"] == "Two visits."
    assert session.exec(select(Summary)).one().text == "Two visits."


def test_create_summary_rate_limited_stores_nothing(client, session, fake_llm, owner_headers, case_id):
    fake_llm.error = RateLimitedError(retry_after=30)
    response = client.post(f"/cases/{case_id}/summary", headers=owner_headers)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    # FastAPI's HTTPException body; match the project's error shape.
    assert set(response.json()) == {"detail"}
    assert session.exec(select(Summary)).all() == []
```

<!-- markdownlint-enable MD013 -->

## Next.js Example

Mock the client module with a factory and feed the code under test an ordered
stream:

```ts
import { streamReply } from "@/lib/llm/client";
import { collectReply, SAFE_MESSAGE } from "@/lib/chat/reply";

// A factory keeps the real client, its SDK, settings, and key from loading.
vi.mock("@/lib/llm/client", () => ({ streamReply: vi.fn() }));

describe("collectReply", () => {
  it("joins streamed text and reports the stop reason", async () => {
    // The client's own event type, in stream order.
    vi.mocked(streamReply).mockImplementation(async function* () {
      yield { type: "text", text: "Hel" };
      yield { type: "text", text: "lo" };
      yield { type: "stop", reason: "complete" };
    });

    await expect(collectReply("Hi")).resolves.toEqual({
      text: "Hello",
      stopReason: "complete",
    });
  });

  it("returns the safe message when the reply is filtered", async () => {
    vi.mocked(streamReply).mockImplementation(async function* () {
      yield { type: "stop", reason: "filtered" };
    });

    await expect(collectReply("Hi")).resolves.toMatchObject({
      text: SAFE_MESSAGE,
    });
  });
});
```

## Authoritative References

- [FastAPI testing dependencies with overrides](https://fastapi.tiangolo.com/advanced/testing-dependencies/)
- [Vitest module mocking](https://vitest.dev/guide/mocking/modules)
- [Playwright network](https://playwright.dev/docs/network)
- [OpenAI Python SDK](https://github.com/openai/openai-python)
- [Anthropic Python SDK](https://platform.claude.com/docs/en/api/sdks/python)
- [Anthropic streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- [Anthropic stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)
- [Gemini `generateContent`](https://ai.google.dev/api/generate-content)
