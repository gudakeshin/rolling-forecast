# Rolling Forecast Assistant

AI-powered Rolling Forecast Generation & Refresh platform for FP&A teams. Built with a conversational interface, statistical forecasting engines, and a skill-based agent architecture.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Frontend (React + Vite + Tailwind)                 │
│  ┌─────────┐  ┌──────────┐  ┌─────────────────┐    │
│  │  Chat   │  │  Panels  │  │  Renderers      │    │
│  │Container│  │(Executive│  │(Chart, Table,   │    │
│  │         │  │ Review,  │  │ Status, Action) │    │
│  │         │  │ Accuracy │  │                 │    │
│  │         │  │ Driver)  │  │                 │    │
│  └────┬────┘  └─────┬────┘  └────────┬────────┘    │
│       │             │               │              │
│  ┌────┴─────────────┴───────────────┴──────────┐   │
│  │  Zustand Stores (chat, panel, forecast,     │   │
│  │  dashboard, auth)                            │   │
│  └────────────────────┬────────────────────────┘   │
└───────────────────────┼────────────────────────────┘
                        │  SSE / REST
┌───────────────────────┼────────────────────────────┐
│  Backend (FastAPI)    │                            │
│  ┌────────────────────┴───────────────────────┐    │
│  │  API Layer (chat, panel, dashboard, auth,  │    │
│  │  upload, skills, health)                    │    │
│  └────────────────────┬───────────────────────┘    │
│  ┌────────────────────┴───────────────────────┐    │
│  │  Orchestration Layer                        │    │
│  │  ┌──────────────┐  ┌────────────────────┐  │    │
│  │  │ MasterAgent  │  │ ContextManager     │  │    │
│  │  │ (LangGraph   │  │ (Chat history,     │  │    │
│  │  │  ReACT loop) │  │  working memory)   │  │    │
│  │  └──────┬───────┘  └────────────────────┘  │    │
│  └─────────┼──────────────────────────────────┘    │
│  ┌─────────┴──────────────────────────────────┐    │
│  │  Skills Registry (16 domain skills)         │    │
│  │  .md definitions + Python execution         │    │
│  │  Converted to LangChain tools at runtime    │    │
│  └─────────┬──────────────────────────────────┘    │
│  ┌─────────┴──────────────────────────────────┐    │
│  │  Forecast Engines                           │    │
│  │  ARIMA • Prophet • ETS • Linear Trend       │    │
│  └────────────────────────────────────────────┘    │
│  ┌────────────────────────────────────────────┐    │
│  │  SQLAlchemy ORM + Alembic Migrations        │    │
│  │  SQLite (dev) / PostgreSQL (prod)           │    │
│  └────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, Recharts, Zustand |
| Backend | Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic |
| AI/LLM | Claude (Anthropic), LangChain, LangGraph ReACT agent |
| Forecasting | Prophet, statsmodels (ARIMA, ETS), scikit-learn |
| Streaming | SSE (Server-Sent Events) via sse-starlette |
| Auth | JWT (python-jose) + RBAC (Role-Based Access Control) |
| Deployment | Docker, docker-compose, nginx |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- An Anthropic API key

### 1. Clone and configure

```bash
git clone <repo-url> && cd rolling-forecast

# Copy environment template
cp .env.example backend/.env

# Edit backend/.env and set your ANTHROPIC_API_KEY
```

### 2. Backend setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run migrations (first time)
alembic upgrade head

# Start the API server
uvicorn app.main:app --reload --port 8000
```

### 3. Frontend setup

```bash
cd frontend
npm install
npm run dev
```

The app is now running at **http://localhost:5173**. Log in with `admin`/`admin` or `analyst`/`analyst`.

### Docker (alternative)

```bash
# Development
docker compose up --build

# Production
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

---

## Skills System

The agent's capabilities are defined as **skills** — modular units with:
- A `.md` definition file (metadata, parameters, instructions)
- A Python class providing execution logic

Skills are registered at startup and exposed as LangChain tools to the ReACT agent.

### Available Skills (16)

| Skill | Description |
|-------|-------------|
| `ingest_actuals` | Upload and parse historical actuals data (CSV) |
| `plan_forecast` | Analyze data quality, run model comparisons (ARIMA/Prophet/ETS/Linear) |
| `generate_baseline` | Generate statistical baseline forecasts with auto model selection |
| `score_confidence` | Score forecast confidence (0-100) with composite metrics |
| `manage_versions` | Create, list, compare, archive forecast versions |
| `query_forecast` | Natural language queries against forecast data |
| `apply_override` | Apply human overrides with dependency recalculation |
| `compare_forecasts` | Version-to-version comparison with variance analysis |
| `collect_driver_input` | Collect BU head assumptions via structured forms |
| `review_forecast` | AI-powered triage: auto-approve, flag, or queue for review |
| `export_audit` | Export forecast data and audit trail |
| `run_ensemble` | Run ensemble of multiple models with weighted averaging |
| `generate_commentary` | AI-generated narrative commentary for executives |
| `branch_forecast` | Create what-if scenario branches |
| `auto_accuracy_report` | MAPE trends, bias detection, model comparison |
| `detect_anomalies` | Statistical anomaly detection in actuals and forecasts |

### Editing Skills

Skills can be edited live through the Skill Editor panel in the UI, or by modifying the `.md` files in `backend/skills/`.

---

## API Reference

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/login` | Login with username/password, returns JWT |
| POST | `/api/register` | Register a new user |
| GET | `/api/me` | Get current user info |

### Chat (SSE Streaming)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/message` | Send message, receive SSE stream |
| GET | `/api/chat/conversations` | List user conversations |
| GET | `/api/chat/conversations/{id}` | Get conversation with messages |

### Panels & Dashboards

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/panel/forecast-table/{version_id}` | Forecast data table |
| GET | `/api/panel/review-queue/{version_id}` | Review queue |
| GET | `/api/panel/overrides/{version_id}` | Override history |
| GET | `/api/panel/comparison/{v1}/{v2}` | Version comparison |
| GET | `/api/panel/executive-dashboard/{version_id}` | Executive summary |
| GET | `/api/panel/review-dashboard/{version_id}` | AI review dashboard |
| GET | `/api/panel/accuracy-tracking/{version_id}` | Accuracy metrics |
| GET | `/api/panel/driver-inputs/{version_id}` | Driver input forms |
| POST | `/api/panel/review-item` | Approve/reject a line item |
| POST | `/api/panel/batch-review` | Batch approve/reject |
| POST | `/api/panel/accept-ai-recommendations` | Accept all AI approvals |
| POST | `/api/panel/rescore-forecasts/{version_id}` | Re-run confidence scoring |

### Skills

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/skills/` | List all skills |
| GET | `/api/skills/definitions` | Get skill .md definitions |
| GET | `/api/skills/{name}` | Get a specific skill |
| PUT | `/api/skills/{name}` | Update a skill definition |
| POST | `/api/skills/reload` | Hot-reload all skills |

### Upload

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/upload/actuals` | Upload actuals CSV file |

---

## Database Migrations

Uses Alembic for safe schema evolution.

```bash
cd backend

# Apply all pending migrations
alembic upgrade head

# Create a new migration after model changes
alembic revision --autogenerate -m "description of change"

# Downgrade one step
alembic downgrade -1

# For existing databases (skip initial migration)
alembic stamp head
```

---

## Project Structure

```
rolling-forecast/
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── chat/           # Chat UI (MessageBubble, InputBar, etc.)
│   │   │   │   └── renderers/  # ChartRenderer, TableRenderer, etc.
│   │   │   ├── panels/         # Side panels (Executive, Review, etc.)
│   │   │   └── common/         # Layout, Header, LoginForm
│   │   ├── store/              # Zustand state management
│   │   ├── api/                # API client modules
│   │   ├── types/              # TypeScript interfaces
│   │   └── App.tsx             # Routes and error boundary
│   ├── Dockerfile
│   └── package.json
├── backend/
│   ├── app/
│   │   ├── api/                # FastAPI route handlers
│   │   ├── domain/
│   │   │   ├── skills/         # 16 domain skill implementations
│   │   │   ├── engines/        # Forecast model engines
│   │   │   ├── registry.py     # Skill registry + LangChain tool conversion
│   │   │   └── base_skill.py   # Base skill class
│   │   ├── models/             # SQLAlchemy ORM models
│   │   ├── orchestration/      # MasterAgent, ContextManager
│   │   ├── schemas/            # Pydantic request/response schemas
│   │   ├── services/           # Business logic services
│   │   ├── config.py           # App configuration
│   │   ├── database.py         # DB engine and session
│   │   └── main.py             # FastAPI app entry point
│   ├── skills/                 # Skill .md definitions (editable)
│   ├── alembic/                # Database migrations
│   ├── Dockerfile
│   └── requirements.txt
├── docker-compose.yml          # Development compose
├── docker-compose.prod.yml     # Production overrides
└── README.md
```

---

## User Roles (RBAC)

| Role | Input | Generate | Override | Review | Publish | Admin |
|------|:-----:|:--------:|:--------:|:------:|:-------:|:-----:|
| admin | yes | yes | yes | yes | yes | yes |
| analyst | yes | yes | yes | — | — | — |
| reviewer | yes | yes | yes | yes | — | — |
| publisher | yes | yes | yes | yes | yes | — |
| input_provider | yes | — | — | — | — | — |

---

## SSE Streaming Events

The chat endpoint streams events in real-time:

| Event | Description |
|-------|-------------|
| `message_start` | Start of response, includes conversation_id |
| `token` | Intermediate LLM text token |
| `tool_start` | Skill invocation begins (name + input params) |
| `tool_end` | Skill completes (name + output summary) |
| `content_block` | Structured content (text, table, chart, status, action, panel_trigger) |
| `message_end` | Final response with all content blocks, tool calls, and panel payload |
| `error` | Error occurred during processing |

---

## Chart Types

The ChartRenderer supports these visualization types via Recharts:

| Type | Use Case |
|------|----------|
| `bar` | Category comparisons |
| `stacked_bar` | Composition breakdowns |
| `grouped_bar` | Side-by-side comparisons (forecast vs actual) |
| `line` | Time-series trends |
| `area` | Volume/range visualizations |
| `confidence` | P10/P50/P90 confidence intervals |
| `bridge` | Waterfall/variance bridge charts |
| `variance` | Positive/negative deviation charts |
| `pie` | Proportional distributions |
| `combo` | Mixed bar + line charts |
| `scatter` | Correlation analysis |

---

## Development

### Running Tests

```bash
cd backend
pytest -v
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | (required) | Claude API key |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Model to use |
| `DATABASE_URL` | `sqlite:///./rolling_forecast.db` | Database connection string |
| `JWT_SECRET_KEY` | `dev-secret-key-...` | JWT signing secret |
| `APP_ENV` | `development` | Environment (development/production) |
| `LOG_LEVEL` | `INFO` | Logging level |

---

## License

Proprietary. Internal use only.
