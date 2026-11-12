"""Static checks of the container files (no Docker needed).

The files are checked as text on purpose: no YAML parser is a project dependency.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)", re.MULTILINE)


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_compose_drops_all_capabilities_and_blocks_privilege_gain() -> None:
    compose = read("compose.yml")

    assert re.search(r"cap_drop:\s*\n\s*-\s*ALL\b", compose)
    assert "no-new-privileges:true" in compose


def test_compose_filesystem_is_read_only_with_only_data_and_tmp_writable() -> None:
    compose = read("compose.yml")

    assert re.search(r"^\s*read_only:\s*true\s*$", compose, re.MULTILINE)
    assert re.search(r"tmpfs:\s*\n\s*-\s*/tmp\b", compose)
    volume_targets = re.findall(r"^\s*-\s*[\w.-]+:(/\S+)\s*$", compose, re.MULTILINE)
    assert volume_targets == ["/data"]


def test_compose_runs_as_normal_user_without_docker_socket() -> None:
    compose = read("compose.yml")

    match = re.search(r'^\s*user:\s*"?(\d+):(\d+)"?\s*$', compose, re.MULTILINE)
    assert match is not None
    assert int(match.group(1)) > 0
    assert "docker.sock" not in compose
    assert "privileged" not in compose


def test_compose_publishes_port_only_on_localhost() -> None:
    compose = read("compose.yml")

    block = re.search(r"^\s*ports:\s*\n((?:\s+-\s*.+\n)+)", compose, re.MULTILINE)
    assert block is not None
    ports = re.findall(r'-\s*"?([^"\s]+)"?', block.group(1))
    assert ports, "no published port found"
    assert all(port.startswith("127.0.0.1:") for port in ports)
    assert "0.0.0.0:" not in compose


def test_compose_takes_configuration_from_environment_file_only() -> None:
    compose = read("compose.yml")
    example = read(".env.example")

    assert "env_file" in compose
    assert not re.search(r"^\s*environment:", compose, re.MULTILINE)
    # Only placeholders in the example file: no assigned secret-looking values.
    assert not re.search(r"(?i)(key|token|secret|password)\s*=\s*\S", example)


def test_dockerfile_runs_as_normal_user_on_python_3_12_pinned_by_digest() -> None:
    dockerfile = read("Dockerfile")

    from_lines = re.findall(r"^FROM\s+(\S+)", dockerfile, re.MULTILINE)
    assert from_lines
    assert all(re.fullmatch(r"python:3\.12-slim@sha256:[0-9a-f]{64}", f) for f in from_lines)
    users = re.findall(r"^USER\s+(\S+)", dockerfile, re.MULTILINE)
    assert users
    assert users[-1] not in {"root", "0"}
    assert not users[-1].startswith("0:")


def test_dockerfile_installs_only_from_lockfile_without_dev_tools() -> None:
    dockerfile = read("Dockerfile")

    assert "uv sync" in dockerfile
    assert "--locked" in dockerfile
    assert "--no-dev" in dockerfile
    assert not re.search(r"COPY\s+.*\btests\b", dockerfile)
    assert not re.search(r"COPY\s+.*\.env", dockerfile)


def test_dockerfile_listens_on_all_interfaces_and_has_healthcheck() -> None:
    dockerfile = read("Dockerfile")

    assert "HEALTHCHECK" in dockerfile
    assert "/healthz" in dockerfile
    assert "0.0.0.0" in dockerfile  # noqa: S104


@pytest.mark.parametrize("pattern", [".git", ".env", "tests", ".venv"])
def test_dockerignore_excludes_sensitive_and_unneeded_files(pattern: str) -> None:
    lines = {line.strip().rstrip("/") for line in read(".dockerignore").splitlines()}

    assert pattern in lines or f"{pattern}*" in lines or f"{pattern}.*" in lines


def test_dockerignore_excludes_env_files_except_env_example() -> None:
    dockerignore = read(".dockerignore")

    assert re.search(r"^\.env\*$", dockerignore, re.MULTILINE)
    assert re.search(r"^!\.env\.example$", dockerignore, re.MULTILINE)


def workflow_job(name: str) -> str:
    """Return the text of one job of the image workflow."""
    workflow = read(".github/workflows/docker.yml")
    match = re.search(
        rf"^  {name}:\n(.*?)(?=^  \w[\w-]*:\n|\Z)", workflow, re.MULTILINE | re.DOTALL
    )
    assert match is not None, f"job {name} not found"
    return match.group(1)


def test_image_workflow_default_token_is_read_only_and_actions_are_pinned() -> None:
    workflow = read(".github/workflows/docker.yml")
    top_level = workflow.split("\njobs:\n")[0]

    assert re.search(r"^permissions:\s*\n\s+contents:\s*read\s*$", top_level, re.MULTILINE)
    assert "write" not in re.sub(r"#.*", "", top_level)
    uses = USES_LINE.findall(workflow)
    assert uses
    for reference in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", reference), reference


def test_only_the_publish_job_may_write_packages() -> None:
    workflow = re.sub(r"#.*", "", read(".github/workflows/docker.yml"))

    assert workflow.count("packages: write") == 1
    assert "packages: write" in workflow_job("publish")
    assert "write" not in workflow_job("image")
    assert not re.search(r"(?i)\w+:\s*write", workflow.replace("packages: write", ""))


def test_build_job_does_not_publish_or_log_in() -> None:
    job = workflow_job("image")

    assert re.search(r"^\s*push:\s*false\s*$", job, re.MULTILINE)
    assert "docker/login-action" not in job
    assert "docker push" not in job
    assert not re.search(r"ghcr\.io", job)


def test_publish_runs_only_for_push_to_main_never_for_pull_requests() -> None:
    job = workflow_job("publish")

    assert re.search(
        r"^\s*if:\s*github\.event_name == 'push' && github\.ref == 'refs/heads/main'\s*$",
        job,
        re.MULTILINE,
    )
    assert "pull_request" not in job


def test_publish_waits_for_green_build_and_trivy() -> None:
    job = workflow_job("publish")

    assert re.search(r"^\s*needs:\s*image\s*$", job, re.MULTILINE)
    # The scan step must come before the image is handed over to the publish job.
    image_job = workflow_job("image")
    assert image_job.index("aquasecurity/trivy-action@") < image_job.index("upload-artifact@")


def test_publish_uses_the_scanned_image_not_a_second_build() -> None:
    publish = workflow_job("publish")
    image_job = workflow_job("image")

    assert "docker save" in image_job
    assert "actions/download-artifact@" in publish
    assert "docker load" in publish
    assert "build-push-action" not in publish
    assert "docker build" not in publish


def test_publish_tags_are_latest_and_short_commit_hash_only() -> None:
    job = workflow_job("publish")

    assert re.search(r"GITHUB_REPOSITORY,,", job)  # lower-case repository name
    assert re.search(r'IMAGE="ghcr\.io/\$\{GITHUB_REPOSITORY,,\}"', job)
    assert re.search(r"GITHUB_SHA::7", job)
    pushes = re.findall(r"docker push (\S+)", job)
    assert sorted(pushes) == ['"$IMAGE:$SHORT_SHA"', '"$IMAGE:latest"']


def test_publish_login_uses_only_the_automatic_token() -> None:
    job = workflow_job("publish")

    assert "docker/login-action@" in job
    assert "secrets.GITHUB_TOKEN" in job
    assert not re.search(r"secrets\.(?!GITHUB_TOKEN)\w+", job)


def test_published_image_links_back_to_the_repository() -> None:
    job = workflow_job("image")

    assert "org.opencontainers.image.source=https://github.com/${{ github.repository }}" in job


def test_compose_uses_published_image_when_selected_and_builds_locally_by_default() -> None:
    compose = read("compose.yml")

    assert "build: ." in compose
    assert "${MAKERHUB_IMAGE:-makerhub3d:local}" in compose
    assert "MAKERHUB_IMAGE" in read(".env.example")


def test_instructions_for_starting_and_rolling_back_a_version_exist() -> None:
    guide = read("docs/image-starten.md")

    assert "MAKERHUB_IMAGE" in guide
    assert "latest" in guide
    assert "docker compose pull" in guide
    assert "--no-build" in guide


def test_trivy_fails_on_fixable_high_and_critical_findings() -> None:
    workflow = read(".github/workflows/docker.yml")

    assert "aquasecurity/trivy-action@" in workflow
    assert re.search(r"severity:\s*\"?HIGH,CRITICAL\"?", workflow)
    assert re.search(r"ignore-unfixed:\s*\"?true\"?", workflow)
    assert re.search(r"exit-code:\s*\"?1\"?", workflow)


def test_dependabot_watches_docker_base_image() -> None:
    dependabot = read(".github/dependabot.yml")

    assert re.search(r'package-ecosystem:\s*"docker"', dependabot)


def test_dockerfile_applies_operating_system_security_updates_in_final_image() -> None:
    dockerfile = read("Dockerfile")
    final_stage = dockerfile.rsplit("\nFROM ", maxsplit=1)[-1]

    assert "apt-get upgrade" in final_stage
    assert "rm -rf /var/lib/apt/lists" in final_stage


def dependabot_blocks() -> dict[str, str]:
    """Split dependabot.yml into one text block per package ecosystem."""
    dependabot = read(".github/dependabot.yml")
    parts = re.split(r'(?m)^  - package-ecosystem:\s*"([^"]+)"', dependabot)
    # parts = [header, name1, block1, name2, block2, ...]
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def test_dependabot_docker_ignores_python_major_and_minor_updates() -> None:
    docker = dependabot_blocks()["docker"]

    ignore = re.search(
        r'ignore:\s*\n\s*-\s*dependency-name:\s*"python"\s*\n'
        r"\s*update-types:\s*\n"
        r'\s*-\s*"version-update:semver-major"\s*\n'
        r'\s*-\s*"version-update:semver-minor"',
        docker,
    )
    assert ignore


def test_dependabot_other_ecosystems_have_no_ignore_rule() -> None:
    blocks = dependabot_blocks()

    assert set(blocks) == {"uv", "github-actions", "docker"}
    for name in ("uv", "github-actions"):
        assert "ignore:" not in blocks[name]


def test_dependabot_docker_stays_weekly_and_grouped() -> None:
    docker = dependabot_blocks()["docker"]

    assert re.search(r'interval:\s*"weekly"', docker)
    assert re.search(r"groups:\s*\n\s*docker-base-image:", docker)
