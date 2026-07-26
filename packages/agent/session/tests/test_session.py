# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedFunction=false, reportCallIssue=false, reportAssignmentType=false
"""Tests for the session module."""

from __future__ import annotations

import pytest
from cortex.agent.harness.types import (
    CompactionEntry,
    LabelEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionContext,
    SessionMetadata,
    SessionTreeEntry,
    ThinkingLevelChangeEntry,
)
from cortex.agent.session import Session, build_session_context
from cortex.agent.session.repo import InMemorySessionRepo
from cortex.agent.session.storage import InMemorySessionStorage
from cortex.ai.types import AssistantMessage, TextContent, Usage, UserMessage


def _create_user_message(content: str = "Hello") -> UserMessage:
    """Create a user message for testing."""
    return UserMessage(role="user", content=[TextContent(text=content)], timestamp=1704067200000)


def _create_assistant_message(
    content: str = "Hi there",
    provider: str = "test-provider",
    model: str = "test-model",
) -> AssistantMessage:
    """Create an assistant message for testing."""
    return AssistantMessage(
        role="assistant",
        content=[TextContent(text=content)],
        api="test-api",
        provider=provider,
        model=model,
        usage=Usage(input_tokens=10, output_tokens=5),
        stop_reason="end_turn",
        timestamp=1704067201000,
    )


class TestBuildSessionContext:
    """Tests for build_session_context function."""

    def test_empty_entries(self) -> None:
        """Test building context with empty entries."""
        context = build_session_context([])
        assert isinstance(context, SessionContext)
        assert context.messages == []
        assert context.thinking_level == "off"
        assert context.model is None

    def test_thinking_level_change(self) -> None:
        """Test that thinking level change entries are captured."""
        entry = ThinkingLevelChangeEntry(
            id="test-id",
            parent_id=None,
            timestamp=1704067200000,
            thinking_level="high",
        )
        context = build_session_context([entry])
        assert context.thinking_level == "high"

    def test_model_change(self) -> None:
        """Test that model change entries are captured."""
        entry = ModelChangeEntry(
            id="test-id",
            parent_id=None,
            timestamp=1704067200000,
            provider="test-provider",
            model_id="test-model",
        )
        context = build_session_context([entry])
        assert context.model is not None
        assert context.model["provider"] == "test-provider"
        assert context.model["model_id"] == "test-model"

    def test_message_entry(self) -> None:
        """Test that message entries are converted to AgentMessage."""
        msg = _create_user_message("Hello")
        entry = MessageEntry(
            id="test-id",
            parent_id=None,
            timestamp=1704067200000,
            message=msg,
        )
        context = build_session_context([entry])
        assert len(context.messages) == 1
        assert context.messages[0] is msg

    def test_multiple_entries(self) -> None:
        """Test building context with multiple entries."""
        entries: list[SessionTreeEntry] = [
            ThinkingLevelChangeEntry(
                id="id-1",
                parent_id=None,
                timestamp=1704067200000,
                thinking_level="high",
            ),
            ModelChangeEntry(
                id="id-2",
                parent_id="id-1",
                timestamp=1704067201000,
                provider="openai",
                model_id="gpt-4",
            ),
            MessageEntry(
                id="id-3",
                parent_id="id-2",
                timestamp=1704067202000,
                message=_create_user_message(),
            ),
        ]
        context = build_session_context(entries)
        assert context.thinking_level == "high"
        assert context.model is not None
        assert context.model["provider"] == "openai"
        assert len(context.messages) == 1

    def test_compaction_entry(self) -> None:
        """Test that compaction entries are handled."""
        msg = _create_user_message("Hello")
        entries: list[SessionTreeEntry] = [
            MessageEntry(
                id="msg-1",
                parent_id=None,
                timestamp="2024-01-01T00:00:00Z",
                message=msg,
            ),
            CompactionEntry(
                id="compact-1",
                parent_id="msg-1",
                timestamp="2024-01-01T00:00:01Z",
                summary="Previous context compacted",
                first_kept_entry_id="msg-1",
                tokens_before=100,
                tokens_after=50,
            ),
        ]
        context = build_session_context(entries)
        # The compaction summary message is added plus the kept messages
        assert len(context.messages) >= 1


class TestSession:
    """Tests for Session class."""

    @pytest.fixture
    def storage(self) -> InMemorySessionStorage:
        """Create an in-memory storage for testing."""
        return InMemorySessionStorage()

    @pytest.fixture
    def session(self, storage: InMemorySessionStorage) -> Session:
        """Create a session for testing."""
        return Session(storage)

    @pytest.mark.asyncio
    async def test_get_metadata(self, session: Session) -> None:
        """Test getting session metadata."""
        metadata = await session.get_metadata()
        assert metadata is not None
        assert metadata.id is not None

    @pytest.mark.asyncio
    async def test_get_storage(self, session: Session) -> None:
        """Test getting the underlying storage."""
        assert session.get_storage() is not None

    @pytest.mark.asyncio
    async def test_get_leaf_id_initial(self, session: Session) -> None:
        """Test getting initial leaf ID."""
        leaf_id = await session.get_leaf_id()
        assert leaf_id is None

    @pytest.mark.asyncio
    async def test_get_entries_initial(self, session: Session) -> None:
        """Test getting initial entries."""
        entries = await session.get_entries()
        assert entries == []

    @pytest.mark.asyncio
    async def test_build_context(self, session: Session) -> None:
        """Test building session context."""
        context = await session.build_context()
        assert isinstance(context, SessionContext)
        assert context.messages == []

    @pytest.mark.asyncio
    async def test_get_session_name(self, session: Session) -> None:
        """Test getting session name."""
        name = await session.get_session_name()
        assert name is None


class TestInMemorySessionStorage:
    """Tests for InMemorySessionStorage."""

    def test_initial_state(self) -> None:
        """Test initial storage state."""
        storage = InMemorySessionStorage()
        assert storage._entries == []
        assert storage._leaf_id is None

    @pytest.mark.asyncio
    async def test_append_entry(self) -> None:
        """Test appending an entry."""
        storage = InMemorySessionStorage()
        entry = ThinkingLevelChangeEntry(
            id="test-id",
            parent_id=None,
            timestamp=1704067200000,
            thinking_level="high",
        )
        await storage.append_entry(entry)
        assert len(storage._entries) == 1
        assert storage._leaf_id == "test-id"

    @pytest.mark.asyncio
    async def test_get_entry(self) -> None:
        """Test getting an entry by ID."""
        storage = InMemorySessionStorage()
        entry = ThinkingLevelChangeEntry(
            id="test-id",
            parent_id=None,
            timestamp=1704067200000,
            thinking_level="high",
        )
        await storage.append_entry(entry)
        retrieved = await storage.get_entry("test-id")
        assert retrieved is entry

    @pytest.mark.asyncio
    async def test_get_entry_not_found(self) -> None:
        """Test getting a non-existent entry."""
        storage = InMemorySessionStorage()
        retrieved = await storage.get_entry("non-existent")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_set_leaf_id(self) -> None:
        """Test setting leaf ID."""
        storage = InMemorySessionStorage()
        entry = ThinkingLevelChangeEntry(
            id="test-id",
            parent_id=None,
            timestamp=1704067200000,
            thinking_level="high",
        )
        await storage.append_entry(entry)
        await storage.set_leaf_id(None)
        assert storage._leaf_id is None

    @pytest.mark.asyncio
    async def test_set_leaf_id_not_found(self) -> None:
        """Test setting leaf ID with non-existent entry."""
        storage = InMemorySessionStorage()
        with pytest.raises(ValueError, match="not found"):
            await storage.set_leaf_id("non-existent")

    @pytest.mark.asyncio
    async def test_create_entry_id(self) -> None:
        """Test creating a unique entry ID."""
        storage = InMemorySessionStorage()
        id1 = await storage.create_entry_id()
        id2 = await storage.create_entry_id()
        assert id1 != id2

    @pytest.mark.asyncio
    async def test_find_entries(self) -> None:
        """Test finding entries by type."""
        storage = InMemorySessionStorage()
        await storage.append_entry(
            ThinkingLevelChangeEntry(
                id="id-1",
                parent_id=None,
                timestamp=1704067200000,
                thinking_level="high",
            )
        )
        await storage.append_entry(
            ModelChangeEntry(
                id="id-2",
                parent_id="id-1",
                timestamp=1704067201000,
                provider="openai",
                model_id="gpt-4",
            )
        )
        thinking_entries = await storage.find_entries("thinking_level_change")
        assert len(thinking_entries) == 1
        model_entries = await storage.find_entries("model_change")
        assert len(model_entries) == 1

    @pytest.mark.asyncio
    async def test_get_path_to_root(self) -> None:
        """Test getting path to root."""
        storage = InMemorySessionStorage()
        entry1 = ThinkingLevelChangeEntry(
            id="id-1",
            parent_id=None,
            timestamp=1704067200000,
            thinking_level="high",
        )
        entry2 = ModelChangeEntry(
            id="id-2",
            parent_id="id-1",
            timestamp=1704067201000,
            provider="openai",
            model_id="gpt-4",
        )
        await storage.append_entry(entry1)
        await storage.append_entry(entry2)
        path = await storage.get_path_to_root("id-2")
        assert len(path) == 2
        assert path[0].id == "id-1"
        assert path[1].id == "id-2"

    @pytest.mark.asyncio
    async def test_get_path_to_root_null(self) -> None:
        """Test getting path to root with null leaf."""
        storage = InMemorySessionStorage()
        path = await storage.get_path_to_root(None)
        assert path == []

    @pytest.mark.asyncio
    async def test_label_management(self) -> None:
        """Test label management."""
        storage = InMemorySessionStorage()
        entry = ThinkingLevelChangeEntry(
            id="test-id",
            parent_id=None,
            timestamp=1704067200000,
            thinking_level="high",
        )
        await storage.append_entry(entry)
        label = LabelEntry(
            id="label-id",
            parent_id="test-id",
            timestamp=1704067201000,
            target_id="test-id",
            label="test-label",
        )
        await storage.append_entry(label)
        retrieved_label = await storage.get_label("test-id")
        assert retrieved_label == "test-label"


class TestInMemorySessionRepo:
    """Tests for InMemorySessionRepo."""

    @pytest.mark.asyncio
    async def test_create_session(self) -> None:
        """Test creating a session."""
        repo = InMemorySessionRepo()
        session = await repo.create()
        assert session is not None
        metadata = await session.get_metadata()
        assert metadata.id is not None

    @pytest.mark.asyncio
    async def test_create_session_with_id(self) -> None:
        """Test creating a session with custom ID."""
        repo = InMemorySessionRepo()
        session = await repo.create({"id": "custom-id"})
        metadata = await session.get_metadata()
        assert metadata.id == "custom-id"

    @pytest.mark.asyncio
    async def test_open_session(self) -> None:
        """Test opening a session."""
        repo = InMemorySessionRepo()
        session = await repo.create()
        metadata = await session.get_metadata()
        opened = await repo.open(metadata)
        assert opened is session

    @pytest.mark.asyncio
    async def test_open_session_not_found(self) -> None:
        """Test opening a non-existent session."""
        repo = InMemorySessionRepo()
        metadata = SessionMetadata(id="non-existent", created_at=1704067200000)
        with pytest.raises(ValueError, match="not found"):
            await repo.open(metadata)

    @pytest.mark.asyncio
    async def test_list_sessions(self) -> None:
        """Test listing sessions."""
        repo = InMemorySessionRepo()
        await repo.create()
        await repo.create()
        sessions = await repo.list()
        assert len(sessions) == 2

    @pytest.mark.asyncio
    async def test_delete_session(self) -> None:
        """Test deleting a session."""
        repo = InMemorySessionRepo()
        session = await repo.create()
        metadata = await session.get_metadata()
        await repo.delete(metadata)
        sessions = await repo.list()
        assert len(sessions) == 0

    @pytest.mark.asyncio
    async def test_fork_session(self) -> None:
        """Test forking a session."""
        repo = InMemorySessionRepo()
        session = await repo.create()
        metadata = await session.get_metadata()
        forked = await repo.fork(metadata)
        assert forked is not session
        forked_metadata = await forked.get_metadata()
        assert forked_metadata.id != metadata.id
