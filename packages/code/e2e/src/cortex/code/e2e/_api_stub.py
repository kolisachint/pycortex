"""A provider API on localhost, so one scenario can use the real provider code.

Every other scenario replaces the provider: `faux_session` registers
`ai/provider-faux`, and a couple hand the agent a `stream_fn` of their own. That
is right for them — they are about what the *screen* does — and it means the
whole provider half of the product has never run in the corpus: the HTTP
request, its headers, the SSE parse, the usage numbers on the way back.

`e2e/first-run` is the scenario that must not skip it, so it swaps the smallest
possible piece: the endpoint. `models.json` supports a per-provider `base_url`
override (it is how a user points the tool at a gateway), so the clean install
the scenario builds points `anthropic` at this server. Everything above it —
`ModelRegistry`, `find_initial_model`, `cortex.ai.providers.anthropic`, the
agent loop — is the code a real run uses.

The recorded requests are half the value: a scenario can assert the key reached
the header and the prompt reached the body, which is the part of "it answered"
that the screen cannot show.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self

__all__ = [
    "AnthropicApiStub",
    "OpenAICompletionsApiStub",
    "RecordedRequest",
    "anthropic_sse",
    "openai_completions_sse",
]


@dataclass
class RecordedRequest:
    """One request the stand-in answered."""

    path: str
    headers: dict[str, str]
    body: dict[str, Any]


def anthropic_sse(
    text: str, *, input_tokens: int = 11, output_tokens: int = 7, stop_reason: str = "end_turn"
) -> bytes:
    """One complete text response, as the Messages API streams it.

    The event sequence the provider's parser requires, in order: it raises if
    `message_start` is never closed by `message_stop`, so a stub that sends a
    convenient subset would fail against the real code — which is the point.
    """
    events: list[tuple[str, dict[str, Any]]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_stub",
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason},
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    lines: list[str] = []
    for name, data in events:
        lines.append(f"event: {name}")
        lines.append(f"data: {json.dumps(data)}")
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def openai_completions_sse(
    text: str, *, prompt_tokens: int = 11, completion_tokens: int = 7, finish_reason: str = "stop"
) -> bytes:
    """One complete text response, as Chat Completions streams it.

    The chunk sequence the `openai` SDK's stream parser expects: an opening
    delta carrying the role, a content delta, a chunk bearing `finish_reason`,
    a choice-less usage chunk (what `stream_options.include_usage` produces),
    and the `[DONE]` sentinel that ends the iteration.
    """
    chunk_base: dict[str, Any] = {
        "id": "chatcmpl-stub",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "stub-model",
    }
    chunks: list[dict[str, Any]] = [
        {**chunk_base, "choices": [{"index": 0, "delta": {"role": "assistant"}}]},
        {**chunk_base, "choices": [{"index": 0, "delta": {"content": text}}]},
        {**chunk_base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]},
        {
            **chunk_base,
            "choices": [],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        },
    ]
    lines: list[str] = []
    for chunk in chunks:
        lines.append(f"data: {json.dumps(chunk)}")
        lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


@dataclass
class _ApiStub:
    """The server half, shared by the per-API stubs below.

    Only the response body differs between them; the socket, the recording and
    the shutdown are identical, and a second copy of them is a second place for
    a leaked thread to hide.
    """

    reply: str = "Hello from the stand-in."
    requests: list[RecordedRequest] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    def _response_body(self) -> bytes:
        raise NotImplementedError

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("the stub is not running")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> Self:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
                length = int(self.headers.get("content-length", "0"))
                raw = self.rfile.read(length)
                stub.requests.append(
                    RecordedRequest(
                        path=self.path,
                        headers={key.lower(): value for key, value in self.headers.items()},
                        body=json.loads(raw or b"{}"),
                    )
                )
                body = stub._response_body()
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                """Silence the default stderr access log."""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()


@dataclass
class AnthropicApiStub(_ApiStub):
    """A Messages API on 127.0.0.1 that answers with `reply`, and records asks."""

    def _response_body(self) -> bytes:
        return anthropic_sse(self.reply)


@dataclass
class OpenAICompletionsApiStub(_ApiStub):
    """A Chat Completions API on 127.0.0.1, for the `openai-completions` path.

    The Anthropic stub cannot stand in here: `stream_openai_completions` drives
    the official `openai` SDK, which parses a different wire format and would
    reject the Messages API's event stream.
    """

    def _response_body(self) -> bytes:
        return openai_completions_sse(self.reply)
