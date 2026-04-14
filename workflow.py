from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from database import StudyDatabase
from models import StudySession, WorkflowEvent, manual_step_slug, normalize_role_label, utc_now_iso
from tasks import apply_patch_to_repo, run_test_command
from vanderbilt import VanderbiltClient


@dataclass(frozen=True)
class RouteRule:
    pattern: str
    next_idx: int
    description: str


@dataclass(frozen=True)
class StepTeaching:
    why_needed: str
    how_to_do_well: str
    core_concepts: List[str]
    human_prompt_template: str
    success_signals: List[str]


@dataclass(frozen=True)
class WorkflowStep:
    role: str
    slug: str
    ai_instruction: str
    teaching: StepTeaching
    default_next: int
    routes: List[RouteRule] = field(default_factory=list)


WORKFLOW_STEPS: List[WorkflowStep] = [
    WorkflowStep(
        role="Task Planner",
        slug="planner",
        ai_instruction=(
            "Study the issue and create an implementation plan for the Patch Author. Explain the likely "
            "root cause, the code areas that probably matter, and the checks the team should run before "
            "calling the task complete."
        ),
        teaching=StepTeaching(
            why_needed=(
                "Planning turns an ambiguous bug report into a concrete path. It keeps the rest of "
                "the workflow focused and helps novices learn to reason before editing code."
            ),
            how_to_do_well=(
                "Translate the issue into a short hypothesis, name the likely module boundaries, "
                "and propose a small set of verification checks. Good plans are specific enough to "
                "guide coding without pretending you already know every line of the fix."
            ),
            core_concepts=[
                "problem decomposition",
                "root-cause hypotheses",
                "acceptance criteria",
                "scope control",
            ],
            human_prompt_template=(
                "PLAN_SUMMARY: <2-4 sentence implementation plan>\n"
                "ROOT_CAUSE: <most likely cause>\n"
                "LIKELY_AREAS: <modules, files, or subsystems to inspect>\n"
                "ACCEPTANCE_CHECKS: <tests or behaviors that should pass>"
            ),
            success_signals=[
                "Explains the bug in plain language.",
                "Narrows the likely code area instead of listing the whole repo.",
                "Defines what success looks like before coding starts.",
            ],
        ),
        default_next=1,
    ),
    WorkflowStep(
        role="Patch Author",
        slug="coder",
        ai_instruction=(
            "Turn the latest plan and feedback into a concrete code-change proposal for the reviewer. "
            "Name the files, functions, or classes to touch and describe the smallest fix that should "
            "satisfy the issue."
        ),
        teaching=StepTeaching(
            why_needed=(
                "This step transforms reasoning into implementation. For learners, it is the bridge "
                "between understanding a bug and expressing a maintainable fix."
            ),
            how_to_do_well=(
                "Keep the fix minimal, tie every edit back to the bug, and mention the exact code "
                "surfaces that need to change. Strong patch plans anticipate edge cases and avoid "
                "unrelated cleanup."
            ),
            core_concepts=[
                "minimal diffs",
                "local reasoning",
                "change impact",
                "implementation tradeoffs",
            ],
            human_prompt_template=(
                "PATCH_STATUS: <READY|BLOCKED>\n"
                "FILES_CHANGED: <comma-separated files>\n"
                "IMPLEMENTATION_PLAN: <precise change description>\n"
                "PATCH_DIFF: <unified diff or before/after code snippet showing the exact changed lines>\n"
                "DONE_CRITERIA: <what the reviewer and tester should verify>"
            ),
            success_signals=[
                "Names the concrete edit locations.",
                "Keeps scope tight.",
                "Shows the exact changed lines, not just a high-level idea.",
                "Connects the fix to the original acceptance checks.",
            ],
        ),
        default_next=2,
    ),
    WorkflowStep(
        role="Code Reviewer",
        slug="reviewer",
        ai_instruction=(
            "Review the proposed fix for correctness, regressions, missing edge cases, and clarity. "
            "Decide whether the patch is ready to hand off to testing or should return to the patch author "
            "for revision."
        ),
        teaching=StepTeaching(
            why_needed=(
                "Review catches design and correctness problems before the team spends time validating "
                "a weak implementation. It teaches learners to evaluate code, not just write it."
            ),
            how_to_do_well=(
                "Check whether the fix actually addresses the issue, whether it could break nearby "
                "behavior, and whether the implementation is simpler than necessary. Approval should "
                "mean the reviewer can explain why the change is safe to test, and revision should be "
                "reserved for concrete problems that block testing."
            ),
            core_concepts=[
                "correctness review",
                "regression risk",
                "evidence-based approval",
                "feedback loops",
            ],
            human_prompt_template=(
                "REVIEW_DECISION: <APPROVE|REVISE>\n"
                "PATCH_STATUS: <copy upstream>\n"
                "FILES_CHANGED: <copy upstream>\n"
                "REVIEW_NOTES: <specific review feedback>"
            ),
            success_signals=[
                "Mentions concrete risks or evidence.",
                "Approves only when the patch is coherent and testable.",
                "Gives actionable revision feedback when rejecting.",
                "Uses revise only when there is a concrete problem that should block testing.",
            ],
        ),
        default_next=3,
        routes=[
            RouteRule(
                pattern=r"(?im)^\s*REVIEW_DECISION\s*:\s*APPROVE\b",
                next_idx=3,
                description="Approved patches move to testing.",
            ),
            RouteRule(
                pattern=r"(?im)^\s*REVIEW_DECISION\s*:\s*REVISE\b",
                next_idx=1,
                description="Revision requests return to the patch author.",
            ),
        ],
    ),
    WorkflowStep(
        role="Test Runner",
        slug="tester",
        ai_instruction=(
            "Judge whether the proposed fix has enough evidence to pass the right checks. Use the issue, "
            "patch plan, review notes, and any recorded test output to decide whether the workflow can "
            "finish or must loop back because the evidence points to a real defect."
        ),
        teaching=StepTeaching(
            why_needed=(
                "Testing closes the loop between intended behavior and observed evidence. It teaches "
                "learners to think in terms of proof, not just plausible code changes."
            ),
            how_to_do_well=(
                "Focus on the checks most directly tied to the bug, name the evidence you would want, "
                "and explain what failure would mean. Good test reasoning balances confidence with realism, "
                "and treats blocked local execution as missing evidence rather than automatic proof that the "
                "patch is wrong."
            ),
            core_concepts=[
                "verification",
                "evidence of correctness",
                "failure signals",
                "release confidence",
            ],
            human_prompt_template=(
                "TEST_DECISION: <PASS|FAIL>\n"
                "TEST_NOTES: <key evidence, missing evidence, or recommended checks>"
            ),
            success_signals=[
                "Explains why the fix should pass or fail.",
                "Connects the verdict to concrete checks.",
                "Loops back only when the workflow still lacks evidence.",
                "Uses FAIL only when the current evidence points to a real problem in the patch.",
            ],
        ),
        default_next=-1,
        routes=[
            RouteRule(
                pattern=r"(?im)^\s*TEST_DECISION\s*:\s*PASS\b",
                next_idx=-1,
                description="Passing tests complete the workflow.",
            ),
            RouteRule(
                pattern=r"(?im)^\s*TEST_DECISION\s*:\s*FAIL\b",
                next_idx=1,
                description="Failed evidence returns the workflow to the patch author.",
            ),
        ],
    ),
]

WORKFLOW_TYPES = [step.slug for step in WORKFLOW_STEPS]
WORKFLOW_STEP_BY_ROLE = {step.role: step for step in WORKFLOW_STEPS}
WORKFLOW_STEP_BY_SLUG = {step.slug: step for step in WORKFLOW_STEPS}
MAX_LOOP_ITERATIONS = 6


def resolve_manual_step(selection: Optional[str]) -> WorkflowStep:
    normalized = normalize_role_label((selection or "planner").strip())
    if not normalized:
        return WORKFLOW_STEPS[0]
    if normalized in WORKFLOW_STEP_BY_ROLE:
        return WORKFLOW_STEP_BY_ROLE[normalized]
    if normalized in WORKFLOW_STEP_BY_SLUG:
        return WORKFLOW_STEP_BY_SLUG[normalized]
    raise ValueError(
        "manual step must be one of: "
        + ", ".join(step.slug for step in WORKFLOW_STEPS)
        + " or one of: "
        + ", ".join(step.role for step in WORKFLOW_STEPS)
    )


class StudyWorkflowEngine:
    def __init__(
        self,
        db: StudyDatabase,
        llm: VanderbiltClient,
        test_runner: Optional[Any] = None,
        **_kwargs: Any,
    ) -> None:
        self.db = db
        self.llm = llm
        self.test_runner = test_runner

    def workflow_catalog(self) -> Dict[str, Any]:
        return {
            "overview": (
                "A four-step educational code-repair workflow. One agent owns planning, coding, "
                "review, and testing respectively. You can replace any one step with your own input."
            ),
            "manual_step_options": [self._serialize_step(step, current=False, status="pending", mode="agent") for step in WORKFLOW_STEPS],
            "max_loop_iterations": MAX_LOOP_ITERATIONS,
            "selection_policy": (
                "The task selector favors small patches, few edited files, concise issue statements, "
                "and strong Python-file affinity."
            ),
            "model_selection": {
                step.slug: self.llm.resolve_model_id(step.role)
                for step in WORKFLOW_STEPS
            },
        }

    def create_session(
        self,
        *,
        participant_id: Optional[str] = None,
        participant_name: str,
        task: Dict[str, Any],
        repo_path: str,
        workflow_type: str,
    ) -> StudySession:
        manual_step = resolve_manual_step(workflow_type)
        session = StudySession.create(
            participant_id=participant_id,
            participant_name=participant_name,
            task=task,
            repo_path=repo_path,
            workflow_type=manual_step.slug,
            manual_step_role=manual_step.role,
        )
        self.db.save_session(session)
        self._event(
            session,
            event_type="session_created",
            content=(
                f"Session created for {participant_name}. "
                f"Your step: {manual_step.role}. "
                f"Task: {task['instance_id']} ({task['repo']})"
            ),
            metadata={
                "participant_id": session.participant_id,
                "workflow_type": manual_step.slug,
                "manual_step_role": manual_step.role,
                "instance_id": task["instance_id"],
                "difficulty_band": task.get("educational_fit", {}).get("difficulty_band"),
            },
        )
        return session

    def update_manual_step(self, session: StudySession, manual_step: str) -> StudySession:
        with session.lock:
            if session.status == "completed":
                raise ValueError("Cannot change the manual step after the workflow is completed")

            next_manual_step = resolve_manual_step(manual_step)
            if next_manual_step.role == session.manual_step_role:
                return session

            previous_step = session.manual_step_role
            session.manual_step_role = next_manual_step.role
            session.workflow_type = next_manual_step.slug

            current_step = self._step_by_index(session.current_node_idx)
            if session.waiting_for_human and current_step is not None and current_step.role != next_manual_step.role:
                session.waiting_for_human = False
                session.waiting_role = None
                session.wait_started_at = None
                session.status = "running"

            self._event(
                session,
                event_type="manual_step_updated",
                content=f"Manual practice step changed from {previous_step} to {next_manual_step.role}.",
                metadata={
                    "previous_manual_step": previous_step,
                    "manual_step_role": next_manual_step.role,
                    "current_role": session.current_role,
                },
            )
            self.db.save_session(session)
            return session

    def serialize_session(self, session: StudySession) -> Dict[str, Any]:
        payload = session.to_dict()
        current_step = self._step_by_index(session.current_node_idx)
        payload.update(
            {
                "manual_step_role": session.manual_step_role,
                "manual_step_slug": manual_step_slug(session.manual_step_role),
                "current_step": (
                    self._serialize_step(
                        current_step,
                        current=True,
                        status="current",
                        mode=self._step_mode(session, current_step),
                    )
                    if current_step is not None
                    else None
                ),
                "workflow_visualization": self._workflow_visualization(session),
                "workflow_process": self._workflow_process(session),
                "system_status": self._system_status(session),
                "step_briefing": self._step_briefing(session),
                "next_handoff": session.last_handoff,
                "manual_step_options": [
                    self._serialize_step(step, current=False, status="pending", mode="manual")
                    for step in WORKFLOW_STEPS
                ],
                "model_selection": {
                    step.slug: self.llm.resolve_model_id(step.role)
                    for step in WORKFLOW_STEPS
                },
            }
        )
        return payload

    def advance(self, session: StudySession, human_input: Optional[str] = None) -> StudySession:
        with session.lock:
            if session.status == "completed":
                return session

            if session.status == "created":
                session.status = "running"

            if human_input is not None and not session.waiting_for_human:
                raise ValueError("Session is not waiting for human input")

            if session.waiting_for_human:
                if not human_input:
                    return session

                current_step = self._step_by_index(session.current_node_idx)
                if current_step is None:
                    return session

                self._human_turn(session, current_step, human_input)
                session.waiting_for_human = False
                session.waiting_role = None
                session.wait_started_at = None
                session.status = "running"

                next_idx = self._route_with_context(
                    session,
                    current_step,
                    human_input,
                    actor="human",
                )
                if 0 <= next_idx < session.current_node_idx:
                    session.iteration_count += 1
                    self._event(
                        session,
                        event_type="workflow_rerouted",
                        role=current_step.role,
                        content=self._reroute_message(current_step.role, next_idx),
                        metadata={
                            "from_role": current_step.role,
                            "to_role": self._step_by_index(next_idx).role if self._step_by_index(next_idx) else None,
                            "trigger": "human",
                        },
                    )

                self._store_handoff(
                    session,
                    current_step=current_step,
                    output=human_input,
                    next_idx=next_idx,
                    actor="human",
                )
                session.current_node_idx = next_idx
                self._finish_if_needed(session)
                self.db.save_session(session)
                return session

            if session.current_node_idx < 0:
                self._complete_workflow(session, "Workflow complete.")
                self.db.save_session(session)
                return session

            if session.iteration_count >= MAX_LOOP_ITERATIONS:
                self._complete_workflow(
                    session,
                    f"Iteration limit ({MAX_LOOP_ITERATIONS}) reached. Workflow ended.",
                )
                self.db.save_session(session)
                return session

            current_step = self._step_by_index(session.current_node_idx)
            if current_step is None:
                self._complete_workflow(session, "Workflow complete.")
                self.db.save_session(session)
                return session

            self._prepare_step_evidence(session, current_step)

            if self._step_mode(session, current_step) == "manual":
                session.waiting_for_human = True
                session.waiting_role = current_step.role
                session.wait_started_at = utc_now_iso()
                session.status = "waiting_for_human"
                self._event(
                    session,
                    event_type="human_input_required",
                    role=current_step.role,
                    content=f"Your input is needed for {current_step.role}.",
                    metadata={
                        "node_idx": session.current_node_idx,
                        "iteration": session.iteration_count,
                        "guidance": self._serialize_step(
                            current_step,
                            current=True,
                            status="current",
                            mode="manual",
                        ),
                        "prompt": self._build_human_step_prompt(session, current_step),
                    },
                )
                self.db.save_session(session)
                return session

            output = self._ai_turn(session, current_step)
            next_idx = self._route_with_context(
                session,
                current_step,
                output,
                actor="ai",
            )
            if 0 <= next_idx < session.current_node_idx:
                session.iteration_count += 1
                self._event(
                    session,
                    event_type="workflow_rerouted",
                    role=current_step.role,
                    content=self._reroute_message(current_step.role, next_idx),
                    metadata={
                        "from_role": current_step.role,
                        "to_role": self._step_by_index(next_idx).role if self._step_by_index(next_idx) else None,
                        "trigger": "ai",
                    },
                )

            self._store_handoff(
                session,
                current_step=current_step,
                output=output,
                next_idx=next_idx,
                actor="ai",
            )
            session.current_node_idx = next_idx
            self._finish_if_needed(session)
            self.db.save_session(session)
            return session

    def get_support(self, session: StudySession, question: str) -> str:
        with session.lock:
            step = self._step_by_role(session.waiting_role or session.manual_step_role)
            recent = self._recent_transcript(session.messages)
            turn_num = len(session.messages) + 1
            support_idx = session.support_requests_count + 1
            evidence_block = self._role_evidence_block(session, step)
            prior_support = self._prior_support_for_turn(session, turn_num)

        alternative_block = ""
        if prior_support:
            previous = "\n\n".join(
                f"Prior support draft {idx + 1}:\n{self._clip(text, 1200)}"
                for idx, text in enumerate(prior_support[-2:])
            )
            alternative_block = (
                f"Prior support for this same step already exists.\n{previous}\n\n"
                "Do not repeat the same draft. Either improve the strongest version with sharper evidence "
                "or provide a materially different alternative that still fits the required format.\n\n"
            )

        prompt = (
            f"You are coaching a novice student who is currently acting as {step.role}.\n"
            f"Why this step exists: {step.teaching.why_needed}\n"
            f"How to do it well: {step.teaching.how_to_do_well}\n"
            f"Core concepts: {', '.join(step.teaching.core_concepts)}\n"
            f"Coaching instructions: {self._support_style_instructions(step)}\n"
            f"Required output template:\n{step.teaching.human_prompt_template}\n\n"
            f"Issue:\n{session.task['problem_statement']}\n\n"
            f"Hints:\n{session.task.get('hints_text', '')}\n\n"
            f"{evidence_block}"
            f"Recent workflow transcript:\n{recent}\n\n"
            f"{alternative_block}"
            f"Student question or draft:\n{question}\n\n"
            "Give concise coaching. Explain the reasoning and, if helpful, draft an answer using the required format."
        )
        text = self.llm.chat_once(
            prompt,
            temperature=0.35 if prior_support else 0.2,
            max_tokens=1000,
            role=step.role,
        )
        text = self._coerce_text(text)

        with session.lock:
            session.support_requests_count += 1
            self._event(
                session,
                event_type="human_support",
                role=step.role,
                content=text,
                metadata={
                    "question": question,
                    "turn": turn_num,
                    "support_index": support_idx,
                },
            )
            self.db.save_session(session)
        return text

    def evaluate_submission(self, session: StudySession, patch: str) -> Dict[str, Any]:
        from difflib import SequenceMatcher

        gold_patch = session.task.get("patch", "")

        def normalize(value: str) -> str:
            return "\n".join(line.rstrip() for line in (value or "").strip().splitlines())

        norm_gold = normalize(gold_patch)
        norm_user = normalize(patch)
        ratio = SequenceMatcher(a=norm_gold, b=norm_user).ratio()

        evaluation = {
            "exact_match": norm_gold == norm_user,
            "similarity_ratio": round(ratio, 4),
            "gold_patch_changed_lines": session.task.get("patch_changed_lines", 0),
            "participant_patch_changed_lines": self._patch_line_count(patch),
            "gold_patch_preview": gold_patch[:1200],
            "participant_patch_preview": patch[:1200],
        }

        session.submitted_patch = patch
        session.evaluation = evaluation
        session.status = "completed"

        self._event(
            session,
            event_type="submission",
            content="Participant submitted final patch.",
            metadata=evaluation,
        )
        self.db.save_session(session)
        return evaluation

    def build_turn_metrics(self, session: StudySession) -> List[Dict[str, Any]]:
        support_lookup: Dict[int, int] = {}
        for event in session.events:
            if event.event_type != "human_support":
                continue
            turn_id = int(event.metadata.get("turn", 0) or 0)
            support_lookup[turn_id] = support_lookup.get(turn_id, 0) + 1

        rows: List[Dict[str, Any]] = []
        for i, msg in enumerate(session.messages, start=1):
            content = str(msg.get("content", "") or "")
            rows.append(
                {
                    "session_id": session.session_id,
                    "participant_id": session.participant_id,
                    "participant_name": session.participant_name,
                    "workflow_type": session.workflow_type,
                    "human_role": session.manual_step_role,
                    "task_instance_id": session.task.get("instance_id", ""),
                    "task_repo": session.task.get("repo", ""),
                    "turn": i,
                    "role": msg.get("role", ""),
                    "actor": msg.get("actor", ""),
                    "started_at": msg.get("started_at", ""),
                    "completed_at": msg.get("completed_at", ""),
                    "duration_seconds": msg.get("duration_seconds"),
                    "response_latency_seconds": msg.get("response_latency_seconds"),
                    "support_requests_this_turn": support_lookup.get(i, 0),
                    "content_chars": len(content),
                    "content_words": len(content.split()),
                }
            )
        return rows

    def record_patch_application(self, session: StudySession, result: Dict[str, Any]) -> None:
        with session.lock:
            session.last_patch_application = result
            self._event(
                session,
                event_type="patch_applied",
                content=(
                    "Patch applied to checked-out repo."
                    if result.get("applied")
                    else "Patch application failed."
                ),
                metadata=result,
            )
            self.db.save_session(session)

    def record_test_run(self, session: StudySession, result: Dict[str, Any]) -> None:
        with session.lock:
            session.last_test_run = result
            self._event(
                session,
                event_type="test_run",
                content=(
                    "Test command passed."
                    if result.get("passed")
                    else "Test command failed."
                ),
                metadata=result,
            )
            self.db.save_session(session)

    def _step_by_index(self, idx: int) -> Optional[WorkflowStep]:
        if 0 <= idx < len(WORKFLOW_STEPS):
            return WORKFLOW_STEPS[idx]
        return None

    def _step_by_role(self, role: Optional[str]) -> WorkflowStep:
        normalized = normalize_role_label(role) or WORKFLOW_STEPS[0].role
        return WORKFLOW_STEP_BY_ROLE.get(normalized, WORKFLOW_STEPS[0])

    def _step_mode(self, session: StudySession, step: Optional[WorkflowStep]) -> str:
        if step is None:
            return "agent"
        return "manual" if step.role == session.manual_step_role else "agent"

    def _workflow_visualization(self, session: StudySession) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for idx, step in enumerate(WORKFLOW_STEPS):
            status = self._step_status(session, idx)
            items.append(
                self._serialize_step(
                    step,
                    current=status == "current",
                    status=status,
                    mode=self._step_mode(session, step),
                )
            )
        return items

    def _workflow_process(self, session: StudySession) -> Dict[str, Any]:
        return {
            "current_activity": self._current_activity(session),
            "agents": [
                self._agent_workflow_summary(session, idx, step)
                for idx, step in enumerate(WORKFLOW_STEPS)
            ],
        }

    def _agent_workflow_summary(
        self,
        session: StudySession,
        idx: int,
        step: WorkflowStep,
    ) -> Dict[str, Any]:
        status = self._step_status(session, idx)
        runs = [
            self._serialize_agent_run(session, step, message)
            for message in session.messages
            if message.get("role") == step.role
        ]

        return {
            **self._serialize_step(
                step,
                current=status == "current",
                status=status,
                mode=self._step_mode(session, step),
            ),
            "run_count": len(runs),
            "latest_output_summary": runs[-1]["output_summary"] if runs else "",
            "latest_handoff_summary": runs[-1]["handoff"]["summary"] if runs and runs[-1]["handoff"] else "",
            "files_in_scope": self._step_files_in_scope(session, step),
            "runs": runs,
            "current_activity": self._current_activity(session) if status == "current" else None,
        }

    def _serialize_agent_run(
        self,
        session: StudySession,
        step: WorkflowStep,
        message: Dict[str, Any],
    ) -> Dict[str, Any]:
        turn = int(message.get("turn", 0) or 0)
        incoming_handoff = self._incoming_handoff_for_turn(session, step.role, turn)
        outgoing_handoff = self._handoff_after_turn(session, turn)
        prompt = str(message.get("prompt") or "").strip()
        output = str(message.get("content") or "").strip()
        input_artifacts = self._step_input_artifacts(
            session,
            step,
            turn=turn,
            incoming_handoff=incoming_handoff,
        )

        return {
            "turn": turn,
            "actor": message.get("actor"),
            "started_at": message.get("started_at"),
            "completed_at": message.get("completed_at"),
            "duration_seconds": message.get("duration_seconds"),
            "output_summary": self._build_handoff_summary(output),
            "sequence": self._step_sequence(step, outgoing_handoff),
            "files_in_scope": self._step_files_in_scope(session, step),
            "prompt_artifact": self._text_artifact(
                title=f"{step.role} prompt",
                kind="prompt",
                content=prompt or "(prompt not recorded)",
                origin=step.role,
            ),
            "input_artifacts": input_artifacts,
            "output_artifacts": [
                self._text_artifact(
                    title=f"{step.role} output",
                    kind="output",
                    content=output or "(no output recorded)",
                    origin=step.role,
                )
            ],
            "handoff": (
                {
                    **outgoing_handoff,
                    "prompt_artifact": self._text_artifact(
                        title=f"Handoff to {outgoing_handoff['to_role']}",
                        kind="handoff",
                        content=outgoing_handoff.get("prompt", ""),
                        origin=step.role,
                    ),
                }
                if outgoing_handoff
                else None
            ),
        }

    def _current_activity(self, session: StudySession) -> Optional[Dict[str, Any]]:
        step = self._step_by_index(session.current_node_idx)
        if step is None or session.status == "completed":
            return None

        prompt = (
            self._build_human_step_prompt(session, step)
            if self._step_mode(session, step) == "manual"
            else self._build_step_prompt(session, step)
        )
        incoming_handoff = session.last_handoff if (session.last_handoff or {}).get("to_role") == step.role else None

        return {
            "role": step.role,
            "mode": self._step_mode(session, step),
            "status": session.status,
            "goal": step.ai_instruction,
            "sequence": self._step_sequence(step, session.last_handoff if (session.last_handoff or {}).get("from_role") == step.role else None),
            "files_in_scope": self._step_files_in_scope(session, step),
            "prompt_artifact": self._text_artifact(
                title=f"Current {step.role} prompt",
                kind="prompt",
                content=prompt,
                origin=step.role,
            ),
            "input_artifacts": self._step_input_artifacts(
                session,
                step,
                turn=None,
                incoming_handoff=incoming_handoff,
            ),
            "output_artifacts": [],
            "handoff": (
                {
                    **session.last_handoff,
                    "prompt_artifact": self._text_artifact(
                        title=f"Handoff to {session.last_handoff['to_role']}",
                        kind="handoff",
                        content=session.last_handoff.get("prompt", ""),
                        origin=session.last_handoff.get("from_role"),
                    ),
                }
                if session.last_handoff and session.last_handoff.get("from_role") == step.role
                else None
            ),
        }

    def _step_sequence(
        self,
        step: WorkflowStep,
        handoff: Optional[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        sequence = [
            {"label": "Goal", "detail": step.ai_instruction},
            {"label": "Processing approach", "detail": step.teaching.how_to_do_well},
            {"label": "Produce structured output", "detail": step.teaching.human_prompt_template},
        ]
        if handoff:
            sequence.append(
                {
                    "label": "Pass work forward",
                    "detail": f"Prepare the next handoff for {handoff.get('to_role', 'the next step')}.",
                }
            )
        else:
            sequence.append(
                {
                    "label": "Finish this stage",
                    "detail": self._next_step_expectation(step),
                }
            )
        return sequence

    def _step_files_in_scope(self, session: StudySession, step: WorkflowStep) -> List[Dict[str, Any]]:
        files: List[Dict[str, Any]] = []
        seen = set()

        def add_file(path: str, reason: str, category: str) -> None:
            cleaned = (path or "").strip()
            if not cleaned or cleaned in seen:
                return
            seen.add(cleaned)
            files.append(
                {
                    "path": cleaned,
                    "label": cleaned,
                    "reason": reason,
                    "category": category,
                }
            )

        for path in self._reported_changed_files(session):
            add_file(path, "File named in the current proposed change.", "proposal")

        for path in session.last_patch_application.get("changed_files", []) if session.last_patch_application else []:
            add_file(path, "File changed by the latest applied patch.", "applied")

        for path in session.task.get("changed_files", [])[:8]:
            add_file(path, "Code file likely related to this issue.", "code")

        if step.role == "Task Planner":
            return files[:6]
        if step.role in {"Patch Author", "Code Reviewer", "Test Runner"}:
            return files[:8]
        return files[:6]

    def _step_input_artifacts(
        self,
        session: StudySession,
        step: WorkflowStep,
        *,
        turn: Optional[int],
        incoming_handoff: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = [
            self._text_artifact(
                title="Issue statement",
                kind="issue",
                content=session.task.get("problem_statement", ""),
                origin="Task Context",
            )
        ]

        hints = str(session.task.get("hints_text") or "").strip()
        if hints:
            artifacts.append(
                self._text_artifact(
                    title="Hints",
                    kind="hint",
                    content=hints,
                    origin="Task Context",
                )
            )

        if incoming_handoff:
            artifacts.append(
                self._text_artifact(
                    title=f"Handoff from {incoming_handoff.get('from_role', 'previous step')}",
                    kind="handoff",
                    content=incoming_handoff.get("prompt", "") or incoming_handoff.get("summary", ""),
                    origin=incoming_handoff.get("from_role"),
                )
            )

        if step.role == "Code Reviewer":
            artifacts.extend(self._patch_proposal_artifacts(session))

        if step.role == "Test Runner":
            artifacts.extend(self._planner_check_artifacts(session))
            artifacts.extend(self._patch_proposal_artifacts(session))
            artifacts.extend(self._review_context_artifacts(session))
            validation_focus = self._validation_focus_artifact(session)
            if validation_focus:
                artifacts.append(validation_focus)

        if step.role == "Test Runner" and session.last_test_run:
            artifacts.append(
                self._text_artifact(
                    title="Latest test execution",
                    kind="test_run",
                    content=self._format_test_run(session.last_test_run),
                    origin="Test Runner",
                )
            )

        return artifacts

    def _format_test_run(self, payload: Dict[str, Any]) -> str:
        lines = [
            f"Command: {payload.get('command', '')}",
            f"Passed: {payload.get('passed')}",
            f"Exit code: {self._test_run_exit_code(payload)}",
        ]
        validation_state = str(payload.get("validation_state") or "").strip()
        if validation_state:
            lines.append(f"Validation state: {validation_state}")
        apply_result = payload.get("apply_result") or {}
        diff_stat = str(apply_result.get("diff_stat") or "").strip()
        if diff_stat:
            lines.append(f"Applied patch:\n{diff_stat}")
        stdout = str(payload.get("stdout") or "").strip()
        stderr = str(payload.get("stderr") or "").strip()
        if stdout:
            lines.append(f"Stdout:\n{self._clip(stdout, 1600)}")
        if stderr:
            lines.append(f"Stderr:\n{self._clip(stderr, 1600)}")
        return "\n".join(lines)

    def _incoming_handoff_for_turn(
        self,
        session: StudySession,
        role: str,
        turn: int,
    ) -> Optional[Dict[str, Any]]:
        matches = [
            event.metadata
            for event in session.events
            if event.event_type == "handoff_generated"
            and event.metadata.get("to_role") == role
            and int(event.metadata.get("turn", 0) or 0) < turn
        ]
        return matches[-1] if matches else None

    def _handoff_after_turn(self, session: StudySession, turn: int) -> Optional[Dict[str, Any]]:
        matches = [
            event.metadata
            for event in session.events
            if event.event_type == "handoff_generated"
            and int(event.metadata.get("turn", 0) or 0) == turn
        ]
        return matches[-1] if matches else None

    def _text_artifact(
        self,
        *,
        title: str,
        kind: str,
        content: str,
        origin: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "title": title,
            "kind": kind,
            "content": self._coerce_text(content),
            "origin": origin,
        }

    def _latest_message_for_role(
        self,
        session: StudySession,
        role: str,
    ) -> Optional[Dict[str, Any]]:
        matches = [message for message in session.messages if message.get("role") == role]
        return matches[-1] if matches else None

    def _latest_role_output(self, session: StudySession, role: str) -> str:
        message = self._latest_message_for_role(session, role)
        return str(message.get("content") or "").strip() if message else ""

    def _extract_structured_field(self, text: str, field_name: str) -> str:
        if not text:
            return ""
        match = re.search(
            rf"(?ims)^\s*{re.escape(field_name)}\s*:\s*(.*?)(?=^\s*[A-Z][A-Z0-9_ ]*\s*:\s*|\Z)",
            text,
        )
        return match.group(1).strip() if match else ""

    def _split_list_field(self, text: str) -> List[str]:
        values: List[str] = []
        seen = set()
        for chunk in (text or "").replace("\n", ",").split(","):
            cleaned = chunk.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            values.append(cleaned)
        return values

    def _reported_changed_files(self, session: StudySession) -> List[str]:
        files: List[str] = []
        seen = set()
        for role in ("Patch Author", "Code Reviewer"):
            output = self._latest_role_output(session, role)
            for path in self._split_list_field(self._extract_structured_field(output, "FILES_CHANGED")):
                if path in seen:
                    continue
                seen.add(path)
                files.append(path)
        return files

    def _patch_proposal_artifacts(self, session: StudySession) -> List[Dict[str, Any]]:
        proposal = self._latest_role_output(session, "Patch Author")
        if not proposal:
            return []

        patch_diff = self._current_patch_diff(session)
        patch_origin = "Practice Task" if self._use_reference_patch_for_session(session) else "Patch Author"
        changed_files_values = self._current_changed_files(session)
        proposal_content = self._proposal_context_text(session, proposal)

        artifacts = [
            self._text_artifact(
                title="Latest Patch Author proposal",
                kind="proposal",
                content=proposal_content,
                origin="Patch Author",
            )
        ]

        if patch_diff:
            artifacts.append(
                self._text_artifact(
                    title="Proposed code change",
                    kind="patch",
                    content=patch_diff,
                    origin=patch_origin,
                )
            )

        changed_files = ", ".join(changed_files_values)
        if changed_files:
            artifacts.append(
                self._text_artifact(
                    title="Files proposed to change",
                    kind="files_changed",
                    content=changed_files,
                    origin=patch_origin,
                )
            )

        done_criteria = self._extract_structured_field(proposal, "DONE_CRITERIA")
        if done_criteria:
            artifacts.append(
                self._text_artifact(
                    title="Expected behavior to validate",
                    kind="validation_focus",
                    content=done_criteria,
                    origin="Patch Author",
                )
            )
        return artifacts

    def _planner_check_artifacts(self, session: StudySession) -> List[Dict[str, Any]]:
        planner_output = self._latest_role_output(session, "Task Planner")
        acceptance_checks = self._extract_structured_field(planner_output, "ACCEPTANCE_CHECKS")
        if not acceptance_checks:
            return []
        return [
            self._text_artifact(
                title="Planning checks",
                kind="plan_checks",
                content=acceptance_checks,
                origin="Task Planner",
            )
        ]

    def _review_context_artifacts(self, session: StudySession) -> List[Dict[str, Any]]:
        review = self._latest_role_output(session, "Code Reviewer")
        if not review:
            return []

        artifacts = [
            self._text_artifact(
                title="Latest Code Reviewer decision",
                kind="review",
                content=review,
                origin="Code Reviewer",
            )
        ]
        review_notes = self._extract_structured_field(review, "REVIEW_NOTES")
        if review_notes:
            artifacts.append(
                self._text_artifact(
                    title="Review focus",
                    kind="review_notes",
                    content=review_notes,
                    origin="Code Reviewer",
                )
            )
        return artifacts

    def _validation_focus_artifact(self, session: StudySession) -> Optional[Dict[str, Any]]:
        planner_checks = self._extract_structured_field(self._latest_role_output(session, "Task Planner"), "ACCEPTANCE_CHECKS")
        done_criteria = self._extract_structured_field(self._latest_role_output(session, "Patch Author"), "DONE_CRITERIA")
        review_notes = self._extract_structured_field(self._latest_role_output(session, "Code Reviewer"), "REVIEW_NOTES")

        lines = []
        if planner_checks:
            lines.append(f"Planner checks: {planner_checks}")
        if done_criteria:
            lines.append(f"Patch Author expectations: {done_criteria}")
        if review_notes:
            lines.append(f"Reviewer concerns or evidence: {review_notes}")
        if not lines:
            return None
        return self._text_artifact(
            title="What to validate",
            kind="validation_focus",
            content="\n".join(lines),
            origin="Workflow",
        )

    def _step_status(self, session: StudySession, idx: int) -> str:
        if session.status == "completed":
            return "completed"
        if session.current_node_idx < 0:
            return "completed"
        if idx < session.current_node_idx:
            return "completed"
        if idx == session.current_node_idx:
            return "current"
        return "pending"

    def _system_status(self, session: StudySession) -> Dict[str, Any]:
        visualization = self._workflow_visualization(session)
        completed_steps = len([step for step in visualization if step["status"] == "completed"])
        pending_steps = len([step for step in visualization if step["status"] == "pending"])

        if session.status == "waiting_for_human":
            label = f"Waiting for your input on {session.waiting_role or session.manual_step_role}"
            detail = "Write your response when you're ready, then click Send."
        elif session.status == "running":
            current = session.current_role or "workflow"
            label = f"Running {current}"
            from_role = (session.last_handoff or {}).get("from_role")
            if current == "Patch Author" and from_role == "Code Reviewer":
                detail = "A revision was requested, so the Patch Author is up next."
            elif current == "Patch Author" and from_role == "Test Runner":
                detail = "Tests did not pass, so the workflow is returning to Patch Author for another revision."
            else:
                detail = "The system is processing the current workflow step."
        elif session.status == "completed":
            label = "Workflow completed"
            detail = "All workflow steps are done."
        else:
            label = "Ready to start"
            detail = "Click Run / Continue to begin."

        return {
            "label": label,
            "detail": detail,
            "completed_steps": completed_steps,
            "pending_steps": pending_steps,
            "iteration_count": session.iteration_count,
            "current_role": session.current_role,
            "manual_step_role": session.manual_step_role,
            "status": session.status,
        }

    def _step_briefing(self, session: StudySession) -> Dict[str, Any]:
        step = self._step_by_index(session.current_node_idx)
        if step is None:
            return {
                "title": "Workflow complete",
                "current_role": None,
                "current_mode": None,
                "what_happens_here": "All workflow steps are finished.",
                "why_this_step_exists": "",
                "what_you_should_do": "Review the transcript and handoffs to see how the workflow reached its result.",
                "success_signals": [],
                "response_format": "",
                "what_happens_next": "There is no next step because the workflow is complete.",
                "prompt_box_note": (
                    "The input box is only for your own responses or AI Coach questions. "
                    "Use the workflow board and the role detail view to review generated handoffs."
                ),
            }

        mode = self._step_mode(session, step)
        waiting_for_you = (
            session.status == "waiting_for_human"
            and (session.waiting_role or step.role) == step.role
            and mode == "manual"
        )

        if waiting_for_you:
            what_you_should_do = (
                "It is your turn now. Write your response in the box on the left, follow the response format "
                "shown below, then click Send. Use AI Coach only if you want help drafting your answer."
            )
        elif mode == "manual":
            what_you_should_do = (
                "This is your assigned step. The system will pause here and wait for you when the workflow reaches it."
            )
        else:
            what_you_should_do = (
                "The system is handling this step. Watch the live workflow and the role detail view so you can see what "
                "information gets passed forward."
            )

        return {
            "title": f"How {step.role} works",
            "current_role": step.role,
            "current_mode": mode,
            "what_happens_here": step.ai_instruction,
            "why_this_step_exists": step.teaching.why_needed,
            "what_you_should_do": what_you_should_do,
            "success_signals": step.teaching.success_signals,
            "response_format": step.teaching.human_prompt_template,
            "what_happens_next": self._next_step_expectation(step),
            "prompt_box_note": (
                "Use the workflow board and the role detail view to review generated prompts, handoffs, and evidence. "
                "The input box is reserved for your own response or for asking AI Coach a question."
            ),
        }

    def _serialize_step(
        self,
        step: WorkflowStep,
        *,
        current: bool,
        status: str,
        mode: str,
    ) -> Dict[str, Any]:
        return {
            "role": step.role,
            "slug": step.slug,
            "status": status,
            "mode": mode,
            "is_current": current,
            "model_id": self.llm.resolve_model_id(step.role),
            "why_needed": step.teaching.why_needed,
            "how_to_do_well": step.teaching.how_to_do_well,
            "core_concepts": step.teaching.core_concepts,
            "human_prompt_template": step.teaching.human_prompt_template,
            "success_signals": step.teaching.success_signals,
            "ai_instruction": step.ai_instruction,
            "routes": [
                {
                    "pattern": rule.pattern,
                    "next_idx": rule.next_idx,
                    "description": rule.description,
                }
                for rule in step.routes
            ],
        }

    def _next_step_expectation(self, step: WorkflowStep) -> str:
        if step.routes:
            return " ".join(rule.description for rule in step.routes)
        next_step = self._step_by_index(step.default_next)
        if next_step is None:
            return "This step can finish the workflow."
        return f"After this step, the workflow moves to {next_step.role}."

    def _route(self, step: WorkflowStep, output: str) -> int:
        for route in step.routes:
            if re.search(route.pattern, output):
                return route.next_idx
        return step.default_next

    def _route_with_context(
        self,
        session: StudySession,
        step: WorkflowStep,
        output: str,
        *,
        actor: str,
    ) -> int:
        next_idx = self._route(step, output)
        if step.role == "Code Reviewer" and next_idx == 1 and actor == "ai":
            # Override REVISE to APPROVE unless the patch is empty or explicitly blocked.
            # This prevents the AI Code Reviewer from looping the workflow on vague concerns.
            if not self._ai_review_has_concrete_blocker(session, output):
                return step.default_next
        if step.role == "Test Runner" and next_idx == 1 and actor == "ai":
            if not self._ai_test_failure_has_real_evidence(session, output):
                return step.default_next
        return next_idx

    def _ai_turn(self, session: StudySession, step: WorkflowStep) -> str:
        prompt = self._build_step_prompt(session, step)
        started_at = utc_now_iso()
        t0 = time.perf_counter()
        output = self.llm.chat_once(prompt, temperature=0.1, max_tokens=1200, role=step.role)
        output = self._coerce_text(output)
        duration = round(time.perf_counter() - t0, 4)
        completed_at = utc_now_iso()

        turn_num = len(session.messages) + 1
        session.messages.append(
            {
                "turn": turn_num,
                "role": step.role,
                "actor": "ai",
                "content": output,
                "prompt": prompt,
                "started_at": started_at,
                "completed_at": completed_at,
                "duration_seconds": duration,
                "response_latency_seconds": None,
            }
        )
        self._event(
            session,
            event_type="ai_turn",
            role=step.role,
            content=output,
            metadata={
                "turn": turn_num,
                "node_idx": session.current_node_idx,
                "duration_seconds": duration,
                "model_id": self.llm.resolve_model_id(step.role),
            },
        )
        return output

    def _human_turn(self, session: StudySession, step: WorkflowStep, human_input: str) -> None:
        completed_at = utc_now_iso()
        started_at = session.wait_started_at or completed_at
        latency = self._seconds_between(started_at, completed_at)
        turn_num = len(session.messages) + 1
        prompt = self._build_human_step_prompt(session, step)

        session.messages.append(
            {
                "turn": turn_num,
                "role": step.role,
                "actor": "human",
                "content": human_input,
                "prompt": prompt,
                "started_at": started_at,
                "completed_at": completed_at,
                "duration_seconds": latency,
                "response_latency_seconds": latency,
            }
        )
        self._event(
            session,
            event_type="human_turn",
            role=step.role,
            content=human_input,
            metadata={
                "turn": turn_num,
                "node_idx": session.current_node_idx,
                "response_latency_seconds": latency,
            },
        )

    def _build_step_prompt(self, session: StudySession, step: WorkflowStep) -> str:
        task = session.task
        recent = self._recent_transcript(session.messages)
        handoff = session.last_handoff or {}
        handoff_block = ""
        evidence_block = self._role_evidence_block(session, step)
        decision_guidance = self._automation_decision_guidance(step)
        if handoff.get("to_role") == step.role:
            handoff_block = (
                "Structured handoff from the previous step:\n"
                f"{handoff.get('prompt', '')}\n\n"
            )

        return (
            f"You are acting as {step.role} in an educational multi-agent code-repair workflow.\n"
            f"Your responsibility: {step.ai_instruction}\n"
            f"Why this step matters: {step.teaching.why_needed}\n"
            f"Quality bar: {step.teaching.how_to_do_well}\n"
            f"Success signals: {'; '.join(step.teaching.success_signals)}\n\n"
            f"Practice task: {task['instance_id']}\n"
            f"Repository: {task['repo']}\n"
            f"Base commit: {task['base_commit']}\n"
            f"Issue URL: {task['issue_url']}\n"
            f"Relevant files for this task: {', '.join(task.get('changed_files', [])[:8])}\n\n"
            f"Issue statement:\n{task['problem_statement']}\n\n"
            f"Hints:\n{task.get('hints_text', '')}\n\n"
            f"Recent workflow transcript:\n{recent}\n\n"
            f"{handoff_block}"
            f"{evidence_block}"
            f"{decision_guidance}"
            "Respond in the exact role-specific format below.\n"
            f"{step.teaching.human_prompt_template}"
        )

    def _build_human_step_prompt(self, session: StudySession, step: WorkflowStep) -> str:
        handoff = session.last_handoff or {}
        handoff_block = ""
        if handoff.get("to_role") == step.role:
            handoff_block = (
                "Incoming handoff from the previous workflow step:\n"
                f"{handoff.get('prompt', '')}\n\n"
            )

        evidence_block = self._role_evidence_block(session, step)
        return (
            f"You are completing the {step.role} step in an educational software-engineering workflow.\n"
            f"Why this step matters: {step.teaching.why_needed}\n"
            f"How to do it well: {step.teaching.how_to_do_well}\n"
            f"Success signals: {'; '.join(step.teaching.success_signals)}\n\n"
            f"Task focus: {step.ai_instruction}\n\n"
            f"Issue statement:\n{session.task['problem_statement']}\n\n"
            f"Hints:\n{session.task.get('hints_text', '')}\n\n"
            f"{handoff_block}"
            f"{evidence_block}"
            "Use the exact response format below.\n"
            f"{step.teaching.human_prompt_template}"
        )

    def _store_handoff(
        self,
        session: StudySession,
        *,
        current_step: WorkflowStep,
        output: str,
        next_idx: int,
        actor: str,
    ) -> None:
        next_step = self._step_by_index(next_idx)
        if next_step is None:
            session.last_handoff = None
            return

        handoff_output = self._handoff_output_text(session, current_step, next_step, output)
        prompt = (
            f"Upstream role: {current_step.role} ({actor})\n"
            f"Next role: {next_step.role} ({self._step_mode(session, next_step)})\n"
            f"Task: {session.task['instance_id']} in {session.task['repo']}\n"
            f"Carry forward the most recent output below, then do the next step well.\n\n"
            f"Latest output from {current_step.role}:\n{handoff_output}\n\n"
            f"What the next role should focus on:\n{next_step.ai_instruction}\n\n"
            f"{self._role_evidence_block(session, next_step)}"
            f"Required response format:\n{next_step.teaching.human_prompt_template}"
        )
        if next_step.role == "Test Runner":
            session.last_test_run = None
        handoff = {
            "generated_at": utc_now_iso(),
            "turn": len(session.messages),
            "from_role": current_step.role,
            "from_actor": actor,
            "to_role": next_step.role,
            "to_mode": self._step_mode(session, next_step),
            "summary": self._build_handoff_summary(handoff_output),
            "prompt": prompt,
            "why_this_handoff_matters": next_step.teaching.why_needed,
        }
        session.last_handoff = handoff
        self._event(
            session,
            event_type="handoff_generated",
            role=current_step.role,
            content=f"Handoff prepared for {next_step.role}.",
            metadata=handoff,
        )

    def _build_handoff_summary(self, output: str) -> str:
        output = self._coerce_text(output)
        signal_lines = []
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            signal_lines.append(stripped)
            if len(signal_lines) >= 4:
                break
        if signal_lines:
            return "\n".join(signal_lines)
        return self._clip(output, 220)

    def _recent_transcript(self, messages: List[Dict[str, Any]], max_items: int = 10) -> str:
        if not messages:
            return "(no prior messages)"
        lines = []
        for msg in messages[-max_items:]:
            prefix = f"T{msg.get('turn', '?')} {msg['role']} ({msg['actor']})"
            lines.append(f"{prefix}: {self._clip(str(msg.get('content', '')), 400)}")
        return "\n".join(lines)

    def _prior_support_for_turn(self, session: StudySession, turn_num: int) -> List[str]:
        texts: List[str] = []
        for event in session.events:
            if event.event_type != "human_support":
                continue
            if int(event.metadata.get("turn", 0) or 0) != turn_num:
                continue
            content = str(event.content or "").strip()
            if content:
                texts.append(content)
        return texts

    def _support_style_instructions(self, step: WorkflowStep) -> str:
        if step.role == "Code Reviewer":
            return (
                "Judge from the latest proposed fix, current handoff, and current issue context, not from any hidden reference solution. "
                "Point to concrete code changes, likely risks, and approval/revision signals. "
                "If the issue description asks for more than the concrete patch/tests support, call out that "
                "scope mismatch explicitly instead of repeatedly requesting the same unsupported revision. "
                "By default, give a ready-to-submit answer in the required format, but make sure the decision and "
                "review notes are grounded in the real patch rather than generic issue-level commentary."
            )
        if step.role == "Test Runner":
            return (
                "Judge from the available evidence in the patch, current review notes, and any recorded test output. "
                "Be explicit about what is proven versus what is still missing. "
                "Distinguish a real code/test failure from a local tooling or environment failure such as a missing "
                "test executable. If the recorded run failed because the environment could not launch the command, "
                "do not automatically choose FAIL just because execution was blocked; weigh the current patch plan, review "
                "notes, and actual runtime evidence, and explain that local validation was blocked by setup rather than "
                "contradicted by evidence. By default, give a ready-to-submit answer in the required format, grounded in "
                "the available evidence."
            )
        return (
            "Ground the advice in the current task evidence and, by default, provide a ready-to-submit answer in "
            "the required format."
        )

    def _automation_decision_guidance(self, step: WorkflowStep) -> str:
        if step.role == "Code Reviewer":
            return (
                "Decision discipline:\n"
                "Approve when the current proposal is coherent, scoped to the issue, and there is no concrete reason to send it back. "
                "Only choose REVISE when the current proposal itself shows a real correctness gap, regression risk, or missing code change that blocks testing.\n\n"
            )
        if step.role == "Test Runner":
            return (
                "Decision discipline:\n"
                "Only choose FAIL when current evidence points to a real problem in the proposed change. "
                "Do not choose FAIL just because local validation is incomplete, no test command has run yet, or the environment blocked execution. "
                "When evidence is coherent but incomplete, explain the missing validation in TEST_NOTES and still keep the workflow moving forward instead of forcing a revision loop.\n\n"
            )
        return ""

    def _complete_workflow(self, session: StudySession, message: str) -> None:
        session.status = "completed"
        session.current_node_idx = -1
        session.waiting_for_human = False
        session.waiting_role = None
        session.wait_started_at = None
        session.last_handoff = None
        self._event(
            session,
            event_type="workflow_complete",
            content=message,
        )

    def _finish_if_needed(self, session: StudySession) -> None:
        if session.current_node_idx < 0:
            self._complete_workflow(session, "Workflow complete.")
        else:
            session.status = "running"

    def _event(
        self,
        session: StudySession,
        *,
        event_type: str,
        content: str,
        role: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = WorkflowEvent.create(
            session_id=session.session_id,
            event_type=event_type,
            content=content,
            role=role,
            metadata=metadata,
        )
        session.events.append(event)
        self.db.append_event(event)

    def _reroute_message(self, current_role: str, next_idx: int) -> str:
        next_step = self._step_by_index(next_idx)
        next_role = next_step.role if next_step is not None else "the next step"
        if current_role == "Code Reviewer" and next_role == "Patch Author":
            return "The review requested changes, so the workflow is returning to Patch Author."
        if current_role == "Test Runner" and next_role == "Patch Author":
            return "The test decision sent the workflow back to Patch Author for another revision."
        return f"The workflow is moving from {current_role} to {next_role}."

    def _patch_line_count(self, patch: str) -> int:
        count = 0
        for line in (patch or "").splitlines():
            if line.startswith(("diff --git", "index ", "---", "+++", "@@")):
                continue
            if line.startswith("+") or line.startswith("-"):
                count += 1
        return count

    def _seconds_between(self, iso_start: str, iso_end: str) -> Optional[float]:
        start_dt = self._parse_iso(iso_start)
        end_dt = self._parse_iso(iso_end)
        if start_dt is None or end_dt is None:
            return None
        return round((end_dt - start_dt).total_seconds(), 4)

    def _parse_iso(self, value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None

    def _clip(self, text: str, limit: int) -> str:
        text = self._coerce_text(text)
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def _coerce_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            except TypeError:
                return str(value)
        return str(value)

    def _ai_review_has_concrete_blocker(self, session: StudySession, output: str) -> bool:
        """Returns True only when the Code Reviewer's REVISE decision has a concrete justification.

        The AI Code Reviewer should only send work back to the Patch Author when the
        proposed change is empty, explicitly blocked, or has a clear structural problem.
        Vague concerns (style, hypothetical risks, missing tests) are not concrete blockers.
        """
        proposal = self._latest_role_output(session, "Patch Author")
        # No proposal at all — revision is justified.
        if not proposal:
            return True
        patch_status = self._extract_structured_field(proposal, "PATCH_STATUS").upper().strip()
        patch_diff = self._extract_structured_field(proposal, "PATCH_DIFF").strip()
        implementation_plan = self._extract_structured_field(proposal, "IMPLEMENTATION_PLAN").strip()
        changed_files = self._split_list_field(self._extract_structured_field(proposal, "FILES_CHANGED"))
        if patch_status == "BLOCKED":
            return True
        if patch_diff:
            return False
        return not (patch_status == "READY" and implementation_plan and changed_files)

    def _prepare_step_evidence(self, session: StudySession, step: WorkflowStep) -> None:
        if step.role == "Test Runner":
            self._prepare_test_runner_evidence(session)

    def _prepare_test_runner_evidence(self, session: StudySession) -> None:
        if session.last_test_run:
            return

        command = next(
            (str(item).strip() for item in session.task.get("suggested_test_commands", []) if str(item).strip()),
            "",
        )
        if not command:
            return

        patch_diff = self._current_patch_diff(session)
        if not patch_diff:
            return
        result = self._run_validation_for_patch(session, command, patch_diff)
        session.last_test_run = result
        self._event(
            session,
            event_type="test_run",
            role="Test Runner",
            content=(
                "Automatic validation evidence prepared."
                if result.get("auto_prepared")
                else "Validation evidence prepared."
            ),
            metadata=result,
        )

    def _run_validation_for_patch(
        self,
        session: StudySession,
        command: str,
        patch_diff: str,
    ) -> Dict[str, Any]:
        normalized_patch = patch_diff if patch_diff.endswith("\n") else f"{patch_diff}\n"
        source_repo = Path(session.repo_path).resolve()
        try:
            with tempfile.TemporaryDirectory(prefix="workflow-validation-") as temp_dir:
                candidate_repo = Path(temp_dir) / source_repo.name
                shutil.copytree(source_repo, candidate_repo)
                apply_result = apply_patch_to_repo(candidate_repo, normalized_patch)
                if not apply_result.get("applied"):
                    return {
                        "command": command,
                        "argv": [],
                        "exit_code": None,
                        "passed": False,
                        "stdout": str(apply_result.get("stdout") or ""),
                        "stderr": (
                            "Automatic test execution could not apply the proposed PATCH_DIFF before "
                            f"validation.\n{str(apply_result.get('stderr') or '').strip()}".strip()
                        ),
                        "timed_out": False,
                        "auto_prepared": True,
                        "validation_state": "patch_apply_failed",
                        "apply_result": apply_result,
                    }

                runner = self.test_runner or run_test_command
                run_result = runner(candidate_repo, command, timeout_seconds=120)
                return {
                    **run_result,
                    "auto_prepared": True,
                    "validation_state": "executed",
                    "apply_result": apply_result,
                }
        except Exception as exc:
            return {
                "command": command,
                "argv": [],
                "exit_code": None,
                "passed": False,
                "stdout": "",
                "stderr": f"Automatic test execution failed: {exc}",
                "timed_out": False,
                "auto_prepared": True,
                "validation_state": "execution_error",
            }

    def _ai_test_failure_has_real_evidence(self, session: StudySession, output: str) -> bool:
        last_run = session.last_test_run or {}
        if last_run:
            validation_state = str(last_run.get("validation_state") or "executed")
            if validation_state in {"execution_error", "patch_apply_failed"}:
                return False
            if self._test_environment_note(last_run):
                return False
            if last_run.get("passed") is False:
                return True
            if last_run.get("passed") is True:
                return False

        notes = self._extract_structured_field(output, "TEST_NOTES") or output
        lowered = notes.lower()

        insufficient_evidence_signals = [
            "no test command has been run",
            "missing evidence",
            "not enough evidence",
            "need more evidence",
            "would run",
            "should run",
            "recommend",
            "environment",
            "tooling",
            "setup",
            "blocked",
            "cannot confirm",
            "can't confirm",
            "unclear",
        ]
        # Use only specific phrases that unambiguously indicate the CURRENT patch is failing.
        # Generic words like "error", "wrong", "broken" are intentionally excluded because the
        # AI frequently uses them to describe the ORIGINAL bug (e.g. "fixes the error"), which
        # would cause false positives and send the workflow back to Patch Author incorrectly.
        concrete_failure_signals = [
            "still fails",
            "still broken",
            "still incorrect",
            "regression",
            "does not fix",
            "does not address",
            "fails because",
            "contradicts",
            "contradict",
            "patch is wrong",
            "patch is broken",
            "patch is incorrect",
        ]

        # Check insufficient evidence FIRST: if the notes acknowledge missing validation,
        # do not treat the output as concrete proof of failure.
        if any(signal in lowered for signal in insufficient_evidence_signals):
            return False
        if any(signal in lowered for signal in concrete_failure_signals):
            return True
        return False

    def _current_patch_diff(self, session: StudySession) -> str:
        proposal_patch = self._extract_structured_field(
            self._latest_role_output(session, "Patch Author"),
            "PATCH_DIFF",
        ).strip()
        if self._use_reference_patch_for_session(session):
            task_patch = str(session.task.get("patch") or "").strip()
            if task_patch:
                return task_patch
        if proposal_patch:
            return proposal_patch
        return ""

    def _current_changed_files(self, session: StudySession) -> List[str]:
        if self._use_reference_patch_for_session(session):
            return [str(path).strip() for path in session.task.get("changed_files", []) if str(path).strip()]
        proposal = self._latest_role_output(session, "Patch Author")
        proposal_patch = self._extract_structured_field(proposal, "PATCH_DIFF").strip()
        proposal_files = self._split_list_field(self._extract_structured_field(proposal, "FILES_CHANGED"))
        if proposal_patch and proposal_files:
            return proposal_files
        return proposal_files

    def _use_reference_patch_for_session(self, session: StudySession) -> bool:
        return bool(session.task.get("benchmark_suite")) and session.manual_step_role in {
            "Task Planner",
            "Test Runner",
        }

    def _role_evidence_block(self, session: StudySession, step: WorkflowStep) -> str:
        sections: List[str] = []
        validation = self._validation_evidence_block(session)

        if step.role == "Test Runner" and validation:
            sections.append(validation)
        elif step.role == "Code Reviewer":
            sections.append(self._review_evidence_block(session))
            sections.append(self._review_scope_evidence_block(session))
        elif step.role == "Patch Author":
            revision = self._patch_author_revision_evidence_block(session)
            if revision:
                sections.append(revision)

        if not sections:
            return ""
        return "\n\n".join(section for section in sections if section) + "\n\n"

    def _review_evidence_block(self, session: StudySession) -> str:
        sections: List[str] = []
        handoff = session.last_handoff or {}
        summary = handoff.get("summary") or ""
        if summary:
            sections.append(f"Review context summary:\n{self._clip(summary, 1000)}")

        proposal = self._latest_role_output(session, "Patch Author")
        if proposal:
            sections.append(f"Patch Author proposal:\n{self._proposal_context_text(session, proposal)}")

        changed_files = self._current_changed_files(session)
        if changed_files:
            sections.append(f"Changed files under review:\n{', '.join(changed_files)}")

        patch_diff = self._current_patch_diff(session)
        if patch_diff:
            sections.append(f"Proposed code change:\n{patch_diff}")

        return "\n\n".join(sections)

    def _review_scope_evidence_block(self, session: StudySession) -> str:
        sections: List[str] = []

        sections.append(
            "Review scope note:\n"
            "Judge the current proposed fix using the actual handoff, current issue statement, and available evidence. "
            "If the issue text asks for more than the available proposal supports, mention the mismatch but avoid "
            "inventing hidden requirements or repeating the same revision request forever."
        )
        return "\n\n".join(sections)

    def _patch_author_revision_evidence_block(self, session: StudySession) -> str:
        if session.manual_step_role != "Code Reviewer":
            return ""

        handoff = session.last_handoff or {}
        if handoff.get("to_role") != "Patch Author" or handoff.get("from_role") != "Code Reviewer":
            return ""

        sections: List[str] = [
            "Revision guidance:\n"
            "Revise the current patch in response to the review notes below. Preserve the parts of the "
            "existing patch that already look correct, and change only the lines needed to address the "
            "reviewer's concrete concerns."
        ]

        proposal = self._latest_role_output(session, "Patch Author")
        if proposal:
            sections.append(f"Current patch proposal to revise:\n{proposal}")

        patch_diff = self._current_patch_diff(session)
        if patch_diff:
            sections.append(f"Current patch diff to revise:\n{patch_diff}")

        review = self._latest_role_output(session, "Code Reviewer")
        if review:
            sections.append(f"Latest review feedback:\n{review}")

        return "\n\n".join(sections)

    def _validation_evidence_block(self, session: StudySession) -> str:
        sections: List[str] = []

        last_run = session.last_test_run or {}
        if last_run:
            run_lines = [
                f"Command: {last_run.get('command', '')}",
                f"Passed: {last_run.get('passed')}",
                f"Exit code: {self._test_run_exit_code(last_run)}",
            ]
            validation_state = str(last_run.get("validation_state") or "").strip()
            if validation_state:
                run_lines.append(f"Validation state: {validation_state}")
            stdout = (last_run.get("stdout") or "").strip()
            stderr = (last_run.get("stderr") or "").strip()
            env_note = self._test_environment_note(last_run)
            apply_result = last_run.get("apply_result") or {}
            diff_stat = str(apply_result.get("diff_stat") or "").strip()
            if diff_stat:
                run_lines.append(f"Applied patch:\n{diff_stat}")
            if env_note:
                run_lines.append(f"Environment note: {env_note}")
            if stdout:
                run_lines.append(f"Stdout:\n{self._clip(stdout, 1200)}")
            if stderr:
                run_lines.append(f"Stderr:\n{self._clip(stderr, 1200)}")
            sections.append("Executed test output:\n" + "\n".join(run_lines))
        else:
            sections.append(
                "Executed test output:\nNo test command has been run yet, so rely on the current proposal, review notes, and any directly available evidence."
            )

        handoff = session.last_handoff or {}
        if handoff.get("summary"):
            sections.append(f"Validation handoff summary:\n{self._clip(handoff['summary'], 1000)}")

        planner_checks = self._extract_structured_field(self._latest_role_output(session, "Task Planner"), "ACCEPTANCE_CHECKS")
        if planner_checks:
            sections.append(f"Planning checks:\n{self._clip(planner_checks, 1200)}")

        proposal = self._latest_role_output(session, "Patch Author")
        if proposal:
            sections.append(f"Patch Author proposal:\n{self._proposal_context_text(session, proposal)}")

        changed_files = self._current_changed_files(session)
        if changed_files:
            sections.append(f"Changed files under validation:\n{', '.join(changed_files)}")

        patch_diff = self._current_patch_diff(session)
        if patch_diff:
            sections.append(f"Proposed code change:\n{patch_diff}")

        review = self._latest_role_output(session, "Code Reviewer")
        if review:
            sections.append(f"Latest review decision:\n{self._clip(review, 1400)}")

        env_guidance = self._validation_environment_guidance(session)
        if env_guidance:
            sections.append(env_guidance)

        sections.append(
            "Validation decision note:\n"
            "If local execution was blocked by missing tooling or environment setup, treat that as missing local "
            "validation evidence, not as direct proof that the proposed fix is wrong. Only choose FAIL when the "
            "available evidence suggests the patch is incorrect or contradicted."
        )

        return "\n\n".join(sections)

    def _proposal_context_text(self, session: StudySession, proposal: str) -> str:
        proposal_text = self._coerce_text(proposal).strip()
        if not proposal_text:
            return ""
        if not self._use_reference_patch_for_session(session):
            return proposal_text

        lines = []
        lines.append("PATCH_STATUS: READY")
        changed_files = self._current_changed_files(session)
        if changed_files:
            lines.append(f"FILES_CHANGED: {', '.join(changed_files)}")
        task_focus = self._coerce_text(
            session.task.get("task_focus")
            or session.task.get("issue_summary")
            or session.task.get("problem_statement")
        ).strip()
        if task_focus:
            lines.append(f"IMPLEMENTATION_PLAN: {task_focus}")
        lines.append(
            "PATCH_DIFF: See the 'Proposed code change' section below for the canonical benchmark patch used in this practice task."
        )
        return "\n".join(lines)

    def _handoff_output_text(
        self,
        session: StudySession,
        current_step: WorkflowStep,
        next_step: WorkflowStep,
        output: str,
    ) -> str:
        if (
            current_step.role == "Patch Author"
            and next_step.role in {"Code Reviewer", "Test Runner"}
            and self._use_reference_patch_for_session(session)
        ):
            return self._proposal_context_text(session, output)
        return self._clip(output, 1000)

    def _test_environment_note(self, last_run: Dict[str, Any]) -> str:
        stderr = str(last_run.get("stderr") or "")
        command = str(last_run.get("command") or "")
        auto_prepared = bool(last_run.get("auto_prepared"))

        if "No such file or directory" in stderr:
            missing_target = command.split()[0] if command else "the requested tool"
            return (
                f"Local validation was blocked because the environment could not launch {missing_target}. "
                "This is a setup/tooling problem, not direct evidence against the patch."
            )
        if "No module named pytest" in stderr or "ModuleNotFoundError: No module named 'pytest'" in stderr:
            return (
                "Local validation was blocked because pytest is not installed in this environment. "
                "This is a setup/tooling problem, not direct evidence against the patch."
            )
        if auto_prepared and "could not apply the proposed PATCH_DIFF" in stderr:
            return (
                "Automatic local validation could not apply the reviewer-facing PATCH_DIFF verbatim, so the "
                "environment could not complete a real test run. Treat this as missing local validation evidence, "
                "not direct proof that the fix is wrong."
            )
        if auto_prepared and "Automatic test execution failed" in stderr:
            return (
                "The automatic local validation step failed due to environment/setup constraints rather than a "
                "confirmed product regression."
            )
        return ""

    def _validation_environment_guidance(self, session: StudySession) -> str:
        last_run = session.last_test_run or {}
        note = self._test_environment_note(last_run)
        if not note:
            return ""
        return (
            "Environment guidance:\n"
            f"{note}\n"
            "Base your PASS/FAIL decision on whether the current proposed fix, review notes, and runtime evidence are coherent, while clearly "
            "calling out that local execution could not be completed in this environment."
        )

    def _test_run_exit_code(self, payload: Dict[str, Any]) -> Any:
        if "exit_code" in payload:
            return payload.get("exit_code")
        return payload.get("returncode")
