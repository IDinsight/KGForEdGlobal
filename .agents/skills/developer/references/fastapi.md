# FastAPI Reference

Read when the project uses FastAPI and the work creates or changes API routes,
services, schemas, persistence access, or project setup. It supplements
`../styles/fastapi.md` and never overrides the protocol, the approved design,
repository-enforced configuration, or established project conventions. When the
installed version or official documentation differs from this file, follow the
installed version.

Security patterns such as headers, refresh tokens, account lockout, and audit
logging live in `security-fastapi.md`.

## Default Stack

The IDinsight default stack for a new FastAPI service is FastAPI, SQLModel,
pydantic-settings, Alembic, PostgreSQL through psycopg 3, PyJWT, pwdlib with
Argon2, Loguru, and slowapi, managed with `uv` on Python 3.12. The examples in
this file use that stack. An existing project keeps its established libraries.
Adding or replacing a dependency is architecture when the approved design has
not already settled it.

When the project uses the default stack:

- Use `pyjwt` for JWTs, not `python-jose`, which is unmaintained.
- Use `pwdlib[argon2]` for password hashing, not `passlib`, which is
  unmaintained and breaks with modern `bcrypt` releases.
- Use `psycopg[binary]`, the psycopg 3 driver, with the `postgresql+psycopg://`
  connection URL prefix.
- Run project commands through `uv run`, for example `uv run pytest` or
  `uv run alembic upgrade head`.

When the approved design selects MongoDB instead of a relational database,
Beanie, backed by PyMongo Async, replaces SQLModel and Alembic. The layer rules
below still apply. See `../../architect/references/mongodb.md` for setup,
lifespan, modeling, migration, and index guidance.

## Layer Responsibilities

Each layer has one job and never does another layer's job.

- Router: HTTP concerns only. Parse the request, validate input, inject
  dependencies including auth, call the service, and map the result to a
  response schema and status code. No business rules and no direct database
  access.
- Service: business logic, rules, orchestration, invariants, and data-dependent
  authorization. Services are framework-agnostic: they take `session: Session`
  as the first parameter, return domain values or sentinels (`None`, `False`),
  never raise `HTTPException`, and never read the request object. In the default
  stack, services write queries inline through the session.
- Persistence or repository module: when the approved design or the established
  structure separates query logic into query modules or a `repository.py`,
  services call those functions instead of writing queries. Treat adding either
  layer as architecture when it is not already established. See
  [Repository Layer](#repository-layer).
- Model: the ORM entity, in `<feature>/model.py`.
- Schema: request and response shapes (DTOs), in `<feature>/schema.py`. Keep
  schemas separate from models and declare a schema, never a table model, as the
  `response_model`.

DTOs cross the HTTP boundary, ORM entities cross the database boundary, and
domain models, when the project has them, live between the two.

## Project Layout

Match the existing project's layout. Do not reorganize folders as part of a
feature change.

The default-stack layout uses one package per feature:

```text
app/
├── main.py            # app creation, middleware, router registration
├── models.py          # imports every feature model for Alembic
├── utils/             # config, database, security, logging, limiter
├── dependencies/      # reusable FastAPI dependencies such as auth
└── orders/            # one package per feature
    ├── __init__.py
    ├── model.py       # SQLModel table class (ORM entity)
    ├── schema.py      # Pydantic request and response shapes
    ├── router.py      # HTTP concerns only
    ├── service.py     # business logic; queries inline through the session
    └── repository.py  # only when the design includes a repository layer
```

Smaller projects grow toward this shape:

- A throwaway spike may keep routes in `app/main.py`, with inline Pydantic
  models or an optional `app/schemas.py`, and no layers.
- A database-backed prototype adds `models.py`, `utils/` for config and
  database, and feature packages with `model.py`, `schema.py`, `router.py`, and
  `service.py`.
- When the design adds authentication, structured logging, and rate limiting,
  auth dependencies move into `dependencies/`, and `utils/` gains security,
  logging, and limiter modules.

## Project Setup

Use this section when the approved design establishes a new FastAPI service on
the default stack.

<!-- markdownlint-disable MD013 -->

```bash
mkdir <project_name> && cd <project_name>
uv init --name <project_name> --python 3.12
rm -f main.py hello.py
curl -fsSL -o .gitignore \
  https://raw.githubusercontent.com/github/gitignore/refs/heads/main/Python.gitignore
```

<!-- markdownlint-enable MD013 -->

Append `.env`, `.env.test`, and `*.db` to `.gitignore`. Commit an `.env.example`
that documents every variable with placeholder values:

<!-- markdownlint-disable MD013 -->

```text
SECRET_KEY=[SECRET_KEY]
DATABASE_URL=postgresql+psycopg://[DB_USER]:[DB_PASSWORD]@localhost:5432/[DB_NAME]
TEST_DATABASE_URL=postgresql+psycopg://[DB_USER]:[DB_PASSWORD]@localhost:5432/[TEST_DB_NAME]
CORS_ORIGINS=["http://localhost:3000"]
```

<!-- markdownlint-enable MD013 -->

Create `.dockerignore` with one pattern per line:

```text
.venv
.git
__pycache__
*.pyc
.pytest_cache
.ruff_cache
.mypy_cache
tests
.env
.env.test
```

### Dependencies

Install only what the approved design needs:

```bash
# Core API
uv add "fastapi[standard]"

# Relational persistence, settings, and migrations
uv add sqlmodel pydantic-settings "pydantic[email]" alembic "psycopg[binary]"

# MongoDB persistence and migrations, instead of the relational dependencies
uv add beanie pymongo pydantic-settings "pydantic[email]"

# Authentication, rate limiting, form login, and structured logging
uv add pyjwt "pwdlib[argon2]" slowapi python-multipart loguru

# Development tools used for self-checks
uv add --dev pytest httpx ruff mypy
```

Observability tooling depends on the hosting platform, so its choice is
architecture. Common options include `prometheus-fastapi-instrumentator`,
`sentry-sdk[fastapi]`, and `opentelemetry-sdk`.

## Settings

Read configuration through one `pydantic-settings` class in
`app/utils/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    project_name: str = "API"
    app_version: str = "0.1.0"
    environment: str = "development"

    # Required secrets have no default, so startup fails fast when unset.
    secret_key: str
    database_url: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    test_database_url: str | None = None
    # Read from the environment as JSON, for example '["https://a.org"]'.
    cors_origins: list[str] = ["http://localhost:3000"]
    rate_limit_enabled: bool = True

    # PostgreSQL pool sizing, connect retries, and slow-query logging.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 1800
    db_pool_timeout: int = 30
    db_connect_retries: int = 5
    db_connect_retry_interval_seconds: float = 2.0
    db_slow_query_ms: int = 500


settings = Settings()
```

- Never give `secret_key` or a credential-bearing URL a working default.
- `pydantic-settings` parses list and dict fields from environment variables as
  JSON, so `CORS_ORIGINS=https://a.org` fails while
  `CORS_ORIGINS=["https://a.org"]` works.
- mypy reports a missing argument for `Settings()` when required fields have no
  default unless the `pydantic.mypy` plugin is enabled. See
  [Lint, Format, and Type Check](#lint-format-and-type-check).

## Database Engine and Session

`app/utils/database.py` owns the engine and the session dependency:

```python
from collections.abc import Iterator

from sqlmodel import Session, create_engine

from app.utils.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # replace connections the server has closed
)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
```

- Do not call `SQLModel.metadata.create_all()` at startup. Alembic owns the
  schema.
- When the design calls for a tuned PostgreSQL pool, pass
  `pool_size=settings.db_pool_size`, `max_overflow=settings.db_max_overflow`,
  `pool_recycle=settings.db_pool_recycle`, and
  `pool_timeout=settings.db_pool_timeout` to `create_engine`.
- Handlers and services receive the session through `Depends(get_session)`. They
  never create their own engine or session.

## Models

Use `SQLModel` with `table=True`. The primary key is `int | None` defaulting to
`None`. Set an explicit `__tablename__`. Index by access pattern: index the
columns that frequent `WHERE`, `JOIN`, and `ORDER BY` clauses depend on when the
access pattern justifies the write cost, and check the query plan for
performance-sensitive queries rather than indexing reflexively.

```python
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="users.id", index=True)
    status: str = Field(default="pending", index=True)
    delivery_address: str = Field(max_length=500)
    total: float = Field(default=0.0, ge=0)
    # timezone=True stores an aware timestamp; the default column type
    # drops the offset on PostgreSQL.
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=DateTime(timezone=True),
    )
```

- Use `Decimal` with `max_digits` and `decimal_places` for currency, because
  float rounding errors accumulate. Pydantic v2 serializes `Decimal` as a JSON
  string by default, so the response type is a contract decision.
- Comparing a naive datetime read from the database with an aware
  `datetime.now(timezone.utc)` raises `TypeError`. Keep timestamp columns
  consistently timezone-aware.

Register every model in `app/models.py` after creating it:

```python
from app.orders.model import Order  # noqa: F401
from app.users.model import User  # noqa: F401
```

Alembic autogenerate only detects models imported there. Each feature's
`model.py` defines its tables, and `app/models.py` re-imports them in one place.

## Migrations

Schema changes go through Alembic. Never use `create_all()` and never apply a
manual `ALTER`.

Initialize once per project:

```bash
uv run alembic init migrations
```

Then configure the generated files:

- In `migrations/env.py`, import `app.models` so autogenerate sees every model,
  read the database URL from settings, and set
  `target_metadata = SQLModel.metadata`.
- In `migrations/script.py.mako`, add `import sqlmodel` below
  `import sqlalchemy as sa`. Generated revisions reference types such as
  `sqlmodel.sql.sqltypes.AutoString` and fail to import without it.

```python
# migrations/env.py (excerpt)
from alembic import context
from sqlmodel import SQLModel

import app.models  # noqa: F401  registers every feature model
from app.utils.config import settings

config = context.config
# Alembic's config uses configparser interpolation, so a literal "%" in a
# URL-encoded password must be doubled.
config.set_main_option(
    "sqlalchemy.url", settings.database_url.replace("%", "%%")
)
target_metadata = SQLModel.metadata
```

Generate, review, and apply a revision:

```bash
uv run alembic revision --autogenerate -m "create orders table"
# Review the generated file under migrations/versions/ before applying.
uv run alembic upgrade head
```

- Review every autogenerated revision before applying it. Autogenerate can miss
  renames, which appear as a drop plus an add, server defaults, enum changes,
  and missing indexes.
- Confirm with the user before applying a migration.
- If a revision contains `drop_table`, `drop_column`, or `drop_index`, never
  apply it automatically. Report the command and a backup step so the user can
  run it after taking the backup.
- In container deployments, run `alembic upgrade head` as a deploy step before
  the application starts, not from application startup code.

## Schemas

Separate Base, Create, Update, and Read schemas with Pydantic v2:

```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrderBase(BaseModel):
    delivery_address: str = Field(max_length=500)


class OrderCreate(OrderBase):
    pass


class OrderUpdate(BaseModel):
    delivery_address: str | None = Field(default=None, max_length=500)


class OrderRead(OrderBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    status: str
    total: float
    created_at: datetime
```

- `OrderCreate` is the request body for `POST`.
- `OrderUpdate` makes every field optional for partial updates.
- Create and Update schemas carry only client-settable fields. The service sets
  the owner from the authenticated user, and status changes go through dedicated
  service operations that check the transition, so neither appears in a request
  body. See `security-fastapi.md`.
- `OrderRead` is the `response_model` and always includes the primary key. Use
  `ConfigDict(from_attributes=True)`, not the Pydantic v1 `class Config`.
- Never expose sensitive fields such as password hashes, tokens, or internal
  flags in a Read schema. When working from an API specification, sensitive
  fields belong on the model but not on any response schema.
- Use `Literal` or an `Enum` for fixed value sets so FastAPI validates them and
  returns `422` automatically.

## Routers

Use `APIRouter` with `prefix` and `tags`. Handlers stay thin: inject
dependencies, call the service, and translate `None` or `False` into the right
HTTP error.

```python
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session

from app.dependencies.auth import get_admin_user, get_current_user
from app.orders.model import Order
from app.orders.schema import OrderCreate, OrderRead
from app.orders.service import (
    create_order,
    delete_order,
    get_order,
    list_orders,
)
from app.users.model import User
from app.utils.database import get_session

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("/", response_model=list[OrderRead])
def read_orders(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[Order]:
    return list_orders(session, current_user.id, offset=offset, limit=limit)


@router.get("/{order_id}", response_model=OrderRead)
def read_order(
    order_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Order:
    # None covers both a missing order and another user's order, so the
    # response does not reveal which orders exist.
    order = get_order(session, current_user.id, order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )
    return order


@router.post(
    "/", response_model=OrderRead, status_code=status.HTTP_201_CREATED
)
def create_new_order(
    order_in: OrderCreate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Order:
    order = create_order(session, current_user.id, order_in)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Order already exists"
        )
    return order


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_order(
    order_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_admin_user),
) -> None:
    if not delete_order(session, order_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )
```

- Paginate every list endpoint with bounded `offset` and `limit` parameters that
  set `le`. Never return an unbounded collection, including filtered and search
  endpoints such as `/search` or `/findByStatus`.
- Apply auth through dependencies, never in the service. In the default stack,
  `Depends(get_current_user)` admits any authenticated user and
  `Depends(get_admin_user)` admits administrators only. Pass
  `dependencies=[Depends(get_current_user)]` to `APIRouter` when every route in
  the router needs it.
- Authentication alone does not authorize access to a particular object. When
  orders belong to users, the router passes `current_user.id` and the service
  filters by owner in the query. See `security-fastapi.md`.
- Register each new router in `app/main.py`:

  ```python
  from app.orders.router import router as order_router

  app.include_router(order_router)
  ```

- When the project uses slowapi, rate-limit a route by adding
  `@limiter.limit("N/minute")` below the route decorator and `request: Request`
  to the handler signature. slowapi needs the route decorator on top and the
  `request` parameter to find the client.
- Group handler parameters into a schema object when there are more than about
  three.

## Services

Services are plain functions that hold the business logic. `session: Session` is
always the first parameter. Return the object on success, `None` when not found
or on conflict, and `bool` for deletes.

```python
from sqlmodel import Session, select

from app.orders.model import Order
from app.orders.schema import OrderCreate, OrderUpdate


def list_orders(
    session: Session, customer_id: int, offset: int = 0, limit: int = 50
) -> list[Order]:
    statement = (
        select(Order)
        .where(Order.customer_id == customer_id)
        .order_by(Order.id)
        .offset(offset)
        .limit(limit)
    )
    return list(session.exec(statement).all())


def get_order(
    session: Session, customer_id: int, order_id: int
) -> Order | None:
    # Scope by owner in the query so another user's order looks missing.
    statement = select(Order).where(
        Order.id == order_id, Order.customer_id == customer_id
    )
    return session.exec(statement).first()


def create_order(
    session: Session, customer_id: int, order_in: OrderCreate
) -> Order | None:
    # The owner comes from the authenticated user, never the request body.
    order = Order.model_validate(order_in, update={"customer_id": customer_id})
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


def update_order(
    session: Session, customer_id: int, order_id: int, order_in: OrderUpdate
) -> Order | None:
    order = get_order(session, customer_id, order_id)
    if order is None:
        return None
    # exclude_unset applies only the fields the client actually sent.
    for field, value in order_in.model_dump(exclude_unset=True).items():
        setattr(order, field, value)
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


def delete_order(session: Session, order_id: int) -> bool:
    order = session.get(Order, order_id)
    if order is None:
        return False
    session.delete(order)
    session.commit()
    return True
```

Structure each service function as validate (existence, uniqueness,
permissions), perform the operation, then return the result or a sentinel.

- Name functions `create_x`, `get_x`, `update_x`, `delete_x`, and `list_x`.
- Use `Model.model_validate(schema_in)`. `from_orm` is deprecated in Pydantic
  v2.
- Use `model_dump(exclude_unset=True)` for partial updates, not `.dict()`.
- Enforce uniqueness and other invariants explicitly before inserting. Do not
  rely only on database constraint errors.
- Never raise `HTTPException` in a service. Return `None` or `False`, and let
  the router translate the sentinel into an HTTP error.
- Perform bulk creates and updates in one transaction with `session.add_all()`
  and a single commit. Never loop over service calls that each commit.
- Use `session.get(Model, id)` for primary-key lookups; it uses the session's
  identity map.
- Filter, sort, and slice with `where()`, `order_by()`, `offset()`, and
  `limit()` in the query, never in Python after fetching.
- Avoid N+1 queries. Load related data for list results with a join or
  `selectinload`, not a per-item helper called in a loop.

## Repository Layer

Do not add a repository layer unless the approved design includes one. Without
it, query logic stays inline in the service.

A repository earns its place when a service is genuinely cluttered with data
access: long multi-clause selects, the same query reused across services, or
query logic that obscures the business rule. A simple single-table filter used
once does not qualify. A repository is not a generic CRUD wrapper for every
model. When a service seems to need one and the design does not include it,
treat the choice as architecture rather than adding it.

When the design includes it, repository functions live in
`app/<feature>/repository.py`, take `session: Session` first, and contain no
business rules. The service imports the repository; the router never touches it.

```python
# app/orders/repository.py: only the queries that earned extraction
from sqlmodel import Session, select

from app.orders.model import Order


def get_active_for_customer(
    session: Session, customer_id: int
) -> Order | None:
    statement = select(Order).where(
        Order.customer_id == customer_id, Order.status != "cancelled"
    )
    return session.exec(statement).first()


def save(session: Session, order: Order) -> Order:
    session.add(order)
    session.commit()
    session.refresh(order)
    return order
```

```python
# app/orders/service.py: business logic only, delegates data access
from sqlmodel import Session

from app.orders import repository as order_repo
from app.orders.model import Order
from app.orders.schema import OrderCreate


def create_order(
    session: Session, customer_id: int, order_in: OrderCreate
) -> Order | None:
    if order_repo.get_active_for_customer(session, customer_id):
        return None  # the router maps this to 409
    order = Order.model_validate(order_in, update={"customer_id": customer_id})
    return order_repo.save(session, order)
```

## Responses and Errors

Use native FastAPI responses. Do not add a custom `{ success, data, error }`
wrapper.

| Scenario          | Pattern                                                            |
| ----------------- | ------------------------------------------------------------------ |
| Successful GET    | Return the object or list; FastAPI serializes via `response_model` |
| Successful POST   | Return the created object with `status_code=201`                   |
| Successful DELETE | Return nothing with `status_code=204`                              |
| Not found         | `raise HTTPException(status_code=404, detail="X not found")`       |
| Validation error  | FastAPI returns `422` automatically for schema violations          |
| Business conflict | `raise HTTPException(status_code=409, detail="...")`               |
| Unauthenticated   | `raise HTTPException(status_code=401, ...)` in the auth dependency |
| Forbidden         | `raise HTTPException(status_code=403, ...)` in `get_admin_user`    |

How routers interpret service sentinels:

| Operation        | Sentinel means                  | HTTP response   |
| ---------------- | ------------------------------- | --------------- |
| Create (`POST`)  | Business or uniqueness conflict | `409 Conflict`  |
| Get or update    | Not found                       | `404 Not Found` |
| Delete (`False`) | Not found                       | `404 Not Found` |

- `DELETE` returns `204` with no body. Never return `200` with
  `{"message": "..."}` for a delete.
- Keep `detail` messages human-readable without exposing internals such as stack
  traces, SQL, or constraint names.

## Authentication Utilities

Choosing the authentication, session, or token mechanism is architecture. When
the approved design selects JWT bearer tokens with app-managed passwords, the
default-stack utilities live in `app/utils/security.py`:

```python
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.utils.config import settings

password_hash = PasswordHash.recommended()  # Argon2id


def hash_password(plain: str) -> str:
    return password_hash.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return password_hash.verify(plain, hashed)


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    return jwt.encode(
        {"sub": subject, "exp": expire},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def decode_token(token: str) -> str | None:
    try:
        # Pass an explicit allowlist; never trust the token's own header.
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None
```

`app/dependencies/auth.py` provides `get_current_user` and `get_admin_user`
using `OAuth2PasswordBearer`. It raises `401` for missing, invalid, or expired
tokens and `403` for authenticated users without the required role:

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from app.users.model import User
from app.utils.database import get_session
from app.utils.security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    subject = decode_token(token)
    if subject is None or not subject.isdigit():
        raise credentials_error
    user = session.get(User, int(subject))
    if user is None:
        raise credentials_error
    return user


def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not enough rights"
        )
    return current_user
```

- `app/auth/service.py` holds `signup_user` and `authenticate_user` as plain
  functions that return `None` on failure and never raise `HTTPException`.
- `app/auth/router.py` exposes `POST /auth/signup` (`201`), `POST /auth/login`
  (OAuth2 password form, which needs `python-multipart`), and `GET /auth/me`,
  translating `None` from the service into HTTP errors.
- When the project uses slowapi, apply `@limiter.limit("10/minute")` to signup
  and `@limiter.limit("5/minute")` to login. Both routes must accept
  `request: Request`.
- Deeper controls, including JWT claim validation, password-hash migration,
  refresh tokens, lockout, and audit events, are in `security-fastapi.md`.

## Application Entry Point

`app/main.py` creates the app, wires the lifespan, registers middleware and
exception handlers, and includes routers. The pieces depend on what the design
includes:

- Every app: a `FastAPI` instance and its routers.
- Browser clients: `CORSMiddleware` with exact origins from settings. Never
  combine `allow_origins=["*"]` with `allow_credentials=True`.
- Layered services with structured logging and rate limiting: a request ID
  middleware that generates an ID per request, stores it in `request_id_var`,
  and returns it as `X-Request-ID`; the slowapi exception handler; and a global
  handler for unhandled exceptions that logs the error and returns
  `{"detail": "Internal server error"}` without a stack trace.
- Database lifecycle and `setup_logging()` run in the `lifespan` context
  manager.
- For MongoDB, initialize Beanie once during lifespan startup and await the
  shared `AsyncMongoClient.close()` during shutdown. Follow
  `../../architect/references/mongodb.md`; do not create a client per request.

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.orders.router import router as order_router
from app.utils.config import settings
from app.utils.database import engine
from app.utils.limiter import limiter
from app.utils.logging import setup_logging
from app.utils.request_id import RequestIDMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    yield
    engine.dispose()  # close pooled connections on shutdown


app = FastAPI(
    title=settings.project_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# Starlette runs middleware in reverse order of registration: the last
# add_middleware call wraps all the others.
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.state.limiter = limiter
# slowapi types its handler for RateLimitExceeded, which mypy rejects.
app.add_exception_handler(
    RateLimitExceeded, _rate_limit_exceeded_handler  # type: ignore[arg-type]
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    logger.opt(exception=exc).error(
        "Unhandled exception", path=request.url.path
    )
    return JSONResponse(
        status_code=500, content={"detail": "Internal server error"}
    )


app.include_router(order_router)
```

The limiter lives in `app/utils/limiter.py`:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.utils.config import settings

limiter = Limiter(
    key_func=get_remote_address, enabled=settings.rate_limit_enabled
)
```

- `get_remote_address` reads the connecting address. Behind a reverse proxy, run
  Uvicorn with `--proxy-headers` and a specific `--forwarded-allow-ips`, or
  every client shares the proxy's limit.
- slowapi's default in-memory storage counts per process. With several workers
  or instances, the design must provide shared storage such as Redis through
  `storage_uri`.

The request ID middleware lives in `app/utils/request_id.py`:

```python
import time
import uuid

from loguru import logger
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import Response

from app.utils.logging import request_id_var


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Generate the ID server-side. Accept an inbound X-Request-ID only
        # when a trusted proxy sets it.
        request_id = uuid.uuid4().hex
        # ASGI servers such as Uvicorn run each request in its own task
        # context, so the value does not leak into other requests.
        request_id_var.set(request_id)
        start = time.perf_counter()
        response = await call_next(request)
        logger.info(
            "Request completed",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - start) * 1000),
        )
        response.headers["X-Request-ID"] = request_id
        return response
```

## Logging

Use the project's existing logger. Feature code logs business-relevant events
only; request logging belongs to middleware.

### Standard Library Logging

A spike or prototype without structured logging can use Python's `logging`
module with a basic format, stdout output, and no extra dependencies:

```python
# app/utils/logging.py
import logging
import sys


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
```

Call `setup_logging()` at startup and use `logging.getLogger(__name__)` in
modules.

### Loguru

The default stack uses Loguru for structured JSON logs with request ID
propagation. When the project uses Loguru, import `from loguru import logger`
rather than the standard library logger. Loguru replaces the standard library
logger entirely.

```python
# app/utils/logging.py
import contextvars
import sys
from typing import TYPE_CHECKING

from loguru import logger

from app.utils.config import settings

if TYPE_CHECKING:
    # Loguru exposes Record only to type checkers.
    from loguru import Record

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


def request_id_patcher(record: "Record") -> None:
    record["extra"]["request_id"] = request_id_var.get()


def setup_logging() -> None:
    logger.remove()  # drop the default stderr handler
    logger.configure(patcher=request_id_patcher)
    logger.add(
        sys.stdout,
        serialize=True,  # structured JSON output
        level="INFO" if settings.environment == "production" else "DEBUG",
        backtrace=True,
        # diagnose prints local variable values, which can include secrets.
        diagnose=settings.environment != "production",
    )
```

Usage in application code:

```python
from loguru import logger

# In a service: log key transformations.
logger.info(
    "Order status transition",
    order_id=order.id,
    from_status=old,
    to_status=new,
)

# In error handling.
logger.exception("Payment processing failed", user_id=user.id, amount=amount)
```

Each serialized line carries the message and the bound fields under `extra`,
abbreviated here:

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

Logging exists to debug production-like environments: every line should help an
engineer trace what happened during a request.

- Baseline for a layered service: requests (method, path, status, duration,
  request ID), errors with full context and the request ID, and key business
  decisions, state transitions, and data transformations, such as "user upgraded
  from plan X to Y", "payment intent created", or "retry attempt 2/3".
- Commonly expected for pre-production work, when the design calls for it: audit
  events, slow queries, rate-limit triggers, and outbound service calls.
- Commonly expected for production work, when the design calls for it:
  SLO-relevant metrics, security events, and data-retention actions.
- Never log passwords, tokens, authorization headers, cookies, or PII. Do not
  log routine success of trivial operations.

## Lint, Format, and Type Check

When the project uses Ruff and mypy without existing configuration, the
default-stack settings in `pyproject.toml` are:

```toml
[tool.ruff]
target-version = "py312"
line-length = 100
exclude = ["migrations/versions/*"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
disallow_untyped_defs = true
# Lets mypy treat Settings() fields as environment-provided.
plugins = ["pydantic.mypy"]
```

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app
```

Follow existing configuration when the project already has it.

## Containers

When the approved design includes container deployment, use a multi-stage
Dockerfile whose runtime stage runs as a non-root user with no secrets baked in.
Python 3.12 is shown; 3.13 and 3.14 are also valid targets. See the uv Docker
guide for base image options.

```dockerfile
FROM python:3.12-slim AS builder
# Pin a specific uv image tag in real projects instead of latest.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
WORKDIR /app
RUN groupadd -r appuser && useradd -r -g appuser appuser
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/app /app/app
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- `--no-editable` installs the project into the virtual environment instead of
  referencing the source tree, so the final stage needs only the virtual
  environment and the app package.
- Run `alembic upgrade head` as a deploy step before starting the container.

## Self-Checks

Run the project's existing checks as implementation-level self-checks:

```bash
uv run pytest -v
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```

Depending on the change, also confirm that:

- a new service starts with `uv run fastapi dev app/main.py` and the example
  endpoint responds;
- migrations apply cleanly to a development database, with the user's
  confirmation;
- with containers, `docker compose up` starts the stack and the health endpoint
  returns `200` with its checks passing;
- no unused imports or variables remain;
- primary-key lookups use `session.get`, and filtering, sorting, and slicing
  happen in the query;
- no N+1 query patterns remain;
- indexes follow the access patterns, with query plans checked for
  performance-sensitive queries;
- no response schema exposes sensitive fields;
- every protected route has its auth dependency;
- no new file contains hard-coded secrets or credentials.

Do not add or rewrite formal tests. Tester owns them.

## Authoritative References

- [FastAPI documentation](https://fastapi.tiangolo.com/)
- [FastAPI: Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/)
- [FastAPI: Security](https://fastapi.tiangolo.com/tutorial/security/)
- [FastAPI: OAuth2 with JWT](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)
- [FastAPI: CORS](https://fastapi.tiangolo.com/tutorial/cors/)
- [FastAPI: SQL databases (SQLModel)](https://fastapi.tiangolo.com/tutorial/sql-databases/)
- [SQLModel documentation](https://sqlmodel.tiangolo.com/)
- [Beanie ODM documentation](https://beanie-odm.dev/)
- [Pydantic v2 documentation](https://docs.pydantic.dev/latest/)
- [Pydantic v2 migration guide](https://docs.pydantic.dev/latest/migration/),
  covering `model_validate`, `model_dump`, and
  `ConfigDict(from_attributes=True)`
- [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [Alembic documentation](https://alembic.sqlalchemy.org/)
- [SQLAlchemy engine configuration](https://docs.sqlalchemy.org/en/20/core/engines.html)
- [psycopg 3 documentation](https://www.psycopg.org/psycopg3/docs/)
- [PyJWT documentation](https://pyjwt.readthedocs.io/)
- [pwdlib documentation](https://frankie567.github.io/pwdlib/)
- [Loguru documentation](https://loguru.readthedocs.io/)
- [slowapi documentation](https://slowapi.readthedocs.io/)
- [Ruff documentation](https://docs.astral.sh/ruff/)
- [uv documentation](https://docs.astral.sh/uv/)
- [uv: Docker integration](https://docs.astral.sh/uv/guides/integration/docker/)
- [uv: GitHub Actions integration](https://docs.astral.sh/uv/guides/integration/github/)
