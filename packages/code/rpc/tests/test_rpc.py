# pyright: reportMissingParameterType=false, reportUnknownParameterType=false
"""Tests for RPC protocol types and serialization.

Tests verify:
- Dataclass creation
- JSON serialization
- Response creation
"""

from __future__ import annotations

import json

from cortex.code.rpc import make_error_response, make_success_response, serialize_json_line
from cortex.code.rpc.types import (
    AbortCommand,
    BashCommand,
    GetStateCommand,
    PromptCommand,
    RpcExtensionUIRequest,
    RpcExtensionUIResponse,
    RpcResponse,
    RpcSessionState,
    RpcSlashCommand,
    SetModelCommand,
    SetThinkingLevelCommand,
)


class TestPromptCommand:
    def test_default_values(self):
        cmd = PromptCommand()
        assert cmd.type == "prompt"
        assert cmd.id is None
        assert cmd.message == ""
        assert cmd.images == []
        assert cmd.streaming_behavior is None

    def test_with_values(self):
        cmd = PromptCommand(
            id="123",
            message="Hello",
            images=[{"type": "image", "data": "abc"}],
            streaming_behavior="steer",
        )
        assert cmd.id == "123"
        assert cmd.message == "Hello"
        assert len(cmd.images) == 1
        assert cmd.streaming_behavior == "steer"


class TestAbortCommand:
    def test_default_values(self):
        cmd = AbortCommand()
        assert cmd.type == "abort"
        assert cmd.id is None

    def test_with_id(self):
        cmd = AbortCommand(id="456")
        assert cmd.id == "456"


class TestBashCommand:
    def test_default_values(self):
        cmd = BashCommand()
        assert cmd.type == "bash"
        assert cmd.command == ""

    def test_with_command(self):
        cmd = BashCommand(command="ls -la")
        assert cmd.command == "ls -la"


class TestGetStateCommand:
    def test_default_values(self):
        cmd = GetStateCommand()
        assert cmd.type == "get_state"
        assert cmd.id is None


class TestSetModelCommand:
    def test_default_values(self):
        cmd = SetModelCommand()
        assert cmd.type == "set_model"
        assert cmd.provider == ""
        assert cmd.model_id == ""

    def test_with_values(self):
        cmd = SetModelCommand(provider="anthropic", model_id="claude-sonnet-4-5")
        assert cmd.provider == "anthropic"
        assert cmd.model_id == "claude-sonnet-4-5"


class TestSetThinkingLevelCommand:
    def test_default_values(self):
        cmd = SetThinkingLevelCommand()
        assert cmd.type == "set_thinking_level"
        assert cmd.level == "off"

    def test_with_level(self):
        cmd = SetThinkingLevelCommand(level="high")
        assert cmd.level == "high"


class TestRpcResponse:
    def test_default_values(self):
        resp = RpcResponse()
        assert resp.type == "response"
        assert resp.id is None
        assert resp.command == ""
        assert resp.success is True
        assert resp.data is None
        assert resp.error is None

    def test_with_values(self):
        resp = RpcResponse(
            id="123",
            command="prompt",
            success=True,
            data={"key": "value"},
        )
        assert resp.id == "123"
        assert resp.command == "prompt"
        assert resp.success is True
        assert resp.data == {"key": "value"}

    def test_error_response(self):
        resp = RpcResponse(
            id="456",
            command="bash",
            success=False,
            error="Command failed",
        )
        assert resp.success is False
        assert resp.error == "Command failed"


class TestRpcSessionState:
    def test_default_values(self):
        state = RpcSessionState()
        assert state.thinking_level == "off"
        assert state.is_streaming is False
        assert state.is_compacting is False
        assert state.steering_mode == "all"
        assert state.follow_up_mode == "all"
        assert state.session_id == ""
        assert state.auto_compaction_enabled is True
        assert state.message_count == 0
        assert state.pending_message_count == 0


class TestRpcSlashCommand:
    def test_default_values(self):
        cmd = RpcSlashCommand()
        assert cmd.name == ""
        assert cmd.description == ""
        assert cmd.source == "extension"
        assert cmd.source_info is None


class TestRpcExtensionUIRequest:
    def test_default_values(self):
        req = RpcExtensionUIRequest()
        assert req.type == "extension_ui_request"
        assert req.id == ""
        assert req.method == ""


class TestRpcExtensionUIResponse:
    def test_default_values(self):
        resp = RpcExtensionUIResponse()
        assert resp.type == "extension_ui_response"
        assert resp.id == ""
        assert resp.value is None
        assert resp.confirmed is None
        assert resp.cancelled is False

    def test_with_value(self):
        resp = RpcExtensionUIResponse(id="123", value="selected")
        assert resp.id == "123"
        assert resp.value == "selected"

    def test_with_confirmed(self):
        resp = RpcExtensionUIResponse(id="456", confirmed=True)
        assert resp.id == "456"
        assert resp.confirmed is True

    def test_cancelled(self):
        resp = RpcExtensionUIResponse(id="789", cancelled=True)
        assert resp.id == "789"
        assert resp.cancelled is True


class TestSerializeJsonLine:
    def test_simple_dict(self):
        result = serialize_json_line({"key": "value"})
        assert result == '{"key": "value"}\n'

    def test_nested_dict(self):
        result = serialize_json_line({"nested": {"key": "value"}})
        assert result == '{"nested": {"key": "value"}}\n'

    def test_list(self):
        result = serialize_json_line([1, 2, 3])
        assert result == "[1, 2, 3]\n"

    def test_string(self):
        result = serialize_json_line("hello")
        assert result == '"hello"\n'

    def test_none(self):
        result = serialize_json_line(None)
        assert result == "null\n"

    def test_with_special_characters(self):
        result = serialize_json_line({"key": 'value with "quotes"'})
        parsed = json.loads(result)
        assert parsed["key"] == 'value with "quotes"'


class TestMakeSuccessResponse:
    def test_without_data(self):
        resp = make_success_response("123", "prompt")
        assert resp.id == "123"
        assert resp.command == "prompt"
        assert resp.success is True
        assert resp.data is None

    def test_with_dict_data(self):
        resp = make_success_response("456", "get_state", {"key": "value"})
        assert resp.data == {"key": "value"}

    def test_with_non_dict_data(self):
        resp = make_success_response("789", "bash", "output")
        assert resp.data == {"value": "output"}

    def test_without_id(self):
        resp = make_success_response(None, "prompt")
        assert resp.id is None


class TestMakeErrorResponse:
    def test_basic_error(self):
        resp = make_error_response("123", "bash", "Command failed")
        assert resp.id == "123"
        assert resp.command == "bash"
        assert resp.success is False
        assert resp.error == "Command failed"

    def test_without_id(self):
        resp = make_error_response(None, "parse", "Invalid JSON")
        assert resp.id is None
        assert resp.error == "Invalid JSON"
