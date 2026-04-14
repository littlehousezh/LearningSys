from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import StudyDatabase
from workflow import StudyWorkflowEngine


class FakeLLM:
    def chat_once(self, prompt, **kwargs):
        return "PLAN_SUMMARY: plan\nROOT_CAUSE: root\nLIKELY_AREAS: parser.py\nACCEPTANCE_CHECKS: pytest"

    def resolve_model_id(self, role=None):
        return "fake-model"


def sample_task():
    return {
        "instance_id": "demo__repo-1",
        "repo": "demo/repo",
        "base_commit": "abc12345",
        "issue_url": "https://example.com/issue",
        "problem_statement": "Parser loses state after retry.",
        "hints_text": "Inspect parser state transitions.",
        "changed_files": ["parser.py"],
        "patch_changed_lines": 14,
        "educational_fit": {"difficulty_band": "intro", "python_affinity": 1.0},
        "suggested_test_commands": ["pytest"],
    }


class DatabaseResumeTests(unittest.TestCase):
    def test_saved_session_can_be_loaded_back(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StudyDatabase(Path(temp_dir) / "study.db")
            engine = StudyWorkflowEngine(db, FakeLLM())
            session = engine.create_session(
                participant_id="student-01",
                participant_name="learner",
                task=sample_task(),
                repo_path=temp_dir,
                workflow_type="planner",
            )
            engine.record_patch_application(session, {"applied": True, "changed_files": ["parser.py"]})
            engine.record_test_run(session, {"passed": False, "command": "pytest"})

            loaded = db.get_session(session.session_id)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.participant_id, "student-01")
            self.assertEqual(loaded.participant_name, "learner")
            self.assertEqual(loaded.manual_step_role, "Task Planner")
            self.assertTrue(loaded.last_patch_application["applied"])
            self.assertEqual(loaded.last_test_run["command"], "pytest")

    def test_sessions_and_events_are_scoped_by_participant_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StudyDatabase(Path(temp_dir) / "study.db")
            engine = StudyWorkflowEngine(db, FakeLLM())
            first = engine.create_session(
                participant_id="student-01",
                participant_name="student-01",
                task=sample_task(),
                repo_path=temp_dir,
                workflow_type="planner",
            )
            second = engine.create_session(
                participant_id="student-02",
                participant_name="student-02",
                task=sample_task(),
                repo_path=temp_dir,
                workflow_type="reviewer",
            )

            self.assertIsNotNone(db.get_session(first.session_id, participant_id="student-01"))
            self.assertIsNone(db.get_session(first.session_id, participant_id="student-02"))
            self.assertEqual(len(db.list_sessions(participant_id="student-01")), 1)
            self.assertEqual(len(db.list_sessions(participant_id="student-02")), 1)
            self.assertEqual(db.list_sessions(participant_id="student-01")[0]["session_id"], first.session_id)
            self.assertEqual(db.list_sessions(participant_id="student-02")[0]["session_id"], second.session_id)
            self.assertTrue(db.get_events(first.session_id, participant_id="student-01"))
            self.assertEqual(db.get_events(first.session_id, participant_id="student-02"), [])

    def test_clear_all_removes_sessions_and_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = StudyDatabase(Path(temp_dir) / "study.db")
            engine = StudyWorkflowEngine(db, FakeLLM())
            session = engine.create_session(
                participant_name="learner",
                task=sample_task(),
                repo_path=temp_dir,
                workflow_type="planner",
            )

            self.assertIsNotNone(db.get_session(session.session_id))
            self.assertTrue(db.list_sessions())

            db.clear_all()

            self.assertIsNone(db.get_session(session.session_id))
            self.assertEqual(db.list_sessions(), [])
            self.assertEqual(db.get_events(session.session_id), [])


if __name__ == "__main__":
    unittest.main()
