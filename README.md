# AI ADHD Companion

AI ADHD Companion is a completed academic prototype of an empathetic,
adaptive task manager for ADHD users.

The application combines task planning, recurring task scheduling, adaptive
state transitions, guided focus flows, and AI-assisted messaging into a single
Streamlit experience backed by Supabase.

> Academic project. No license specified.

## Screenshots

Place screenshots in `assets/readme/` and update these image paths when
publishing the repository.

### Task List And Subtasks

<!-- TODO: Add screenshot of the task list with subtasks. -->
<!-- Example: ![Task list with subtasks](assets/readme/task-list-subtasks.png) -->

### Body-Doubling Overlay

<!-- TODO: Add screenshot of the Body-Doubling focus overlay. -->
<!-- Example: ![Body-Doubling overlay](assets/readme/body-doubling-overlay.png) -->

## Features

- Task and subtask management with adaptive task statuses.
- Recurring task instance generation through a Supabase Edge Function.
- Parent-task synchronization from child task instances.
- User-state finite state machine for adaptive behavior.
- Task-state finite state machine for task lifecycle transitions.
- Persona-aware adaptation rules.
- Pomodoro focus flow with sprint review and protected rest breaks.
- Time Chunks focus flow with adaptive work-cycle duration planning.
- Body-Doubling flow with microsteps, guided sessions, review dialogs, and
  optional AI-generated encouragement.
- Adaptive UI messages catalogued in `app/application/adaptive/message_catalog.py`.
- Optional OpenAI-powered guidance and fallback messages when the API is not
  configured.
- Optional ElevenLabs voice playback for selected messages.
- Docker-first local development workflow.

## Architecture

The project follows a simple layered structure:

```text
app/
  ui/                       Streamlit UI, focus flows, dialogs, timers
  application/              Use cases, DTOs, adaptive rules, prompts
  config.py                 Shared runtime defaults
infra/
  docker/                   Docker development environment
  supabase/
    functions/              Supabase Edge Functions
    migrations/             Historical SQL migrations
scripts/                    Test data loaders and scheduler helpers
tests/                      Unit tests
```

Important modules:

- `app/ui/main.py`: Streamlit application entrypoint and orchestration layer.
- `app/ui/body_doubling.py`: Body-Doubling flow.
- `app/ui/chunk.py`: adaptive Chunk focus-flow planning and review.
- `app/ui/pomodoro.py`: Pomodoro, rest flow, and shared focus overlay.
- `app/ui/state/timers.py`: shared timer state primitives.
- `app/application/use_cases/task_state_machine.py`: task-status FSM.
- `app/application/use_cases/user_state_machine.py`: user-state FSM.
- `infra/supabase/functions/recurrent_task_sched/index.ts`: recurrent task
  scheduler Edge Function.

## Technology Stack

- Python 3.11+
- Streamlit
- Supabase
- Supabase Edge Functions, Deno/TypeScript
- OpenAI API
- ElevenLabs API
- Docker and Docker Compose
- Pytest

## Requirements

- Docker Desktop or Docker Engine with Docker Compose.
- A Supabase project.
- A SQL schema file exported from Supabase and imported into the target
  project.
- Optional: OpenAI API key for AI-generated guidance.
- Optional: ElevenLabs API key for voice playback.

## Environment Variables

Create a `.env` file in the repository root. Do not commit real secrets.

```dotenv
APP_ENV=development

SUPABASE_URL=
SUPABASE_KEY=
SUPABASE_DB_PASSWORD=
SUPABASE_SECRET_KEY=

# Used by the recurrent task scheduler invocation flow.
# In this project it should match SUPABASE_SECRET_KEY.
ADHD_COMPANION_KEY=

OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini

ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
ELEVENLABS_MODEL_ID=eleven_multilingual_v2

# Optional. If omitted, the app falls back to SUPABASE_KEY for local cookies.
COOKIES_PASSWORD=
```

Notes:

- `SUPABASE_KEY` is used by the Streamlit app and local helper scripts.
- `ADHD_COMPANION_KEY` is used as the bearer token for
  `recurrent_task_sched`.
- The Edge Function expects `SB_API_URL` and `ADHD_COMPANION_KEY` in its
  Supabase function runtime environment.
- The database cron job also reads `SB_API_URL` and `ADHD_COMPANION_KEY` from
  Supabase Vault.
- OpenAI and ElevenLabs are optional; the app has deterministic fallbacks for
  missing OpenAI configuration and disables voice playback when ElevenLabs is
  unavailable.
- `COOKIES_PASSWORD` can be set explicitly when deploying outside local
  development.

## Run With Docker

Docker is the recommended local workflow.

```bash
docker compose -f infra/docker/docker-compose.dev.yml up --build
```

Open the app at:

```text
http://localhost:8501
```

Stop the environment:

```bash
docker compose -f infra/docker/docker-compose.dev.yml down
```

The same commands are available through the `Makefile`:

```bash
make dev
make stop
```

## Local Python Run

Use this path only when you want to run without Docker.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app/ui/main.py
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app/ui/main.py
```

## Supabase Setup

This repository keeps the historical migrations under
`infra/supabase/migrations/`, but the publication-ready setup is expected to
use a single SQL schema dump exported from Supabase.

Recommended setup:

1. Create or select a Supabase project.
2. Import the schema SQL dump into the Supabase SQL editor or database.
3. Configure the project URL and keys in `.env`.
4. Configure Supabase Vault secrets for the database cron job.
5. Deploy the recurrent scheduler Edge Function.
6. Configure Edge Function runtime secrets.

### Supabase Vault Secrets

The database-side scheduled job calls the Edge Function through `pg_cron` and
`pg_net`. That SQL job reads secrets from `vault.decrypted_secrets`.

Required Vault secret names:

| Secret name | Purpose |
|---|---|
| `SB_API_URL` | Base Supabase project URL, for example `https://<project-ref>.supabase.co`. |
| `ADHD_COMPANION_KEY` | Bearer token sent to the scheduler Edge Function. It should match local `ADHD_COMPANION_KEY` and `SUPABASE_SECRET_KEY`. |

Create them from the Supabase SQL editor:

```sql
select vault.create_secret(
  'https://<project-ref>.supabase.co',
  'SB_API_URL'
);

select vault.create_secret(
  '<same-value-as-ADHD_COMPANION_KEY>',
  'ADHD_COMPANION_KEY'
);
```

If a secret already exists, update it from the Supabase Vault UI or remove and
recreate it according to the current Supabase project policy.

The cron schedule in the historical migrations is named `rtask-sched-task` and
posts to:

```text
<SB_API_URL>/functions/v1/recurrent_task_sched
```

### Edge Function Runtime Secrets

Deploy the scheduler:

```bash
supabase functions deploy recurrent_task_sched --no-verify-jwt
```

Set function secrets in Supabase:

```bash
supabase secrets set SB_API_URL="https://<project-ref>.supabase.co"
supabase secrets set ADHD_COMPANION_KEY="<same-value-as-local-ADHD_COMPANION_KEY>"
```

The function is intentionally protected by an application-level bearer token.
The helper script invokes it using `ADHD_COMPANION_KEY`.

Dry run:

```bash
python scripts/invoke_recurrent_task_sched.py --dry-run
```

Real execution:

```bash
python scripts/invoke_recurrent_task_sched.py --for-good
```

## Tests

Run all tests:

```bash
pytest
```

With Docker:

```bash
docker compose -f infra/docker/docker-compose.dev.yml run --rm ai-adhd-companion-app pytest
```

Focused examples:

```bash
docker compose -f infra/docker/docker-compose.dev.yml run --rm ai-adhd-companion-app pytest tests/unit/test_timers.py
docker compose -f infra/docker/docker-compose.dev.yml run --rm ai-adhd-companion-app pytest tests/unit/test_chunk.py
docker compose -f infra/docker/docker-compose.dev.yml run --rm ai-adhd-companion-app pytest tests/unit/test_pomodoro.py
```

## Utility Scripts

The `scripts/` directory contains development and validation helpers for test
data, recurrent scheduling, and status aging. Task loader scripts read their
input datasets from the `seeder/` folder, which is intended to be published
with the repository.

Use these scripts only against development, demo, or disposable Supabase
projects. Several of them write data or trigger status changes.

| Script | Purpose |
|---|---|
| `scripts/invoke_recurrent_task_sched.py` | Invokes the `recurrent_task_sched` Edge Function manually. Defaults to `--dry-run`; use `--for-good` for real execution. |
| `scripts/load_simple_tasks_testset.py` | Loads a small task dataset from `seeder/` for quick UI checks. |
| `scripts/load_recurrent_testset.py` | Loads recurrent-task scenarios from `seeder/` for scheduler validation. |
| `scripts/load_full_testset.py` | Loads a broader end-to-end dataset from `seeder/` and invokes the recurrent scheduler during the flow. |
| `scripts/mark_old_instances_stale.py` | Ages old task instances into stale/debt scenarios for testing lifecycle behavior. |

Invoke the scheduler manually:

```bash
python scripts/invoke_recurrent_task_sched.py --dry-run
python scripts/invoke_recurrent_task_sched.py --for-good
```

Run a script through Docker:

```bash
docker compose -f infra/docker/docker-compose.dev.yml run --rm ai-adhd-companion-app python scripts/load_full_testset.py
```

## Development Commands

```bash
make dev      # Start Docker development app
make stop     # Stop Docker development app
make run      # Run Streamlit locally
make test     # Run pytest locally
make format   # Format app and tests with black
make lint     # Lint app and tests with ruff
```

## Security Notes

- Never commit `.env` files or real Supabase/OpenAI/ElevenLabs credentials.
- Use separate Supabase projects or credentials for development and production.
- The recurrent scheduler should be deployed with `--no-verify-jwt` only when
  protected by the `ADHD_COMPANION_KEY` bearer-token check.
- Review Row Level Security policies before exposing a Supabase project beyond
  local or academic evaluation use.

## Project Status

Completed prototype.

This project was built as an academic final-degree project and is intended as a
working prototype for evaluation, demonstration, and further research-oriented
development.
