"""Setup of the browser tests: kept apart from the normal run, results not in git."""

import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def collect(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 (fixed arguments, no user input)
        [sys.executable, "-m", "pytest", "tests/browser", "--collect-only", "-q", *args],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout + result.stderr


# --- Test case 10: browser tests run apart from the normal run --------------------------


def test_normal_run_leaves_out_the_browser_tests() -> None:
    output = collect()

    assert "test_pages.py::" not in output
    assert "deselected" in output


def test_browser_marker_runs_only_the_browser_tests() -> None:
    output = collect("-m", "browser")

    assert "test_pages.py::" in output
    assert "test_browser_setup.py" not in output


# --- Test case 7: screenshots of failed tests stay out of git ---------------------------


def test_screenshot_folder_is_not_tracked_by_git() -> None:
    ignored = (REPO_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    docker_ignored = (REPO_DIR / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "/test-results/" in ignored
    assert "test-results" in docker_ignored
