# Voice-to-Action Enterprise Agent

A voice-enabled enterprise assistant that turns a spoken or typed instruction into a
**structured, planned, risk-gated, auditable action**. It understands intent, plans a
workflow, selects an enterprise tool over **MCP (Model Context Protocol)**, asks for
human approval on sensitive actions, executes, and **emails the assignee** when a task
is created.

```
Voice / Text ─▶ Whisper STT ─▶ Intent + Planner (LangGraph) ─▶ Risk gate ─▶
Human approval ─▶ MCP tool call ─▶ PostgreSQL + Qdrant ─▶ Email to assignee ─▶ Audit log
```

Stack: **FastAPI · LangGraph · MCP · PostgreSQL · Qdrant · React + TypeScript + Tailwind · Docker**

---

## Extensible capabilities (v3)

Two things make this production-shaped rather than a fixed demo:

### 1. Pluggable delivery channels (email / WhatsApp / Teams / Slack / …)
Notifications are no longer hardcoded to email. Every channel is a small plug-in under
`backend/app/channels/` implementing one interface (`DeliveryChannel`):

```
channels/
  base.py            # DeliveryChannel interface + Notification dataclass
  email_channel.py   # SMTP (works out of the box with Mailpit)
  whatsapp_channel.py# Twilio WhatsApp API
  teams_channel.py   # Microsoft Teams incoming webhook
  slack_channel.py   # Slack incoming webhook
  registry.py        # registers channels + deliver() dispatcher
```

`deliver(contact, note, channels=[...])` sends one message across one or many channels
and returns a per-channel status. Adding a brand-new medium (SMS, Telegram, …) is **one
new file + a line in the registry** — no change to the agent, the API, or the database.
Every send is *graceful*: a channel that isn't configured reports a clear reason instead
of raising, so a missing credential never breaks a flow. `GET /channels` shows which are
ready.

### 1b. The agent can also send free-form messages
A second write action, **`send_message`**, sends a short message as-is (no document):

> "Send hi on WhatsApp to David Kim"  ·  "Message Alice on email and slack: standup at 10"

It routes through the same channel layer. This is the pattern for "make it do any task":
each new capability is a new tool + one planner line — the agent does what it has tools for.

### 1c. A customizable "Compose & Send" panel in the UI
The frontend has a **Compose & Send** panel so you don't have to type commands:
- a toggle: **Send message** (as-is) or **Write with AI** (generate a document),
- a **recipient** dropdown (from your contacts),
- **channel checkboxes** (email / WhatsApp / Teams / Slack),
- a live per-channel delivery result.

It calls `POST /send` (raw message) or `POST /compose` (AI document). Contacts now also
carry a **phone** number (for WhatsApp), editable in the Contacts tab.

### 2. The agent can author and deliver documents
A new write action, **`compose_document`**, lets the agent *generate* a document (a leave
letter, memo, notice, …) with the LLM and then *deliver* it across the chosen channels —
in one approved step. Example command:

> "Write a leave letter for David Kim and send it on email and WhatsApp."

→ plans `compose_document` → waits for approval → generates the letter → delivers it to
David over email and WhatsApp → stores it in `generated_documents`. Without an LLM key it
falls back to a clean template, so it still works in a demo. Direct endpoint: `POST
/compose`.

```
Voice / Text ─▶ Planner ─▶ Risk gate ─▶ Approval ─▶ compose_document
            ─▶ generate document (LLM) ─▶ deliver across channels ─▶ store + audit
```

---

## What was wrong, and what was fixed

The uploaded project failed on every command with an opaque
`unhandled errors in a TaskGroup (1 sub-exception)`. Root causes were found by running
the code, not by guessing:

1. **The MCP tool server never received the app's environment.** The old client spawned
   a fresh `python -m app.mcp_server` subprocess for **every** tool call, and MCP's
   stdio transport gives the child a *minimal* environment. So `DATABASE_URL`,
   `GROQ_API_KEY`, and the SMTP settings never reached the tool process — it fell back to
   a default Postgres URL and crashed. **Fix:** the MCP client now passes the full
   environment (`env=os.environ`) and working directory to the subprocess.
2. **One persistent MCP session instead of one-per-call.** Spawning and tearing down the
   subprocess on each call also caused async cancel-scope errors. The client was rewritten
   as a **single persistent worker** that owns the MCP session in one async task and
   serves calls through a queue. It restarts the server automatically if it dies.
3. **MCP version drift.** The code imports `mcp.server.fastmcp` (v1). `mcp` 2.x moved it,
   so the dependency is pinned to `mcp==1.12.3`.
4. **A buggy fallback parser** produced garbage like `assignee="Karla by"` and
   `due_date="date tomorrow"`. The extraction logic was rewritten and unit-tested.

## What was added (the two requested features)

### 1. Dynamic data instead of hardcoded values
Contacts, tickets, and the knowledge base used to be **hardcoded Python lists**. They are
now **database-backed** (`Contact`, `Ticket`, `KnowledgeDoc` tables), seeded on first
startup and fully queryable and extendable at runtime:

- `GET /contacts`, `POST /contacts` — read and add people to the directory.
- `GET /tickets` — live ticket data.
- Knowledge search reads from the DB and (when Qdrant is up) uses vector retrieval.

Because the directory is dynamic, when you say *"assign it to Alice"* the agent looks
**Alice** up in the database and resolves her real email address automatically.

### 2. Email the assignee when a task is created
When a task is created, the person it is assigned to is **emailed automatically**.

- The assignee's address is resolved from the contacts directory (or you can pass an
  explicit email / say one in the command).
- Sending **never breaks task creation** — if email is disabled or SMTP is not
  configured, the task is still created and the response reports why no mail was sent.
- Every task records `notified` and `notification_status`, shown in the UI.
- **Docker Compose ships a [Mailpit](https://mailpit.axllent.org/) inbox**, so email
  works out of the box with **no real credentials** — open the messages at
  <http://localhost:8025>.

---

## Run it with Docker (recommended)

```bash
docker compose up --build
```

Then:

| Service            | URL                              |
| ------------------ | -------------------------------- |
| Backend API + docs | http://localhost:8000/docs       |
| Mailpit inbox      | http://localhost:8025            |
| Postgres           | localhost:5432                   |
| Qdrant             | http://localhost:6333            |

Compose overrides the DB and SMTP hosts to the internal service names, so the containers
find each other automatically. Create a task assigned to one of the seeded people and
watch the email land in Mailpit.

### Frontend

```bash
cd frontend
cp .env.example .env         # VITE_API_URL=http://localhost:8000
npm install
npm run dev                  # http://localhost:5173
```

---

## Run the backend locally (without Docker)

You still need Postgres and (optionally) Qdrant reachable, or point `DATABASE_URL` at
SQLite for a quick spin.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Quick SQLite + no-SMTP smoke run:

```bash
DATABASE_URL="sqlite:///./dev.db" EMAIL_ENABLED=false uvicorn app.main:app --port 8000
```

---

## Configuration (`backend/.env`)

Key settings (see `backend/.env.example` for the full list and comments):

| Variable            | Purpose                                                                 |
| ------------------- | ----------------------------------------------------------------------- |
| `GROQ_API_KEY`      | Powers Whisper STT + the LLM planner. **Without it the agent still runs** on a deterministic fallback planner; only voice transcription needs it. |
| `DATABASE_URL`      | PostgreSQL DSN (`postgresql+psycopg://…`) or `sqlite:///…`.              |
| `SEED_ON_STARTUP`   | Seed contacts/tickets/knowledge on first run (default `true`).           |
| `QDRANT_URL`        | Qdrant endpoint for vector knowledge search (optional).                  |
| `EMAIL_ENABLED`     | Master switch for assignee emails.                                       |
| `SMTP_HOST/PORT`    | `mailpit`/`1025` in Docker. For Gmail: `smtp.gmail.com`/`587`.           |
| `SMTP_USE_TLS`      | `true` for Gmail/most providers; `false` for Mailpit.                    |
| `SMTP_USERNAME/PASSWORD` | Real SMTP credentials (Gmail needs an **App Password**).            |
| `AUTH_ENABLED` / `APP_API_KEY` | Optional API-key auth on all endpoints.                      |

### Using Gmail for real delivery

```env
EMAIL_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=your_16_char_app_password
SMTP_FROM=Enterprise Agent <you@gmail.com>
```

---

## API overview

| Method & path              | Description                                                     |
| -------------------------- | -------------------------------------------------------------- |
| `GET  /health`             | Liveness + whether email is configured.                        |
| `POST /commands/text`      | Plan a typed command. Returns the plan; sensitive actions come back `awaiting approval` with an `execution_id`. |
| `POST /commands/audio`     | Same, from an uploaded audio file (Whisper STT).               |
| `POST /commands/confirm`   | Approve/reject a pending action by `execution_id`; on approval the tool runs and (for tasks) the email is sent. |
| `GET  /tasks`              | All tasks, with assignee, email, and notification status.      |
| `GET  /contacts` / `POST /contacts` | Read / add directory contacts (dynamic data).         |
| `GET  /tickets`            | Ticket data (dynamic).                                         |
| `GET  /executions` / `GET /executions/{id}` | Execution + audit history.                    |

**Sensitive-action confirmation:** creating a task is treated as high-risk and requires
explicit approval before any write tool is called — a task assignment (and its email)
never fires without a human clicking approve.

---

## Tests

```bash
cd backend
PYTHONPATH=. pytest -q
```

The suite (SQLite-backed, no network) covers the rewritten parser, the email
validation + graceful-skip logic, dynamic contact resolution on task creation, and the
API endpoints.

---

## Project layout

```
backend/
  app/
    main.py           FastAPI app + lifespan (init DB, seed, start/stop MCP)
    agent.py          LangGraph plan → validate → risk gate → execute
    mcp_client.py     Persistent, env-propagating MCP client (the core fix)
    mcp_server.py     FastMCP tool server (task/contact/ticket/knowledge tools)
    task_store.py     DB-backed task + contact + ticket logic
    email_service.py  SMTP assignee notifications (never raises)
    retrieval.py      Qdrant + keyword knowledge search
    db.py             SQLAlchemy models, seeding, audit
    seed_data.py      Seed contacts / tickets / knowledge
    config.py         Settings (LLM, DB, Qdrant, SMTP, auth)
    llm.py, transcription.py, security.py, models.py
  tests/
frontend/
  src/main.tsx        Single-file React UI (command, approval, email status, data explorer)
docker-compose.yml    Postgres + Qdrant + Mailpit + backend
```
