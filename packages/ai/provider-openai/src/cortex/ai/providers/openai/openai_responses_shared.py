"""Shared helpers for OpenAI Responses API providers.

Mechanical port of hoocode's ``packages/ai/src/providers/openai-responses-shared.ts``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cortex.ai.models import calculate_cost
from cortex.ai.providers._common import transform_messages
from cortex.ai.stream import AssistantMessageEventStream
from cortex.ai.types import (
    Api,
    AssistantMessage,
    Context,
    Model,
    StopReason,
    Tool,
    ToolCall,
    Usage,
)
from cortex.ai.util import (
    parse_streaming_json,
    sanitize_surrogates,
    short_hash,
    to_strict_json_schema,
)

# ---------------------------------------------------------------------------
# Text Signature helpers
# ---------------------------------------------------------------------------


def _encode_text_signature_v1(signature_id: str, phase: str | None = None) -> str:
    """Encode a text signature as JSON."""
    payload: dict[str, Any] = {"v": 1, "id": signature_id}
    if phase:
        payload["phase"] = phase
    return json.dumps(payload)


def _parse_text_signature(
    signature: str | None,
) -> dict[str, Any] | None:
    """Parse a text signature string."""
    if not signature:
        return None
    if signature.startswith("{"):
        try:
            parsed = json.loads(signature)
            if parsed.get("v") == 1 and isinstance(parsed.get("id"), str):
                phase = parsed.get("phase")
                if phase in ("commentary", "final_answer"):
                    return {"id": parsed["id"], "phase": phase}
                return {"id": parsed["id"]}
        except (json.JSONDecodeError, KeyError):
            # Fall through to legacy plain-string handling.
            pass
    return {"id": signature}


# ---------------------------------------------------------------------------
# Options dataclasses
# ---------------------------------------------------------------------------


@dataclass
class OpenAIResponsesStreamOptions:
    """Options for OpenAI Responses API stream processing."""

    serviceTier: str | None = None
    resolveServiceTier: Callable[..., str | None] | None = None
    applyServiceTierPricing: Callable[[Usage, str | None], None] | None = None


@dataclass
class ConvertResponsesMessagesOptions:
    """Options for converting messages to Responses API format."""

    includeSystemPrompt: bool = True


@dataclass
class ConvertResponsesToolsOptions:
    """Options for converting tools to Responses API format."""

    strict: bool | None = None
    constrainToolCalls: bool | None = None


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------


def convert_responses_messages(
    model: Model[Api],
    context: Context,
    allowed_tool_call_providers: frozenset[str],
    options: ConvertResponsesMessagesOptions | None = None,
) -> list[dict[str, Any]]:
    """Convert messages to OpenAI Responses API format."""
    messages: list[dict[str, Any]] = []

    def normalize_id_part(part: str) -> str:
        import re

        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", part)
        normalized = sanitized[:64] if len(sanitized) > 64 else sanitized
        return normalized.rstrip("_")

    def build_foreign_responses_item_id(item_id: str) -> str:
        normalized = f"fc_{short_hash(item_id)}"
        return normalized[:64] if len(normalized) > 64 else normalized

    def normalize_tool_call_id(
        id_str: str, target_model: Model[Api], source: AssistantMessage
    ) -> str:
        if model.provider not in allowed_tool_call_providers:
            return normalize_id_part(id_str)
        if "|" not in id_str:
            return normalize_id_part(id_str)
        call_id, item_id = id_str.split("|", 1)
        normalized_call_id = normalize_id_part(call_id)
        is_foreign_tool_call = source.provider != model.provider or source.api != model.api
        normalized_item_id = (
            build_foreign_responses_item_id(item_id)
            if is_foreign_tool_call
            else normalize_id_part(item_id)
        )
        # OpenAI Responses API requires item id to start with "fc"
        if not normalized_item_id.startswith("fc_"):
            normalized_item_id = normalize_id_part(f"fc_{normalized_item_id}")
        return f"{normalized_call_id}|{normalized_item_id}"

    transformed_messages = transform_messages(context.messages, model, normalize_tool_call_id)

    include_system_prompt = options.includeSystemPrompt if options is not None else True
    if include_system_prompt and context.system_prompt:
        role = "developer" if model.reasoning else "system"
        messages.append(
            {
                "role": role,
                "content": sanitize_surrogates(context.system_prompt),
            }
        )

    msg_index = 0
    for msg in transformed_messages:
        if msg.role == "user":
            if isinstance(msg.content, str):
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": sanitize_surrogates(msg.content)}
                        ],
                    }
                )
            else:
                content: list[dict[str, Any]] = []
                for item in msg.content:
                    if item.type == "text":
                        content.append(
                            {
                                "type": "input_text",
                                "text": sanitize_surrogates(item.text),
                            }
                        )
                    elif item.type == "image":
                        content.append(
                            {
                                "type": "input_image",
                                "detail": "auto",
                                "image_url": f"data:{item.mime_type};base64,{item.data}",
                            }
                        )
                if len(content) == 0:
                    msg_index += 1
                    continue
                messages.append(
                    {
                        "role": "user",
                        "content": content,
                    }
                )
        elif msg.role == "assistant":
            output: list[dict[str, Any]] = []
            assistant_msg = msg
            is_different_model = (
                assistant_msg.model != model.id
                and assistant_msg.provider == model.provider
                and assistant_msg.api == model.api
            )

            for block in msg.content:
                if block.type == "thinking":
                    if block.thinking_signature:
                        reasoning_item = json.loads(block.thinking_signature)
                        output.append(reasoning_item)
                elif block.type == "text":
                    text_block = block
                    parsed_signature = _parse_text_signature(text_block.text_signature)
                    # OpenAI requires id to be max 64 characters
                    msg_id = parsed_signature.get("id") if parsed_signature else None
                    if not msg_id:
                        msg_id = f"msg_{msg_index}"
                    elif len(msg_id) > 64:
                        msg_id = f"msg_{short_hash(msg_id)}"
                    output.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": sanitize_surrogates(text_block.text),
                                    "annotations": [],
                                }
                            ],
                            "status": "completed",
                            "id": msg_id,
                            "phase": parsed_signature.get("phase") if parsed_signature else None,
                        }
                    )
                elif block.type == "toolCall":
                    tool_call = block
                    call_id, item_id_raw = tool_call.id.split("|", 1)
                    item_id = item_id_raw if item_id_raw else None

                    # For different-model messages, set id to undefined to avoid pairing validation.
                    # OpenAI tracks which fc_xxx IDs were paired with rs_xxx reasoning items.
                    # By omitting the id, we avoid triggering that validation.
                    if is_different_model and item_id and item_id.startswith("fc_"):
                        item_id = None

                    output.append(
                        {
                            "type": "function_call",
                            "id": item_id,
                            "call_id": call_id,
                            "name": tool_call.name,
                            "arguments": json.dumps(tool_call.arguments),
                        }
                    )
            if len(output) == 0:
                msg_index += 1
                continue
            messages.extend(output)
        elif msg.role == "toolResult":
            text_result = "\n".join(c.text for c in msg.content if c.type == "text")
            has_images = any(c.type == "image" for c in msg.content)
            has_text = len(text_result) > 0
            call_id = msg.tool_call_id.split("|", 1)[0]

            output_val: str | list[dict[str, Any]]
            if has_images and "image" in model.input:
                content_parts: list[dict[str, Any]] = []

                if has_text:
                    content_parts.append(
                        {
                            "type": "input_text",
                            "text": sanitize_surrogates(text_result),
                        }
                    )

                for block in msg.content:
                    if block.type == "image":
                        content_parts.append(
                            {
                                "type": "input_image",
                                "detail": "auto",
                                "image_url": f"data:{block.mime_type};base64,{block.data}",
                            }
                        )

                output_val = content_parts
            else:
                output_val = sanitize_surrogates(
                    text_result if has_text else "(see attached image)"
                )

            messages.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output_val,
                }
            )
        msg_index += 1

    return messages


# ---------------------------------------------------------------------------
# Tool conversion
# ---------------------------------------------------------------------------


def convert_responses_tools(
    tools: list[Tool], options: ConvertResponsesToolsOptions | None = None
) -> list[dict[str, Any]]:
    """Convert tools to OpenAI Responses API format."""
    if options and options.constrainToolCalls:
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": to_strict_json_schema(tool.parameters),
                "strict": True,
            }
            for tool in tools
        ]
    strict = False if options is None or options.strict is None else options.strict
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "strict": strict,
        }
        for tool in tools
    ]


# ---------------------------------------------------------------------------
# Stream processing
# ---------------------------------------------------------------------------


async def process_responses_stream(
    openai_stream: Any,
    output: AssistantMessage,
    stream: AssistantMessageEventStream,
    model: Model[Api],
    options: OpenAIResponsesStreamOptions | None = None,
) -> None:
    """Process the OpenAI Responses API stream."""
    current_item: dict[str, Any] | None = None
    current_block: dict[str, Any] | None = None
    blocks = output.content

    def block_index() -> int:
        return len(blocks) - 1

    async for event in openai_stream:
        event_type = event.get("type")

        if event_type == "response.created":
            output.response_id = event.get("response", {}).get("id")
        elif event_type == "response.output_item.added":
            item = event.get("item", {})
            item_type = item.get("type")
            if item_type == "reasoning":
                current_item = item
                current_block = {"type": "thinking", "thinking": ""}
                output.content.append(current_block)  # type: ignore[arg-type]
                stream.push(  # type: ignore[arg-type]
                    {  # type: ignore[arg-type]
                        "type": "thinking_start",  # type: ignore[arg-type]
                        "contentIndex": block_index(),  # type: ignore[arg-type]
                        "partial": output,  # type: ignore[arg-type]
                    }  # type: ignore[arg-type]
                )
            elif item_type == "message":
                current_item = item
                current_block = {"type": "text", "text": ""}
                output.content.append(current_block)  # type: ignore[arg-type]
                stream.push(  # type: ignore[arg-type]
                    {  # type: ignore[arg-type]
                        "type": "text_start",  # type: ignore[arg-type]
                        "contentIndex": block_index(),  # type: ignore[arg-type]
                        "partial": output,  # type: ignore[arg-type]
                    }  # type: ignore[arg-type]
                )
            elif item_type == "function_call":
                current_item = item
                current_block = {
                    "type": "toolCall",
                    "id": f"{item.get('call_id', '')}|{item.get('id', '')}",
                    "name": item.get("name", ""),
                    "arguments": {},
                    "partialJson": item.get("arguments", ""),
                }
                output.content.append(current_block)  # type: ignore[arg-type]
                stream.push(  # type: ignore[arg-type]
                    {  # type: ignore[arg-type]
                        "type": "toolcall_start",  # type: ignore[arg-type]
                        "contentIndex": block_index(),  # type: ignore[arg-type]
                        "partial": output,  # type: ignore[arg-type]
                    }  # type: ignore[arg-type]
                )
        elif event_type == "response.reasoning_summary_part.added":
            if current_item and current_item.get("type") == "reasoning":
                current_item.setdefault("summary", [])
                current_item["summary"].append(event.get("part"))
        elif event_type == "response.reasoning_summary_text.delta":
            if (
                current_item
                and current_item.get("type") == "reasoning"
                and current_block
                and current_block.get("type") == "thinking"
            ):
                current_item.setdefault("summary", [])
                summary = current_item["summary"]
                if summary:
                    current_block["thinking"] += event.get("delta", "")
                    summary[-1]["text"] = summary[-1].get("text", "") + event.get("delta", "")
                    stream.push(  # type: ignore[arg-type]
                        {  # type: ignore[arg-type]
                            "type": "thinking_delta",  # type: ignore[arg-type]
                            "contentIndex": block_index(),  # type: ignore[arg-type]
                            "delta": event.get("delta", ""),  # type: ignore[arg-type]
                            "partial": output,  # type: ignore[arg-type]
                        }  # type: ignore[arg-type]
                    )
        elif event_type == "response.reasoning_summary_part.done":
            if (
                current_item
                and current_item.get("type") == "reasoning"
                and current_block
                and current_block.get("type") == "thinking"
            ):
                current_item.setdefault("summary", [])
                summary = current_item["summary"]
                if summary:
                    current_block["thinking"] += "\n\n"
                    summary[-1]["text"] = summary[-1].get("text", "") + "\n\n"
                    stream.push(  # type: ignore[arg-type]
                        {  # type: ignore[arg-type]
                            "type": "thinking_delta",  # type: ignore[arg-type]
                            "contentIndex": block_index(),  # type: ignore[arg-type]
                            "delta": "\n\n",  # type: ignore[arg-type]
                            "partial": output,  # type: ignore[arg-type]
                        }  # type: ignore[arg-type]
                    )
        elif event_type == "response.reasoning_text.delta":
            if (
                current_item
                and current_item.get("type") == "reasoning"
                and current_block
                and current_block.get("type") == "thinking"
            ):
                current_block["thinking"] += event.get("delta", "")
                stream.push(  # type: ignore[arg-type]
                    {  # type: ignore[arg-type]
                        "type": "thinking_delta",  # type: ignore[arg-type]
                        "contentIndex": block_index(),  # type: ignore[arg-type]
                        "delta": event.get("delta", ""),  # type: ignore[arg-type]
                        "partial": output,  # type: ignore[arg-type]
                    }  # type: ignore[arg-type]
                )
        elif event_type == "response.content_part.added":
            if current_item and current_item.get("type") == "message":
                current_item.setdefault("content", [])
                part = event.get("part", {})
                # Filter out ReasoningText, only accept output_text and refusal
                if part.get("type") in ("output_text", "refusal"):
                    current_item["content"].append(part)
        elif event_type == "response.output_text.delta":
            if (
                current_item
                and current_item.get("type") == "message"
                and current_block
                and current_block.get("type") == "text"
            ):
                content_list = current_item.get("content")
                if not content_list or len(content_list) == 0:
                    continue
                last_part = content_list[-1]
                part_type = getattr(last_part, "type", None)
                if part_type == "output_text":
                    delta = event.get("delta", "")
                    current_block["text"] += delta
                    last_part["text"] = last_part.get("text", "") + delta
                    stream.push(  # type: ignore[arg-type]
                        {  # type: ignore[arg-type]
                            "type": "text_delta",  # type: ignore[arg-type]
                            "contentIndex": block_index(),  # type: ignore[arg-type]
                            "delta": delta,  # type: ignore[arg-type]
                            "partial": output,  # type: ignore[arg-type]
                        }  # type: ignore[arg-type]
                    )
        elif event_type == "response.refusal.delta":
            if (
                current_item
                and current_item.get("type") == "message"
                and current_block
                and current_block.get("type") == "text"
            ):
                content_list = current_item.get("content")
                if not content_list or len(content_list) == 0:
                    continue
                last_part = content_list[-1]
                part_type = getattr(last_part, "type", None)
                if part_type == "refusal":
                    delta = event.get("delta", "")
                    current_block["text"] += delta
                    last_part["refusal"] = last_part.get("refusal", "") + delta
                    stream.push(  # type: ignore[arg-type]
                        {  # type: ignore[arg-type]
                            "type": "text_delta",  # type: ignore[arg-type]
                            "contentIndex": block_index(),  # type: ignore[arg-type]
                            "delta": delta,  # type: ignore[arg-type]
                            "partial": output,  # type: ignore[arg-type]
                        }  # type: ignore[arg-type]
                    )
        elif event_type == "response.function_call_arguments.delta":
            if (
                current_item
                and current_item.get("type") == "function_call"
                and current_block
                and current_block.get("type") == "toolCall"
            ):
                delta = event.get("delta", "")
                current_block["partialJson"] += delta
                current_block["arguments"] = parse_streaming_json(current_block["partialJson"])
                stream.push(  # type: ignore[arg-type]
                    {  # type: ignore[arg-type]
                        "type": "toolcall_delta",  # type: ignore[arg-type]
                        "contentIndex": block_index(),  # type: ignore[arg-type]
                        "delta": delta,  # type: ignore[arg-type]
                        "partial": output,  # type: ignore[arg-type]
                    }  # type: ignore[arg-type]
                )
        elif event_type == "response.function_call_arguments.done":
            if (
                current_item
                and current_item.get("type") == "function_call"
                and current_block
                and current_block.get("type") == "toolCall"
            ):
                previous_partial_json = current_block["partialJson"]
                current_block["partialJson"] = event.get("arguments", "")
                current_block["arguments"] = parse_streaming_json(current_block["partialJson"])

                arguments = event.get("arguments", "")
                if arguments.startswith(previous_partial_json):
                    delta = arguments[len(previous_partial_json) :]
                    if delta:
                        stream.push(  # type: ignore[arg-type]
                            {  # type: ignore[arg-type]
                                "type": "toolcall_delta",  # type: ignore[arg-type]
                                "contentIndex": block_index(),  # type: ignore[arg-type]
                                "delta": delta,  # type: ignore[arg-type]
                                "partial": output,  # type: ignore[arg-type]
                            }  # type: ignore[arg-type]
                        )
        elif event_type == "response.output_item.done":
            item = event.get("item", {})

            if (
                item.get("type") == "reasoning"
                and current_block
                and current_block.get("type") == "thinking"
            ):
                summary_list = item.get("summary")
                summary_text = (
                    "\n\n".join(s.get("text", "") for s in summary_list) if summary_list else ""
                )
                content_list = item.get("content")
                content_text = (
                    "\n\n".join(c.get("text", "") for c in content_list) if content_list else ""
                )
                current_block["thinking"] = (
                    summary_text or content_text or current_block["thinking"]
                )
                current_block["thinkingSignature"] = json.dumps(item)
                stream.push(  # type: ignore[arg-type]
                    {  # type: ignore[arg-type]
                        "type": "thinking_end",  # type: ignore[arg-type]
                        "contentIndex": block_index(),  # type: ignore[arg-type]
                        "content": current_block["thinking"],  # type: ignore[arg-type]
                        "partial": output,  # type: ignore[arg-type]
                    }  # type: ignore[arg-type]
                )
                current_block = None
            elif (
                item.get("type") == "message"
                and current_block
                and current_block.get("type") == "text"
            ):
                item_content = item.get("content", [])
                current_block["text"] = "".join(
                    c.get("text", "") if c.get("type") == "output_text" else c.get("refusal", "")
                    for c in item_content
                )
                current_block["text_signature"] = _encode_text_signature_v1(
                    item.get("id", ""), item.get("phase")
                )
                stream.push(  # type: ignore[arg-type]
                    {  # type: ignore[arg-type]
                        "type": "text_end",  # type: ignore[arg-type]
                        "contentIndex": block_index(),  # type: ignore[arg-type]
                        "content": current_block["text"],  # type: ignore[arg-type]
                        "partial": output,  # type: ignore[arg-type]
                    }  # type: ignore[arg-type]
                )
                current_block = None
            elif item.get("type") == "function_call":
                args: dict[str, Any]
                if (
                    current_block
                    and current_block.get("type") == "toolCall"
                    and current_block.get("partialJson")
                ):
                    args = parse_streaming_json(current_block["partialJson"])
                else:
                    args = parse_streaming_json(item.get("arguments", "{}"))

                tool_call: dict[str, Any]
                if current_block and current_block.get("type") == "toolCall":
                    # Finalize in-place and strip the scratch buffer
                    current_block["arguments"] = args
                    current_block.pop("partialJson", None)
                    tool_call = current_block
                else:
                    tool_call = {
                        "type": "toolCall",
                        "id": f"{item.get('call_id', '')}|{item.get('id', '')}",
                        "name": item.get("name", ""),
                        "arguments": args,
                    }

                current_block = None
                stream.push(  # type: ignore[arg-type]
                    {  # type: ignore[arg-type]
                        "type": "toolcall_end",  # type: ignore[arg-type]
                        "contentIndex": block_index(),  # type: ignore[arg-type]
                        "toolCall": tool_call,  # type: ignore[arg-type]
                        "partial": output,  # type: ignore[arg-type]
                    }  # type: ignore[arg-type]
                )
        elif event_type == "response.completed":
            response = event.get("response", {})
            if response and response.get("id"):
                output.response_id = response.get("id")
            if response and response.get("usage"):
                usage_data = response["usage"]
                cached_tokens = usage_data.get("input_tokens_details", {}).get("cached_tokens", 0)
                input_tokens = usage_data.get("input_tokens", 0)
                output.usage = Usage(
                    input=input_tokens - cached_tokens,
                    output=usage_data.get("output_tokens", 0),
                    cache_read=cached_tokens,
                    cache_write=0,
                    total_tokens=usage_data.get("total_tokens", 0),
                    cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
                )
            calculate_cost(model, output.usage)
            if options and options.applyServiceTierPricing:
                service_tier: str | None = None
                if options.resolveServiceTier:
                    service_tier = options.resolveServiceTier(
                        response.get("service_tier"), options.serviceTier
                    )
                else:
                    service_tier = response.get("service_tier") or options.serviceTier
                options.applyServiceTierPricing(output.usage, service_tier)
            # Map status to stop reason
            status = response.get("status") if response else None
            output.stop_reason = _map_stop_reason(status)
            if (
                any(isinstance(b, ToolCall) for b in output.content)
                and output.stop_reason == "stop"
            ):
                output.stop_reason = "toolUse"
        elif event_type == "error":
            code = event.get("code", "unknown")
            message = event.get("message", "Unknown error")
            raise Exception(f"Error Code {code}: {message}")
        elif event_type == "response.failed":
            response = event.get("response", {})
            error = response.get("error") if response else None
            details = response.get("incomplete_details") if response else None
            if error:
                msg = f"{error.get('code', 'unknown')}: {error.get('message', 'no message')}"
            elif details and details.get("reason"):
                msg = f"incomplete: {details['reason']}"
            else:
                msg = "Unknown error (no error details in response)"
            raise Exception(msg)


def _map_stop_reason(status: str | None) -> StopReason:
    """Map OpenAI Responses status to StopReason."""
    if not status:
        return "stop"
    # Keyed by ResponseStatus so adding a new status fails to compile until mapped
    openai_status_to_stop_reason: dict[str, StopReason] = {
        "completed": "stop",
        "incomplete": "length",
        "failed": "error",
        "cancelled": "error",
        "in_progress": "stop",
        "queued": "stop",
    }
    return openai_status_to_stop_reason.get(status, "stop")
