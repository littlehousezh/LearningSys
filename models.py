from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


WORKFLOW_STEP_SEQUENCE: List[str] = [
    "Task Planner",
    "Patch Author",
    "Code Reviewer",
    "Test Runner",
]

ROLE_TO_SLUG: Dict[str, str] = {
    "Task Planner": "planner",
    "Patch Author": "coder",
    "Code Reviewer": "reviewer",
    "Test Runner": "tester",
}

SLUG_TO_ROLE: Dict[str, str] = {slug: role for role, slug in ROLE_TO_SLUG.items()}

ROLE_ALIASES: Dict[str, str] = {
    "Patch Agent": "Patch Author",
    "Test Agent": "Test Runner",
    "planner": "Task Planner",
    "coder": "Patch Author",
    "reviewer": "Code Reviewer",
    "tester": "Test Runner",
}


def normalize_role_label(role: Optional[str]) -> Optional[str]:
    if role is None:
        return None
    return ROLE_ALIASES.get(role, role)


def normalize_role_text(text: str) -> str:
    if not text:
        return text

    normalized = text
    for source, target in ROLE_ALIASES.items():
        normalized = normalized.replace(source, target)
    return normalized


def manual_step_slug(role: str) -> str:
    normalized = normalize_role_label(role) or ""
    return ROLE_TO_SLUG.get(normalized, normalized.lower().replace(" ", "_"))


@dataclass
class WorkflowEvent:
    event_id: str
    session_id: str
    created_at: str
    event_type: str
    role: Optional[str]
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        event_type: str,
        content: str,
        role: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "WorkflowEvent":
        return cls(
            event_id=f"evt_{uuid4().hex}",
            session_id=session_id,
            created_at=utc_now_iso(),
            event_type=event_type,
            role=role,
            content=content,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "event_type": self.event_type,
            "role": normalize_role_label(self.role),
            "content": normalize_role_text(self.content),
            "metadata": self.metadata,
        }


@dataclass
class StudySession:
    session_id: str
    participant_id: str
    participant_name: str
    created_at: str
    task: Dict[str, Any]
    repo_path: str
    workflow_type: str
    manual_step_role: str
    status: str = "created"
    current_node_idx: int = 0
    iteration_count: int = 0
    waiting_for_human: bool = False
    waiting_role: Optional[str] = None
    wait_started_at: Optional[str] = None
    support_requests_count: int = 0
    messages: List[Dict[str, Any]] = field(default_factory=list)
    events: List[WorkflowEvent] = field(default_factory=list)
    submitted_patch: Optional[str] = None
    evaluation: Optional[Dict[str, Any]] = None
    last_handoff: Optional[Dict[str, Any]] = None
    last_patch_application: Optional[Dict[str, Any]] = None
    last_test_run: Optional[Dict[str, Any]] = None
    lock: RLock = field(default_factory=RLock, repr=False)

    @classmethod
    def create(
        cls,
        *,
        participant_id: Optional[str] = None,
        participant_name: str,
        task: Dict[str, Any],
        repo_path: str,
        workflow_type: str,
        manual_step_role: str,
    ) -> "StudySession":
        return cls(
            session_id=f"sess_{uuid4().hex}",
            participant_id=(participant_id or participant_name).strip(),
            participant_name=participant_name,
            created_at=utc_now_iso(),
            task=task,
            repo_path=repo_path,
            workflow_type=workflow_type,
            manual_step_role=normalize_role_label(manual_step_role) or manual_step_role,
        )

    @property
    def human_role(self) -> str:
        return self.manual_step_role

    @property
    def manual_step_index(self) -> int:
        try:
            return WORKFLOW_STEP_SEQUENCE.index(self.manual_step_role)
        except ValueError:
            return 0

    @property
    def current_role(self) -> Optional[str]:
        if 0 <= self.current_node_idx < len(WORKFLOW_STEP_SEQUENCE):
            return WORKFLOW_STEP_SEQUENCE[self.current_node_idx]
        return None

    def to_dict(self, *, include_events: bool = True) -> Dict[str, Any]:
        active_role = normalize_role_label(self.current_role)
        payload = {
            "session_id": self.session_id,
            "participant_id": self.participant_id,
            "participant_name": self.participant_name,
            "created_at": self.created_at,
            "status": self.status,
            "task": self.task,
            "repo_path": self.repo_path,
            "workflow_type": self.workflow_type,
            "manual_step_role": normalize_role_label(self.manual_step_role),
            "manual_step_slug": manual_step_slug(self.manual_step_role),
            "current_node_idx": self.current_node_idx,
            "iteration_count": self.iteration_count,
            "human_role_for_round": normalize_role_label(self.human_role),
            "active_role": active_role,
            "role_sequence": list(WORKFLOW_STEP_SEQUENCE),
            "workflow_nodes": [
                {
                    "role": normalize_role_label(role),
                    "actor": "human" if role == self.manual_step_role else "ai",
                }
                for role in WORKFLOW_STEP_SEQUENCE
            ],
            "current_round": 0,
            "total_rounds": 1,
            "waiting_for_human": self.waiting_for_human,
            "waiting_role": normalize_role_label(self.waiting_role),
            "wait_started_at": self.wait_started_at,
            "support_requests_count": self.support_requests_count,
            "messages": [
                {
                    **msg,
                    "role": normalize_role_label(msg.get("role")),
                    "content": normalize_role_text(str(msg.get("content", ""))),
                }
                for msg in self.messages
            ],
            "submitted_patch": self.submitted_patch,
            "evaluation": self.evaluation,
            "last_handoff": self.last_handoff,
            "last_patch_application": self.last_patch_application,
            "last_test_run": self.last_test_run,
        }
        if include_events:
            payload["events"] = [event.to_dict() for event in self.events]
        return payload
