from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    from datasets import load_dataset
except ModuleNotFoundError:  # pragma: no cover - depends on local environment
    load_dataset = None


PYTHON_FRIENDLY_SUFFIXES = {
    ".py",
    ".pyi",
    ".pyx",
    ".rst",
    ".txt",
    ".md",
    ".toml",
    ".cfg",
    ".ini",
    ".yaml",
    ".yml",
}

SAFE_TEST_COMMAND_PREFIXES = (
    ("pytest",),
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("python", "-m", "unittest"),
    ("python3", "-m", "unittest"),
    ("tox",),
    ("nox",),
    ("uv", "run", "pytest"),
    ("poetry", "run", "pytest"),
    ("pipenv", "run", "pytest"),
    ("python", "manage.py", "test"),
    ("python3", "manage.py", "test"),
)


CURATED_TQDM_TASK_SPECS: List[Dict[str, Any]] = [
    {
        "instance_id": "bugsinpy-tqdm-1",
        "bugsinpy_bug_id": "1",
        "repo": "tqdm/tqdm",
        "base_commit": "8cc777fe8401a05d07f2c97e65d15e4460feab88",
        "fixed_commit": "c0dcf39b046d1b4ff6de14ac99ad9a1b10487512",
        "issue_url": "https://github.com/tqdm/tqdm/issues/840",
        "issue_summary": (
            "Fix `tqdm.contrib.tenumerate` so custom `start` values are passed to "
            "`enumerate(...)` instead of being misrouted into `tqdm`."
        ),
        "problem_statement": (
            "`tqdm.contrib.tenumerate` sends the caller's `start` value into "
            "`tqdm_class(...)` as a positional argument instead of passing it to "
            "`enumerate(..., start=...)`. As a result, custom enumeration offsets "
            "are ignored and the wrapper routes the argument to the wrong call."
        ),
        "hints_text": (
            "Focus on `tqdm/contrib/__init__.py` and how `tenumerate` constructs "
            "its wrapped iterator. The fix should stay localized and preserve the "
            "existing tqdm wrapper behavior while moving `start` onto `enumerate(...)`."
        ),
        "patch": (
            "diff --git a/tqdm/contrib/__init__.py b/tqdm/contrib/__init__.py\n"
            "index 1dddacf..935ab63 100644\n"
            "--- a/tqdm/contrib/__init__.py\n"
            "+++ b/tqdm/contrib/__init__.py\n"
            "@@ -38,7 +38,7 @@ def tenumerate(iterable, start=0, total=None, tqdm_class=tqdm_auto,\n"
            "         if isinstance(iterable, np.ndarray):\n"
            "             return tqdm_class(np.ndenumerate(iterable),\n"
            "                               total=total or len(iterable), **tqdm_kwargs)\n"
            "-    return enumerate(tqdm_class(iterable, start, **tqdm_kwargs))\n"
            "+    return enumerate(tqdm_class(iterable, **tqdm_kwargs), start)\n"
            " \n"
            " \n"
            " def _tzip(iter1, *iter2plus, **tqdm_kwargs):\n"
        ),
        "test_patch": "",
        "suggested_test_commands": [
            "python3 -m pytest tqdm/tests/tests_contrib.py",
        ],
        "target_test_file": "tqdm/tests/tests_contrib.py",
        "difficulty_band": "easy",
        "difficulty_label": "Easy",
        "task_focus": "Fix argument routing in `tenumerate` so custom start values are preserved.",
        "selection_reason": (
            "Chosen as an easy starter because it changes one line in one file, "
            "has a small relevant test surface, and the bug is easy to explain."
        ),
        "workflow_guidance": {
            "review_scope_note": (
                "Approve any patch plan that moves the start argument onto enumerate(...) and keeps "
                "the tqdm wrapper call intact."
            ),
            "validation_scope_note": (
                "Treat fixes as sufficient when they preserve tqdm wrapping behavior and make the "
                "enumeration start value come from the caller."
            ),
            "acceptance_signal_groups": [
                ["tenumerate", "tqdm.contrib"],
                ["enumerate"],
                ["start"],
            ],
        },
    },
    {
        "instance_id": "bugsinpy-tqdm-4",
        "bugsinpy_bug_id": "4",
        "repo": "tqdm/tqdm",
        "base_commit": "03b347646492131d889871939b40457d29147216",
        "fixed_commit": "964dee631d0ed30e2f799b42fc58ba5e73795a08",
        "issue_url": "https://github.com/tqdm/tqdm/issues/742",
        "issue_summary": (
            "Guard format_meter so unit scaling does not break when total is unknown."
        ),
        "problem_statement": (
            "Formatting a progress bar with `unit_scale` can break when the total "
            "is unknown. `format_meter` currently multiplies `total` even when "
            "there is no total value, instead of scaling only the quantities that exist."
        ),
        "hints_text": (
            "Look at `tqdm/_tqdm.py` inside `format_meter`. The bug lives in the "
            "no-total formatting path, so focus on the `format_meter` and `si_format` "
            "assertions in `tqdm/tests/tests_tqdm.py`."
        ),
        "patch": (
            "diff --git a/tqdm/_tqdm.py b/tqdm/_tqdm.py\n"
            "index df6414e..ea58409 100755\n"
            "--- a/tqdm/_tqdm.py\n"
            "+++ b/tqdm/_tqdm.py\n"
            "@@ -320,7 +320,8 @@ class tqdm(Comparable):\n"
            " \n"
            "         # apply custom scale if necessary\n"
            "         if unit_scale and unit_scale not in (True, 1):\n"
            "-            total *= unit_scale\n"
            "+            if total:\n"
            "+                total *= unit_scale\n"
            "             n *= unit_scale\n"
            "             if rate:\n"
            "                 rate *= unit_scale  # by default rate = 1 / self.avg_time\n"
        ),
        "test_patch": "",
        "suggested_test_commands": [
            "python3 -m pytest tqdm/tests/tests_tqdm.py::test_format_meter tqdm/tests/tests_tqdm.py::test_si_format",
        ],
        "target_test_file": "tqdm/tests/tests_tqdm.py",
        "difficulty_band": "easy",
        "difficulty_label": "Easy",
        "task_focus": "Guard a no-total edge case in `format_meter`.",
        "selection_reason": (
            "Chosen as an easy task because it is a single-file edge-case fix with "
            "a tiny patch and a clear failing test."
        ),
        "workflow_guidance": {
            "review_scope_note": (
                "For this exercise, any localized fix that guards total-scaling when total is unknown is "
                "acceptable. Do not reject a correct plan just because it uses `if total is not None` "
                "instead of the benchmark's smaller truthiness guard."
            ),
            "validation_scope_note": (
                "PASS when the proposal keeps scaling for n/rate, limits the change to format_meter, and "
                "prevents total from being scaled when total is missing."
            ),
            "acceptance_signal_groups": [
                ["format_meter", "tqdm/_tqdm.py"],
                ["unit_scale"],
                ["total"],
                ["unknown total", "no-total", "none", "missing total"],
                ["if total", "if total is not none", "guard"],
            ],
        },
    },
    {
        "instance_id": "bugsinpy-tqdm-2",
        "bugsinpy_bug_id": "2",
        "repo": "tqdm/tqdm",
        "base_commit": "bef86db56654d271838b145ad77f7040a73a7b4d",
        "fixed_commit": "127af5caf19e7d29c346f5ca8a9c7ef3004b664b",
        "issue_url": "https://github.com/tqdm/tqdm/issues/716",
        "issue_summary": (
            "Fix tqdm's width trimming so ANSI-colored output is trimmed safely without stray reset codes."
        ),
        "problem_statement": (
            "When `format_meter` trims progress-bar output to `ncols`, ANSI-coloured "
            "strings can be trimmed incorrectly and may receive a stray reset code. "
            "The display trimming logic needs to stay correct for both plain text and ANSI output."
        ),
        "hints_text": (
            "This task spans `tqdm/std.py` and `tqdm/utils.py`. Pay attention to how "
            "`disp_trim` behaves after removing characters one-by-one from a string with ANSI escapes."
        ),
        "patch": (
            "diff --git a/tqdm/std.py b/tqdm/std.py\n"
            "index 0b57f31..14ab11a 100644\n"
            "--- a/tqdm/std.py\n"
            "+++ b/tqdm/std.py\n"
            "@@ -485,8 +485,7 @@ class tqdm(Comparable):\n"
            "             if not _is_ascii(full_bar.charset) and _is_ascii(bar_format):\n"
            "                 bar_format = _unicode(bar_format)\n"
            "             res = bar_format.format(bar=full_bar, **format_dict)\n"
            "-            if ncols:\n"
            "-                return disp_trim(res, ncols)\n"
            "+            return disp_trim(res, ncols) if ncols else res\n"
            " \n"
            "         elif bar_format:\n"
            "             # user-specified bar_format but no total\n"
            "@@ -502,8 +501,7 @@ class tqdm(Comparable):\n"
            "                 if ncols else 10,\n"
            "                 charset=Bar.BLANK)\n"
            "             res = bar_format.format(bar=full_bar, **format_dict)\n"
            "-            if ncols:\n"
            "-                return disp_trim(res, ncols)\n"
            "+            return disp_trim(res, ncols) if ncols else res\n"
            "         else:\n"
            "             # no total: no progressbar, ETA, just progress stats\n"
            "             return ((prefix + \": \") if prefix else '') + \\\n"
            "diff --git a/tqdm/utils.py b/tqdm/utils.py\n"
            "index 474b1c8..a9a42be 100644\n"
            "--- a/tqdm/utils.py\n"
            "+++ b/tqdm/utils.py\n"
            "@@ -360,8 +360,10 @@ def disp_trim(data, length):\n"
            "     if len(data) == disp_len(data):\n"
            "         return data[:length]\n"
            " \n"
            "+    ansi_present = bool(RE_ANSI.search(data))\n"
            "     while disp_len(data) > length:  # carefully delete one char at a time\n"
            "         data = data[:-1]\n"
            "-    if RE_ANSI.search(data):  # assume ANSI reset is required\n"
            "-        return data + \"\\033[0m\"\n"
            "+    if ansi_present and bool(RE_ANSI.search(data)):\n"
            "+        # assume ANSI reset is required\n"
            "+        return data if data.endswith(\"\\033[0m\") else data + \"\\033[0m\"\n"
            "     return data\n"
        ),
        "test_patch": "",
        "suggested_test_commands": [
            "python3 -m pytest tqdm/tests/tests_tqdm.py::test_format_meter",
        ],
        "target_test_file": "tqdm/tests/tests_tqdm.py",
        "difficulty_band": "moderate",
        "difficulty_label": "Medium",
        "task_focus": "Repair width-trimming and ANSI reset handling.",
        "selection_reason": (
            "Chosen as a slightly harder task because it spans two files and mixes "
            "display formatting with ANSI-string edge cases."
        ),
        "workflow_guidance": {
            "review_scope_note": (
                "Keep review scoped to the benchmark-sized fix: the change should update disp_trim's reset "
                "handling and the std.py ncols call sites. Do not require a full ANSI parser rewrite when the "
                "proposal already matches the intended benchmark scope."
            ),
            "validation_scope_note": (
                "PASS when the proposal covers the two-file benchmark fix: only trim through disp_trim when "
                "ncols is set, and avoid stray ANSI resets in disp_trim without demanding a broader redesign."
            ),
            "acceptance_signal_groups": [
                ["disp_trim", "tqdm/utils.py", "utils.py"],
                ["std.py", "tqdm/std.py", "format_meter"],
                ["ansi", "reset"],
                ["ncols", "disp_trim(res, ncols) if ncols else res", "only trim when ncols is set"],
                ["ansi_present", "stray reset", "already reset", "append a reset only when needed"],
            ],
        },
    },
    {
        "instance_id": "bugsinpy-tqdm-5",
        "bugsinpy_bug_id": "5",
        "repo": "tqdm/tqdm",
        "base_commit": "19b08ab34fdbfa0275bc5cb2430436c724c7e759",
        "fixed_commit": "4f340697af69b71850aad496387c9c5aa1904136",
        "issue_url": "https://github.com/tqdm/tqdm/issues/539",
        "issue_summary": (
            "Make disabled `tqdm` instances keep consistent internal total state instead of "
            "returning before `self.total` is set."
        ),
        "problem_statement": (
            "When `disable=True`, `tqdm` returns early before it finishes preparing the "
            "instance state. That means disabled bars can skip total inference and may never "
            "set `self.total`, leaving disabled objects behaviorally inconsistent and prone to "
            "attribute errors in code paths that still inspect `total`."
        ),
        "hints_text": (
            "Inspect the `__init__` flow in `tqdm/_tqdm.py`. The bug is about what state gets "
            "prepared before the early return for disabled bars, especially around inferred "
            "totals and `self.total`."
        ),
        "patch": (
            "diff --git a/tqdm/_tqdm.py b/tqdm/_tqdm.py\n"
            "index f0261da..2ab2854 100755\n"
            "--- a/tqdm/_tqdm.py\n"
            "+++ b/tqdm/_tqdm.py\n"
            "@@ -748,12 +748,19 @@ class tqdm(Comparable):\n"
            "         if disable is None and hasattr(file, \"isatty\") and not file.isatty():\n"
            "             disable = True\n"
            " \n"
            "+        if total is None and iterable is not None:\n"
            "+            try:\n"
            "+                total = len(iterable)\n"
            "+            except (TypeError, AttributeError):\n"
            "+                total = None\n"
            "+\n"
            "         if disable:\n"
            "             self.iterable = iterable\n"
            "             self.disable = disable\n"
            "             self.pos = self._get_free_pos(self)\n"
            "             self._instances.remove(self)\n"
            "             self.n = initial\n"
            "+            self.total = total\n"
            "             return\n"
            " \n"
            "         if kwargs:\n"
            "@@ -766,12 +773,6 @@ class tqdm(Comparable):\n"
            "                 else TqdmKeyError(\"Unknown argument(s): \" + str(kwargs)))\n"
            " \n"
            "         # Preprocess the arguments\n"
            "-        if total is None and iterable is not None:\n"
            "-            try:\n"
            "-                total = len(iterable)\n"
            "-            except (TypeError, AttributeError):\n"
            "-                total = None\n"
            "-\n"
            "         if ((ncols is None) and (file in (sys.stderr, sys.stdout))) or \\\n"
            "                 dynamic_ncols:  # pragma: no cover\n"
            "             if dynamic_ncols:\n"
        ),
        "test_patch": "",
        "suggested_test_commands": [
            "python3 -m pytest tqdm/tests/tests_tqdm.py::test_disable",
        ],
        "target_test_file": "tqdm/tests/tests_tqdm.py",
        "difficulty_band": "moderate",
        "difficulty_label": "Medium",
        "task_focus": "Repair disabled-bar initialization so `self.total` stays available and consistent.",
        "selection_reason": (
            "Chosen as a slightly harder task because it requires reasoning about constructor "
            "order, early returns, and object state consistency."
        ),
        "workflow_guidance": {
            "review_scope_note": (
                "Approve fixes that make disabled instances keep the same total-related state as enabled ones, "
                "including moving inference ahead of the early return and preserving `self.total`."
            ),
            "validation_scope_note": (
                "PASS when the proposal keeps disabled bars behaviorally aligned with enabled ones for total "
                "handling and avoids leaving `self.total` unset, even if no additional refactor is proposed."
            ),
            "acceptance_signal_groups": [
                ["disable", "disabled"],
                ["total"],
                ["len(iterable)", "infer total", "total is none"],
                ["self.total", "attribute error", "before the early return", "before disable return"],
            ],
        },
    },
]


def _changed_line_count(patch: str) -> int:
    count = 0
    for line in (patch or "").splitlines():
        if line.startswith(("diff --git", "index ", "---", "+++", "@@")):
            continue
        if line.startswith("+") or line.startswith("-"):
            count += 1
    return count


def _changed_files_from_patch(patch: str) -> List[str]:
    files: List[str] = []
    for line in (patch or "").splitlines():
        if not line.startswith("+++ b/"):
            continue
        candidate = line.removeprefix("+++ b/").strip()
        if candidate == "/dev/null" or candidate in files:
            continue
        files.append(candidate)
    return files


def _python_affinity(changed_files: Iterable[str]) -> float:
    files = list(changed_files)
    if not files:
        return 0.0
    python_like = 0
    for path in files:
        suffix = Path(path).suffix.lower()
        if suffix in PYTHON_FRIENDLY_SUFFIXES or suffix == "":
            python_like += 1
    return round(python_like / len(files), 3)


def _difficulty_band(patch_lines: int, file_count: int, issue_len: int) -> str:
    if patch_lines <= 30 and file_count <= 2 and issue_len <= 800:
        return "intro"
    if patch_lines <= 80 and file_count <= 4 and issue_len <= 1600:
        return "easy"
    if patch_lines <= 150 and file_count <= 6:
        return "moderate"
    return "stretch"


@lru_cache(maxsize=8)
def _load_split(dataset_name: str, split: str):
    if load_dataset is None:
        raise RuntimeError(
            "The datasets package is not installed. Install dependencies before loading SWE-bench tasks."
        )
    return load_dataset(dataset_name, split=split)


def _build_issue_url(repo: str, instance_id: str) -> str:
    issue_suffix = instance_id.rsplit("-", 1)[-1]
    if not issue_suffix.isdigit():
        return ""
    return f"https://github.com/{repo}/issues/{issue_suffix}"


def _resolve_issue_url(row: Dict[str, Any]) -> str:
    for key in ("issue_url", "issue_link", "problem_url", "problem_statement_url", "html_url"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return _build_issue_url(row["repo"], row["instance_id"])


def _task_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    repo = row["repo"]
    instance_id = row["instance_id"]
    base_commit = row["base_commit"]
    patch = row.get("patch", "")
    changed_files = _changed_files_from_patch(patch)
    changed_lines = _changed_line_count(patch)
    issue_text = (row.get("problem_statement") or "").strip()
    hints_text = (row.get("hints_text") or "").strip()
    python_affinity = _python_affinity(changed_files)
    suggested_test_commands = suggest_test_commands(
        patch=row.get("patch", ""),
        test_patch=row.get("test_patch", ""),
    )
    difficulty = _difficulty_band(
        patch_lines=changed_lines,
        file_count=len(changed_files),
        issue_len=len(issue_text),
    )

    return {
        "instance_id": instance_id,
        "repo": repo,
        "base_commit": base_commit,
        "problem_statement": issue_text,
        "hints_text": hints_text,
        "created_at": row.get("created_at", ""),
        "version": row.get("version", ""),
        "patch": patch,
        "test_patch": row.get("test_patch", ""),
        "issue_url": _resolve_issue_url(row),
        "repo_url": f"https://github.com/{repo}",
        "commit_url": f"https://github.com/{repo}/commit/{base_commit}",
        "patch_changed_lines": changed_lines,
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "suggested_test_commands": suggested_test_commands,
        "educational_fit": {
            "difficulty_band": difficulty,
            "python_affinity": python_affinity,
            "selection_reason": (
                "Ranked for small diffs, short issue descriptions, few edited files, "
                "and strong Python-file affinity."
            ),
        },
    }


def _curated_tqdm_task_payload(spec: Dict[str, Any]) -> Dict[str, Any]:
    patch = spec["patch"]
    changed_files = _changed_files_from_patch(patch)
    changed_lines = _changed_line_count(patch)
    repo = spec["repo"]
    base_commit = spec["base_commit"]
    fixed_commit = spec["fixed_commit"]
    benchmark_bug_id = str(spec["bugsinpy_bug_id"])
    benchmark_url = f"https://github.com/soarsmu/BugsInPy/tree/master/projects/tqdm/bugs/{benchmark_bug_id}"
    compare_url = f"https://github.com/{repo}/compare/{base_commit}...{fixed_commit}"

    return {
        "instance_id": spec["instance_id"],
        "repo": repo,
        "base_commit": base_commit,
        "fixed_commit": fixed_commit,
        "problem_statement": spec["problem_statement"],
        "issue_summary": spec.get("issue_summary", spec["problem_statement"]),
        "hints_text": spec.get("hints_text", ""),
        "created_at": "",
        "version": "BugsInPy",
        "patch": patch,
        "test_patch": spec.get("test_patch", ""),
        "issue_url": spec.get("issue_url", benchmark_url),
        "repo_url": f"https://github.com/{repo}",
        "commit_url": f"https://github.com/{repo}/commit/{base_commit}",
        "fixed_commit_url": f"https://github.com/{repo}/commit/{fixed_commit}",
        "compare_url": compare_url,
        "benchmark_url": benchmark_url,
        "benchmark_suite": "BugsInPy",
        "benchmark_project": "tqdm",
        "benchmark_bug_id": benchmark_bug_id,
        "patch_changed_lines": changed_lines,
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "suggested_test_commands": list(spec.get("suggested_test_commands") or []),
        "target_test_file": spec.get("target_test_file", ""),
        "task_focus": spec.get("task_focus", ""),
        "workflow_guidance": spec.get("workflow_guidance", {}),
        "educational_fit": {
            "difficulty_band": spec["difficulty_band"],
            "difficulty_label": spec.get("difficulty_label", spec["difficulty_band"].title()),
            "python_affinity": _python_affinity(changed_files),
            "selection_reason": spec["selection_reason"],
        },
    }


@lru_cache(maxsize=1)
def curated_bugsinpy_tqdm_tasks() -> List[Dict[str, Any]]:
    return [_curated_tqdm_task_payload(spec) for spec in CURATED_TQDM_TASK_SPECS]


def curated_bugsinpy_tqdm_task_by_instance(instance_id: str) -> Dict[str, Any]:
    normalized = (instance_id or "").strip()
    for task in curated_bugsinpy_tqdm_tasks():
        if task["instance_id"] == normalized:
            return task
    raise ValueError(f"Task not found: {instance_id}")


def get_task_by_instance(
    *,
    instance_id: str,
    dataset_name: str,
    split: str,
) -> Dict[str, Any]:
    ds = _load_split(dataset_name, split)
    for row in ds:
        if row["instance_id"] == instance_id:
            return _task_payload(dict(row))
    raise ValueError(f"Task not found: {instance_id}")


def choose_easiest_python_task(
    *,
    dataset_name: str,
    split: str,
) -> Dict[str, Any]:
    return choose_n_easiest_python_tasks(n=1, dataset_name=dataset_name, split=split)[0]


def choose_n_easiest_python_tasks(
    *,
    n: int,
    dataset_name: str,
    split: str,
) -> List[Dict[str, Any]]:
    """Return educationally approachable SWE-bench tasks.

    The ranker intentionally favors beginner-friendly tasks:
    mostly Python file edits, small patches, few touched files, and concise issues.
    """

    ds = _load_split(dataset_name, split)
    ranked: List[Dict[str, Any]] = []

    for row in ds:
        payload = _task_payload(dict(row))
        issue_len = len(payload["problem_statement"])
        hints_len = len(payload.get("hints_text", ""))
        patch_lines = payload["patch_changed_lines"]
        file_count = payload["changed_file_count"]
        python_penalty = 0 if payload["educational_fit"]["python_affinity"] >= 0.8 else 1
        stretch_penalty = 0 if payload["educational_fit"]["difficulty_band"] in {"intro", "easy"} else 1

        ranked.append(
            {
                "score": (
                    stretch_penalty,
                    python_penalty,
                    patch_lines,
                    file_count,
                    issue_len,
                    hints_len,
                ),
                "payload": payload,
            }
        )

    if not ranked:
        raise RuntimeError("Dataset appears to be empty")

    ranked.sort(key=lambda item: item["score"])
    return [item["payload"] for item in ranked[:n]]


def suggest_test_commands(*, patch: str, test_patch: str) -> List[str]:
    changed_test_files = [
        path for path in _changed_files_from_patch(test_patch)
        if Path(path).name.startswith("test") or "/tests/" in f"/{path}/" or path.endswith("_test.py")
    ]
    suggestions: List[str] = []

    if changed_test_files:
        suggestions.append("python3 -m pytest " + " ".join(changed_test_files[:4]))

    changed_files = _changed_files_from_patch(patch)
    python_affinity = _python_affinity(changed_files)
    if python_affinity >= 0.5:
        suggestions.extend(
            [
                "python -m pytest",
                "python3 -m pytest",
                "pytest",
            ]
        )
    else:
        suggestions.append("python -m unittest")

    deduped: List[str] = []
    for command in suggestions:
        if command not in deduped:
            deduped.append(command)
    return deduped


def checkout_task_repo(task: Dict[str, Any], repos_root: Path) -> Path:
    repo = task["repo"]
    base_commit = task["base_commit"]
    repo_name = repo.replace("/", "__")
    dest = repos_root / f"{repo_name}_{base_commit[:8]}"

    repos_root.mkdir(parents=True, exist_ok=True)
    repo_url = f"https://github.com/{repo}.git"
    if (dest / ".git").exists():
        if _repo_is_clean_checkout(dest, base_commit):
            return dest
        shutil.rmtree(dest)

    _clone_repo_at_commit(repo_url, dest, base_commit)

    return dest


def _repo_is_clean_checkout(repo_path: Path, base_commit: str) -> bool:
    try:
        head_run = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        status_run = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--short"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.SubprocessError:
        return False
    return head_run.stdout.strip() == base_commit and not status_run.stdout.strip()


def _clone_repo_at_commit(repo_url: str, dest: Path, base_commit: str) -> None:
    subprocess.run(
        ["git", "clone", "--no-checkout", "--filter=blob:none", repo_url, str(dest)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    subprocess.run(
        ["git", "-C", str(dest), "checkout", base_commit],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def create_session_repo_copy(cache_repo_path: Path, workspaces_root: Path) -> Path:
    cache_repo_path = cache_repo_path.resolve()
    if not (cache_repo_path / ".git").exists():
        raise ValueError(f"Cache repo is missing .git metadata: {cache_repo_path}")

    workspaces_root.mkdir(parents=True, exist_ok=True)
    workspace_str = tempfile.mkdtemp(
        prefix=f"{cache_repo_path.name}_session_",
        dir=str(workspaces_root),
    )
    workspace_path = Path(workspace_str)
    shutil.rmtree(workspace_path)
    shutil.copytree(cache_repo_path, workspace_path, symlinks=True)
    return workspace_path


def _resolve_repo_path(repo_path: Path, relative_path: str) -> Path:
    rel = relative_path.strip().lstrip("/")
    target = (repo_path / rel).resolve()
    if repo_path.resolve() not in target.parents and target != repo_path.resolve():
        raise ValueError("Invalid path")
    return target


def list_repo_tree(repo_path: Path, relative_path: str = "", limit: int = 500) -> List[Dict[str, Any]]:
    target = _resolve_repo_path(repo_path, relative_path)
    if not target.exists() or not target.is_dir():
        raise ValueError(f"Directory not found: {relative_path}")

    items: List[Dict[str, Any]] = []
    for entry in sorted(os.scandir(target), key=lambda item: (not item.is_dir(), item.name.lower())):
        if entry.name == ".git":
            continue
        rel = str(Path(entry.path).resolve().relative_to(repo_path.resolve()))
        items.append(
            {
                "name": entry.name,
                "path": rel,
                "type": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
            }
        )
        if len(items) >= limit:
            break
    return items


def read_repo_file(repo_path: Path, relative_path: str, max_chars: int = 200_000) -> Dict[str, Any]:
    target = _resolve_repo_path(repo_path, relative_path)
    if not target.exists() or not target.is_file():
        raise ValueError(f"File not found: {relative_path}")

    raw = target.read_text(encoding="utf-8", errors="replace")
    truncated = len(raw) > max_chars
    return {
        "path": relative_path,
        "content": raw[:max_chars],
        "truncated": truncated,
        "full_size": len(raw),
    }


def apply_patch_to_repo(repo_path: Path, patch: str) -> Dict[str, Any]:
    if not patch.strip():
        raise ValueError("Patch is empty")

    repo_path = repo_path.resolve()
    _ = _resolve_repo_path(repo_path, "")
    check_cmd = [
        "git",
        "-C",
        str(repo_path),
        "apply",
        "--check",
        "--recount",
        "--whitespace=nowarn",
        "-",
    ]
    apply_cmd = [
        "git",
        "-C",
        str(repo_path),
        "apply",
        "--recount",
        "--whitespace=nowarn",
        "-",
    ]

    check_run = subprocess.run(
        check_cmd,
        input=patch,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check_run.returncode != 0:
        return {
            "applied": False,
            "returncode": check_run.returncode,
            "stdout": check_run.stdout,
            "stderr": check_run.stderr,
            "changed_files": _changed_files_from_patch(patch),
        }

    apply_run = subprocess.run(
        apply_cmd,
        input=patch,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    diff_stat = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--stat"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return {
        "applied": apply_run.returncode == 0,
        "returncode": apply_run.returncode,
        "stdout": apply_run.stdout,
        "stderr": apply_run.stderr,
        "changed_files": _changed_files_from_patch(patch),
        "diff_stat": diff_stat.stdout.strip(),
    }


def run_test_command(repo_path: Path, command: str, timeout_seconds: int = 120) -> Dict[str, Any]:
    argv = _validate_test_command(command)
    repo_path = repo_path.resolve()
    _ = _resolve_repo_path(repo_path, "")

    try:
        completed, used_argv, used_command = _run_with_pytest_fallback(
            argv,
            command=command,
            repo_path=repo_path,
            timeout_seconds=timeout_seconds,
        )
        return {
            "command": used_command,
            "argv": used_argv,
            "exit_code": completed.returncode,
            "passed": completed.returncode == 0,
            "stdout": completed.stdout[-12000:],
            "stderr": completed.stderr[-12000:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "argv": argv,
            "exit_code": None,
            "passed": False,
            "stdout": (exc.stdout or "")[-12000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-12000:] if isinstance(exc.stderr, str) else "",
            "timed_out": True,
        }


def _run_with_pytest_fallback(
    argv: List[str],
    *,
    command: str,
    repo_path: Path,
    timeout_seconds: int,
):
    try:
        completed = subprocess.run(
            argv,
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return completed, argv, command
    except FileNotFoundError:
        fallback_argv = _pytest_module_fallback_argv(argv)
        if fallback_argv is None:
            raise
        completed = subprocess.run(
            fallback_argv,
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return completed, fallback_argv, shlex.join(fallback_argv)


def _pytest_module_fallback_argv(argv: List[str]) -> List[str] | None:
    if not argv:
        return None
    if argv[0] != "pytest":
        return None
    return ["python3", "-m", "pytest", *argv[1:]]


def _validate_test_command(command: str) -> List[str]:
    text = (command or "").strip()
    if not text:
        raise ValueError("Test command is empty")

    argv = shlex.split(text)
    if not argv:
        raise ValueError("Test command is empty")

    for prefix in SAFE_TEST_COMMAND_PREFIXES:
        if tuple(argv[: len(prefix)]) == prefix:
            return argv

    allowed = ", ".join(" ".join(prefix) for prefix in SAFE_TEST_COMMAND_PREFIXES)
    raise ValueError(f"Unsupported test command. Allowed prefixes: {allowed}")
