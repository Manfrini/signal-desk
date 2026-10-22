"""src/processors/llm_provider.py: the provider table and its pure helpers.

Only claude is implemented today; these tests pin the argv it builds and the
non-zero-exit/parsing behaviour that ClaudeCliAnalyzer relies on.
"""

from __future__ import annotations

import subprocess

import pytest

from src.processors.llm_provider import (
    DEFAULT_PROVIDER_ID,
    ProviderInvocationError,
    build_command,
    get_provider,
    normalize_output,
)


def _cp(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_get_provider_defaults_to_claude():
    assert get_provider("").id == DEFAULT_PROVIDER_ID == "claude"


def test_get_provider_falls_back_on_unknown_id():
    assert get_provider("not-a-real-provider").id == "claude"


def test_codex_login_uses_device_authorization():
    assert get_provider("codex").login_cmd == ("codex", "login", "--device-auth")


def test_build_command_shape_for_claude():
    spec = get_provider("claude")
    command = build_command(spec, "hello", {"type": "object"}, "sonnet")

    assert command[0] == "claude"
    assert "-p" in command and command[command.index("-p") + 1] == "hello"
    assert "--model" in command and command[command.index("--model") + 1] == "sonnet"
    assert "--json-schema" in command
    assert "--allowedTools" in command and command[command.index("--allowedTools") + 1] == ""
    assert "--strict-mcp-config" in command


def test_build_command_honors_explicit_binary_override():
    spec = get_provider("claude")
    command = build_command(spec, "hi", {}, "sonnet", binary="/opt/custom/claude")
    assert command[0] == "/opt/custom/claude"


def test_build_command_falls_back_to_default_model():
    spec = get_provider("claude")
    command = build_command(spec, "hi", {}, "")
    assert command[command.index("--model") + 1] == spec.default_model


def test_normalize_output_passes_through_stdout_on_success():
    spec = get_provider("claude")
    assert normalize_output(spec, _cp(stdout='{"ok": true}')) == '{"ok": true}'


def test_normalize_output_raises_on_nonzero_exit():
    spec = get_provider("claude")
    with pytest.raises(ProviderInvocationError):
        normalize_output(spec, _cp(returncode=1, stderr="boom"))


def test_normalize_output_error_message_is_capped_and_labeled():
    spec = get_provider("claude")
    with pytest.raises(ProviderInvocationError) as excinfo:
        normalize_output(spec, _cp(returncode=2, stderr="x" * 2000))
    message = str(excinfo.value)
    assert message.startswith("Claude Code CLI exited with 2")
    assert len(message) < 600
