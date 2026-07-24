from __future__ import annotations

from typing import Literal

from cortex.ai.types import Message


def infer_copilot_initiator(messages: list[Message]) -> Literal["user", "agent"]:
    # Copilot expects X-Initiator to indicate whether the request is
    # user-initiated or agent-initiated (e.g. follow-up after assistant/tool
    # messages).
    last = messages[-1] if messages else None
    return "agent" if last and last.role != "user" else "user"


def has_copilot_vision_input(messages: list[Message]) -> bool:
    # Copilot requires Copilot-Vision-Request header when sending images
    for msg in messages:
        if msg.role == "user" and isinstance(msg.content, list):
            if any(c.type == "image" for c in msg.content):
                return True
        if msg.role == "toolResult":
            if any(c.type == "image" for c in msg.content):
                return True
    return False


def build_copilot_dynamic_headers(
    messages: list[Message],
    has_images: bool,
) -> dict[str, str]:
    headers: dict[str, str] = {
        "X-Initiator": infer_copilot_initiator(messages),
        "Openai-Intent": "conversation-edits",
    }

    if has_images:
        headers["Copilot-Vision-Request"] = "true"

    return headers
