"""Temporary local-cache build path for Docker Hub metadata failures."""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

BASE = "python:3.12-slim"
IMAGE = "sip-lab-media:latest"
FILES = frozenset(
    "backend/app/" + name + ".py"
    for name in (
        "__init__",
        "sip_lab",
        "sip_lab_media",
        "sip_lab_bridge",
        "sip_lab_voice",
        "sip_lab_voice_session",
        "sip_lab_voice_config",
        "sip_lab_qwen_voice",
        "sip_lab_qwen_voice_probe",
        "sip_lab_qwen_probe",
    )
)


def stage_context(root, destination):
    root, destination = Path(root).resolve(), Path(destination)
    recipe = (root / "deploy/sip-lab/media.Dockerfile").read_text()
    if not recipe.startswith("FROM " + BASE + "\n"):
        raise ValueError("unsupported_media_recipe")
    sources = []
    for line in recipe.splitlines():
        if line.startswith("COPY "):
            words = shlex.split(line)
            if words[-1] != "./app/" or any(name not in FILES for name in words[1:-1]):
                raise ValueError("unsupported_media_copy")
            sources.extend(words[1:-1])
    if len(sources) != len(FILES) or set(sources) != FILES:
        raise ValueError("incomplete_media_copy")
    for name in sources:
        source = root / name
        if (
            source.is_symlink()
            or not source.resolve().is_relative_to(root)
            or not source.is_file()
        ):
            raise ValueError("invalid_media_source")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (destination / "Dockerfile").write_text(recipe)


def build(root, *, runner=subprocess.run):
    inspected = runner(
        ["docker", "image", "inspect", BASE, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if inspected.returncode:
        raise ValueError("local_base_missing_or_docker_unavailable")
    with tempfile.TemporaryDirectory(prefix="sip-lab-media-build-") as temporary:
        stage_context(root, temporary)
        environment = dict(os.environ, DOCKER_BUILDKIT="0")
        result = runner(
            ["docker", "build", "--pull=false", "--tag", IMAGE, temporary],
            env=environment,
            check=False,
        )
        if result.returncode:
            raise ValueError("local_media_build_failed")


def main():
    try:
        build(Path(__file__).resolve().parents[1])
        report = {
            "mode": "sip_lab_cached_media_build",
            "image": IMAGE,
            "build_state": "completed",
        }
    except (OSError, ValueError) as error:
        known_errors = {
            "local_base_missing_or_docker_unavailable",
            "local_media_build_failed",
            "unsupported_media_recipe",
            "unsupported_media_copy",
            "incomplete_media_copy",
            "invalid_media_source",
        }
        code = str(error) if isinstance(error, ValueError) else ""
        report = {
            "mode": "sip_lab_cached_media_build",
            "image": IMAGE,
            "build_state": "failed",
            "error": code
            if code in known_errors
            else "local_build_tool_or_source_unavailable",
        }
    report["cloud_provider_calls"] = 0
    print(json.dumps(report))
    return 0 if report["build_state"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
