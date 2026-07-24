"""Generic async event stream with a final result.

Port of hoocode's `packages/ai/src/utils/event-stream.ts`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Generic, TypeVar

from cortex.ai.types import AssistantMessage, AssistantMessageEvent

T = TypeVar("T")
R = TypeVar("R")

_END = object()  # sentinel: a waiter woke up because the stream ended


class EventStream(Generic[T, R]):
    """Async-iterable queue of events that resolves to a final result.

    Producers call `push()` for each event and `end()` once no more events
    will be pushed. Consumers iterate with `async for` and can await
    `result()` to get the final value (derived from the completing event, or
    from an explicit `end(result)`).
    """

    def __init__(
        self,
        is_complete: Callable[[T], bool],
        extract_result: Callable[[T], R],
    ) -> None:
        self._is_complete = is_complete
        self._extract_result = extract_result
        self._queue: list[T] = []
        self._waiting: list[asyncio.Future[object]] = []
        self._done = False
        self._final_result_future: asyncio.Future[R] = asyncio.get_running_loop().create_future()

    def push(self, event: T) -> None:
        if self._done:
            return

        if self._is_complete(event):
            self._done = True
            if not self._final_result_future.done():
                self._final_result_future.set_result(self._extract_result(event))

        # Deliver to waiting consumer or queue it
        if self._waiting:
            waiter = self._waiting.pop(0)
            if not waiter.done():
                waiter.set_result(event)
        else:
            self._queue.append(event)

    def end(self, result: R | None = None) -> None:
        self._done = True
        if result is not None and not self._final_result_future.done():
            self._final_result_future.set_result(result)
        # Notify all waiting consumers that we're done
        while self._waiting:
            waiter = self._waiting.pop(0)
            if not waiter.done():
                waiter.set_result(_END)

    def __aiter__(self) -> AsyncIterator[T]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[T]:
        while True:
            if self._queue:
                yield self._queue.pop(0)
            elif self._done:
                return
            else:
                waiter: asyncio.Future[object] = asyncio.get_running_loop().create_future()
                self._waiting.append(waiter)
                value = await waiter
                if value is _END:
                    return
                yield value  # type: ignore[misc]

    async def result(self) -> R:
        return await self._final_result_future


class AssistantMessageEventStream(EventStream[AssistantMessageEvent, AssistantMessage]):
    """Event stream specialized for `AssistantMessageEvent` → `AssistantMessage`."""

    def __init__(self) -> None:
        def is_complete(event: AssistantMessageEvent) -> bool:
            return event.type in ("done", "error")

        def extract_result(event: AssistantMessageEvent) -> AssistantMessage:
            if event.type == "done":
                return event.message
            if event.type == "error":
                return event.error
            raise ValueError("Unexpected event type for final result")

        super().__init__(is_complete, extract_result)


def create_assistant_message_event_stream() -> AssistantMessageEventStream:
    """Factory function for `AssistantMessageEventStream` (for use in extensions)."""
    return AssistantMessageEventStream()
