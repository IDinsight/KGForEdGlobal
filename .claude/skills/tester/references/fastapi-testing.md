# FastAPI Testing Patterns

Read when verifying a FastAPI service and the assignment needs more depth than
`../styles/fastapi.md`. It supplements Tester's styles and never overrides the
protocol, the active contract, the test budget, repository-enforced
configuration, or the project's established fixtures and tools. When the
installed version or official documentation differs from this file, follow the
installed version.

The examples assume the default-stack layout: SQLModel models, feature modules
under `app/<feature>/`, Alembic migrations, PostgreSQL, and pytest with
`fastapi.testclient.TestClient`. Map them to the project's actual modules,
fixtures, and commands, and reuse existing fixtures before adding new ones.

## Selecting Cases

The budget allows at most five new high-value scenarios per changed source file
for the active change. The outcomes below are candidates, not a checklist.
Choose by the acceptance conditions, the technical criteria, and the riskiest
failure boundaries:

- the success response for each new operation, such as `201`, `200`, or `204`;
- access control: unauthenticated `401`, wrong role `403`, and cross-user or
  cross-tenant object access when ownership matters;
- validation `422` for the inputs the contract constrains;
- business conflicts `409` and missing resources `404` when the contract defines
  them;
- pagination bounds, response-field allowlists, and mass assignment when the
  change touches them.

When the contract needs more than the budget allows, record the uncovered
obligations and ask for a targeted increase rather than combining unrelated
outcomes into one test.

## Test Setup

Settings are usually built at import time, so set required environment values in
`tests/conftest.py` before importing the application:

```python
# tests/conftest.py: before any `app` import
import os
import secrets

# Test-only values; never reuse real secrets in tests.
os.environ.setdefault("SECRET_KEY", secrets.token_urlsafe(32))
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
```

Run tests against an isolated database on the same engine as production. Do not
substitute SQLite for PostgreSQL; dialects, constraints, and transaction
behavior differ. Apply the real migrations once per session and roll back each
test:

<!-- markdownlint-disable MD013 -->

```python
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from app.main import app
from app.utils.config import settings
from app.utils.database import get_session

test_engine = create_engine(settings.test_database_url)


@pytest.fixture(scope="session", autouse=True)
def run_migrations():
    # Build the schema exactly as production does.
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", settings.test_database_url)
    command.upgrade(cfg, "head")


@pytest.fixture(name="session")
def session_fixture():
    # Bind the session to an outer transaction. Commits made by the app become
    # savepoints (SQLAlchemy 2.0+), so the final rollback undoes everything the
    # test wrote, including data committed through routes.
    with test_engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()


@pytest.fixture(name="client")
def client_fixture(session):
    # Route the app's session dependency to the test transaction.
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()
```

<!-- markdownlint-enable MD013 -->

Replace external clients the same way, through `app.dependency_overrides`,
rather than patching module internals.

When feature tests need an authenticated user, a factory fixture keeps each test
explicit about the role it uses:

<!-- markdownlint-disable MD013 -->

```python
import secrets

import pytest

from app.users.model import User
from app.utils.security import hash_password


@pytest.fixture(name="auth_headers")
def auth_headers_fixture(client, session):
    def _make(email="user@example.test", admin=False):
        # A fresh random password per user; tests never depend on its value.
        password = secrets.token_urlsafe(16)
        user = User(email=email, hashed_password=hash_password(password), is_admin=admin)
        session.add(user)
        session.commit()
        token = client.post(
            "/auth/login", data={"username": email, "password": password}
        ).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _make
```

<!-- markdownlint-enable MD013 -->

## Route Tests

Assert the status code and the response or error body together. One scenario per
test keeps failures readable. An example selection for an orders router:

<!-- markdownlint-disable MD013 -->

```python
def test_create_order_success(client, auth_headers):
    response = client.post(
        "/orders/",
        json={"delivery_address": "123 Main St"},
        headers=auth_headers(),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    # Response allowlist: internal fields never leave the API.
    assert "internal_fraud_score" not in body


def test_create_order_missing_required_field(client, auth_headers):
    response = client.post("/orders/", json={}, headers=auth_headers())
    assert response.status_code == 422


def test_create_order_unauthenticated(client):
    response = client.post("/orders/", json={"delivery_address": "A"})
    assert response.status_code == 401


def test_get_order_of_another_user_forbidden(client, auth_headers):
    owner = auth_headers(email="owner@example.test")
    order_id = client.post(
        "/orders/", json={"delivery_address": "A"}, headers=owner
    ).json()["id"]
    other = auth_headers(email="other@example.test")
    assert client.get(f"/orders/{order_id}", headers=other).status_code in (403, 404)


def test_list_orders_rejects_limit_above_maximum(client, auth_headers):
    response = client.get("/orders/?limit=10000", headers=auth_headers())
    assert response.status_code == 422
```

<!-- markdownlint-enable MD013 -->

The cross-user case accepts `403` or `404` only when the contract leaves that
choice open; assert the exact code when it does not.

## Service Tests

Exercise business rules directly with the session fixture, without the HTTP
client or auth headers. Use them for rules that are awkward to reach through
routes, such as partial updates or conflict detection:

<!-- markdownlint-disable MD013 -->

```python
from app.orders.schema import OrderCreate, OrderUpdate
from app.orders.service import create_order, update_order


def test_create_order_conflict_returns_none(session):
    create_order(session, 1, OrderCreate(delivery_address="A"))
    assert create_order(session, 1, OrderCreate(delivery_address="B")) is None


def test_update_order_partial_leaves_unset_fields(session):
    order = create_order(session, 1, OrderCreate(delivery_address="Old"))
    updated = update_order(session, 1, order.id, OrderUpdate(delivery_address="New"))
    assert updated.delivery_address == "New"
    assert updated.status == "pending"
```

<!-- markdownlint-enable MD013 -->

Match the project's failure signal; the examples assume services return `None`
or `False` rather than raising.

## Security Behavior

Select from these when the change touches the mechanism:

- missing, malformed, and expired tokens, and wrong issuer or audience;
- revoked or reused refresh tokens when the design rotates them;
- lockout thresholds, the `429` response with `Retry-After`, and identical
  behavior for unknown accounts when the design adds lockout;
- CORS preflight from a disallowed origin, and CSRF on cookie-authenticated
  mutations;
- a deliberate unhandled error returning the generic error body with no stack
  trace, and startup failing when required secrets are missing;
- audit events with the expected content and redacted logs when the design adds
  audit logging.

## Naming and Running

Name tests `test_<action>_<resource>_<scenario>` unless the project has another
convention, and run them with the project's established command, such as
`uv run pytest -v`. Record the exact command and result in the verification
report.

## Authoritative References

- [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/)
- [FastAPI testing dependencies with overrides](https://fastapi.tiangolo.com/advanced/testing-dependencies/)
- [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)
- [Alembic command API](https://alembic.sqlalchemy.org/en/latest/api/commands.html)
