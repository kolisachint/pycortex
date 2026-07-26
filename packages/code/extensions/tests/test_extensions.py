# pyright: reportMissingParameterType=false, reportUnknownParameterType=false
"""Tests for extension system.

Tests verify:
- Extension metadata
- Extension loading
- Extension activation/deactivation
- Extension registry
- Event handling
"""

from __future__ import annotations

from cortex.code.extensions import (
    ExtensionContext,
    ExtensionInfo,
    ExtensionLoader,
    ExtensionMetadata,
    ExtensionRegistry,
)


class TestExtensionMetadata:
    def test_default_values(self):
        metadata = ExtensionMetadata(
            name="test-extension",
            version="1.0.0",
            description="A test extension",
        )
        assert metadata.name == "test-extension"
        assert metadata.version == "1.0.0"
        assert metadata.description == "A test extension"
        assert metadata.author is None
        assert metadata.homepage is None
        assert metadata.keywords == []

    def test_with_optional_fields(self):
        metadata = ExtensionMetadata(
            name="test-extension",
            version="1.0.0",
            description="A test extension",
            author="Test Author",
            homepage="https://example.com",
            keywords=["test", "example"],
        )
        assert metadata.author == "Test Author"
        assert metadata.homepage == "https://example.com"
        assert metadata.keywords == ["test", "example"]


class TestExtensionContext:
    def test_default_values(self):
        metadata = ExtensionMetadata(
            name="test",
            version="1.0.0",
            description="Test",
        )
        context = ExtensionContext(metadata=metadata)
        assert context.metadata == metadata
        assert context.config == {}
        assert context.state == {}

    def test_with_config(self):
        metadata = ExtensionMetadata(
            name="test",
            version="1.0.0",
            description="Test",
        )
        context = ExtensionContext(
            metadata=metadata,
            config={"key": "value"},
        )
        assert context.config == {"key": "value"}


class TestExtensionInfo:
    def test_default_values(self):
        metadata = ExtensionMetadata(
            name="test",
            version="1.0.0",
            description="Test",
        )
        context = ExtensionContext(metadata=metadata)
        info = ExtensionInfo(
            metadata=metadata,
            module=None,
            context=context,
        )
        assert info.metadata == metadata
        assert info.module is None
        assert info.context == context
        assert info.is_active is False


class TestExtensionLoader:
    def test_initialization(self):
        loader = ExtensionLoader()
        assert loader.extension_paths == []
        assert loader.extensions == {}

    def test_with_paths(self):
        loader = ExtensionLoader(extension_paths=["/path/to/extensions"])
        assert loader.extension_paths == ["/path/to/extensions"]

    def test_load_nonexistent_extension(self):
        loader = ExtensionLoader()
        info = loader.load_extension("/nonexistent/path.py")
        assert info is None

    def test_list_extensions_empty(self):
        loader = ExtensionLoader()
        extensions = loader.list_extensions()
        assert extensions == []

    def test_get_extension_nonexistent(self):
        loader = ExtensionLoader()
        ext = loader.get_extension("nonexistent")
        assert ext is None

    def test_activate_nonexistent_extension(self):
        loader = ExtensionLoader()
        result = loader.activate_extension("nonexistent")
        assert result is False

    def test_deactivate_nonexistent_extension(self):
        loader = ExtensionLoader()
        result = loader.deactivate_extension("nonexistent")
        assert result is False

    def test_unload_nonexistent_extension(self):
        loader = ExtensionLoader()
        result = loader.unload_extension("nonexistent")
        assert result is False

    def test_event_handlers(self):
        loader = ExtensionLoader()
        events = []

        def handler(value):
            events.append(value)

        loader.on("test_event", handler)
        loader.emit("test_event", "hello")
        assert events == ["hello"]

    def test_multiple_event_handlers(self):
        loader = ExtensionLoader()
        events = []

        def handler1(value):
            events.append(f"h1:{value}")

        def handler2(value):
            events.append(f"h2:{value}")

        loader.on("test_event", handler1)
        loader.on("test_event", handler2)
        loader.emit("test_event", "hello")
        assert events == ["h1:hello", "h2:hello"]

    def test_event_handler_exception(self):
        loader = ExtensionLoader()

        def bad_handler(value):
            raise ValueError("Bad handler")

        def good_handler(value):
            pass

        loader.on("test_event", bad_handler)
        loader.on("test_event", good_handler)

        # Should not raise
        loader.emit("test_event", "hello")


class TestExtensionRegistry:
    def test_initialization(self):
        registry = ExtensionRegistry()
        assert registry.loaders == {}
        assert registry.global_handlers == {}

    def test_register_loader(self):
        registry = ExtensionRegistry()
        loader = ExtensionLoader()
        registry.register_loader("test", loader)
        assert "test" in registry.loaders

    def test_get_loader(self):
        registry = ExtensionRegistry()
        loader = ExtensionLoader()
        registry.register_loader("test", loader)
        assert registry.get_loader("test") is loader

    def test_get_loader_nonexistent(self):
        registry = ExtensionRegistry()
        assert registry.get_loader("nonexistent") is None

    def test_activate_all(self):
        registry = ExtensionRegistry()
        loader = ExtensionLoader()
        registry.register_loader("test", loader)

        # No extensions loaded
        count = registry.activate_all()
        assert count == 0

    def test_deactivate_all(self):
        registry = ExtensionRegistry()
        loader = ExtensionLoader()
        registry.register_loader("test", loader)

        # No extensions loaded
        count = registry.deactivate_all()
        assert count == 0
