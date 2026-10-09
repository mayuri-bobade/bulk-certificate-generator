# Bulk Certificate Generator — Backend

A backend API that accepts a certificate generation request for a list of
recipients, generates a PDF certificate per recipient from a single predefined
template, tracks progress in real time, and lets clients retrieve, export and
verify the generated files.

Built with **Python + FastAPI**, a relational database (**SQLite by default,
PostgreSQL-ready**), **ReportLab** for PDF rendering, a **background thread
pool** for bulk processing, **Alembic** for migrations and **Pytest** for tests.

---

## 1. Features

**Core (assignment requirements)**

- Submit **one** request containing many recipients (bulk generation).
- Request-level validation (Pydantic) plus **per-recipient validation** that
  isolates failures so one bad record never aborts the whole job.
- Background processing with a configurable worker pool.
- Job status/progress: `total`, `processed`, `succeeded`, `failed`, plus a
  per-recipient result list with error messages and attempt counts.
- Download generated certificates as PDFs.
- Interactive OpenAPI docs at `/docs`.
- Tests covering job creation, validation, generation, progress,
  per-recipient failures and retrieval.

**Enhancements**

- **Idempotency keys** — replaying a request with the same `Idempotency-Key`
  returns the original job instead of creating a duplicate.
- **Cancellation** — stop a running job gracefully between recipients.
- **Retry failed recipients** — re-run only the failed records of a job.
- **Crash recovery** — jobs left `PENDING`/`PROCESSING` are automatically
  re-dispatched on startup.
- **Bounded automatic retries** for transient errors (validation failures are
  never retried).
- **ZIP export** — download every certificate of a job in one archive.
- **Live progress via SSE** — `GET /jobs/{id}/events` streams progress events.
- **Pagination** of per-recipient results and filtering by status.
- **Integrity & trust** — each certificate gets a verification code and a
  SHA-256 digest, plus a QR code linking to a public verification page.
- **Revocation** — revoke a certificate so verification fails.
- **Observability** — `X-Request-ID` correlation on every response, structured
  access logging, `/health` (liveness) and `/ready` (readiness) probes.
- **Operations** — Alembic migrations, Dockerfile and `docker-compose` with
  PostgreSQL.

## 2. Project layout

```
app/
  main.py          FastAPI app, lifespan, middleware, health/ready
  config.py        Settings (env / .env driven)
  database.py      Engine, session factory, declarative base
  models.py        SQLAlchemy models: Job, Recipient, Certificate
  schemas.py       Pydantic request/response models
  certificate.py   Predefined PDF template, validation, SHA-256 helpers
  worker.py        Background worker: retries, cancellation, recovery
  services.py      Job creation, state transitions, serialization
  middleware.py    Request-id + access-log middleware
  routers/
    jobs.py          Submit / status / recipients / cancel / retry / zip / SSE
    certificates.py  Metadata / download / revoke
    verify.py        Public verification endpoints
migrations/        Alembic environment + revisions
tests/             Pytest suite
Dockerfile, docker-compose.yml
requirements.txt, pytest.ini, alembic.ini, .env.example
```

## 3. Setup

Requires **Python 3.10+**.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # Windows PowerShell
# source .venv/bin/activate        # macOS / Linux

pip install -r requirements.txt
```

Configuration is optional; copy `.env.example` to `.env` to override defaults:

| Variable                     | Default                            | Purpose                                       |
|------------------------------|------------------------------------|-----------------------------------------------|
| `DATABASE_URL`               | `sqlite:///./data/certificates.db` | SQLAlchemy database URL                        |
| `STORAGE_DIR`                | `./storage`                        | Where generated PDFs are written              |
| `MAX_WORKERS`                | `4`                                | Background worker pool size                   |
| `MAX_RECIPIENTS_PER_JOB`     | `5000`                             | Request size guard                            |
| `GENERATION_MODE`            | `thread`                           | `thread` (async) or `inline` (sync)           |
| `MAX_ATTEMPTS`               | `3`                                | Retries per recipient for transient errors    |
| `RETRY_BACKOFF_SECONDS`      | `0.2`                              | Base backoff between retries                  |
| `GENERATION_DELAY_MS`        | `0`                                | Artificial per-recipient delay (demos/tests)  |
| `RECOVER_INCOMPLETE_JOBS`    | `true`                             | Re-dispatch unfinished jobs on startup        |
| `PUBLIC_BASE_URL`            | `http://localhost:8000`            | Base URL for the QR/verification link         |
| `LOG_LEVEL`                  | `INFO`                             | Logging verbosity                             |

Tables are created automatically on startup. For managed schema changes use
Alembic instead:

```powershell
alembic upgrade head
```

## 4. Run the application

```powershell
uvicorn app.main:app --reload
```

- API base: `http://127.0.0.1:8000`
- Swagger UI: `http://127.0.0.1:8000/docs`
- Liveness: `http://127.0.0.1:8000/health`  •  Readiness: `/ready`

### Run with Docker (PostgreSQL)

```powershell
docker compose up --build
```

This starts PostgreSQL and the API (running migrations first) and exposes the
API on `http://localhost:8000`.

## 5. Run the tests

```powershell
pytest
```

The suite uses a temporary database and storage directory and never touches
your real data.

## 6. API overview

| Method | Path                                              | Description                                    |
|--------|---------------------------------------------------|------------------------------------------------|
| POST   | `/api/v1/jobs`                                    | Submit a bulk request (202, or 200 on replay)  |
| GET    | `/api/v1/jobs`                                    | List jobs (`limit`, `offset`)                  |
| GET    | `/api/v1/jobs/{job_id}`                           | Job status/progress (`include_recipients`)     |
| GET    | `/api/v1/jobs/{job_id}/recipients`                | Paginated results (`limit`, `offset`, `status`)|
| POST   | `/api/v1/jobs/{job_id}/cancel`                    | Request cancellation                           |
| POST   | `/api/v1/jobs/{job_id}/retry`                     | Retry only the failed recipients               |
| GET    | `/api/v1/jobs/{job_id}/certificates`              | Certificates produced by a job                 |
| GET    | `/api/v1/jobs/{job_id}/download`                  | Download all certificates as a ZIP             |
| GET    | `/api/v1/jobs/{job_id}/events`                    | Server-Sent Events progress stream             |
| GET    | `/api/v1/certificates/{id}`                       | Certificate metadata                           |
| GET    | `/api/v1/certificates/{id}/download`              | Download the PDF                               |
| POST   | `/api/v1/certificates/{id}/revoke`                | Revoke a certificate                           |
| GET    | `/api/v1/verify/{code}`                           | Publicly verify a certificate                  |
| GET    | `/api/v1/verify/{code}/download`                  | Download via verification code                 |

### Submit a request

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: event-2026-batch-1" \
  -d '{
    "title": "Certificate of Completion",
    "subtitle": "Backend Engineering Bootcamp",
    "issuer": "Acme Academy",
    "signatory_name": "Jane Doe",
    "signatory_title": "Program Director",
    "issue_date": "2026-01-31",
    "recipients": [
      {"name": "Alice Example", "email": "alice@example.com", "course": "FastAPI", "identifier": "STU-001"},
      {"name": "Bob Example",   "email": "bob@example.com",   "course": "FastAPI", "identifier": "STU-002"}
    ]
  }'
```

The response is `202 Accepted` with a job `id`. Re-sending the same payload with
the same `Idempotency-Key` returns `200` and the original job.

### Track progress

```bash
# Poll
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>"

# Or stream progress as Server-Sent Events
curl -N "http://127.0.0.1:8000/api/v1/jobs/<job_id>/events"
```

Terminal job statuses: `COMPLETED`, `COMPLETED_WITH_ERRORS`, `FAILED`,
`CANCELLED`. Each recipient reports `status`, `attempts`, an optional `error`,
the `certificate_id` and a ready-to-use `download_url`.

### Paginate / filter recipients

```bash
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/recipients?limit=50&offset=0&status=FAILED"
```

### Cancel / retry

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/<job_id>/cancel"
curl -X POST "http://127.0.0.1:8000/api/v1/jobs/<job_id>/retry"   # only FAILED recipients
```

### Retrieve and export certificates

```bash
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates"
curl -OJ "http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download"
curl -OJ "http://127.0.0.1:8000/api/v1/jobs/<job_id>/download"   # ZIP of all
```

### Verify / revoke

```bash
curl "http://127.0.0.1:8000/api/v1/verify/<verification_code>"
curl -X POST "http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/revoke"
```

## 7. Design decisions

**Bulk, not per-certificate requests.** A single `POST /api/v1/jobs` accepts an
array of recipients and returns a job id, so the client never makes one call per
certificate.

**Background thread pool instead of fully synchronous work.** Rendering many
PDFs can take seconds to minutes, so the request returns immediately and a
`ThreadPoolExecutor` does the work. A pool was chosen over Celery/RQ because it
requires no extra broker and keeps the project runnable with one command. The
processing model is isolated behind `app/worker.py::dispatch`, so swapping in a
real queue is a localized change. Set `GENERATION_MODE=inline` to process
synchronously.

**Per-recipient isolation with its own session.** Each recipient is an
independent unit of work processed in its own short-lived DB session. Any
exception is recorded against that recipient only and never rolls back other
recipients. Transient errors retry up to `MAX_ATTEMPTS`; validation failures do
not.

**Two validation layers.**
1. *Request validation* (Pydantic): required fields, email format, non-empty
   name, request size cap. Violations return `422` and no job is created.
2. *Generation validation* (`app/certificate.py`): template constraints (e.g.
   maximum renderable name length). Violations fail only that recipient.

**Reliable state transitions.** Counters (`processed`/`succeeded`/`failed`) and
`attempts` are committed as work progresses, so status is durable and precise.
Cancellation is cooperative: the worker checks a flag between recipients and
marks the remainder `CANCELLED`. On startup, unfinished jobs are automatically
re-dispatched (crash recovery).

**Idempotency.** A unique `idempotency_key` column plus an application-level
lookup (with an `IntegrityError` fallback for races) makes retries of the same
request safe.

**Trust.** Each certificate stores a unique verification code and a SHA-256 of
the rendered PDF, and carries a QR code pointing at the public verification
endpoint. Revocation flips a flag that verification and download honour.

**Relational persistence.** `Job`, `Recipient` and `Certificate` are normalized
tables managed by SQLAlchemy. SQLite is the zero-setup default; point
`DATABASE_URL` at PostgreSQL (or any SQLAlchemy-supported DB) for production and
run `alembic upgrade head`. Docker Compose wires this up end-to-end.

## 8. Possible next steps

- Move processing to Celery + Redis for horizontal scaling.
- Authentication/authorization and per-tenant isolation.
- Email generated certificates, or push to object storage (S3/GCS).
- Optional background image templates and digital signatures.
- Rate limiting and per-client quotas.
