# FastAPI Security Reference

Read when the project uses FastAPI and the work touches authentication,
authorization, sessions or tokens, secrets, input handling, response shaping,
security headers, CORS or CSRF, abuse controls, security logging, or dependency
hygiene. It supplements `../styles/fastapi.md` and `fastapi.md` and never
overrides the protocol, the approved design, repository-enforced configuration,
or established project conventions. When the installed version or official
documentation differs from this file, follow the installed version.

Many controls live outside Python code, in proxies, gateways, identity
providers, and cloud configuration. Do not assume application code alone
provides a control that the deployment is expected to supply.

## Principles

- Separate authentication from authorization. Knowing who the caller is does not
  decide what the caller may do.
- Do not add JWT merely to "add security". Session, cookie, token, and identity
  provider choices follow from revocation and client needs, and they are
  architecture when not already established.
- Treat browsers, mobile clients, request metadata, and every client-supplied
  identifier as untrusted.
- Keep secrets, credentials, signing operations, and privileged provider calls
  server-side and out of URLs, logs, analytics, errors, and responses.
- Minimize sensitive inputs, storage, and outputs. Fail closed when a security
  dependency fails.
- Treat middleware, hidden UI, CORS, security headers, API documentation
  settings, and obscurity as defense in depth, not primary access control.
- Never invent cryptography or weaken a control to make a check pass.
- Do not claim code is "secure" or OWASP-compliant without evidence. Record what
  remains unverified.

## Security-Relevant Surfaces

Before changing security-relevant code, locate the repository equivalents of:

- app creation, middleware, exception handlers, lifespan hooks, mounted apps,
  and OpenAPI or docs configuration;
- every router and alternate entry point, including WebSockets, callbacks and
  webhooks, background jobs, admin routes, and health or debug endpoints;
- `Depends`, `Security`, authentication dependencies, permission policies, and
  data-access filters;
- password, API-key, OAuth or OIDC, session, JWT, refresh, revocation, and
  recovery code;
- Pydantic request and response models, file uploads, serializers, and database
  queries;
- settings, secret loading, logging and redaction, CORS, trusted hosts, proxy
  headers, TLS termination, containers, and gateways;
- Python dependency manifests and lockfiles, CI security checks, migrations, and
  runbooks.

Look for bypasses near the change: routers missing the protected dependency,
direct service calls without authorization context, raw SQL or shell execution,
unsafe deserialization, user-controlled URLs or filenames, wildcard CORS, token
decoding options, debug mode, default secrets, and sensitive response fields.

## Authentication

Official FastAPI security guides change over time. Re-check them before
implementing or modernizing authentication.

### Password Hashing

- When the app manages passwords, hash new passwords with Argon2 through
  `pwdlib[argon2]` and `PasswordHash.recommended()`. Do not choose `passlib` or
  bcrypt for new work because an older example used it.
- Before migrating existing hashes, inventory the schemes in use. When bcrypt
  hashes must keep working, the approved design adds `pwdlib[argon2,bcrypt]`,
  configures Argon2 as current and bcrypt as a fallback, and rehashes on login.
  Remove the fallback only after evidence that migration is complete.
- Return the same generic message for an unknown account and a wrong password
  wherever account existence is sensitive, including login and recovery.

```python
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlmodel import Session

from app.users.model import User

# The first hasher is current; later hashers only verify legacy hashes.
password_hash = PasswordHash((Argon2Hasher(), BcryptHasher()))


def verify_and_upgrade(session: Session, user: User, plain: str) -> bool:
    valid, updated_hash = password_hash.verify_and_update(
        plain, user.hashed_password
    )
    if valid and updated_hash is not None:
        # Persist the Argon2 replacement in the same transaction as login.
        user.hashed_password = updated_hash
        session.add(user)
        session.commit()
    return valid
```

When the account does not exist, still spend hashing time so response timing
does not reveal which emails are registered:

```python
import secrets

# Computed once; it is never stored or compared with a real password.
DUMMY_HASH = password_hash.hash(secrets.token_urlsafe(16))


def authenticate(session: Session, email: str, plain: str) -> User | None:
    user = get_user_by_email(session, email)
    if user is None:
        password_hash.verify(plain, DUMMY_HASH)
        return None
    return user if verify_and_upgrade(session, user, plain) else None
```

### Tokens and Sessions

- In the default stack, new FastAPI JWT code uses `PyJWT`, matching the current
  official tutorial. RSA or ECDSA algorithms need `pyjwt[crypto]`.
- Treat existing `python-jose` usage as a supply-chain review signal: check its
  version, advisories, transitive dependencies, algorithm behavior, and
  migration compatibility. Replacing it is a dependency change and is
  architecture when not already settled.
- JWT is optional. Opaque server-side sessions or an external OIDC provider can
  fit better when immediate revocation, browser sessions, or centralized
  identity matter. That choice belongs to the design.
- When the project uses JWT, pin an algorithm allowlist, verify the signature
  and time claims, validate issuer and audience when tokens cross service
  boundaries, validate a stable `sub`, and keep claims non-sensitive. Never use
  a checked-in or default HMAC secret.

```python
import jwt

from app.utils.config import settings


def decode_access_token(token: str) -> dict[str, object] | None:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            # Never read the algorithm from the token header.
            algorithms=[settings.algorithm],
            # jwt_audience and jwt_issuer are project settings; drop both
            # when tokens never leave this service.
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
```

### CORS and CSRF

- For browser clients, list trusted origins exactly. Never combine
  `allow_origins=["*"]` with credentials. Restrict methods and headers to what
  the client needs, and check the deployed response headers, not only code.
- CORS is not CSRF protection or authentication. Cookie-authenticated mutations
  need the project's CSRF defense, such as `SameSite` cookies plus a token or
  origin check.

## Access Control

- Require authentication on every protected operation through a shared
  dependency. Cover every router, mounted sub-application, WebSocket, webhook,
  and admin endpoint, not only the main routers.
- Protect a whole router with
  `APIRouter(dependencies=[Depends(get_current_user)])` when every route needs
  it, so a new route cannot silently skip the check.
- Authorize function-level, object-level, and tenant-level access on the server,
  close to the data. Never trust client-supplied user, role, tenant, household,
  or owner IDs; derive them from the authenticated identity.
- Deny by default. Keep `401`, `403`, and `404` behavior consistent with the
  project's convention for missing authentication, insufficient permission, and
  objects the caller may not see.
- WebSocket routes cannot return `HTTPException`. Reject in the dependency with
  `WebSocketException(code=status.WS_1008_POLICY_VIOLATION)`. Browsers cannot
  set an `Authorization` header on a WebSocket handshake, so the design must say
  how WebSocket clients authenticate.

```python
from sqlmodel import Session, select

from app.orders.model import Order
from app.users.model import User


def get_order_for_user(
    session: Session, order_id: int, user: User
) -> Order | None:
    # Scope by owner in the query; the router maps None to the project's
    # not-found or forbidden convention.
    statement = select(Order).where(
        Order.id == order_id, Order.customer_id == user.id
    )
    return session.exec(statement).first()
```

## Input, Output, and Injection

- Pydantic validates shape, not authorization-relevant IDs, query cost, file
  size, or domain invariants. Check those explicitly.
- Prevent mass assignment by mapping only client-settable fields onto
  persistence models. Never accept fields such as `is_admin`, `owner_id`, or
  `tenant_id` in a Create or Update schema unless the design makes them
  client-settable and the service authorizes the change.
- Define response models that allowlist safe fields. Never serialize password
  hashes, tokens, internal flags, tenant IDs, or unnecessary PII.
- Use parameterized ORM and query APIs. Never interpolate untrusted input into
  SQL (including `text()`), shell commands, filesystem paths, templates, or
  dynamic imports. Call `subprocess` with an argument list and no `shell=True`.
- Never pass untrusted data to `pickle`, `yaml.load` without a safe loader, or
  archive extraction without validating member paths.
- Enforce request body, upload, pagination, and query-complexity limits at the
  earliest reliable layer, such as the proxy or the route.
- When the app fetches user-supplied URLs, restrict schemes, hosts, and ports,
  block loopback, private, link-local, and cloud metadata addresses, re-check
  every redirect, and set timeouts and response size limits.

## Security Headers

The baseline headers fit in one middleware with no extra dependency, in
`app/utils/security_headers.py`:

```python
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import Response

from app.utils.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers["X-Content-Type-Options"] = "nosniff"
        headers["X-Frame-Options"] = "DENY"
        # Modern browsers ignore the XSS auditor; "0" avoids legacy quirks.
        headers["X-XSS-Protection"] = "0"
        headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        headers["Permissions-Policy"] = (
            "geolocation=(), camera=(), microphone=()"
        )
        # Default for API responses; override per route for cacheable data.
        headers["Cache-Control"] = "no-store"
        if settings.environment == "production":
            # Only behind HTTPS; self-signed local setups break with HSTS.
            headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )
        return response
```

Register it in `app/main.py` after the CORS middleware, which makes it the
outermost layer so its headers also reach CORS preflight responses:

```python
from app.utils.security_headers import SecurityHeadersMiddleware

app.add_middleware(SecurityHeadersMiddleware)
```

- Never advertise the stack. Starlette's `MutableHeaders` has no `pop`, and
  Uvicorn adds its `server` header outside the app, so run Uvicorn with
  `--no-server-header` instead of trying to delete it in middleware.
- Add a `Content-Security-Policy` once allowed origins are known. Start with
  `default-src 'self'` and loosen only what the UI needs. FastAPI's `/docs` and
  `/redoc` pages load assets from a CDN by default, so a strict policy breaks
  them.
- When the design says API docs are not public in production, disable them with
  `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`.
- Behind a proxy, run Uvicorn with `--proxy-headers` and a specific
  `--forwarded-allow-ips` so client addresses used for rate limits and audit
  logs cannot be spoofed. Add `TrustedHostMiddleware` when the deployment does
  not already validate the `Host` header.

## Refresh Tokens

Use this section when the approved design calls for refresh tokens with rotation
and revocation, which is commonly expected for pre-production work.

Short-lived access tokens, typically 15 to 30 minutes, limit exposure when a
token leaks but force frequent re-login. A refresh token is a longer-lived
credential that exchanges for a new access token without re-authentication.

- Store refresh tokens hashed, so a compromised token store does not expose raw
  tokens. A SHA-256 digest suffices because the tokens are long and random.
- Rotate on every use: the presented token is invalidated immediately and a new
  one is issued.
- Logout deletes the user's refresh tokens; the access token expires on its own,
  so keep its lifetime short.
- Send refresh tokens only in an `HttpOnly; Secure; SameSite=Strict` cookie,
  never in a response body that JavaScript can read.
- `SameSite=Strict` works only when the frontend and API are same-site. A
  cross-site deployment needs `SameSite=None; Secure` plus CSRF protection;
  treat that as architecture.

Model, in the feature's `model.py`, registered in `app/models.py`:

```python
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(unique=True)  # SHA-256 of the raw token
    # Aware timestamps keep the expiry comparison below valid; the index
    # supports purging expired rows.
    expires_at: datetime = Field(sa_type=DateTime(timezone=True), index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=DateTime(timezone=True),
    )
```

Service helpers:

```python
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.auth.model import RefreshToken

REFRESH_TOKEN_BYTES = 64
REFRESH_TOKEN_EXPIRE_DAYS = 30


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _new_token(user_id: int) -> tuple[str, RefreshToken]:
    raw = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=REFRESH_TOKEN_EXPIRE_DAYS
    )
    record = RefreshToken(
        user_id=user_id, token_hash=_hash(raw), expires_at=expires_at
    )
    return raw, record


def issue_refresh_token(session: Session, user_id: int) -> str:
    raw, record = _new_token(user_id)
    session.add(record)
    session.commit()
    return raw


def rotate_refresh_token(
    session: Session, raw_token: str
) -> tuple[int, str] | None:
    """Return (user_id, new_raw_token), or None if invalid or expired."""
    # Lock the row so two concurrent refreshes cannot both succeed.
    statement = (
        select(RefreshToken)
        .where(RefreshToken.token_hash == _hash(raw_token))
        .with_for_update()
    )
    record = session.exec(statement).first()
    if record is None:
        return None  # unknown token, possibly a replayed one
    user_id = record.user_id
    session.delete(record)
    if record.expires_at <= datetime.now(timezone.utc):
        session.commit()
        return None
    # Delete the old token and store its replacement in one transaction.
    new_raw, replacement = _new_token(user_id)
    session.add(replacement)
    session.commit()
    return user_id, new_raw


def revoke_all_tokens(session: Session, user_id: int) -> None:
    """Call on logout or detected token reuse."""
    statement = select(RefreshToken).where(RefreshToken.user_id == user_id)
    for token in session.exec(statement).all():
        session.delete(token)
    session.commit()
```

Deleting rotated rows means a replayed token simply looks unknown, so this
version cannot tell which user to revoke. When the design calls for reuse
detection, keep rotated rows marked as used, with a token family identifier, and
revoke the whole family when a used token is presented.

Router endpoints in `app/auth/router.py`:

```python
from fastapi import Cookie, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session

COOKIE_NAME = "refresh_token"
COOKIE_MAX_AGE = 30 * 86400
# The narrow path keeps the cookie off every other request.
COOKIE_OPTS = {
    "httponly": True,
    "secure": True,
    "samesite": "strict",
    "path": "/auth/refresh",
}


@router.post("/login", response_model=TokenResponse)
def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
) -> TokenResponse:
    user = authenticate(session, form_data.username, form_data.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    raw_refresh = issue_refresh_token(session, user.id)
    response.set_cookie(
        COOKIE_NAME, raw_refresh, max_age=COOKIE_MAX_AGE, **COOKIE_OPTS
    )
    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    session: Session = Depends(get_session),
) -> TokenResponse | JSONResponse:
    if refresh_token is None:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    result = rotate_refresh_token(session, refresh_token)
    if result is None:
        # Raising HTTPException discards cookies set on `response`, so
        # return the 401 directly to make the deletion reach the browser.
        rejected = JSONResponse(
            status_code=401,
            content={"detail": "Invalid or expired refresh token"},
        )
        rejected.delete_cookie(COOKIE_NAME, path="/auth/refresh")
        return rejected
    user_id, new_raw = result
    response.set_cookie(
        COOKIE_NAME, new_raw, max_age=COOKIE_MAX_AGE, **COOKIE_OPTS
    )
    return TokenResponse(access_token=create_access_token(str(user_id)))


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    revoke_all_tokens(session, current_user.id)
    response.delete_cookie(COOKIE_NAME, path="/auth/refresh")
```

After adding the model, generate a migration and review it as `fastapi.md`
describes. Confirm with the user before applying it:

```bash
uv run alembic revision --autogenerate -m "create refresh_tokens table"
uv run alembic upgrade head
```

Expired rows accumulate. The design should name the scheduled job or maintenance
step that purges them.

## Account Lockout

Use this section when the approved design calls for account lockout. Rate
limiting slows brute-force attempts but does not lock accounts, so lockout is a
separate mechanism.

Default thresholds, documented in `.env.example`:

```text
AUTH_LOCKOUT_MAX_ATTEMPTS=5
AUTH_LOCKOUT_WINDOW_SECONDS=900
```

Redis is the preferred counter store when the project already has it: it is
fast, expires keys automatically, and works across instances. Without Redis, the
primary database can hold the counters. Choosing the store is architecture when
not already established.

```python
# app/utils/lockout.py
from loguru import logger
from redis import Redis
from redis.exceptions import RedisError

from app.utils.config import settings


def _key(identifier: str) -> str:
    return f"lockout:{identifier}"


def is_locked(redis: Redis, identifier: str) -> bool:
    try:
        count = redis.get(_key(identifier))
    except RedisError:
        # Fail closed: a Redis outage blocks logins rather than silently
        # disabling brute-force protection.
        logger.error("Lockout check failed; denying login")
        return True
    return int(count or 0) >= settings.auth_lockout_max_attempts


def record_failure(redis: Redis, identifier: str) -> None:
    try:
        pipe = redis.pipeline()
        pipe.incr(_key(identifier))
        # Each failure restarts the window.
        pipe.expire(_key(identifier), settings.auth_lockout_window_seconds)
        pipe.execute()
    except RedisError:
        logger.warning("Lockout increment failed")


def clear_lockout(redis: Redis, identifier: str) -> None:
    try:
        redis.delete(_key(identifier))
    except RedisError:
        logger.warning("Lockout reset failed")
```

The examples use the synchronous client to match sync handlers and sessions. In
an async project, use `redis.asyncio` with async sessions instead of mixing
blocking calls into the event loop.

The service signals a lockout with a domain exception, so the router can tell it
apart from bad credentials:

```python
# app/auth/service.py
class AccountLockedError(Exception):
    """Raised when too many recent login failures lock the account."""


def authenticate_user(
    session: Session, redis: Redis, email: str, password: str
) -> User | None:
    key = f"login:{email}"
    if is_locked(redis, key):
        raise AccountLockedError
    user = authenticate(session, email, password)
    if user is None:
        # Count failures for unknown emails too, so lockout behavior does
        # not reveal which accounts exist.
        record_failure(redis, key)
        return None
    clear_lockout(redis, key)
    return user
```

The router returns `429 Too Many Requests` with `Retry-After` for a locked
account and the generic `401` for bad credentials:

```python
try:
    user = authenticate_user(
        session, redis, form_data.username, form_data.password
    )
except AccountLockedError:
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed attempts. Try again later.",
        headers={"Retry-After": str(settings.auth_lockout_window_seconds)},
    ) from None
if user is None:
    raise HTTPException(status_code=401, detail="Invalid credentials")
```

- Key lockout on the account identifier, such as email, not the IP address. IP
  lockout is easy to bypass with proxies and blocks legitimate users on shared
  addresses.
- Never reveal which field was wrong.
- Never let lockout reveal whether an account exists: count failures for every
  submitted identifier and use the same messages for existing and unknown
  accounts.
- `is_locked` fails closed, so a counter-store outage blocks every login.
  Monitor the store's availability. Failing open instead is an exception to the
  fail-closed principle and needs an explicit, recorded design decision.

## Audit Logging

Use this section when the approved design calls for audit logging. Audit logs
record who did what to which resource. Request logs capture every HTTP call;
audit logs capture only security-relevant events.

| Event                 | Example                                                 |
| --------------------- | ------------------------------------------------------- |
| Auth                  | Login success, login failure, logout, token refresh     |
| Account               | Signup, password change, email change, account deletion |
| Admin actions         | User role change, user suspension, config change        |
| Sensitive data access | Bulk export, PII access, payment details viewed         |

Do not audit ordinary read and list endpoints; the volume makes the log
unusable.

When the project uses Loguru, bind an `audit` flag and route flagged records to
their own sink:

```python
# app/utils/audit.py
from fastapi import Request
from loguru import logger


def audit_log(
    action: str,
    *,
    resource_type: str,
    user_id: int | None = None,
    resource_id: str | int | None = None,
    result: str = "ok",
    detail: str | None = None,
    request: Request | None = None,
) -> None:
    """Emit a structured audit event.

    Call from the router layer, where request context is available, never
    from services.
    """
    client = request.client if request else None
    logger.bind(
        audit=True,
        action=action,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        result=result,
        detail=detail,
        ip=client.host if client else None,
        user_agent=request.headers.get("User-Agent") if request else None,
    ).info(f"{action} {resource_type} {result}")
```

In `setup_logging()`, send audit records to a dedicated sink and keep them out
of the request log, for example by adding
`filter=lambda record: not record["extra"].get("audit")` to the main sink and a
second `logger.add(...)` whose filter selects only audit records. With the
standard library, use a dedicated `logging.getLogger("audit")` with its own
handler instead.

Usage in a router:

```python
user = authenticate(session, form_data.username, form_data.password)
if user is None:
    audit_log(
        "login.failure",
        resource_type="user",
        result="fail",
        detail=form_data.username,
        request=request,
    )
    raise HTTPException(status_code=401, detail="Invalid credentials")
audit_log(
    "login.success",
    resource_type="user",
    user_id=user.id,
    resource_id=user.id,
    request=request,
)
```

- Emit the event after the operation succeeds, and emit a failure event as soon
  as the operation is rejected.
- Never include passwords, tokens, or raw secrets in audit events.
- In production, audit logs go to a separate stream from request logs.
- Production audit logs must be tamper-evident: append-only storage or a SIEM,
  not a mutable database table. The destination is a deployment decision.

## Secrets, Errors, and Dependencies

- Load secrets from the environment or from local secret files excluded from
  version control. Required secrets have no default, so a production start with
  a missing secret fails.
- `SECRET_KEY` is the most sensitive variable. Rotate it independently of
  database passwords, and remember that rotating it invalidates every active
  JWT.
- Never log the value of any variable whose name contains "key", "secret",
  "token", "password", or "credential", even at debug level. Redact
  authorization headers, cookies, secret query values, and sensitive bodies from
  logs, traces, and error trackers.
- The global exception handler returns a generic
  `{"detail": "Internal server error"}` for unhandled exceptions. No response
  ever includes a stack trace, file path, or database error message.
- `4xx` responses may carry a human-readable `detail` but must not reveal
  internals, such as "column 'email' violates unique constraint".
- Never let a broad `except` suppress an authorization, persistence, or
  dependency failure and continue. Roll back partial transactions.
- When the project runs dependency audits, such as `uv run pip-audit` in CI,
  findings rated high or critical block the build.
- Like `default-stack.md`, this guidance does not pin versions. Use currently
  supported releases of security-critical libraries such as JWT, password
  hashing, and cryptography, and record any version constraint that matters in
  the design.

## Controls by Delivery Stage

These lists show what is commonly expected at each stage. Implement a control
when the approved design or acceptance conditions call for it; the stage label
alone does not authorize new architecture or dependencies.

### Throwaway Prototypes

- Dummy secrets and dummy identities only. Never connect prototype auth to
  production credentials or real sensitive data.
- Mock authentication and authorization are labelled clearly in code comments or
  the README.
- Basic request shape is validated with Pydantic or route constraints, and
  user-triggered failures return safe errors instead of tracebacks.
- CORS and docs exposure suit local or demo use only, with a note that they are
  not production-safe.

### Any Real Data

- Authentication on every protected operation and server-side authorization, as
  in [Access Control](#access-control).
- Environment-based secrets, exact CORS origins for credentialed browser
  traffic, allowlisted response models, and parameterized queries.

### Internal Layered Services

- HTTP concerns in routers, business authorization in services, and query
  filtering in persistence code, as `fastapi.md` describes.
- Maintained identity and session libraries or provider integrations, with
  Argon2 hashing and generic login and recovery responses where the app manages
  passwords.
- JWT validation rules, deny by default, mass-assignment protection, security
  headers, a global safe exception handler, and log redaction.

### Pre-Production Hardening

- Short-lived access credentials with a defined renewal mechanism; refresh
  rotation, reuse detection where supported, and revocation on logout or reuse.
- MFA for administrative or materially privileged accounts.
- Rate limits and monitoring on login, OTP, password recovery, registration,
  token refresh, uploads, searches, exports, webhooks, and expensive endpoints,
  keyed by appropriate dimensions.
- Request, body, and upload limits, pagination caps, query complexity bounds,
  timeouts, concurrency limits, and queue limits.
- Least-privilege database identities, with migration and admin credentials
  separate from runtime credentials.
- Structured audit events for login, renewal, logout, credential changes,
  permission changes, denials, exports, sensitive reads, admin actions, and
  security configuration changes, plus alerts on credential stuffing, token
  reuse, repeated denials, high-volume enumeration, unexpected admin actions,
  and audit-pipeline failure.
- Container posture: non-root user, minimal image, dropped capabilities,
  resource limits, and patched base images.
- A pre-production checklist: headers verified on the deployed service, HSTS
  present in production, access-token lifetime of 30 minutes or less, lockout on
  login, audit events on auth and admin routes, dependency audit in CI, a
  deliberate `500` returning no stack trace, and expired refresh tokens purged.

These controls usually come with operational procedures for staging deployment,
migration and rollback, key and session rotation, dependency patching, backup
and restore, and incident response. Writing those procedures is documentation
and operations work outside Developer; report missing ones rather than inventing
them.

### Public Production

Most production controls are architecture or operations decisions: a versioned
threat model and abuse cases, requirements traced to OWASP ASVS, a hardened OIDC
identity provider with phishing-resistant MFA, a centralized deny-by-default
policy over subject, action, resource, tenant, and context, keys in a secrets
manager or KMS with rotation and access logging, SBOM and provenance for
critical artifacts, tamper-resistant audit records in a separately controlled
SIEM, and exercised incident playbooks. Implement only the parts the approved
design assigns to code. Production security cannot be established from code
alone; evidence about runtime, identity providers, gateways, cloud, and incident
exercises stays unverified until someone supplies it.

## OWASP Top 10:2025 Map

Use this map to check which categories a change touches. It is an index, not
evidence.

| Category                                   | FastAPI evidence to inspect                                                                                                                               |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A01 Broken Access Control                  | Router and dependency coverage, function, object, and tenant policy, CORS and CSRF, paths, redirects, SSRF, and denial cases.                             |
| A02 Security Misconfiguration              | App, proxy, and cloud settings, debug, docs, and metrics exposure, trusted hosts, headers, permissions, defaults, and error configuration.                |
| A03 Software Supply Chain Failures         | Python and image lockfiles, direct and transitive inventory, supported versions, SCA and patch process, registry trust, CI identities, SBOM, provenance.  |
| A04 Cryptographic Failures                 | TLS, password hashing, random and token generation, JWT and session keys, algorithms, key storage and rotation, sensitive-data encryption and retention.  |
| A05 Injection                              | Pydantic boundaries plus SQL and NoSQL, shell, template, path, header, log, parser and deserializer, and ORM query construction.                          |
| A06 Insecure Design                        | Trust boundaries, business invariants, tenant model, upload and outbound-request design, quotas, failure states, abuse cases, threat model.               |
| A07 Authentication Failures                | Login, recovery, and MFA, enumeration and automation controls, JWT and OAuth validation, rotation, revocation, logout, audience and scope.                |
| A08 Software or Data Integrity Failures    | Mass assignment, unsafe deserialization, webhook and callback signatures and replay, artifact and update integrity, CI/CD trust and promotion.            |
| A09 Security Logging and Alerting Failures | Auth, access-control, validation, admin, data, and error events, redaction and encoding, monitoring, alert ownership, retention, tamper resistance.       |
| A10 Mishandling of Exceptional Conditions  | Local and global handlers, safe errors, fail-closed auth and dependencies, transaction rollback, idempotency and races, cleanup, limits, repeated errors. |

## Common Failure Patterns

- A router has `Depends(get_current_user)` but never checks access to the
  requested object or tenant.
- A shared dependency is missing from one router, mounted sub-application,
  WebSocket, webhook, or admin endpoint.
- JWT validation disables signature or expiry checks, accepts the algorithm from
  the token, omits audience or issuer boundaries, or uses a checked-in or
  default HMAC secret.
- New password hashes still use a legacy bcrypt or passlib default without a
  migration plan.
- `python-jose` remains in active JWT code without a current maintenance and
  advisory review.
- Long-lived bearer tokens have no rotation, revocation, reuse detection, or
  incident response path.
- Cookie authentication is added without CSRF protection, or permissive CORS is
  mistaken for CSRF or authentication protection.
- Pydantic validates syntax, but authorization-relevant IDs, query cost, file
  size, or domain invariants remain unconstrained.
- ORM objects expose hashes, internal flags, tenant IDs, tokens, or private
  fields.
- User input reaches raw SQL, `subprocess`, archive extraction, filesystem
  paths, templates, pickle or YAML loaders, or outbound URLs unsafely.
- Logs, APM spans, error trackers, or audit payloads capture authorization
  headers, cookies, bodies, or presigned URLs.
- A broad exception handler suppresses an authorization, persistence, or
  dependency failure and continues.
- A lockout or login path returns different messages or timing for existing and
  unknown accounts.

## Before Handoff

Run the project's existing tests, linters, type checks, and any dependency or
secret scans as self-checks, then confirm that:

- every changed entry point authenticates and authorizes as the design requires,
  including object and tenant access;
- inputs, outputs, logs, caches, and errors do not leak sensitive data;
- session, token, cookie, CORS, and CSRF behavior matches the chosen
  architecture;
- secrets and production settings fail closed with no unsafe defaults;
- webhook, serialized-data, and dependency trust assumptions have the integrity
  controls the design calls for;
- exceptional conditions preserve authorization and transaction integrity,
  release resources, and return safe errors;
- security events are observable as the design requires without recording
  credentials or sensitive content.

Do not add or rewrite formal security tests. Tester owns them.

## Authoritative References

- [FastAPI security tutorial](https://fastapi.tiangolo.com/tutorial/security/)
- [FastAPI OAuth2, password hashing, and JWT example](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)
- [FastAPI CORS guidance](https://fastapi.tiangolo.com/tutorial/cors/)
- [pwdlib password migration guide](https://frankie567.github.io/pwdlib/guide/)
- [PyJWT documentation](https://pyjwt.readthedocs.io/)
- [Starlette middleware](https://www.starlette.io/middleware/)
- [Uvicorn settings](https://www.uvicorn.org/settings/)
- [pip-audit](https://github.com/pypa/pip-audit)
- [Security Headers scanner](https://securityheaders.com/)
- [OWASP Top 10: 2025 introduction](https://owasp.org/Top10/2025/0x00_2025-Introduction/)
- [OWASP API Security Top 10: 2023](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)
- [OWASP Application Security Verification Standard](https://owasp.org/www-project-application-security-verification-standard/)
- [OWASP REST Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html)
- [OWASP JSON Web Token Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/JSON_Web_Token_Cheat_Sheet.html)
- [OWASP Server-Side Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
- [OWASP File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)
