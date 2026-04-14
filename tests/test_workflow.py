from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import StudyDatabase
from tasks import curated_bugsinpy_tqdm_task_by_instance
from workflow import StudyWorkflowEngine


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = outputs

    def chat_once(self, prompt, **kwargs):
        role = kwargs.get("role")
        values = self.outputs.get(role, [])
        if values:
            return values.pop(0)
        return "TEST_DECISION: PASS\nTEST_NOTES: default"

    def resolve_model_id(self, role=None):
        return f"fake-{(role or 'default').lower().replace(' ', '-')}"


def sample_task():
    return {
        "instance_id": "demo__repo-1",
        "repo": "demo/repo",
        "base_commit": "abc12345",
        "issue_url": "https://example.com/issue",
        "problem_statement": "Parser loses state after retry.",
        "hints_text": "Inspect parser state transitions.",
        "patch": (
            "diff --git a/parser.py b/parser.py\n"
            "--- a/parser.py\n"
            "+++ b/parser.py\n"
            "@@\n"
            "-state = None\n"
            "+state = previous_state\n"
        ),
        "test_patch": (
            "diff --git a/tests/test_parser.py b/tests/test_parser.py\n"
            "--- a/tests/test_parser.py\n"
            "+++ b/tests/test_parser.py\n"
            "@@\n"
            "+def test_retry_keeps_state():\n"
            "+    assert True\n"
        ),
        "changed_files": ["parser.py"],
        "patch_changed_lines": 14,
        "educational_fit": {"difficulty_band": "intro", "python_affinity": 1.0},
        "suggested_test_commands": ["pytest"],
    }


class WorkflowEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = StudyDatabase(Path(self.temp_dir.name) / "test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_reviewer_revise_routes_back_to_patch_author(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest"
                ],
                "Patch Author": [
                    "PATCH_STATUS: BLOCKED\nFILES_CHANGED: parser.py\nIMPLEMENTATION_PLAN: fix parser\nDONE_CRITERIA: tests pass"
                ],
                "Code Reviewer": [
                    "REVIEW_DECISION: REVISE\nPATCH_STATUS: READY\nFILES_CHANGED: parser.py\nREVIEW_NOTES: needs one more check"
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="tester",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)

        self.assertEqual(session.current_node_idx, 1)
        self.assertEqual(session.iteration_count, 1)
        self.assertEqual(session.last_handoff["to_role"], "Patch Author")

    def test_manual_input_generates_next_handoff(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: parser.py\nIMPLEMENTATION_PLAN: fix parser\n"
                    "PATCH_DIFF: diff --git a/parser.py b/parser.py\n--- a/parser.py\n+++ b/parser.py\n@@\n"
                    "-state = None\n+state = previous_state\n"
                    "DONE_CRITERIA: tests pass"
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        self.assertTrue(session.waiting_for_human)
        self.assertEqual(session.waiting_role, "Code Reviewer")

        engine.advance(
            session,
            human_input=(
                "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: parser.py\n"
                "REVIEW_NOTES: ready for tests"
            ),
        )

        self.assertFalse(session.waiting_for_human)
        self.assertEqual(session.current_node_idx, 3)
        self.assertEqual(session.last_handoff["to_role"], "Test Runner")

    def test_manual_step_can_change_mid_session(self):
        llm = FakeLLM({})
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="planner",
        )

        engine.advance(session)
        self.assertTrue(session.waiting_for_human)
        self.assertEqual(session.waiting_role, "Task Planner")

        engine.update_manual_step(session, "reviewer")
        self.assertEqual(session.manual_step_role, "Code Reviewer")
        self.assertFalse(session.waiting_for_human)
        self.assertEqual(session.status, "running")

    def test_serialized_session_uses_step_briefing(self):
        llm = FakeLLM({})
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="planner",
        )

        engine.advance(session)
        payload = engine.serialize_session(session)

        self.assertIn("step_briefing", payload)
        self.assertNotIn("learning_scaffold", payload)
        self.assertIn("It is your turn now", payload["step_briefing"]["what_you_should_do"])
        self.assertIn("role detail view", payload["step_briefing"]["prompt_box_note"])
        self.assertIn("workflow_process", payload)
        self.assertEqual(payload["workflow_process"]["current_activity"]["role"], "Task Planner")

    def test_serialized_workflow_process_exposes_prompt_and_artifacts(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest"
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="coder",
        )

        engine.advance(session)
        payload = engine.serialize_session(session)
        planner = payload["workflow_process"]["agents"][0]

        self.assertEqual(planner["role"], "Task Planner")
        self.assertEqual(planner["run_count"], 1)
        self.assertIn("runs", planner)
        self.assertIn("prompt_artifact", planner["runs"][0])
        self.assertIn("input_artifacts", planner["runs"][0])
        self.assertNotIn("Golden patch", [item["title"] for item in payload["workflow_process"]["agents"][1]["current_activity"]["input_artifacts"]])

    def test_code_reviewer_context_includes_live_patch_change(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest tests/test_parser.py"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: parser.py\nIMPLEMENTATION_PLAN: restore previous state on retry.\n"
                    "PATCH_DIFF: diff --git a/parser.py b/parser.py\n--- a/parser.py\n+++ b/parser.py\n@@\n-state = None\n+state = previous_state\n"
                    "DONE_CRITERIA: retry keeps the previous parser state."
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        payload = engine.serialize_session(session)
        current = payload["workflow_process"]["current_activity"]
        titles = [item["title"] for item in current["input_artifacts"]]

        self.assertEqual(current["role"], "Code Reviewer")
        self.assertIn("Latest Patch Author proposal", titles)
        self.assertIn("Proposed code change", titles)
        self.assertIn("parser.py", [item["path"] for item in current["files_in_scope"]])
        diff_artifact = next(item for item in current["input_artifacts"] if item["title"] == "Proposed code change")
        self.assertIn("+state = previous_state", diff_artifact["content"])
        self.assertNotIn("Golden patch", diff_artifact["content"])

    def test_test_runner_context_includes_patch_review_and_validation_focus(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest tests/test_parser.py"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: parser.py\nIMPLEMENTATION_PLAN: restore previous state on retry.\n"
                    "PATCH_DIFF: diff --git a/parser.py b/parser.py\n--- a/parser.py\n+++ b/parser.py\n@@\n-state = None\n+state = previous_state\n"
                    "DONE_CRITERIA: retry keeps the previous parser state."
                ],
                "Code Reviewer": [
                    "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: parser.py\nREVIEW_NOTES: the state restoration logic is coherent; validate the retry path."
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="tester",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        payload = engine.serialize_session(session)
        current = payload["workflow_process"]["current_activity"]
        titles = [item["title"] for item in current["input_artifacts"]]

        self.assertEqual(current["role"], "Test Runner")
        self.assertIn("Planning checks", titles)
        self.assertIn("Latest Patch Author proposal", titles)
        self.assertIn("Proposed code change", titles)
        self.assertIn("Latest Code Reviewer decision", titles)
        self.assertIn("What to validate", titles)
        validate_artifact = next(item for item in current["input_artifacts"] if item["title"] == "What to validate")
        self.assertIn("Planner checks:", validate_artifact["content"])
        self.assertIn("Patch Author expectations:", validate_artifact["content"])
        self.assertIn("Reviewer concerns or evidence:", validate_artifact["content"])

    def test_reviewer_support_prompt_uses_live_context_only(self):
        class CaptureLLM(FakeLLM):
            def __init__(self):
                super().__init__(
                    {
                        "Task Planner": [
                            "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest"
                        ],
                        "Patch Author": [
                            "PATCH_STATUS: READY\nFILES_CHANGED: parser.py\nIMPLEMENTATION_PLAN: fix parser\nDONE_CRITERIA: tests pass"
                        ],
                    }
                )
                self.captured_prompt = ""

            def chat_once(self, prompt, **kwargs):
                self.captured_prompt = prompt
                return super().chat_once(prompt, **kwargs)

        llm = CaptureLLM()
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.get_support(session, "Help me review this.")

        self.assertIn("Judge from the latest proposed fix", llm.captured_prompt)
        self.assertIn("Review scope note:", llm.captured_prompt)
        self.assertIn("By default, give a ready-to-submit answer", llm.captured_prompt)
        self.assertNotIn("Relevant validation targets:", llm.captured_prompt)
        self.assertNotIn("Reference patch diff:", llm.captured_prompt)
        self.assertNotIn("Reference test patch:", llm.captured_prompt)

    def test_tester_support_prompt_includes_validation_evidence(self):
        class CaptureLLM(FakeLLM):
            def __init__(self):
                super().__init__(
                    {
                        "Task Planner": [
                            "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest"
                        ],
                        "Patch Author": [
                            "PATCH_STATUS: READY\nFILES_CHANGED: parser.py\nIMPLEMENTATION_PLAN: fix parser\nDONE_CRITERIA: tests pass"
                        ],
                        "Code Reviewer": [
                            "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: parser.py\nREVIEW_NOTES: ready for tests"
                        ],
                    }
                )
                self.captured_prompt = ""

            def chat_once(self, prompt, **kwargs):
                self.captured_prompt = prompt
                return super().chat_once(prompt, **kwargs)

        llm = CaptureLLM()
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="tester",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.get_support(session, "Help me judge this.")

        self.assertIn("Executed test output:", llm.captured_prompt)
        self.assertNotIn("Suggested test commands:", llm.captured_prompt)
        self.assertNotIn("suggested checks", llm.captured_prompt.lower())
        self.assertNotIn("Reference patch diff:", llm.captured_prompt)
        self.assertNotIn("Reference test patch:", llm.captured_prompt)

    def test_tester_support_prompt_distinguishes_environment_failures(self):
        class CaptureLLM(FakeLLM):
            def __init__(self):
                super().__init__(
                    {
                        "Task Planner": [
                            "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest"
                        ],
                        "Patch Author": [
                            "PATCH_STATUS: READY\nFILES_CHANGED: parser.py\nIMPLEMENTATION_PLAN: fix parser\nDONE_CRITERIA: tests pass"
                        ],
                        "Code Reviewer": [
                            "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: parser.py\nREVIEW_NOTES: ready for tests"
                        ],
                    }
                )
                self.captured_prompt = ""

            def chat_once(self, prompt, **kwargs):
                self.captured_prompt = prompt
                return super().chat_once(prompt, **kwargs)

        def missing_pytest_runner(repo_path, command, timeout_seconds=120):
            return {
                "command": command,
                "argv": [],
                "exit_code": None,
                "passed": False,
                "stdout": "",
                "stderr": "Automatic test execution failed: [Errno 2] No such file or directory: 'pytest'",
                "timed_out": False,
            }

        llm = CaptureLLM()
        engine = StudyWorkflowEngine(self.db, llm, test_runner=missing_pytest_runner)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="tester",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        session.last_test_run = missing_pytest_runner(Path(self.temp_dir.name), "pytest")
        engine.get_support(session, "Help me judge this.")

        self.assertIn("Environment note:", llm.captured_prompt)
        self.assertIn("setup/tooling problem, not direct evidence against the patch", llm.captured_prompt)
        self.assertIn("Validation decision note:", llm.captured_prompt)
        self.assertIn("do not automatically choose FAIL", llm.captured_prompt)

    def test_tester_context_and_prompt_use_exit_code_field(self):
        class CaptureLLM(FakeLLM):
            def __init__(self):
                super().__init__(
                    {
                        "Task Planner": [
                            "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest"
                        ],
                        "Patch Author": [
                            "PATCH_STATUS: READY\nFILES_CHANGED: parser.py\nIMPLEMENTATION_PLAN: fix parser\nDONE_CRITERIA: tests pass"
                        ],
                        "Code Reviewer": [
                            "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: parser.py\nREVIEW_NOTES: ready for tests"
                        ],
                    }
                )
                self.captured_prompt = ""

            def chat_once(self, prompt, **kwargs):
                self.captured_prompt = prompt
                return super().chat_once(prompt, **kwargs)

        llm = CaptureLLM()
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="tester",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        session.last_test_run = {
            "command": "python3 -m pytest tests/test_parser.py",
            "passed": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": "AssertionError: parser state regressed",
        }

        payload = engine.serialize_session(session)
        current = payload["workflow_process"]["current_activity"]
        latest_run = next(item for item in current["input_artifacts"] if item["title"] == "Latest test execution")
        self.assertIn("Exit code: 1", latest_run["content"])

        engine.get_support(session, "Help me judge this.")
        self.assertIn("Exit code: 1", llm.captured_prompt)

    def test_manual_tester_step_waits_with_prepared_validation_evidence(self):
        calls = []

        def fake_test_runner(repo_path, command, timeout_seconds=120):
            calls.append((str(repo_path), command, timeout_seconds))
            return {
                "command": command,
                "argv": command.split(),
                "exit_code": 0,
                "passed": True,
                "stdout": "collected 3 items",
                "stderr": "",
                "timed_out": False,
            }

        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: parser.py\nIMPLEMENTATION_PLAN: fix parser\n"
                    "PATCH_DIFF: diff --git a/parser.py b/parser.py\n--- a/parser.py\n+++ b/parser.py\n@@\n"
                    "-state = None\n+state = previous_state\n"
                    "DONE_CRITERIA: tests pass"
                ],
                "Code Reviewer": [
                    "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: parser.py\nREVIEW_NOTES: ready for tests"
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm, test_runner=fake_test_runner)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="tester",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(session)

        self.assertTrue(session.waiting_for_human)
        self.assertEqual(session.waiting_role, "Test Runner")
        self.assertEqual(len(calls), 0)
        self.assertIsNotNone(session.last_test_run)
        self.assertTrue(session.last_test_run["auto_prepared"])

    def test_repeated_support_prompt_requests_alternative(self):
        class CaptureLLM(FakeLLM):
            def __init__(self):
                super().__init__({})
                self.prompts = []
                self.temperatures = []

            def chat_once(self, prompt, **kwargs):
                self.prompts.append(prompt)
                self.temperatures.append(kwargs.get("temperature"))
                return f"draft-{len(self.prompts)}"

        llm = CaptureLLM()
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )
        session.waiting_for_human = True
        session.waiting_role = "Code Reviewer"

        first = engine.get_support(session, "Help me draft this.")
        second = engine.get_support(session, "Help me draft this.")

        self.assertEqual(first, "draft-1")
        self.assertEqual(second, "draft-2")
        self.assertEqual(llm.temperatures[0], 0.2)
        self.assertEqual(llm.temperatures[1], 0.35)
        self.assertIn("Prior support for this same step already exists.", llm.prompts[1])
        self.assertIn("Do not repeat the same draft.", llm.prompts[1])

    def test_curated_bug4_review_prompt_excludes_benchmark_artifacts(self):
        class CaptureLLM(FakeLLM):
            def __init__(self):
                super().__init__(
                    {
                        "Task Planner": [
                            "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: tqdm/_tqdm.py\nACCEPTANCE_CHECKS: test_nototal"
                        ],
                        "Code Reviewer": [
                            "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: tqdm/_tqdm.py\nREVIEW_NOTES: ready"
                        ],
                    }
                )
                self.captured_prompt = ""

            def chat_once(self, prompt, **kwargs):
                if kwargs.get("role") == "Code Reviewer":
                    self.captured_prompt = prompt
                return super().chat_once(prompt, **kwargs)

        llm = CaptureLLM()
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=curated_bugsinpy_tqdm_task_by_instance("bugsinpy-tqdm-4"),
            repo_path=self.temp_dir.name,
            workflow_type="coder",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(
            session,
            human_input=(
                "PATCH_STATUS: READY\nFILES_CHANGED: tqdm/_tqdm.py\nIMPLEMENTATION_PLAN: guard unit_scale handling of total so it only scales "
                "when total is known.\nDONE_CRITERIA: test_nototal should no longer fail when total is unknown."
            ),
        )
        engine.advance(session)

        self.assertIn("Latest output from Patch Author", llm.captured_prompt)
        self.assertNotIn("Reference patch diff:", llm.captured_prompt)
        self.assertNotIn("Golden patch", llm.captured_prompt)
        self.assertNotIn("benchmark", llm.captured_prompt.lower())

    def test_curated_bug2_test_prompt_excludes_benchmark_artifacts(self):
        class CaptureLLM(FakeLLM):
            def __init__(self):
                super().__init__(
                    {
                        "Task Planner": [
                            "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: tqdm/utils.py, tqdm/std.py\nACCEPTANCE_CHECKS: test_format_meter"
                        ],
                        "Patch Author": [
                            "PATCH_STATUS: READY\nFILES_CHANGED: tqdm/utils.py, tqdm/std.py\nIMPLEMENTATION_PLAN: adjust disp_trim reset handling and the ncols call sites.\nDONE_CRITERIA: test_format_meter should stop producing stray resets."
                        ],
                        "Test Runner": [
                            "TEST_DECISION: PASS\nTEST_NOTES: evidence is sufficient"
                        ],
                    }
                )
                self.captured_prompt = ""

            def chat_once(self, prompt, **kwargs):
                if kwargs.get("role") == "Test Runner":
                    self.captured_prompt = prompt
                return super().chat_once(prompt, **kwargs)

        llm = CaptureLLM()
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=curated_bugsinpy_tqdm_task_by_instance("bugsinpy-tqdm-2"),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(
            session,
            human_input=(
                "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: tqdm/utils.py, tqdm/std.py\nREVIEW_NOTES: current proposal is ready for validation."
            ),
        )
        engine.advance(session)

        self.assertNotIn("Suggested test commands:", llm.captured_prompt)
        self.assertIn("Validation handoff summary:", llm.captured_prompt)
        self.assertNotIn("Reference patch diff:", llm.captured_prompt)
        self.assertNotIn("Reference test patch:", llm.captured_prompt)

    def test_reviewer_path_completes_when_ai_tester_only_reports_missing_evidence(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: tqdm/utils.py, tqdm/std.py\nACCEPTANCE_CHECKS: pytest tqdm/tests/tests_tqdm.py::test_format_meter"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: tqdm/utils.py, tqdm/std.py\nIMPLEMENTATION_PLAN: route trimming through disp_trim only when ncols is set and prevent stray ANSI resets.\n"
                    "PATCH_DIFF: diff --git a/tqdm/std.py b/tqdm/std.py\n--- a/tqdm/std.py\n+++ b/tqdm/std.py\n@@\n-            if ncols:\n-                return disp_trim(res, ncols)\n+            return disp_trim(res, ncols) if ncols else res\n"
                    "DONE_CRITERIA: width trimming should stop producing stray ANSI resets."
                ],
                "Test Runner": [
                    "TEST_DECISION: FAIL\nTEST_NOTES: No test command has been run yet, so I would run the targeted format_meter checks before claiming full validation."
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=curated_bugsinpy_tqdm_task_by_instance("bugsinpy-tqdm-2"),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(
            session,
            human_input=(
                "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: tqdm/utils.py, tqdm/std.py\n"
                "REVIEW_NOTES: the two-file proposal is coherent and ready for validation."
            ),
        )
        engine.advance(session)

        self.assertEqual(session.status, "completed")
        self.assertEqual(session.current_node_idx, -1)
        self.assertEqual(session.iteration_count, 0)

    def test_reviewer_path_returns_to_patch_author_on_real_test_failure(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: tqdm/utils.py, tqdm/std.py\nACCEPTANCE_CHECKS: pytest tqdm/tests/tests_tqdm.py::test_format_meter"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: tqdm/utils.py, tqdm/std.py\nIMPLEMENTATION_PLAN: route trimming through disp_trim only when ncols is set and prevent stray ANSI resets.\n"
                    "PATCH_DIFF: diff --git a/tqdm/utils.py b/tqdm/utils.py\n--- a/tqdm/utils.py\n+++ b/tqdm/utils.py\n@@\n-    if RE_ANSI.search(data):\n-        return data + \"\\\\033[0m\"\n+    if ansi_present and bool(RE_ANSI.search(data)):\n+        return data if data.endswith(\"\\\\033[0m\") else data + \"\\\\033[0m\"\n"
                    "DONE_CRITERIA: width trimming should stop producing stray ANSI resets."
                ],
                "Test Runner": [
                    "TEST_DECISION: FAIL\nTEST_NOTES: The current patch still fails because the format_meter output shows a stray ANSI reset in the failing test output."
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=curated_bugsinpy_tqdm_task_by_instance("bugsinpy-tqdm-2"),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(
            session,
            human_input=(
                "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: tqdm/utils.py, tqdm/std.py\n"
                "REVIEW_NOTES: the two-file proposal is coherent and ready for validation."
            ),
        )
        session.last_test_run = {
            "command": "python3 -m pytest tqdm/tests/tests_tqdm.py::test_format_meter",
            "passed": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "AssertionError: stray ANSI reset still appears",
        }
        engine.advance(session)

        self.assertEqual(session.current_node_idx, 1)
        self.assertEqual(session.iteration_count, 1)
        self.assertEqual(session.last_handoff["to_role"], "Patch Author")

    def test_reviewer_missing_optional_patch_diff_does_not_loop(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: parser.py\nIMPLEMENTATION_PLAN: restore previous state on retry.\n"
                    "DONE_CRITERIA: retry keeps the previous parser state."
                ],
                "Code Reviewer": [
                    "REVIEW_DECISION: REVISE\nPATCH_STATUS: READY\nFILES_CHANGED: parser.py\n"
                    "REVIEW_NOTES: Missing an inline diff, but the proposed change is otherwise coherent."
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="tester",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)

        self.assertEqual(session.current_node_idx, 3)
        self.assertEqual(session.current_role, "Test Runner")
        self.assertEqual(session.iteration_count, 0)

    def test_reviewer_practice_uses_ai_patch_and_files(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: tqdm/utils.py, tqdm/std.py\nACCEPTANCE_CHECKS: pytest tqdm/tests/tests_tqdm.py::test_format_meter"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: tqdm/utils.py\nIMPLEMENTATION_PLAN: adjust disp_trim reset handling.\n"
                    "PATCH_DIFF: diff --git a/tqdm/utils.py b/tqdm/utils.py\n--- a/tqdm/utils.py\n+++ b/tqdm/utils.py\n@@\n"
                    "-    if RE_ANSI.search(data):\n+    if ansi_present and bool(RE_ANSI.search(data)):\n"
                    "DONE_CRITERIA: ANSI reset handling is correct."
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=curated_bugsinpy_tqdm_task_by_instance("bugsinpy-tqdm-2"),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        payload = engine.serialize_session(session)
        current = payload["workflow_process"]["current_activity"]

        diff_artifact = next(item for item in current["input_artifacts"] if item["title"] == "Proposed code change")
        files_artifact = next(item for item in current["input_artifacts"] if item["title"] == "Files proposed to change")
        self.assertEqual(diff_artifact["origin"], "Patch Author")
        self.assertIn("diff --git a/tqdm/utils.py b/tqdm/utils.py", diff_artifact["content"])
        self.assertNotIn("diff --git a/tqdm/std.py b/tqdm/std.py", diff_artifact["content"])
        self.assertEqual(files_artifact["origin"], "Patch Author")
        self.assertIn("tqdm/utils.py", files_artifact["content"])
        self.assertNotIn("tqdm/std.py", files_artifact["content"])

    def test_reviewer_practice_prompt_uses_live_patch_even_when_ai_patch_is_partial(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: tqdm/utils.py, tqdm/std.py\nACCEPTANCE_CHECKS: pytest tqdm/tests/tests_tqdm.py::test_format_meter"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: tqdm/utils.py\nIMPLEMENTATION_PLAN: adjust disp_trim reset handling.\n"
                    "PATCH_DIFF: diff --git a/tqdm/utils.py b/tqdm/utils.py\n--- a/tqdm/utils.py\n+++ b/tqdm/utils.py\n@@\n"
                    "-    if RE_ANSI.search(data):\n+    if ansi_present and bool(RE_ANSI.search(data)):\n"
                    "DONE_CRITERIA: ANSI reset handling is correct."
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=curated_bugsinpy_tqdm_task_by_instance("bugsinpy-tqdm-2"),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        payload = engine.serialize_session(session)
        current = payload["workflow_process"]["current_activity"]

        diff_artifact = next(item for item in current["input_artifacts"] if item["title"] == "Proposed code change")
        files_artifact = next(item for item in current["input_artifacts"] if item["title"] == "Files proposed to change")
        self.assertEqual(diff_artifact["origin"], "Patch Author")
        self.assertIn("diff --git a/tqdm/utils.py b/tqdm/utils.py", diff_artifact["content"])
        self.assertNotIn("diff --git a/tqdm/std.py b/tqdm/std.py", diff_artifact["content"])
        self.assertEqual(files_artifact["origin"], "Patch Author")
        self.assertIn("tqdm/utils.py", files_artifact["content"])
        self.assertNotIn("tqdm/std.py", files_artifact["content"])

    def test_reviewer_prompt_uses_full_live_patch_without_truncating_utils_hunk(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: tqdm/utils.py, tqdm/std.py\nACCEPTANCE_CHECKS: pytest tqdm/tests/tests_tqdm.py::test_format_meter"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: tqdm/utils.py\nIMPLEMENTATION_PLAN: adjust disp_trim reset handling.\n"
                    "PATCH_DIFF: diff --git a/tqdm/utils.py b/tqdm/utils.py\n--- a/tqdm/utils.py\n+++ b/tqdm/utils.py\n@@\n"
                    "-    if RE_ANSI.search(data):\n+    if ansi_present and bool(RE_ANSI.search(data)):\n"
                    "DONE_CRITERIA: ANSI reset handling is correct."
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=curated_bugsinpy_tqdm_task_by_instance("bugsinpy-tqdm-2"),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        prompt = engine.serialize_session(session)["workflow_process"]["current_activity"]["prompt_artifact"]["content"]

        self.assertIn("diff --git a/tqdm/utils.py b/tqdm/utils.py", prompt)
        self.assertIn("+    if ansi_present and bool(RE_ANSI.search(data)):", prompt)
        self.assertNotIn("diff --git a/tqdm/std.py b/tqdm/std.py", prompt)
        self.assertNotIn("carefully delete one char at a time\n         data = data[:-1]\n-    if RE_ANSI.search(data):", prompt[-120:])

    def test_reviewer_prompt_preserves_patch_authors_actual_plan(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: tqdm/utils.py, tqdm/std.py\nACCEPTANCE_CHECKS: pytest tqdm/tests/tests_tqdm.py::test_format_meter"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: tqdm/std.py, tqdm/utils.py\n"
                    "IMPLEMENTATION_PLAN: Tokenize the input into alternating ANSI escape tokens and plain-text tokens, track active_sgr state, and append a reset only when active_sgr is true.\n"
                    "PATCH_DIFF: diff --git a/tqdm/utils.py b/tqdm/utils.py\n--- a/tqdm/utils.py\n+++ b/tqdm/utils.py\n@@\n"
                    "-    if RE_ANSI.search(data):\n+    if ansi_present and bool(RE_ANSI.search(data)):\n"
                    "DONE_CRITERIA: Never cut inside escape sequences and only append a reset when an SGR is active."
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=curated_bugsinpy_tqdm_task_by_instance("bugsinpy-tqdm-2"),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        payload = engine.serialize_session(session)
        prompt = payload["workflow_process"]["current_activity"]["prompt_artifact"]["content"]
        handoff = next(item for item in payload["workflow_process"]["current_activity"]["input_artifacts"] if item["title"] == "Handoff from Patch Author")["content"]

        self.assertIn("active_sgr", prompt)
        self.assertIn("Tokenize the input into alternating ANSI escape tokens", prompt)
        self.assertIn("IMPLEMENTATION_PLAN: Tokenize the input into alternating ANSI escape tokens and plain-text tokens, track active_sgr state, and append a reset only when active_sgr is true.", prompt)
        self.assertIn("active_sgr", handoff)
        self.assertIn("IMPLEMENTATION_PLAN: Tokenize the input into alternating ANSI escape tokens and plain-text tokens, track active_sgr state, and append a reset only when active_sgr is true.", handoff)

    def test_reviewer_revision_gives_patch_author_full_patch_and_review_feedback(self):
        long_patch = (
            "PATCH_STATUS: READY\n"
            "FILES_CHANGED: parser.py\n"
            "IMPLEMENTATION_PLAN: adjust parser recovery logic.\n"
            "PATCH_DIFF: diff --git a/parser.py b/parser.py\n"
            "--- a/parser.py\n"
            "+++ b/parser.py\n"
            "@@\n"
            + "".join(f"-old_line_{idx}\n+new_line_{idx}\n" for idx in range(40))
            + "DONE_CRITERIA: parser keeps the prior state on retry.\n"
        )
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest"
                ],
                "Patch Author": [long_patch],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(
            session,
            human_input=(
                "REVIEW_DECISION: REVISE\nPATCH_STATUS: READY\nFILES_CHANGED: parser.py\n"
                "REVIEW_NOTES: keep the existing state restoration, but guard the retry branch."
            ),
        )
        prompt = engine.serialize_session(session)["workflow_process"]["current_activity"]["prompt_artifact"]["content"]

        self.assertIn("Revision guidance:", prompt)
        self.assertIn("Current patch diff to revise:", prompt)
        self.assertIn("-old_line_39", prompt)
        self.assertIn("+new_line_39", prompt)
        self.assertIn("Latest review feedback:", prompt)
        self.assertIn("guard the retry branch", prompt)

    def test_non_string_llm_output_is_coerced_in_reviewer_flow(self):
        class DictLLM(FakeLLM):
            def __init__(self):
                super().__init__({})
            def chat_once(self, prompt, **kwargs):
                role = kwargs.get("role")
                if role == "Task Planner":
                    return {
                        "PLAN_SUMMARY": "plan",
                        "ROOT_CAUSE": "root",
                        "LIKELY_AREAS": "parser.py",
                        "ACCEPTANCE_CHECKS": "pytest",
                    }
                return super().chat_once(prompt, **kwargs)

        engine = StudyWorkflowEngine(self.db, DictLLM())
        session = engine.create_session(
            participant_name="learner",
            task=sample_task(),
            repo_path=self.temp_dir.name,
            workflow_type="reviewer",
        )

        engine.advance(session)
        self.assertIsInstance(session.messages[0]["content"], str)
        self.assertIn("PLAN_SUMMARY", session.messages[0]["content"])

    def test_auto_validation_runs_candidate_patch_and_completes(self):
        repo = Path(self.temp_dir.name) / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        tests_dir = repo / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_calc.py").write_text(
            "import unittest\n"
            "from calc import add\n\n"
            "class CalcTests(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(1, 2), 3)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )

        import subprocess
        subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        task = {
            **sample_task(),
            "repo": "demo/repo",
            "issue_url": "https://example.com/issue",
            "patch": (
                "diff --git a/calc.py b/calc.py\n"
                "--- a/calc.py\n"
                "+++ b/calc.py\n"
                "@@ -1,2 +1,2 @@\n"
                "-def add(a, b):\n"
                "-    return a - b\n"
                "+def add(a, b):\n"
                "+    return a + b\n"
            ),
            "changed_files": ["calc.py"],
            "suggested_test_commands": ["python3 -m unittest discover -s tests"],
        }

        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: calc.py\nACCEPTANCE_CHECKS: python3 -m unittest discover -s tests"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: calc.py\nIMPLEMENTATION_PLAN: switch add() back to addition.\n"
                    "PATCH_DIFF: diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                    "-def add(a, b):\n-    return a - b\n+def add(a, b):\n+    return a + b\n"
                    "DONE_CRITERIA: unittest passes."
                ],
                "Test Runner": [
                    "TEST_DECISION: PASS\nTEST_NOTES: The candidate patch passed the targeted unittest run."
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=task,
            repo_path=str(repo),
            workflow_type="reviewer",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(
            session,
            human_input=(
                "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: calc.py\n"
                "REVIEW_NOTES: The proposed fix is coherent and ready for validation."
            ),
        )
        engine.advance(session)

        self.assertEqual(session.status, "completed")
        self.assertTrue(session.last_test_run["auto_prepared"])
        self.assertEqual(session.last_test_run["validation_state"], "executed")
        self.assertTrue(session.last_test_run["passed"], msg=session.last_test_run)

    def test_tester_practice_auto_validation_uses_task_patch_when_proposal_has_no_diff(self):
        repo_root = Path(__file__).resolve().parents[1] / "repos"
        task = curated_bugsinpy_tqdm_task_by_instance("bugsinpy-tqdm-5")
        repo_path = repo_root / f"{task['repo'].replace('/', '__')}_{task['base_commit'][:8]}"

        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: tqdm/_tqdm.py\nACCEPTANCE_CHECKS: pytest tqdm/tests/tests_tqdm.py::test_bool"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: tqdm/_tqdm.py\nIMPLEMENTATION_PLAN: preserve total on disabled bars.\n"
                    "DONE_CRITERIA: disabled bars keep total."
                ],
                "Code Reviewer": [
                    "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: tqdm/_tqdm.py\n"
                    "REVIEW_NOTES: validate the disabled-bar total behavior."
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=task,
            repo_path=str(repo_path),
            workflow_type="tester",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(session)

        self.assertTrue(session.waiting_for_human)
        self.assertEqual(session.waiting_role, "Test Runner")
        self.assertIsNotNone(session.last_test_run)
        self.assertTrue(session.last_test_run["auto_prepared"])
        self.assertEqual(session.last_test_run["validation_state"], "executed")

    def test_coder_practice_does_not_hide_missing_human_patch_with_task_patch(self):
        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: tqdm/_tqdm.py\nACCEPTANCE_CHECKS: pytest tqdm/tests/tests_tqdm.py::test_disable"
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm)
        session = engine.create_session(
            participant_name="learner",
            task=curated_bugsinpy_tqdm_task_by_instance("bugsinpy-tqdm-5"),
            repo_path=self.temp_dir.name,
            workflow_type="coder",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(
            session,
            human_input=(
                "PATCH_STATUS: READY\nFILES_CHANGED: tqdm/_tqdm.py\n"
                "IMPLEMENTATION_PLAN: preserve total before the disabled return.\n"
                "DONE_CRITERIA: disabled bars keep total."
            ),
        )

        payload = engine.serialize_session(session)
        reviewer = payload["workflow_process"]["current_activity"]
        titles = [item["title"] for item in reviewer["input_artifacts"]]
        self.assertNotIn("Proposed code change", titles)

    def test_bug5_tester_task_progresses_and_completes(self):
        calls = []

        def fake_test_runner(repo_path, command, timeout_seconds=120):
            calls.append((str(repo_path), command, timeout_seconds))
            return {
                "command": command,
                "argv": command.split(),
                "exit_code": 0,
                "passed": True,
                "stdout": "ok\n",
                "stderr": "",
                "timed_out": False,
            }

        llm = FakeLLM(
            {
                "Task Planner": [
                    "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: tqdm/_tqdm.py\nACCEPTANCE_CHECKS: test_bool"
                ],
                "Patch Author": [
                    "PATCH_STATUS: READY\nFILES_CHANGED: tqdm/_tqdm.py\nIMPLEMENTATION_PLAN: infer total before the disabled early return and store self.total on disabled bars.\nDONE_CRITERIA: disabled bars keep len(iterable)-based totals."
                ],
                "Code Reviewer": [
                    "REVIEW_DECISION: APPROVE\nPATCH_STATUS: READY\nFILES_CHANGED: tqdm/_tqdm.py\nREVIEW_NOTES: ready for validation"
                ],
            }
        )
        engine = StudyWorkflowEngine(self.db, llm, test_runner=fake_test_runner)
        session = engine.create_session(
            participant_name="learner",
            task=curated_bugsinpy_tqdm_task_by_instance("bugsinpy-tqdm-5"),
            repo_path=self.temp_dir.name,
            workflow_type="tester",
        )

        engine.advance(session)
        engine.advance(session)
        engine.advance(session)
        engine.advance(session)

        self.assertTrue(session.waiting_for_human)
        self.assertEqual(session.waiting_role, "Test Runner")
        self.assertEqual(len(calls), 0)

        engine.advance(
            session,
            human_input="TEST_DECISION: PASS\nTEST_NOTES: The fix preserves total inference for disabled bars."
        )

        self.assertEqual(session.status, "completed")
        self.assertEqual(session.current_node_idx, -1)


if __name__ == "__main__":
    unittest.main()
