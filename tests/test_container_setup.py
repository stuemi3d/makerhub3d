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


def test_image_workflow_has_read_only_token_and_pinned_actions() -> None:
    workflow = read(".github/workflows/docker.yml")

    assert re.search(r"^permissions:\s*\n\s+contents:\s*read\s*$", workflow, re.MULTILINE)
    assert "write" not in re.sub(r"#.*", "", workflow)
    uses = USES_LINE.findall(workflow)
    assert uses
    for reference in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", reference), reference


def test_image_workflow_builds_without_publishing() -> None:
    workflow = read(".github/workflows/docker.yml")

    assert re.search(r"^\s*push:\s*false\s*$", workflow, re.MULTILINE)
    assert "registry" not in workflow.lower()
    assert "docker login" not in workflow


def test_trivy_fails_on_fixable_high_and_critical_findings() -> None:
    workflow = read(".github/workflows/docker.yml")

    assert "aquasecurity/trivy-action@" in workflow
    assert re.search(r"severity:\s*\"?HIGH,CRITICAL\"?", workflow)
    assert re.search(r"ignore-unfixed:\s*\"?true\"?", workflow)
    assert re.search(r"exit-code:\s*\"?1\"?", workflow)


def test_dependabot_watches_docker_base_image() -> None:
    dependabot = read(".github/dependabot.yml")

    assert re.search(r'package-ecosystem:\s*"docker"', dependabot)
