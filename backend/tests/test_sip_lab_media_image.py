"""Exercise only the files copied into the media image, without repository imports."""

import importlib.util
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("missing_host_module", [False, True])
def test_media_image_copy_closure_supports_qwen_handshake(tmp_path, missing_host_module):
    root = Path(__file__).resolve().parents[2]
    destination = tmp_path / "app"
    destination.mkdir()
    ignore = (root / "deploy/sip-lab/media.Dockerfile.dockerignore").read_text().splitlines()
    assert ignore[0] == "**"
    allowed = {line[1:] for line in ignore if line.startswith("!")}
    for line in (root / "deploy/sip-lab/media.Dockerfile").read_text().splitlines():
        if line.startswith("COPY "):
            words = shlex.split(line)
            assert words[-1] == "./app/"
            for name in words[1:-1]:
                assert name in allowed, f"Docker build context excludes {name}"
                shutil.copy2(root / name, destination)
    if missing_host_module:
        (destination / "sip_lab_qwen_voice.py").unlink()

    script = r"""
import json, sys, time
from collections import deque
from contextlib import contextmanager
sys.path.insert(0, sys.argv[1])
import app
assert app.__file__.startswith(sys.argv[1])
from app.sip_lab_voice import LocalVoiceClient, QWEN_CONTAINER_URL
provider = {
    "kind": "qwen_token_plan",
    "models": {"asr": "qwen-audio-3.0-asr-flash", "llm": "qwen3.8-flash", "tts": "qwen-audio-3.0-tts-plus"},
    "voice": "longanhuan_v3.6", "sample_rate": 8000, "protocol_version": 1,
}
sent = []
events = deque([json.dumps({"kind": "ready", "provider": provider})])
class Host:
    def recv(self, timeout):
        if events:
            return events.popleft()
        raise TimeoutError()
    def send(self, raw):
        command = json.loads(raw)
        sent.append(command)
        events.extend([
            json.dumps({"kind": "audio", "turn": command["turn"], "samples": 160}),
            b"\0" * 320,
            json.dumps({
                "kind": "complete", "turn": command["turn"], "end": False, "reply_digest": "a" * 64,
                "elapsed_ms": 1, "first_audio_ms": 1, "provider": provider,
                "cloud_provider_calls": 0, "provider_request_state": "not_sent",
                "provider_error": None, "provider_active_stage": None,
            }),
        ])
@contextmanager
def connect(url, **kwargs):
    assert url == "ws://host.docker.internal:18092/lab/voice"
    assert kwargs["proxy"] is None
    yield Host()
voice = LocalVoiceClient("a" * 64, url=QWEN_CONTAINER_URL, expected_provider="qwen_token_plan", connect=connect)
voice.start()
until = time.monotonic() + 2
while not voice.completed and not voice.failed and time.monotonic() < until:
    time.sleep(0.01)
audio = voice.pop()
voice.close()
assert "fastapi" not in sys.modules and "uvicorn" not in sys.modules
print(json.dumps({"summary": voice.summary(), "commands": len(sent), "audio_bytes": len(audio or b"")}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    data = json.loads(result.stdout)
    summary = data["summary"]
    assert summary["cloud_provider_calls"] == 0
    if missing_host_module:
        # Missing image modules must not silently resolve from the full checkout.
        assert summary["local_voice_failed"] and data["commands"] == 0
    else:
        assert not summary["local_voice_failed"] and summary["local_turns_completed"] == 1
        assert summary["provider"]["kind"] == "qwen_token_plan"
        assert data["commands"] == 1 and data["audio_bytes"] == 320


def cached_builder():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("cached_media_builder", root / "scripts/sip-lab-media-local-build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return root, module


def test_cached_builder_stages_only_reviewed_sources_and_never_pulls(tmp_path):
    root, module = cached_builder()
    calls = []
    context = []

    def run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "sha256:synthetic", "")
        assert command[:5] == ["docker", "build", "--pull=false", "--tag", module.IMAGE]
        assert kwargs["env"]["DOCKER_BUILDKIT"] == "0"
        staged = Path(command[-1])
        context.append(staged)
        assert {str(path.relative_to(staged)) for path in staged.rglob("*") if path.is_file()} == module.FILES | {
            "Dockerfile"
        }
        assert (staged / "Dockerfile").read_text() == (root / "deploy/sip-lab/media.Dockerfile").read_text()
        assert not any(name in str(staged) for name in ("generated", ".venv"))
        return subprocess.CompletedProcess(command, 0)

    module.build(root, runner=run)
    assert len(calls) == 2 and not context[0].exists()


def test_cached_builder_missing_base_stops_without_pull_or_build():
    root, module = cached_builder()
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "synthetic private docker failure")

    with pytest.raises(ValueError, match="local_base_missing_or_docker_unavailable"):
        module.build(root, runner=run)
    assert len(calls) == 1 and calls[0][1:3] == ["image", "inspect"]


@pytest.mark.parametrize("known", [True, False])
def test_cached_builder_cli_reports_fixed_failure_without_exception_details(monkeypatch, capsys, known):
    _, module = cached_builder()

    def fail(root):
        if known:
            raise ValueError("local_base_missing_or_docker_unavailable")
        raise OSError("synthetic private path or daemon error")

    monkeypatch.setattr(module, "build", fail)
    assert module.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"] == (
        "local_base_missing_or_docker_unavailable" if known else "local_build_tool_or_source_unavailable"
    )
    assert report["build_state"] == "failed" and report["cloud_provider_calls"] == 0
    assert "private" not in json.dumps(report)


def test_cached_builder_failed_build_cleans_context_without_retry():
    root, module = cached_builder()
    calls, contexts = [], []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 0, "sha256:synthetic", "")
        contexts.append(Path(command[-1]))
        return subprocess.CompletedProcess(command, 1)

    with pytest.raises(ValueError, match="local_media_build_failed"):
        module.build(root, runner=run)
    assert len(calls) == 2 and not contexts[0].exists()


def test_cached_builder_rejects_unreviewed_copy_and_source_symlinks(tmp_path):
    root, module = cached_builder()
    recipe = tmp_path / "deploy/sip-lab/media.Dockerfile"
    recipe.parent.mkdir(parents=True)
    recipe.write_text(
        (root / "deploy/sip-lab/media.Dockerfile").read_text() + "COPY deploy/sip-lab/generated/lab.env ./app/\n"
    )
    with pytest.raises(ValueError, match="unsupported_media_copy"):
        module.stage_context(tmp_path, tmp_path / "context")
    recipe.write_text((root / "deploy/sip-lab/media.Dockerfile").read_text())
    for name in module.FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(root / name)
    with pytest.raises(ValueError, match="invalid_media_source"):
        module.stage_context(tmp_path, tmp_path / "context")
