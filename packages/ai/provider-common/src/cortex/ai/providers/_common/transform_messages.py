from __future__ import annotations

import time
from collections.abc import Callable

from cortex.ai.types import (
    AssistantMessage,
    ImageContent,
    Message,
    Model,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
)

NON_VISION_USER_IMAGE_PLACEHOLDER = "(image omitted: model does not support images)"
NON_VISION_TOOL_IMAGE_PLACEHOLDER = "(tool image omitted: model does not support images)"


def _replace_images_with_placeholder(
    content: list[TextContent | ImageContent], placeholder: str
) -> list[TextContent]:
    result: list[TextContent] = []
    previous_was_placeholder = False

    for block in content:
        if block.type == "image":
            if not previous_was_placeholder:
                result.append(TextContent(type="text", text=placeholder))
            previous_was_placeholder = True
            continue

        result.append(block)
        previous_was_placeholder = block.text == placeholder

    return result


def _downgrade_unsupported_images(messages: list[Message], model: Model) -> list[Message]:
    if "image" in model.input:
        return messages

    result: list[Message] = []
    for msg in messages:
        if msg.role == "user" and isinstance(msg.content, list):
            result.append(
                msg.model_copy(
                    update={
                        "content": _replace_images_with_placeholder(
                            msg.content, NON_VISION_USER_IMAGE_PLACEHOLDER
                        )
                    }
                )
            )
        elif msg.role == "toolResult":
            result.append(
                msg.model_copy(
                    update={
                        "content": _replace_images_with_placeholder(
                            msg.content, NON_VISION_TOOL_IMAGE_PLACEHOLDER
                        )
                    }
                )
            )
        else:
            result.append(msg)
    return result


def transform_messages(
    messages: list[Message],
    model: Model,
    normalize_tool_call_id: Callable[[str, Model, AssistantMessage], str] | None = None,
) -> list[Message]:
    """Normalize tool call ID for cross-provider compatibility.

    OpenAI Responses API generates IDs that are 450+ chars with special
    characters like ``|``. Anthropic APIs require IDs matching
    ``^[a-zA-Z0-9_-]+$`` (max 64 chars).
    """
    # Build a map of original tool call IDs to normalized IDs
    tool_call_id_map: dict[str, str] = {}
    image_aware_messages = _downgrade_unsupported_images(messages, model)

    # First pass: transform messages (unsupported image downgrade, thinking
    # blocks, tool call ID normalization)
    transformed: list[Message] = []
    for msg in image_aware_messages:
        # User messages pass through unchanged
        if msg.role == "user":
            transformed.append(msg)
            continue

        # Handle toolResult messages - normalize toolCallId if we have a mapping
        if msg.role == "toolResult":
            normalized_id = tool_call_id_map.get(msg.tool_call_id)
            if normalized_id and normalized_id != msg.tool_call_id:
                transformed.append(msg.model_copy(update={"tool_call_id": normalized_id}))
            else:
                transformed.append(msg)
            continue

        # Assistant messages need transformation check
        if msg.role == "assistant":
            assistant_msg = msg
            is_same_model = (
                assistant_msg.provider == model.provider
                and assistant_msg.api == model.api
                and assistant_msg.model == model.id
            )

            transformed_content: list[TextContent | ThinkingContent | ToolCall] = []
            for block in assistant_msg.content:
                if block.type == "thinking":
                    # Redacted thinking is opaque encrypted content, only valid for
                    # the same model. Drop it for cross-model to avoid API errors.
                    if block.redacted:
                        if is_same_model:
                            transformed_content.append(block)
                        continue
                    # For same model: keep thinking blocks with signatures (needed
                    # for replay) even if the thinking text is empty (OpenAI
                    # encrypted reasoning)
                    if is_same_model and block.thinking_signature:
                        transformed_content.append(block)
                        continue
                    # Skip empty thinking blocks, convert others to plain text
                    if not block.thinking or block.thinking.strip() == "":
                        continue
                    if is_same_model:
                        transformed_content.append(block)
                        continue
                    transformed_content.append(TextContent(type="text", text=block.thinking))
                    continue

                if block.type == "text":
                    if is_same_model:
                        transformed_content.append(block)
                        continue
                    transformed_content.append(TextContent(type="text", text=block.text))
                    continue

                if block.type == "toolCall":
                    tool_call = block
                    normalized_tool_call = tool_call

                    if not is_same_model and tool_call.thought_signature:
                        normalized_tool_call = normalized_tool_call.model_copy(
                            update={"thought_signature": None}
                        )

                    if not is_same_model and normalize_tool_call_id:
                        normalized_id = normalize_tool_call_id(tool_call.id, model, assistant_msg)
                        if normalized_id != tool_call.id:
                            tool_call_id_map[tool_call.id] = normalized_id
                            normalized_tool_call = normalized_tool_call.model_copy(
                                update={"id": normalized_id}
                            )

                    transformed_content.append(normalized_tool_call)
                    continue

                transformed_content.append(block)

            transformed.append(assistant_msg.model_copy(update={"content": transformed_content}))
            continue

        transformed.append(msg)

    # Second pass: insert synthetic empty tool results for orphaned tool calls
    # This preserves thinking signatures and satisfies API requirements
    result: list[Message] = []
    pending_tool_calls: list[ToolCall] = []
    existing_tool_result_ids: set[str] = set()

    def insert_synthetic_tool_results() -> None:
        nonlocal pending_tool_calls, existing_tool_result_ids
        if len(pending_tool_calls) > 0:
            for tc in pending_tool_calls:
                if tc.id not in existing_tool_result_ids:
                    result.append(
                        ToolResultMessage(
                            role="toolResult",
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            content=[TextContent(type="text", text="No result provided")],
                            is_error=True,
                            timestamp=int(time.time() * 1000),
                        )
                    )
            pending_tool_calls = []
            existing_tool_result_ids = set()

    for msg in transformed:
        if msg.role == "assistant":
            # If we have pending orphaned tool calls from a previous assistant,
            # insert synthetic results now
            insert_synthetic_tool_results()

            # Skip errored/aborted assistant messages entirely.
            # These are incomplete turns that shouldn't be replayed:
            # - May have partial content (reasoning without message, incomplete
            #   tool calls)
            # - Replaying them can cause API errors (e.g., OpenAI "reasoning
            #   without following item")
            # - The model should retry from the last valid state
            assistant_msg = msg
            if assistant_msg.stop_reason in ("error", "aborted"):
                continue

            # Track tool calls from this assistant message
            tool_calls = [b for b in assistant_msg.content if b.type == "toolCall"]
            if len(tool_calls) > 0:
                pending_tool_calls = tool_calls
                existing_tool_result_ids = set()

            result.append(msg)
        elif msg.role == "toolResult":
            existing_tool_result_ids.add(msg.tool_call_id)
            result.append(msg)
        elif msg.role == "user":
            # User message interrupts tool flow - insert synthetic results for
            # orphaned calls
            insert_synthetic_tool_results()
            result.append(msg)
        else:
            result.append(msg)

    # If the conversation ends with unresolved tool calls, synthesize results now.
    insert_synthetic_tool_results()

    return result
