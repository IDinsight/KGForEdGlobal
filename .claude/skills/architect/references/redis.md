# Redis

Read when the technical design must decide whether to add Redis, or configure or
change an existing Redis deployment. Record the resulting decisions in the
technical design; Developer implements them. Setup steps here are implementation
detail for Developer once the design selects this option. This reference never
overrides the protocol, the completed scope, established project constraints, or
official documentation for the installed version.

Redis is an optional add-on. Adopt it only when the scope needs what it
provides, and design the application so it starts and serves every non-cached
route correctly when Redis is unreachable. Sections run from first adoption to
production hardening. The maturity levels in
`../../scoper/references/maturity-levels.md` indicate when each is commonly
expected, but the completed scope decides what the design needs.

**When Redis is appropriate:** response caching, rate limit counters, session
storage (when stateless JWT is insufficient), distributed locks, lightweight
pub/sub.

**When Redis is not appropriate:** primary database, critical job queue (use a
durable queue instead), substitute for database indexes.

Spikes and early prototypes (maturity Levels 1 and 2) rarely justify Redis.

---

## Basic Caching

**Docker Compose:**

```yaml
services:
  redis:
    image: redis:8-alpine
    command: redis-server --save 60 1 --loglevel warning
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  redis_data:
```

**Environment variables** — add to `.env.example`:

```dotenv
REDIS_URL=redis://localhost:6379/0
REDIS_TTL_SECONDS=300
REDIS_CONNECT_TIMEOUT_SECONDS=2
REDIS_SOCKET_TIMEOUT_SECONDS=1
```

**Connection** — Redis failure must not prevent app startup:

```bash
uv add redis
```

```python
# utils/cache.py
_pool = aioredis.ConnectionPool.from_url(
    settings.redis_url,
    socket_connect_timeout=settings.redis_connect_timeout_seconds,
    socket_timeout=settings.redis_socket_timeout_seconds,
    decode_responses=True,
)

def get_redis():
    return aioredis.Redis(connection_pool=_pool)
```

In the application lifespan, attempt a ping but catch and log the failure — do
not raise:

```python
try:
    await get_redis().ping()
except Exception as exc:
    logger.warning("Redis unavailable at startup — caching disabled: %s", exc)
```

**Cache helper.** All cache operations must have a timeout and a fallback to the
primary data source. Never let a Redis error reach the API response.

```python
async def get_cached(key: str, fetch, ttl: int | None = None):
    r = get_redis()
    ttl = ttl or settings.redis_ttl_seconds
    try:
        raw = await r.get(key)
        if raw is not None:
            return json.loads(raw)
    except RedisError as exc:
        logger.warning("Redis GET %s failed: %s", key, exc)

    value = await fetch()

    try:
        await r.setex(key, ttl, json.dumps(value))
    except RedisError as exc:
        logger.warning("Redis SET %s failed: %s", key, exc)

    return value

async def invalidate(key: str) -> None:
    try:
        await get_redis().delete(key)
    except RedisError as exc:
        logger.warning("Redis DELETE %s failed: %s", key, exc)
```

**All cache helpers are async** — any route or service calling them must be
`async def`.

**Health check.** Redis unreachable returns `"degraded"`, not `"error"`. Return
200 for degraded (Redis down, primary DB up); return 503 only when the primary
database is unreachable.

```python
try:
    await get_redis().ping()
    checks["redis"] = "ok"
except RedisError:
    checks["redis"] = "unreachable"
    status = "degraded"
```

**Test databases.** Point tests at a separate Redis database or instance, such
as `redis://localhost:6379/1`, flushed between runs, or replace the client with
one that raises `RedisError` to exercise the fallback path. Tester owns those
fixtures.

---

## Key Hygiene and TTL Policy

Commonly expected from maturity Level 4 (pre-production).

**Key naming convention:**

```text
{resource}:{id}                   # single record
{resource}:list:{hash_of_params}  # paginated list
{user_id}:{resource}:{id}         # user-scoped record
v1:{resource}:{id}                # versioned (when format may change)
```

Rules:

- Always namespace by resource — never bare IDs
- Set TTLs based on data change frequency — do not use a single global TTL
- Invalidate on write — never serve stale data past the write event

**Cache hit/miss monitoring.** Expose hit/miss ratio from Redis `INFO STATS` in
your metrics pipeline.

---

## Hardening

Commonly expected at maturity Level 5 (public production).

- `AUTH` — set `requirepass` or use ACL rules; supply the password through the
  connection URL from the secrets manager, such as
  `redis://:[REDIS_PASSWORD]@host:6379/0`
- **TLS** — use the `rediss://` scheme for encrypted connections
- **Eviction policy** — set `maxmemory-policy allkeys-lru` for a pure cache;
  configure `maxmemory` to a safe ceiling
- **Persistence** — disable for a pure cache (`save ""`); enable AOF if cache
  data must survive restarts
- **High availability** — Sentinel or Redis Cluster for production; not database
  numbers across environments
- **Separate instances** — one Redis instance per environment in production, not
  database index isolation

---

## Authoritative References

- [Redis documentation](https://redis.io/docs/latest/)
- [Redis 8.0 release notes](https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/release-notes/redisce/redisos-8.0-release-notes/)
- [redis-py (Python client) documentation](https://redis.readthedocs.io/en/stable/)
