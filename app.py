from __future__ import annotations

import csv
import os
from threading import RLock
from io import StringIO
from pathlib import Path
from typing import Dict, Optional

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - depends on local environment
    def load_dotenv() -> bool:
        return False
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import StudyDatabase
from models import StudySession
from tasks import (
    apply_patch_to_repo,
    checkout_task_repo,
    create_session_repo_copy,
    curated_bugsinpy_tqdm_task_by_instance,
    curated_bugsinpy_tqdm_tasks,
    list_repo_tree,
    read_repo_file,
    run_test_command,
)
from vanderbilt import VanderbiltClient
from workflow import StudyWorkflowEngine, WORKFLOW_TYPES

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REPO_ROOT = BASE_DIR / "repos"
WORKSPACES_ROOT = Path(
    os.getenv(
        "LEARNINGSYS_WORKSPACES_ROOT",
        DATA_DIR / "session_workspaces",
    )
).resolve()
DB_PATH = DATA_DIR / "study_sessions.db"

app = FastAPI(title="AgentForge", version="3.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


db = StudyDatabase(DB_PATH)
llm = VanderbiltClient.from_env()
engine = StudyWorkflowEngine(db, llm)
session_locks: Dict[str, RLock] = {}


class SessionCreateRequest(BaseModel):
    participant_id: str
    participant_name: Optional[str] = None
    # Legacy-compatible practice step selector. Accepts planner/coder/reviewer/tester
    # or the full role label.
    workflow_type: str
    manual_step: Optional[str] = None
    # Optional specific curated BugsInPy instance; defaults to the first task if omitted.
    instance_id: Optional[str] = None


class HumanInputRequest(BaseModel):
    text: str


class SupportRequest(BaseModel):
    question: str


class PatchSubmissionRequest(BaseModel):
    patch: str


class RepoPatchApplyRequest(BaseModel):
    patch: str


class TestRunRequest(BaseModel):
    command: str
    timeout_seconds: Optional[int] = 120


class ManualStepUpdateRequest(BaseModel):
    manual_step: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "loaded_sessions": str(len(db.load_sessions()))}


@app.get("/api/chatdev/framework")
def chatdev_framework() -> Dict[str, object]:
    payload = engine.workflow_catalog()
    payload["workflow_types"] = WORKFLOW_TYPES
    return payload


@app.get("/api/tasks/easiest")
def easiest_tasks() -> Dict[str, object]:
    tasks = curated_bugsinpy_tqdm_tasks()
    return {
        "tasks": tasks,
        "dataset_name": "BugsInPy",
        "split": "curated-tqdm",
    }


@app.get("/api/tasks/{instance_id}")
def task_by_id(instance_id: str) -> Dict[str, object]:
    task = curated_bugsinpy_tqdm_task_by_instance(instance_id)
    return {"task": task}


@app.post("/api/sessions")
def create_session(req: SessionCreateRequest) -> Dict[str, object]:
    participant_id = _normalize_participant_id(req.participant_id)
    participant = (req.participant_name or participant_id).strip() or participant_id

    workflow_type = (req.manual_step or req.workflow_type).strip()
    if workflow_type.lower() not in set(WORKFLOW_TYPES) and workflow_type not in {
        "Task Planner",
        "Patch Author",
        "Code Reviewer",
        "Test Runner",
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                "workflow_type/manual_step must be one of: "
                + ", ".join(WORKFLOW_TYPES)
                + " or a full role label"
            ),
        )

    try:
        if req.instance_id:
            task = curated_bugsinpy_tqdm_task_by_instance(req.instance_id.strip())
        else:
            task = curated_bugsinpy_tqdm_tasks()[0]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed loading BugsInPy task: {exc}") from exc

    try:
        cache_repo_path = checkout_task_repo(task, REPO_ROOT)
        repo_path = str(create_session_repo_copy(cache_repo_path, WORKSPACES_ROOT))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to checkout repo: {exc}") from exc

    session = engine.create_session(
        participant_id=participant_id,
        participant_name=participant,
        task=task,
        repo_path=repo_path,
        workflow_type=workflow_type,
    )
    session.lock = _lock_for_session(session.session_id)
    return {"session": engine.serialize_session(session)}


@app.get("/api/sessions")
def list_sessions(
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    participant_id = _normalize_participant_id(x_participant_id)
    rows = db.list_sessions(participant_id=participant_id)
    return {
        "active_session_count": len(rows),
        "sessions": rows,
    }


def _normalize_participant_id(participant_id: Optional[str]) -> str:
    normalized = str(participant_id or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="participant_id is required")
    return normalized


def _optional_participant_id(participant_id: Optional[str]) -> Optional[str]:
    normalized = str(participant_id or "").strip()
    return normalized or None


def _lock_for_session(session_id: str) -> RLock:
    lock = session_locks.get(session_id)
    if lock is None:
        lock = RLock()
        session_locks[session_id] = lock
    return lock


def _get_session(session_id: str, participant_id: str) -> StudySession:
    session = db.get_session(session_id, participant_id=participant_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown session: {session_id}")
    session.lock = _lock_for_session(session.session_id)
    return session


@app.get("/api/sessions/{session_id}")
def get_session(
    session_id: str,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    return {"session": engine.serialize_session(session)}


@app.post("/api/sessions/{session_id}/start")
def start_or_continue(
    session_id: str,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    engine.advance(session)
    return {"session": engine.serialize_session(session)}


@app.post("/api/sessions/{session_id}/human-input")
def provide_human_input(
    session_id: str,
    req: HumanInputRequest,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Input text is empty")
    try:
        engine.advance(session, human_input=text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session": engine.serialize_session(session)}


@app.post("/api/sessions/{session_id}/support")
def get_support(
    session_id: str,
    req: SupportRequest,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, str]:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is empty")

    text = engine.get_support(session, question)
    return {"support": text}


@app.post("/api/sessions/{session_id}/manual-step")
def update_manual_step(
    session_id: str,
    req: ManualStepUpdateRequest,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    manual_step = req.manual_step.strip()
    if not manual_step:
        raise HTTPException(status_code=400, detail="manual_step is required")
    try:
        engine.update_manual_step(session, manual_step)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session": engine.serialize_session(session)}


@app.get("/api/sessions/{session_id}/events")
def get_events(
    session_id: str,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    participant_id = _normalize_participant_id(x_participant_id)
    session = _get_session(session_id, participant_id)
    return {
        "events": [event.to_dict() for event in session.events],
        "persisted": db.get_events(session_id, participant_id=participant_id),
    }


@app.get("/api/sessions/{session_id}/repo/tree")
def repo_tree(
    session_id: str,
    path: str = Query(default="", description="Relative directory path"),
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    try:
        items = list_repo_tree(Path(session.repo_path), relative_path=path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": path, "items": items}


@app.get("/api/sessions/{session_id}/repo/file")
def repo_file(
    session_id: str,
    path: str = Query(..., description="Relative file path"),
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    try:
        payload = read_repo_file(Path(session.repo_path), relative_path=path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return payload


@app.post("/api/sessions/{session_id}/submit-patch")
def submit_patch(
    session_id: str,
    req: PatchSubmissionRequest,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    patch = req.patch
    if not patch.strip():
        raise HTTPException(status_code=400, detail="Patch is empty")

    evaluation = engine.evaluate_submission(session, patch)
    return {"evaluation": evaluation, "session": engine.serialize_session(session)}


@app.post("/api/sessions/{session_id}/apply-patch")
def apply_repo_patch(
    session_id: str,
    req: RepoPatchApplyRequest,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    patch = req.patch
    if not patch.strip():
        raise HTTPException(status_code=400, detail="Patch is empty")

    try:
        result = apply_patch_to_repo(Path(session.repo_path), patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Patch application failed: {exc}") from exc

    engine.record_patch_application(session, result)
    return {"result": result, "session": engine.serialize_session(session)}


@app.post("/api/sessions/{session_id}/run-tests")
def run_repo_tests(
    session_id: str,
    req: TestRunRequest,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    command = req.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="command is required")

    timeout_seconds = req.timeout_seconds or 120
    try:
        result = run_test_command(Path(session.repo_path), command, timeout_seconds=timeout_seconds)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Test execution failed: {exc}") from exc

    engine.record_test_run(session, result)
    return {"result": result, "session": engine.serialize_session(session)}


@app.get("/api/sessions/{session_id}/evaluation")
def get_evaluation(
    session_id: str,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    if session.evaluation is None:
        raise HTTPException(status_code=404, detail="No evaluation yet")
    return {"evaluation": session.evaluation}


@app.get("/api/sessions/{session_id}/metrics")
def get_metrics(
    session_id: str,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    return {"rows": engine.build_turn_metrics(session)}


@app.get("/api/sessions/{session_id}/metrics.csv")
def get_metrics_csv(
    session_id: str,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Response:
    session = _get_session(session_id, _normalize_participant_id(x_participant_id))
    rows = engine.build_turn_metrics(session)

    headers = [
        "session_id",
        "participant_id",
        "participant_name",
        "workflow_type",
        "human_role",
        "task_instance_id",
        "task_repo",
        "turn",
        "role",
        "actor",
        "started_at",
        "completed_at",
        "duration_seconds",
        "response_latency_seconds",
        "support_requests_this_turn",
        "content_chars",
        "content_words",
    ]

    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    csv_body = buf.getvalue()
    return Response(
        content=csv_body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{session_id}_turn_metrics.csv"',
        },
    )


@app.get("/api/sessions/{session_id}/export")
def export_session(
    session_id: str,
    x_participant_id: str = Header(..., alias="X-Participant-ID"),
) -> Dict[str, object]:
    participant_id = _normalize_participant_id(x_participant_id)
    session = _get_session(session_id, participant_id)
    return {
        "session": engine.serialize_session(session),
        "events": db.get_events(session_id, participant_id=participant_id),
        "metrics": engine.build_turn_metrics(session),
    }
