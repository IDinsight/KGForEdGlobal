# MongoDB

Read when the technical design must choose, configure, or change MongoDB as a
data store. Record the resulting decisions in the technical design; Developer
implements them. Setup steps here are implementation detail for Developer once
the design selects this option. This reference never overrides the protocol, the
completed scope, established project constraints, or official documentation for
the installed version.

Read with `databases.md`. The source of this reference was checked against
MongoDB 8 (8.3 in mid-2026), with 7.0 still supported; check current releases.
Sections run from first adoption to production hardening. The maturity levels in
`../../scoper/references/maturity-levels.md` indicate when each is commonly
expected, but the completed scope decides what the design needs.

---

## Naming

MongoDB creates databases on first write — no creation command needed. Document
the names in `.env.example` as placeholders:

```dotenv
MONGODB_DB_NAME={project_name}_dev
TEST_MONGODB_DB_NAME={project_name}_test
```

---

## Connectivity and Driver Setup

Commonly expected once the project stores real data (maturity Level 2).

**Docker Compose:**

```yaml
services:
  db:
    image: mongo:8
    environment:
      MONGO_INITDB_ROOT_USERNAME: root
      MONGO_INITDB_ROOT_PASSWORD: ${MONGO_ROOT_PASSWORD} # local dev only
    volumes:
      - mongo_data:/data/db
    ports:
      - "27017:27017"
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
      interval: 5s
      timeout: 5s
      retries: 5

  db-test:
    image: mongo:8
    environment:
      MONGO_INITDB_ROOT_USERNAME: root
      MONGO_INITDB_ROOT_PASSWORD: ${MONGO_ROOT_PASSWORD} # local dev only
    ports:
      - "27018:27017"
    # no volume — ephemeral

volumes:
  mongo_data:
```

**Environment variables** — add to `.env.example`:

```dotenv
MONGODB_URL=mongodb://root:[MONGO_ROOT_PASSWORD]@localhost:27017
MONGODB_DB_NAME={project_name}_dev
TEST_MONGODB_URL=mongodb://root:[MONGO_ROOT_PASSWORD]@localhost:27018
TEST_MONGODB_DB_NAME={project_name}_test
DB_CONNECT_RETRIES=5
DB_CONNECT_RETRY_INTERVAL_SECONDS=2
```

**ODM and driver.** The default-stack choice for Python is Beanie, an async
Pydantic-based ODM that uses PyMongo Async. Install PyMongo explicitly because
the application owns and imports its `AsyncMongoClient`:

```bash
uv add pymongo beanie
```

**Connection with startup retry:**

<!-- markdownlint-disable MD013 -->

```python
# utils/database.py
from beanie import init_beanie
from pymongo import AsyncMongoClient

async def connect_with_retry(
    retries: int = 5, interval: float = 2.0
) -> AsyncMongoClient:
    client = AsyncMongoClient(settings.mongodb_url)
    for attempt in range(1, retries + 1):
        try:
            await client.admin.command("ping")
            await init_beanie(
                database=client[settings.mongodb_db_name],
                document_models=all_document_models,
            )
            return client
        except Exception as exc:
            logger.warning("MongoDB not ready (%d/%d): %s", attempt, retries, exc)
            if attempt == retries:
                await client.close()
                raise
            await asyncio.sleep(interval * attempt)
```

<!-- markdownlint-enable MD013 -->

Create one client for the application process and reuse its connection pool.
Wire it into the FastAPI lifespan so shutdown always awaits cleanup:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = await connect_with_retry()
    app.state.mongodb_client = client
    try:
        yield
    finally:
        await client.close()
```

Do not create a client per request or close the shared client before application
shutdown.

**Document design.** Start from the application's read and write access
patterns, not from relational entities. Store data that is usually read or
updated together in the same document. Use references when related data is
accessed independently, relationships are many-to-many, or an embedded array
could grow without bound. MongoDB documents have a hard 16 MB limit. For
established collection shapes, consider server-side `$jsonSchema` validation in
addition to Beanie's Pydantic validation.

**Document model pattern:**

```python
from typing import Annotated

from beanie import Document, Indexed


class Order(Document):
    customer_id: Annotated[str, Indexed()]
    status: str = "pending"

    class Settings:
        name = "orders"
        indexes = [[("customer_id", 1), ("status", 1)]]
```

Register all document models in a list passed to `init_beanie`.

**Schema migrations.** Beanie supplies forward and backward migrations:

- Additive changes (new optional fields, new collections): safe to deploy with
  no data migration when old and new application versions remain compatible.
- Renames, removals, and data transformations: create an ordered migration with
  `beanie new-migration`, review both directions, and run `beanie migrate` as a
  deployment step.
- Beanie migrations use transactions by default and therefore require a replica
  set. `--no-use-transaction` removes rollback protection and needs an explicit
  operational decision.
- `init_beanie` creates declared indexes. Keep `allow_index_dropping=False` and
  handle destructive index changes through a reviewed deployment procedure.

---

## Indexes and Test Databases

Commonly expected from maturity Level 3.

**Indexes.** Review all `Indexed()` fields and compound `Settings.indexes`
entries. Index the fields that frequent queries filter or sort on when the
access pattern justifies the write cost, and check query plans with `explain()`
rather than indexing reflexively. Check existing indexes first, use
`queryPlanner` to detect collection scans and in-memory sorts, then use
`executionStats` to compare documents and keys examined with documents returned.
Prioritize query shapes that are both frequent and slow. Dropping an index on a
large collection takes time — schedule it in a maintenance window.

**Test databases.** Point tests at a separate, disposable database, such as the
`db-test` service above, and drop it after the run. Tester owns the fixtures.

MongoDB does not support multi-document transaction rollback without a replica
set, so test isolation usually truncates collections per test unless the design
runs a local replica set.

---

## Production Pool and Observability

Commonly expected from maturity Level 4 (pre-production).

**Connection pool configuration.** Do not copy fixed pool values between
services. Derive them from deployment type, application instance count,
topology, peak concurrent operations, and observed operation latency. Start with
driver defaults unless measurements justify tuning, then monitor pool wait time,
connection count, and server capacity.

```dotenv
DB_MIN_POOL_SIZE=[MEASURED_MIN_POOL_SIZE]
DB_MAX_POOL_SIZE=[MEASURED_MAX_POOL_SIZE]
DB_CONNECT_TIMEOUT_MS=[CONNECT_TIMEOUT_MS]
DB_SERVER_SELECTION_TIMEOUT_MS=[SERVER_SELECTION_TIMEOUT_MS]
DB_SLOW_QUERY_MS=500
```

Add to `.env.example`. Pass to `AsyncMongoClient`:

```python
from pymongo import AsyncMongoClient

client = AsyncMongoClient(
    settings.mongodb_url,
    minPoolSize=settings.db_min_pool_size,
    maxPoolSize=settings.db_max_pool_size,
    connectTimeoutMS=settings.db_connect_timeout_ms,
    serverSelectionTimeoutMS=settings.db_server_selection_timeout_ms,
)
```

**Slow query detection.** MongoDB's `explain()` and server-side slow query log
(`slowOpThresholdMs` in `mongod.conf`) are the primary tools. Log warnings in
the application for operations that exceed `DB_SLOW_QUERY_MS` where measurable.
On Atlas, use Performance Advisor alongside slow-query logs; validate suggested
indexes against existing indexes and the actual query shape before applying
them.

**Health check:**

<!-- markdownlint-disable MD013 -->

```python
@app.get("/health")
async def health():
    try:
        await get_client().admin.command("ping")
        db_status = "ok"
    except Exception:
        db_status = "unreachable"
    ok = db_status == "ok"
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "checks": {"database": db_status}},
    )
```

<!-- markdownlint-enable MD013 -->

---

## Authentication, TLS, and Least Privilege

Commonly expected at maturity Level 5 (public production), though
least-privilege application users are worth adopting as soon as real data is
stored.

**Authentication.** Create a dedicated application user with `readWrite` on the
app database only — not the admin database:

```javascript
// run in mongosh as admin
db.createUser({
  user: "{app_user}",
  pwd: "[APP_DB_PASSWORD]", // from the secrets manager, never committed
  roles: [{ role: "readWrite", db: "{project_name}_prod" }],
});
```

Update the connection string to use the app user instead of root.

**TLS.** Require TLS for all connections:

<!-- markdownlint-disable MD013 -->

```dotenv
MONGODB_URL=mongodb://{user}:{pass}@host:27017/?tls=true&tlsCAFile=/path/to/ca.pem
```

<!-- markdownlint-enable MD013 -->

**Replica set.** Use a replica set connection string in production — not a
single-node URL. Single-node deployments have no automatic failover.

**Backups.** Automated backups via `mongodump` or Atlas Backup on a daily
schedule, with a tested restoration procedure.

---

## Authoritative References

- [MongoDB documentation](https://www.mongodb.com/docs/manual/)
- [MongoDB release notes](https://www.mongodb.com/docs/manual/release-notes/)
- [PyMongo AsyncMongoClient](https://pymongo.readthedocs.io/en/latest/api/pymongo/asynchronous/mongo_client.html)
- [Beanie ODM documentation](https://beanie-odm.dev/)
- [MongoDB Agent Skills](https://github.com/mongodb/agent-skills)
