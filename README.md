# EVE Healthcare Diagnostic Booking API

A backend for booking diagnostic tests at healthcare centres. Users can create an account, choose a centre and test, book an appointment, and make a simulated payment.

This project was built for the EVE Healthcare backend assignment. Payments are simulated; no real payment gateway is connected.

## What is implemented

- Signup and login with JWT authentication and secure password hashing.
- Create, update and list diagnostic centres and the tests they offer.
- Different prices for the same test at different centres.
- Bookings with appointment time, saved price and booking status.
- Successful and failed simulated payments.
- Payment webhooks that handle repeated events safely.
- Booking ownership checks, request validation and consistent errors.
- Pagination, rate limiting, JSON logs, request IDs and Docker support.

## Tech stack

Python 3.12, FastAPI, Pydantic, PostgreSQL 17, SQLAlchemy, Alembic, PyJWT, Argon2id and pytest.

## Start with Docker

You need Docker with Compose. Python is used below to generate local secrets.

### 1. Configure the environment

From the project folder, copy the example configuration:

```bash
cp .env.example .env
```

In PowerShell, you can also use `Copy-Item .env.example .env`. If you already have a configured `.env`, keep it.

Run this command three times to generate three different values:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set these values in `.env`:

| Setting | What to put here |
| --- | --- |
| `POSTGRES_PASSWORD` | First generated value |
| `JWT_SECRET` | Second generated value |
| `WEBHOOK_SECRET` | Third generated value |
| `DATABASE_URL` | Replace its password placeholder with the same `POSTGRES_PASSWORD` |

Keep the remaining defaults for local use. JWT and webhook secrets must have at least 32 characters; placeholder values are rejected. The generated database password is safe to use in a URL. Other passwords may need URL encoding.

The `.env` file is ignored by Git. Never commit it.

### 2. Start the services

```bash
docker compose up --build --wait
```

This starts PostgreSQL and the API. It waits for the database, applies migrations and checks both services are healthy.

| URL | Purpose |
| --- | --- |
| http://localhost:8000/docs | Swagger: try API requests |
| http://localhost:8000/redoc | Read API documentation |
| http://localhost:8000/health | Check database connectivity |
| http://localhost:8000/openapi.json | Download the API schema |

If a port is occupied, change `API_PORT` or `POSTGRES_PORT` in `.env`. Update the port in the local `DATABASE_URL` too. Compose uses the database service name internally.

Useful commands:

```bash
docker compose ps
docker compose logs -f api
docker compose exec -T api alembic check
docker compose down
```

Stopping the containers keeps database data. Adding `-v` to `down` deletes the volume and its data. Changing the password in `.env` does not change credentials in an existing database volume.

## Try the main flow in Swagger

1. Open `/docs` and call `POST /auth/signup` with an email and password.
2. Call `POST /auth/login` with the same credentials.
3. Copy the returned `access_token`. In **Authorize > HTTPBearer**, paste the token.
4. Create a centre with `POST /centres`, then a test with `POST /tests`.
5. Add the test to the centre with `POST /centres/{centre_id}/tests` and set a positive price.
6. Create a booking with the returned offering ID (`centre_test_id`) and a future appointment time.
7. Call `POST /payments` with `simulate_status: "SUCCESS"`. The booking becomes `CONFIRMED`.
8. Create another booking and use `"FAILED"` to check the failure flow.
9. For webhooks, authorize **APIKeyHeader** with your `WEBHOOK_SECRET` and send an event. Repeat it to check that the same payment is returned.

Use the IDs returned by your requests. There is no hidden seed data.

## API endpoints

Catalogue reads are public. Catalogue writes require a logged-in user. Booking and payment operations require the booking's owner. Webhooks use the separate provider secret.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/auth/signup` | Register a user |
| POST | `/auth/login` | Get a JWT access token |
| POST / GET | `/centres` | Create / list centres |
| GET / PATCH | `/centres/{centre_id}` | Read / update a centre |
| POST / GET | `/tests` | Create / list tests |
| POST / GET | `/centres/{centre_id}/tests` | Add / list centre offerings |
| PATCH | `/centres/{centre_id}/tests/{test_id}` | Update an offering's price |
| POST / GET | `/bookings` | Create / list your bookings |
| GET | `/bookings/{booking_id}` | Read your booking |
| POST | `/bookings/{booking_id}/cancel` | Cancel a pending booking |
| POST | `/payments` | Simulate a payment |
| POST | `/payments/webhook` | Receive a provider event |

List endpoints accept `page` and `page_size`. Defaults are page 1 and 20 items; the maximum page size is 100. Responses contain `items`, `page`, `page_size` and `total`.

### Example request bodies

Signup and login:

```json
{"email": "patient@example.com", "password": "example-password"}
```

Create a centre and a test, respectively:

```json
{"name": "EVE Centre", "location": "Delhi"}
```

```json
{"name": "Blood count"}
```

Add test 1 to a centre:

```json
{"test_id": 1, "price": "1250.50"}
```

Book offering 1:

```json
{"centre_test_id": 1, "appointment_at": "2099-10-05T11:30:00+05:30"}
```

Pay for booking 1:

```json
{"booking_id": 1, "simulate_status": "SUCCESS"}
```

Use `"FAILED"` for a failed payment. The amount comes from the booking, so there is no amount field in this request.

Send a webhook with the `X-Webhook-Secret` header:

```json
{"event_id": "evt_123", "booking_id": 1, "status": "SUCCESS"}
```

A pending booking is confirmed on success. Repeating this event returns 200 and the same payment ID. Changing its booking or status while keeping the same `event_id` returns 409.

## Project structure

```text
app/
  main.py                 App setup and router registration
  config.py               Environment settings
  database.py             Database engine and request sessions
  models.py               All database models
  security.py             Passwords, JWTs and auth dependencies
  bookings.py             Booking rules and cancellation
  payments.py             Payment and webhook transactions
  exceptions.py           Shared API errors
  request_logging.py      JSON logs and request IDs
  pagination.py           Shared list pagination
  rate_limit.py           Request limits
  routers/                Auth, catalogue, booking, payment and health routes
tests/                    Unit, API, database and concurrency tests
alembic/                  Database migrations
compose.yaml              API and PostgreSQL services
Dockerfile                API image
pyproject.toml            Project settings and direct dependencies
requirements.lock         Pinned runtime dependencies
requirements-dev.lock     Pinned dependencies for development and tests
.env.example              Example configuration
```

The structure is deliberately small. Routers handle HTTP requests and responses. Booking and payment modules hold business rules and database transactions. Simple catalogue queries stay in the catalogue router.

```text
Request -> validation/auth -> router -> business rules -> SQLAlchemy -> PostgreSQL
```

## Database design

| Table | Stores |
| --- | --- |
| `users` | Unique normalized email and password hash |
| `diagnostic_centres` | Centre name and location |
| `diagnostic_tests` | Test name |
| `centre_tests` | Centre/test pair and its price |
| `bookings` | User, offering, appointment, saved amount and status |
| `payments` | Booking, amount, payment result and unique provider attempt ID |
| `webhook_events` | Unique event ID, payment, result and processing time |

The same test can have different prices at different centres, so price belongs to `centre_tests`. A booking references this offering, which identifies both the centre and test.

The booking saves the offering's price when it is created. Later price changes do not change existing bookings. Payments use this saved amount. Money uses `Decimal` and database `NUMERIC(12,2)`, and JSON responses return amounts as strings.

Unique constraints protect emails, centre/test pairs and provider IDs. Foreign keys prevent removal of referenced history. Status and positive-money checks are also enforced in the database. There are no delete endpoints.

## Booking and payment rules

```text
PENDING -- successful payment --> CONFIRMED
PENDING -- failed payment -----> FAILED
PENDING -- cancellation -------> CANCELLED
```

Only pending bookings can be paid or cancelled. Repeating cancellation is safe and returns 200. Confirmed or failed bookings cannot be cancelled. To try again after failure, create a new booking.

Users cannot choose another booking owner, supply their own amount or set an arbitrary status. Missing bookings and another user's bookings both return 404.

Payment and booking changes are saved in one transaction: either both succeed or neither does. PostgreSQL row locks make competing payment, webhook and cancellation requests use the same state rules.

### Why repeated webhooks are safe

Each provider event has a database-unique `event_id`. The event receipt, payment and booking update are saved together. A matching duplicate returns the existing result.

The database uses `ON CONFLICT` to handle event-ID races. If two different bookings use the same event ID concurrently, the losing transaction rolls back its changes. A new event with the same completed outcome can reuse the payment; an opposing outcome returns 409.

A provider can safely redeliver an event after a temporary failure. For 429 or 503, respect the `Retry-After` header. Invalid payloads, credentials or conflicting outcomes must be corrected first.

## Run without an API container

Configure `.env` as above. You need Python 3.12 and PostgreSQL; the database can still run in Docker.

```bash
python -m venv .venv
```

Activate the environment:

```powershell
.venv\Scripts\Activate.ps1
```

On Linux/macOS use `source .venv/bin/activate`; on Windows Git Bash use `source .venv/Scripts/activate`.

Then run:

```bash
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
docker compose up -d --wait db
alembic upgrade head
alembic check
uvicorn app.main:create_app --factory --reload --no-access-log --no-proxy-headers
```

If the Docker API is already running, stop it with `docker compose stop api` first or add `--port 8001` to the local Uvicorn command. For an existing PostgreSQL instance, set `DATABASE_URL` and omit the Compose database command.

## Tests and migrations

With the development dependencies installed and the virtual environment active:

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m pip check
```

The default tests use temporary SQLite databases with foreign keys enabled. They do not use your private `.env` or development database. Six PostgreSQL checks are skipped unless a dedicated test database is configured.

| Area | Tests cover |
| --- | --- |
| Authentication | Signup/login, duplicate and normalized email, invalid input, missing/invalid/expired tokens, deleted users |
| Catalogue | Centre/test management, unknown relations, duplicate offerings, invalid prices |
| Bookings | Saved price, future timezone-aware appointments, ownership, cancellation and invalid states |
| Payments | Success/failure, repeated requests, server-derived amounts and rollback of partial writes |
| Webhooks | Repeated/conflicting events, unknown targets, transaction rollback and concurrent delivery |
| Shared behavior | Pagination, rate limits, request IDs, safe logs and database constraints |

### Run the PostgreSQL checks

Create a separate test database once (the default database user is `eve`):

```bash
docker compose exec -T db createdb -U eve eve_test
```

PowerShell:

```powershell
$env:TEST_DATABASE_URL = python -c "from app.config import get_settings; from sqlalchemy.engine import make_url; print(make_url(get_settings().database_url).set(database='eve_test').render_as_string(hide_password=False))"
python -m pytest -q
Remove-Item Env:TEST_DATABASE_URL
```

Bash/Git Bash:

```bash
export TEST_DATABASE_URL="$(python -c "import sys; from app.config import get_settings; from sqlalchemy.engine import make_url; sys.stdout.write(make_url(get_settings().database_url).set(database='eve_test').render_as_string(hide_password=False))")"
python -m pytest -q
unset TEST_DATABASE_URL
```

Adjust the database username if you changed it. Tests require a database name ending in `_test` and use separate temporary schemas. These checks include fresh migration upgrade/downgrade/upgrade and real concurrent webhook, payment and cancellation requests.

### Schema changes

Docker applies existing migrations automatically. For a deliberate model change, generate and review a new migration:

```bash
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
alembic check
```

Review both upgrade and downgrade before applying the new migration. `alembic check` reports whether models and migrations agree.

## Configuration and errors

See `.env.example` for all settings. Environment variables override the file.

JWTs expire after 30 minutes by default. Passwords must be nonblank and 8-128 characters long. Emails are normalized before storage. Prices must be positive, finite and have at most two decimal places. Unknown request fields are rejected.

Errors use the same response shape:

```json
{
  "error": {"code": "booking_not_found", "message": "Booking not found", "details": []},
  "request_id": "review-123"
}
```

| Status | Meaning |
| --- | --- |
| 401 | Missing/invalid user authentication |
| 403 | Missing/invalid webhook provider secret |
| 404 | Resource missing or booking not owned by the user |
| 409 | Duplicate operation or conflicting state/event |
| 422 | Invalid request |
| 429 | Rate limit exceeded |
| 503 | Recognized temporary database failure |
| 500 | Unexpected server error |

Responses do not expose raw database errors or sensitive input. JSON logs include event names, request IDs, statuses and durations; passwords, tokens, secrets and request bodies are excluded. Responses also return `X-Request-ID`.

## Assumptions and trade-offs

- Any authenticated user can update the catalogue because the assignment does not define an admin role.
- Amounts are in INR. Appointments must be in the future and include a timezone; storage and responses use UTC.
- Failed bookings require a new booking. Refunds, opening hours and appointment capacity are outside the assignment.
- The webhook uses a shared secret for this simulation. A real provider would need signature verification.
- Rate limits are per endpoint/IP and per API process: signup/login 5 each, payments 20, webhook 120 per 60 seconds. Settings in `.env` can change these limits. A multi-instance deployment would need shared counters.
- Docker runs a single API instance and applies migrations at startup. A larger deployment should run migrations separately.
- Historical prices are saved, but centre/test names are not versioned. Page totals may change while other requests add records.
- Redis and Celery are omitted because this project does not need distributed counters or background work. Generated caches, local dependencies and credentials are ignored by Git.

