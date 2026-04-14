from __future__ import annotations

import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tasks import (
    apply_patch_to_repo,
    choose_n_easiest_python_tasks,
    create_session_repo_copy,
    curated_bugsinpy_tqdm_tasks,
    run_test_command,
)


class TaskRankingAndRepoActionTests(unittest.TestCase):
    def test_curated_bugsinpy_tqdm_tasks_are_available(self):
        tasks = curated_bugsinpy_tqdm_tasks()

        self.assertEqual(len(tasks), 4)
        self.assertEqual(tasks[0]["repo"], "tqdm/tqdm")
        self.assertEqual(tasks[0]["benchmark_suite"], "BugsInPy")
        self.assertTrue(tasks[0]["compare_url"].startswith("https://github.com/tqdm/tqdm/compare/"))
        self.assertIn(tasks[0]["educational_fit"]["difficulty_label"], {"Easy", "Medium"})

    def test_task_ranking_prefers_small_python_friendly_tasks(self):
        rows = [
            {
                "instance_id": "hard-1",
                "repo": "demo/repo",
                "base_commit": "abc12345",
                "problem_statement": "x" * 2000,
                "hints_text": "",
                "patch": (
                    "--- a/docs.txt\n+++ b/docs.txt\n@@\n-old\n+new\n"
                    "--- a/script.js\n+++ b/script.js\n@@\n-old\n+new\n"
                ),
                "test_patch": "",
            },
            {
                "instance_id": "easy-1",
                "repo": "demo/repo",
                "base_commit": "abc12345",
                "problem_statement": "Short issue",
                "hints_text": "",
                "patch": "--- a/app.py\n+++ b/app.py\n@@\n-return 0\n+return 1\n",
                "test_patch": "--- a/tests/test_app.py\n+++ b/tests/test_app.py\n@@\n-pass\n+assert True\n",
            },
        ]

        with patch("tasks._load_split", return_value=rows):
            ranked = choose_n_easiest_python_tasks(n=2, dataset_name="demo", split="test")

        self.assertEqual(ranked[0]["instance_id"], "easy-1")
        self.assertEqual(ranked[0]["suggested_test_commands"][0], "python3 -m pytest tests/test_app.py")

    def test_issue_url_prefers_explicit_task_metadata(self):
        rows = [
            {
                "instance_id": "demo__repo-123",
                "repo": "demo/repo",
                "base_commit": "abc12345",
                "problem_statement": "Short issue",
                "hints_text": "",
                "patch": "--- a/app.py\n+++ b/app.py\n@@\n-return 0\n+return 1\n",
                "test_patch": "",
                "issue_url": "https://github.com/demo/repo/issues/456",
            },
        ]

        with patch("tasks._load_split", return_value=rows):
            ranked = choose_n_easiest_python_tasks(n=1, dataset_name="demo", split="test")

        self.assertEqual(ranked[0]["issue_url"], "https://github.com/demo/repo/issues/456")

    def test_curated_task_references_and_suggested_tests_match_repo_snapshots(self):
        tasks = curated_bugsinpy_tqdm_tasks()
        repo_root = Path(__file__).resolve().parents[1] / "repos"
        by_id = {task["instance_id"]: task for task in tasks}

        self.assertEqual(by_id["bugsinpy-tqdm-1"]["issue_url"], "https://github.com/tqdm/tqdm/issues/840")
        self.assertIn("start", by_id["bugsinpy-tqdm-1"]["issue_summary"].lower())
        self.assertIn("self.total", by_id["bugsinpy-tqdm-5"]["problem_statement"])

        for task in tasks:
            repo_dir = repo_root / f"{task['repo'].replace('/', '__')}_{task['base_commit'][:8]}"
            self.assertTrue(repo_dir.exists(), msg=f"Missing repo snapshot for {task['instance_id']}")

            for command in task["suggested_test_commands"]:
                argv = shlex.split(command)
                pytest_idx = next((idx for idx, value in enumerate(argv) if value == "pytest"), -1)
                self.assertGreaterEqual(pytest_idx, 0, msg=f"Expected pytest command for {task['instance_id']}: {command}")

                for arg in argv[pytest_idx + 1:]:
                    if arg.startswith("-"):
                        continue
                    path = arg.split("::", 1)[0]
                    if not path.endswith(".py"):
                        continue
                    self.assertTrue(
                        (repo_dir / path).exists(),
                        msg=f"Command references missing test path for {task['instance_id']}: {command}",
                    )

    def test_run_test_command_falls_back_to_python3_module_pytest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)

            first_error = FileNotFoundError(2, "No such file or directory", "pytest")

            import subprocess
            completed = subprocess.CompletedProcess(
                args=["python3", "-m", "pytest", "tests/test_calc.py"],
                returncode=0,
                stdout="ok\n",
                stderr="",
            )

            with patch("tasks.subprocess.run", side_effect=[first_error, completed]) as run_mock:
                result = run_test_command(repo, "pytest tests/test_calc.py", timeout_seconds=30)

            self.assertTrue(result["passed"], msg=result)
            self.assertEqual(result["command"], "python3 -m pytest tests/test_calc.py")
            self.assertEqual(result["argv"], ["python3", "-m", "pytest", "tests/test_calc.py"])
            self.assertEqual(run_mock.call_count, 2)

    def test_create_session_repo_copy_isolated_from_cache_repo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            cache_repo = temp_root / "cache_repo"
            cache_repo.mkdir()
            (cache_repo / "module.py").write_text("VALUE = 'cache'\n", encoding="utf-8")

            import subprocess
            subprocess.run(
                ["git", "init"],
                cwd=cache_repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            workspaces_root = temp_root / "workspaces"
            first_copy = create_session_repo_copy(cache_repo, workspaces_root)
            second_copy = create_session_repo_copy(cache_repo, workspaces_root)

            self.assertNotEqual(first_copy, second_copy)
            self.assertTrue((first_copy / ".git").exists())
            self.assertTrue((second_copy / ".git").exists())
            self.assertEqual(
                (first_copy / "module.py").read_text(encoding="utf-8"),
                "VALUE = 'cache'\n",
            )

            (first_copy / "module.py").write_text("VALUE = 'student one'\n", encoding="utf-8")

            self.assertEqual(
                (cache_repo / "module.py").read_text(encoding="utf-8"),
                "VALUE = 'cache'\n",
            )
            self.assertEqual(
                (second_copy / "module.py").read_text(encoding="utf-8"),
                "VALUE = 'cache'\n",
            )

    def test_apply_patch_and_run_tests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
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

            patch_text = (
                "diff --git a/calc.py b/calc.py\n"
                "--- a/calc.py\n"
                "+++ b/calc.py\n"
                "@@ -1,2 +1,2 @@\n"
                "-def add(a, b):\n"
                "-    return a - b\n"
                "+def add(a, b):\n"
                "+    return a + b\n"
            )
            result = apply_patch_to_repo(repo, patch_text)
            self.assertTrue(result["applied"], msg=result)
            self.assertIn("return a + b", (repo / "calc.py").read_text(encoding="utf-8"))

            test_result = run_test_command(repo, "python3 -m unittest discover -s tests", timeout_seconds=30)
            self.assertTrue(test_result["passed"], msg=test_result)


if __name__ == "__main__":
    unittest.main()
