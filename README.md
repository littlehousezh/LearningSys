# Learning Workflow Studio

This project is a small educational system for novice students to practice realistic software-engineering workflows on simplified code repair tasks.

## What the system does

- Selects relatively easy tasks using an educational ranking heuristic.
- Runs a shared four-step workflow:
  1. `Task Planner`
  2. `Patch Author`
  3. `Code Reviewer`
  4. `Test Runner`
- Lets you replace any one of those steps with manual input.
- Generates a structured handoff for the next step after each turn, including human turns.
- Applies unified-diff patches directly to the checked-out repo.
- Runs real test commands inside the checked-out repo using a small safety allowlist.
- Reloads saved sessions from SQLite on startup.
- Lets you switch which workflow step you own mid-session.
- Visualizes workflow progress, current step, your step, and overall system status.
- Explains each step with an explicit step briefing:
  - what happens in the step,
  - what you should do,
  - why the step matters,
  - what happens next,
  - and the suggested response format.

## Backend module map

- `app.py`
  FastAPI entrypoint and API layer. Creates sessions, starts or advances workflows, exposes repo browsing, support, metrics, and exports.

- `workflow.py`
  Core workflow engine. Defines the educational step catalog, runs one step at a time, routes review/test decisions, creates handoffs, switches your owned step, records repo actions, and serializes the full workflow state for the UI.

- `models.py`
  Shared data models for sessions and events. Stores session state, transcripts, your-step configuration, and serialized handoff data.

- `tasks.py`
  SWE-bench task selection and repo checkout utilities. Ranks tasks by beginner-friendly heuristics such as patch size, file count, issue length, and Python affinity. Also applies patches and runs test commands in checked-out repos.

- `database.py`
  SQLite persistence for sessions and events.

- `vanderbilt.py`
  Thin client for AI support and agent execution. Supports a default model plus optional per-step model overrides and fails gracefully when optional dependencies are not installed yet.

- `static/index.html`
  Learning workflow UI shell.

- `static/app.js`
  Frontend state management, rendering, and API calls.

- `static/style.css`
  UI styling for the workflow dashboard, step briefings, and study hub.

## Key API endpoints

- `GET /api/chatdev/framework`
- `GET /api/tasks/easiest`
- `GET /api/tasks/{instance_id}`
- `POST /api/sessions`
- `GET /api/sessions/{session_id}`
- `POST /api/sessions/{session_id}/start`
- `POST /api/sessions/{session_id}/human-input`
- `POST /api/sessions/{session_id}/manual-step`
- `POST /api/sessions/{session_id}/support`
- `POST /api/sessions/{session_id}/apply-patch`
- `POST /api/sessions/{session_id}/run-tests`
- `GET /api/sessions/{session_id}/repo/tree`
- `GET /api/sessions/{session_id}/repo/file`
- `GET /api/sessions/{session_id}/metrics`
- `GET /api/sessions/{session_id}/metrics.csv`

## Running locally

```bash
./launch.sh
```

Then open `http://localhost:8080`.

`launch.sh` creates a virtual environment, installs dependencies, and starts the FastAPI app.

## Vanderbilt / Amplify configuration

Minimum setup:

```bash
AMPLIFY_BEARER=your_token
AMPLIFY_MODEL_ID=gpt-5
```

Optional role-specific overrides:

```bash
AMPLIFY_MODEL_ID_PLANNER=
AMPLIFY_MODEL_ID_CODER=
AMPLIFY_MODEL_ID_REVIEWER=
AMPLIFY_MODEL_ID_TESTER=
AMPLIFY_MODEL_ID_SUPPORT=
```

The app currently sends requests to `https://prod-api.vanderbilt.ai/chat` using the payload shape already used in this repo:

- bearer auth header
- `data.messages`
- `data.temperature`
- `data.max_tokens`
- `data.options.model.id`

For backward compatibility, the app also accepts the older `VANDERBILT_*` variable names, but the preferred configuration is now `AMPLIFY_*` to match the example repo and Vanderbilt Amplify naming.

## Notes

- If `AMPLIFY_BEARER` is missing, the workflow still runs but AI steps will return a helpful warning instead of a real model response.
- If dependencies such as `requests` or `datasets` are not installed yet, the backend now reports that clearly instead of crashing at import time.
- Allowed test command prefixes are intentionally limited for safety: `pytest`, `python -m pytest`, `python3 -m pytest`, `python -m unittest`, `python3 -m unittest`, `tox`, `nox`, `uv run pytest`, `poetry run pytest`, `pipenv run pytest`, `python manage.py test`, and `python3 manage.py test`.
