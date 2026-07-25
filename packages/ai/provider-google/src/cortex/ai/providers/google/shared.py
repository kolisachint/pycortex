"""Shared helpers for the Google Generative AI and Vertex providers.

Mechanical port of hoocode's ``packages/ai/src/providers/google-shared.ts``.

The TS imports ``Content``/``Part``/``FinishReason``/``FunctionCallingConfigMode``
from ``@google/genai``. There is no SDK here (see the module docstring of
``google.py``), so those become plain ``dict`` payloads and string constants —
the wire format is identical either way.
"""

from __future__ import annotations

import re
from typing import Any

from cortex.ai.providers._common import transform_messages
from cortex.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    StopReason,
    TextContent,
    Tool,
)
from cortex.ai.util import sanitize_surrogates

__all__ = [
    "FUNCTION_CALLING_MODES",
    "GOOGLE_THINKING_LEVELS",
    "convert_messages",
    "convert_tools",
    "is_thinking_part",
    "map_stop_reason",
    "map_stop_reason_string",
    "map_tool_choice",
    "requires_tool_call_id",
    "retain_thought_signature",
]

# Mirrors Google's ThinkingLevel enum values.
GOOGLE_THINKING_LEVELS = ("THINKING_LEVEL_UNSPECIFIED", "MINIMAL", "LOW", "MEDIUM", "HIGH")

FUNCTION_CALLING_MODES = {"auto": "AUTO", "none": "NONE", "any": "ANY"}

# Thought signatures must be base64 for Google APIs (TYPE_BYTES).
_BASE64_SIGNATURE_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")

_TOOL_CALL_ID_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]")

_GEMINI_MAJOR_RE = re.compile(r"^gemini(?:-live)?-(\d+)")

_JSON_SCHEMA_META_DECLARATIONS = frozenset(
    {
        "$schema",
        "$id",
        "$anchor",
        "$dynamicAnchor",
        "$vocabulary",
        "$comment",
        "$defs",
        "definitions",  # pre-draft-2019-09 equivalent of $defs
    }
)


def is_thinking_part(part: dict[str, Any]) -> bool:
    """Whether a streamed Gemini ``Part`` should be treated as "thinking".

    Protocol note (Gemini / Vertex AI thought signatures):
    - ``thought: true`` is the definitive marker for thinking content.
    - ``thoughtSignature`` is an encrypted representation of the model's internal
      reasoning, used to preserve context across turns.
    - ``thoughtSignature`` can appear on ANY part type (text, functionCall, ...) —
      it does NOT mean the part itself is thinking content.
    - When persisting/replaying model outputs, signature-bearing parts must be
      preserved as-is; do not merge or move signatures across parts.

    See https://ai.google.dev/gemini-api/docs/thought-signatures
    """
    return part.get("thought") is True


def retain_thought_signature(existing: str | None, incoming: str | None) -> str | None:
    """Keep the last non-empty thought signature seen for the current block.

    Some backends only send ``thoughtSignature`` on the first delta of a part;
    later deltas omit it. This prevents the signature being overwritten with
    ``None`` mid-block. It does NOT merge signatures across distinct parts.
    """
    if isinstance(incoming, str) and len(incoming) > 0:
        return incoming
    return existing


def _is_valid_thought_signature(signature: str | None) -> bool:
    if not signature:
        return False
    if len(signature) % 4 != 0:
        return False
    return _BASE64_SIGNATURE_RE.match(signature) is not None


def _resolve_thought_signature(
    is_same_provider_and_model: bool, signature: str | None
) -> str | None:
    """Only keep signatures from the same provider/model, and only valid base64."""
    if is_same_provider_and_model and _is_valid_thought_signature(signature):
        return signature
    return None


def requires_tool_call_id(model_id: str) -> bool:
    """Models served via Google APIs that need explicit ids on calls/responses."""
    return model_id.startswith("claude-") or model_id.startswith("gpt-oss-")


def _gemini_major_version(model_id: str) -> int | None:
    match = _GEMINI_MAJOR_RE.match(model_id.lower())
    if not match:
        return None
    return int(match.group(1))


def _supports_multimodal_function_response(model_id: str) -> bool:
    major = _gemini_major_version(model_id)
    if major is not None:
        return major >= 3
    return True


def convert_messages(model: Model, context: Context) -> list[dict[str, Any]]:
    """Convert internal messages to Gemini ``Content[]``."""
    contents: list[dict[str, Any]] = []

    def normalize_tool_call_id(tool_call_id: str, _model: Model, _msg: AssistantMessage) -> str:
        if not requires_tool_call_id(model.id):
            return tool_call_id
        return _TOOL_CALL_ID_SANITIZE_RE.sub("_", tool_call_id)[:64]

    transformed_messages = transform_messages(context.messages, model, normalize_tool_call_id)

    for msg in transformed_messages:
        role = getattr(msg, "role", None)
        if role == "user":
            _append_user(contents, msg)
        elif role == "assistant":
            _append_assistant(contents, msg, model)
        elif role == "toolResult":
            _append_tool_result(contents, msg, model)

    return contents


def _append_user(contents: list[dict[str, Any]], msg: Any) -> None:
    content = msg.content
    if isinstance(content, str):
        contents.append({"role": "user", "parts": [{"text": sanitize_surrogates(content)}]})
        return

    parts: list[dict[str, Any]] = []
    for item in content:
        if item.type == "text":
            parts.append({"text": sanitize_surrogates(item.text)})
        else:
            parts.append({"inlineData": {"mimeType": item.mime_type, "data": item.data}})
    if not parts:
        return
    contents.append({"role": "user", "parts": parts})


def _append_assistant(contents: list[dict[str, Any]], msg: Any, model: Model) -> None:
    parts: list[dict[str, Any]] = []
    # Thinking blocks are only replayable back to the model that produced them.
    is_same = msg.provider == model.provider and msg.model == model.id

    for block in msg.content:
        if block.type == "text":
            if not block.text or block.text.strip() == "":
                continue
            signature = _resolve_thought_signature(is_same, block.text_signature)
            part: dict[str, Any] = {"text": sanitize_surrogates(block.text)}
            if signature:
                part["thoughtSignature"] = signature
            parts.append(part)
        elif block.type == "thinking":
            if not block.thinking or block.thinking.strip() == "":
                continue
            if is_same:
                signature = _resolve_thought_signature(is_same, block.thinking_signature)
                part = {"thought": True, "text": sanitize_surrogates(block.thinking)}
                if signature:
                    part["thoughtSignature"] = signature
                parts.append(part)
            else:
                # Downgrade to plain text — no tags, so the model does not mimic them.
                parts.append({"text": sanitize_surrogates(block.thinking)})
        elif block.type == "toolCall":
            signature = _resolve_thought_signature(is_same, block.thought_signature)
            function_call: dict[str, Any] = {
                "name": block.name,
                "args": block.arguments if block.arguments is not None else {},
            }
            if requires_tool_call_id(model.id):
                function_call["id"] = block.id
            part = {"functionCall": function_call}
            if signature:
                part["thoughtSignature"] = signature
            parts.append(part)

    if not parts:
        return
    contents.append({"role": "model", "parts": parts})


def _append_tool_result(contents: list[dict[str, Any]], msg: Any, model: Model) -> None:
    text_content = [c for c in msg.content if isinstance(c, TextContent)]
    text_result = "\n".join(c.text for c in text_content)
    image_content = (
        [c for c in msg.content if isinstance(c, ImageContent)]
        if "image" in (model.input or [])
        else []
    )

    has_text = len(text_result) > 0
    has_images = len(image_content) > 0

    # Gemini 3+ supports images nested in functionResponse.parts. Claude and
    # Gemini < 3 behind Cloud Code Assist still need a separate user image turn.
    multimodal = _supports_multimodal_function_response(model.id)

    if has_text:
        response_value = sanitize_surrogates(text_result)
    elif has_images:
        response_value = "(see attached image)"
    else:
        response_value = ""

    image_parts: list[dict[str, Any]] = [
        {"inlineData": {"mimeType": block.mime_type, "data": block.data}} for block in image_content
    ]

    function_response: dict[str, Any] = {
        "name": msg.tool_name,
        # "output" for success, "error" for errors, per the SDK docs.
        "response": ({"error": response_value} if msg.is_error else {"output": response_value}),
    }
    if has_images and multimodal:
        function_response["parts"] = image_parts
    if requires_tool_call_id(model.id):
        function_response["id"] = msg.tool_call_id
    function_response_part = {"functionResponse": function_response}

    # Cloud Code Assist requires all function responses in a single user turn, so
    # merge into the previous turn when it is already one.
    last = contents[-1] if contents else None
    merged = False
    if last is not None and last.get("role") == "user":
        last_parts = last.get("parts") or []
        if any("functionResponse" in part for part in last_parts):
            last_parts.append(function_response_part)
            merged = True

    if not merged:
        contents.append({"role": "user", "parts": [function_response_part]})

    # Note: this runs whether or not the response was merged — returning early
    # above would swallow the synthetic image turn for Gemini < 3.
    if has_images and not multimodal:
        contents.append({"role": "user", "parts": [{"text": "Tool result image:"}, *image_parts]})


def _sanitize_for_open_api(schema: Any) -> Any:
    """Strip JSON Schema meta-declarations the OpenAPI 3.03 subset rejects."""
    if not isinstance(schema, dict):
        return schema
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _JSON_SCHEMA_META_DECLARATIONS:
            continue
        result[key] = _sanitize_for_open_api(value)
    return result


def convert_tools(tools: list[Tool], use_parameters: bool = False) -> list[dict[str, Any]] | None:
    """Convert tools to Gemini function declarations.

    Defaults to ``parametersJsonSchema``, which accepts full JSON Schema
    (``anyOf``, ``oneOf``, ``const``, ...). ``use_parameters=True`` selects the
    legacy ``parameters`` field (OpenAPI 3.03 Schema), needed for Cloud Code
    Assist with Claude models where the API translates it into Anthropic's
    ``input_schema``.

    Note on ``constrain_tool_calls``: Gemini already receives the full schema and
    enforces it server-side, so there is no per-request knob and the option is a
    no-op here.
    """
    if len(tools) == 0:
        return None
    declarations: list[dict[str, Any]] = []
    for tool in tools:
        declaration: dict[str, Any] = {"name": tool.name, "description": tool.description}
        if use_parameters:
            declaration["parameters"] = _sanitize_for_open_api(tool.parameters)
        else:
            declaration["parametersJsonSchema"] = tool.parameters
        declarations.append(declaration)
    return [{"functionDeclarations": declarations}]


def map_tool_choice(choice: str) -> str:
    """Map a tool-choice string to a Gemini ``FunctionCallingConfigMode``."""
    return FUNCTION_CALLING_MODES.get(choice, "AUTO")


# Every FinishReason the TS switch enumerates; anything not "stop"/"length" is an
# error. Listed explicitly (rather than defaulting) to mirror the exhaustiveness
# check in the TS and to make an unknown reason from the API visible.
_KNOWN_FINISH_REASONS = frozenset(
    {
        "STOP",
        "MAX_TOKENS",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "SAFETY",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
        "IMAGE_OTHER",
        "RECITATION",
        "FINISH_REASON_UNSPECIFIED",
        "OTHER",
        "LANGUAGE",
        "MALFORMED_FUNCTION_CALL",
        "UNEXPECTED_TOOL_CALL",
        "NO_IMAGE",
    }
)


def map_stop_reason(reason: str) -> StopReason:
    """Map a Gemini ``FinishReason`` to our ``StopReason``.

    The TS has a compile-time exhaustiveness check that throws on an unknown
    reason; Python has no equivalent, so an unrecognised value raises instead of
    silently becoming "error".
    """
    if reason == "STOP":
        return "stop"
    if reason == "MAX_TOKENS":
        return "length"
    if reason in _KNOWN_FINISH_REASONS:
        return "error"
    raise ValueError(f"Unhandled stop reason: {reason}")


def map_stop_reason_string(reason: str) -> StopReason:
    """Map a raw string finish reason to our ``StopReason`` (for raw responses)."""
    if reason == "STOP":
        return "stop"
    if reason == "MAX_TOKENS":
        return "length"
    return "error"
