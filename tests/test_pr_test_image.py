"""Test image per pull request and the script to start it (issue #198)."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "testen.sh"
IMAGE = "ghcr.io/example-owner/makerhub3d"
ENV_TEXT = f"MAKERHUB_PORT=8000\nMAKERHUB_IMAGE={IMAGE}:latest\nMAKERHUB_SECRET_KEY=abc\n"


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def job(name: str) -> str:
    workflow = read(".github/workflows/docker.yml")
    match = re.search(
        rf"^  {name}:\n(.*?)(?=^  \w[\w-]*:\n|\Z)", workflow, re.MULTILINE | re.DOTALL
    )
    assert match is not None, f"job {name} not found"
    return match.group(1)


# --- Test cases 1-6: the workflows ------------------------------------------------------


def test_1_pull_requests_of_this_repository_publish_pr_number_tag() -> None:
    publish = job("publish-pr")

    assert "needs: image" in publish  # only after build, health check and Trivy
    assert "github.event_name == 'pull_request'" in publish
    assert "github.event.pull_request.number" in publish
    assert "docker push" in publish


def test_2_the_tag_is_overwritten_not_multiplied() -> None:
    pushes = re.findall(r"docker push (\S+)", job("publish-pr"))

    assert pushes == ['"$IMAGE:pr-${PR_NUMBER}"']


def test_3_latest_and_commit_tags_stay_with_main() -> None:
    publish = job("publish-pr")

    assert "latest" not in publish
    assert "GITHUB_SHA" not in publish
    assert "github.ref == 'refs/heads/main'" in job("publish")


def test_4_forks_and_failed_scans_publish_nothing() -> None:
    publish = job("publish-pr")

    assert "github.event.pull_request.head.repo.full_name == github.repository" in publish
    assert "needs: image" in publish  # the image job fails on Trivy findings
    assert "github.actor != 'dependabot[bot]'" in publish  # no write token for Dependabot


def test_5_only_the_publish_jobs_have_write_access_and_only_to_packages() -> None:
    workflow = re.sub(r"#.*", "", read(".github/workflows/docker.yml"))
    publish = job("publish-pr")

    assert "packages: write" in publish
    assert "contents: read" in publish
    assert not re.search(r"(?i)\w+:\s*write", publish.replace("packages: write", ""))
    assert "write" not in job("image")
    assert "secrets.GITHUB_TOKEN" in publish
    assert not re.search(r"secrets\.(?!GITHUB_TOKEN)\w+", workflow)
    assert "pull_request_target" not in workflow


CLEANUP = ".github/workflows/docker-pr-cleanup.yml"


def test_6_closing_a_pull_request_deletes_its_test_image_only() -> None:
    cleanup = re.sub(r"#.*", "", read(CLEANUP))

    assert re.search(r"pull_request:\s*\n\s*types:\s*\[closed\]", cleanup)
    assert "pull_request_target" not in cleanup
    assert "github.event.pull_request.head.repo.full_name == github.repository" in cleanup
    assert "pr-${PR_NUMBER}" in cleanup or "pr-$PR_NUMBER" in cleanup
    assert "latest" not in cleanup
    assert ".metadata.container.tags == [$tag]" in cleanup  # exactly this one tag
    assert cleanup.count("packages: write") == 1
    assert not re.search(r"(?i)\w+:\s*write", cleanup.replace("packages: write", ""))
    assert "actions/checkout" not in cleanup  # never runs code of the pull request


def test_6_all_actions_in_the_workflows_are_pinned_by_hash() -> None:
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        for reference in re.findall(r"^\s*(?:-\s*)?uses:\s*(\S+)", path.read_text(), re.MULTILINE):
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", reference), (path.name, reference)


# --- Test cases 7-12: the script, with a fake docker -----------------------------------


@pytest.fixture
def server(tmp_path: Path) -> Path:
    """A folder like the server's (compose file, .env, scripts/) and a fake `docker`."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "testen.sh")
    (tmp_path / ".env").write_text(ENV_TEXT, encoding="utf-8")
    os.chmod(tmp_path / ".env", 0o600)
    (tmp_path / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$FAKE_LOG"\n'
        'if [ "$2" = up ] && [ -n "$FAKE_UP_FAILS" ]; then exit 1; fi\n'
        'if [ "$2" = pull ] && [ -n "$FAKE_PULL_FAILS" ]; then echo "pull failed" >&2; exit 1; fi\n'
        'if [ "$2" = ps ]; then echo "app  running (healthy)"; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return tmp_path


def run(
    server: Path, *args: str, pull_fails: bool = False, up_fails: bool = False
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{server / 'bin'}:/usr/bin:/bin",
        "FAKE_LOG": str(server / "docker.log"),
        "HOME": str(server),
    }
    if pull_fails:
        env["FAKE_PULL_FAILS"] = "1"
    if up_fails:
        env["FAKE_UP_FAILS"] = "1"
    return subprocess.run(  # noqa: S603 - fixed script path, fake docker
        ["/usr/bin/env", "bash", str(server / "scripts" / "testen.sh"), *args],
        cwd=server / "scripts",  # works from any folder
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def calls(server: Path) -> list[str]:
    log = server / "docker.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def env_text(server: Path) -> str:
    return (server / ".env").read_text(encoding="utf-8")


def test_7_number_sets_tag_pulls_restarts_and_reports(server: Path) -> None:
    result = run(server, "198")

    assert result.returncode == 0, result.stderr
    assert f"MAKERHUB_IMAGE={IMAGE}:pr-198\n" in env_text(server)
    assert calls(server)[:2] == ["compose pull", "compose up -d --no-build"]
    assert "pr-198" in result.stdout


def test_8_latest_and_no_argument_select_latest(server: Path) -> None:
    run(server, "198")
    for args in (("latest",), ()):
        run(server, "7")
        result = run(server, *args)
        assert result.returncode == 0, result.stderr
        assert f"MAKERHUB_IMAGE={IMAGE}:latest\n" in env_text(server)


@pytest.mark.parametrize(
    "argument", ["abc", "19 8", "198; rm -rf /", "$(id)", "-1", "pr-198", "", "1" * 12, "../x"]
)
def test_9_other_input_is_refused_and_changes_nothing(server: Path, argument: str) -> None:
    result = run(server, argument)

    assert result.returncode != 0
    assert env_text(server) == ENV_TEXT
    assert calls(server) == []


def test_9_several_arguments_are_refused(server: Path) -> None:
    result = run(server, "1", "2")

    assert result.returncode != 0
    assert env_text(server) == ENV_TEXT
    assert calls(server) == []


def test_10_the_image_name_comes_from_the_env_and_a_missing_line_is_explained(
    server: Path,
) -> None:
    (server / ".env").write_text("MAKERHUB_PORT=8000\n# MAKERHUB_IMAGE=ghcr.io/OWNER/REPO:TAG\n")

    result = run(server, "198")

    assert result.returncode != 0
    assert "MAKERHUB_IMAGE" in result.stderr
    assert "Traceback" not in result.stderr
    assert calls(server) == []
    assert "pr-198" not in env_text(server)


def test_10_a_foreign_image_name_is_refused(server: Path) -> None:
    (server / ".env").write_text("MAKERHUB_IMAGE=evil.example/x/y:latest\n")

    assert run(server, "198").returncode != 0
    assert calls(server) == []


def test_11_other_lines_and_the_permissions_stay(server: Path) -> None:
    run(server, "198")

    lines = env_text(server).splitlines()
    assert "MAKERHUB_PORT=8000" in lines
    assert "MAKERHUB_SECRET_KEY=abc" in lines
    assert len(lines) == 3
    assert stat.S_IMODE((server / ".env").stat().st_mode) == 0o600
    assert [p.name for p in server.iterdir() if p.name.startswith(".env")] == [".env"]


def test_12_a_failing_pull_keeps_the_running_version_and_the_old_tag(server: Path) -> None:
    result = run(server, "198", pull_fails=True)

    assert result.returncode != 0
    assert calls(server) == ["compose pull"]  # no restart
    assert env_text(server) == ENV_TEXT


def test_script_is_executable_and_does_not_print_secrets(server: Path) -> None:
    result = run(server, "198")

    assert os.access(SCRIPT, os.X_OK)
    assert "abc" not in result.stdout + result.stderr  # the secret key line is never shown
    assert read("scripts/testen.sh").startswith("#!/usr/bin/env bash\n")
    assert "set -eu" in read("scripts/testen.sh")


# --- Test case 13: the guide -------------------------------------------------------------


def test_13_the_guide_explains_download_test_and_back() -> None:
    guide = read("docs/image-starten.md")

    for part in (
        "scripts/testen.sh",
        "chmod +x",
        "raw.githubusercontent.com",
        "./scripts/testen.sh latest",
        "pr-",
        "Sicherung",
        "automatisch gelöscht",
    ):
        assert part in guide


def test_12_a_failing_restart_puts_the_old_tag_back(server: Path) -> None:
    result = run(server, "198", up_fails=True)

    assert result.returncode != 0
    assert env_text(server) == ENV_TEXT


def test_the_env_is_untouched_while_the_pull_runs(server: Path) -> None:
    # The new tag is handed to the pull directly; .env only changes after it worked.
    script = read("scripts/testen.sh")

    assert script.index('MAKERHUB_IMAGE="$image:$tag" docker compose pull') < script.index(
        'write_tag "$tag"'
    )


def test_10_a_line_without_a_tag_is_refused(server: Path) -> None:
    (server / ".env").write_text(f"MAKERHUB_IMAGE={IMAGE}\n")

    result = run(server, "198")

    assert result.returncode != 0
    assert "Tag" in result.stderr
    assert calls(server) == []
    assert env_text(server) == f"MAKERHUB_IMAGE={IMAGE}\n"


def test_11_an_env_without_a_final_newline_works(server: Path) -> None:
    (server / ".env").write_text(f"MAKERHUB_PORT=8000\nMAKERHUB_IMAGE={IMAGE}:latest")

    assert run(server, "198").returncode == 0
    assert env_text(server) == f"MAKERHUB_PORT=8000\nMAKERHUB_IMAGE={IMAGE}:pr-198\n"


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
def test_6_the_cleanup_filter_picks_only_versions_with_exactly_the_pr_tag() -> None:
    cleanup = read(CLEANUP)
    expression = re.search(r"'(\.\[\] \| select\([^']*\) \| \.id)'", cleanup)
    assert expression is not None
    versions = (
        '[{"id":1,"metadata":{"container":{"tags":["pr-5"]}}},'
        '{"id":2,"metadata":{"container":{"tags":["pr-5","latest"]}}},'
        '{"id":3,"metadata":{"container":{"tags":["pr-50"]}}},'
        '{"id":4,"metadata":{"container":{"tags":[]}}},'
        '{"id":5,"metadata":{"container":{"tags":["abc1234"]}}}]'
    )

    result = subprocess.run(  # noqa: S603 - fixed jq call with test data
        ["/usr/bin/env", "jq", "-r", "--arg", "tag", "pr-5", expression.group(1)],
        input=versions,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.split() == ["1"]
