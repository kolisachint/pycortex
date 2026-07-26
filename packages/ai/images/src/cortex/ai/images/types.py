"""Image generation types.

Mechanical port of hoocode's image-related types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# Image API types
ImagesApi = Literal["openrouter-images"]


@dataclass
class ImageContent:
    """Image content in a response."""

    type: Literal["image"] = "image"
    mime_type: str = ""
    data: str = ""


@dataclass
class TextContent:
    """Text content in a response."""

    type: Literal["text"] = "text"
    text: str = ""


@dataclass
class ImagesUsage:
    """Usage statistics for image generation."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    cost: dict[str, float] = field(
        default_factory=lambda: {
            "input": 0,
            "output": 0,
            "cacheRead": 0,
            "cacheWrite": 0,
            "total": 0,
        }
    )


@dataclass
class AssistantImages:
    """Result of image generation."""

    api: str = ""
    provider: str = ""
    model: str = ""
    output: list[ImageContent | TextContent] = field(default_factory=list)
    stop_reason: str = "stop"
    error_message: str | None = None
    response_id: str | None = None
    usage: ImagesUsage | None = None
    timestamp: int = 0


@dataclass
class ImagesModel:
    """Model for image generation."""

    id: str
    name: str
    api: ImagesApi
    provider: str
    base_url: str = ""
    headers: dict[str, str] | None = None
    output: list[str] = field(default_factory=lambda: ["image"])
    cost: dict[str, float] = field(
        default_factory=lambda: {
            "input": 0,
            "output": 0,
            "cacheRead": 0,
            "cacheWrite": 0,
        }
    )


@dataclass
class ImagesContext:
    """Context for image generation."""

    input: list[TextContent | ImageContent] = field(default_factory=list)


@dataclass
class ImagesOptions:
    """Options for image generation."""

    api_key: str | None = None
    headers: dict[str, str] | None = None
    signal: Any = None
    timeout_ms: int | None = None
    max_retries: int | None = None
    on_payload: Any = None
    on_response: Any = None


# Type alias for image generation function
class ImagesFunction(Protocol):
    """Function that generates images."""

    def __call__(
        self,
        model: ImagesModel,
        context: ImagesContext,
        options: ImagesOptions | None = None,
    ) -> Any: ...
