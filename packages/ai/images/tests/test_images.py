"""Tests for images module.

Mechanical port of hoocode's images tests (if any).
"""

from __future__ import annotations

import pytest
from cortex.ai.images import (
    AssistantImages,
    ImageContent,
    ImagesApiProvider,
    ImagesContext,
    ImagesModel,
    ImagesOptions,
    ImagesUsage,
    TextContent,
    generate_images,
    get_images_api_provider,
    register_images_api_provider,
)
from cortex.ai.images.api_registry import clear_images_api_providers

# ---------------------------------------------------------------------------
# Types tests
# ---------------------------------------------------------------------------


class TestTypes:
    def test_image_content(self) -> None:
        content = ImageContent(mime_type="image/png", data="base64data")
        assert content.type == "image"
        assert content.mime_type == "image/png"
        assert content.data == "base64data"

    def test_text_content(self) -> None:
        content = TextContent(text="Hello")
        assert content.type == "text"
        assert content.text == "Hello"

    def test_assistant_images(self) -> None:
        result = AssistantImages(
            api="openrouter-images",
            provider="openrouter",
            model="test-model",
        )
        assert result.api == "openrouter-images"
        assert result.stop_reason == "stop"
        assert result.output == []

    def test_images_usage(self) -> None:
        usage = ImagesUsage(input=100, output=50, total_tokens=150)
        assert usage.input == 100
        assert usage.output == 50
        assert usage.total_tokens == 150

    def test_images_model(self) -> None:
        model = ImagesModel(
            id="test-model",
            name="Test Model",
            api="openrouter-images",
            provider="openrouter",
        )
        assert model.id == "test-model"
        assert model.api == "openrouter-images"

    def test_images_context(self) -> None:
        context = ImagesContext(input=[TextContent(text="Generate an image")])
        assert len(context.input) == 1


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestRegistry:
    def setup_method(self) -> None:
        clear_images_api_providers()

    def test_register_and_get_provider(self) -> None:
        async def dummy_generate(
            model: ImagesModel,
            context: ImagesContext,
            options: ImagesOptions | None = None,
        ) -> AssistantImages:
            return AssistantImages()

        provider = ImagesApiProvider(
            api="openrouter-images",
            generate_images=dummy_generate,
        )
        register_images_api_provider(provider)

        retrieved = get_images_api_provider("openrouter-images")
        assert retrieved is not None
        assert retrieved.api == "openrouter-images"

    def test_get_provider_returns_none_for_unknown(self) -> None:
        provider = get_images_api_provider("unknown-api")
        assert provider is None

    def test_register_validates_api(self) -> None:
        async def dummy_generate(
            model: ImagesModel,
            context: ImagesContext,
            options: ImagesOptions | None = None,
        ) -> AssistantImages:
            return AssistantImages()

        provider = ImagesApiProvider(
            api="openrouter-images",
            generate_images=dummy_generate,
        )
        register_images_api_provider(provider)

        retrieved = get_images_api_provider("openrouter-images")
        assert retrieved is not None


# ---------------------------------------------------------------------------
# Generate images tests
# ---------------------------------------------------------------------------


class TestGenerateImages:
    def setup_method(self) -> None:
        clear_images_api_providers()

    async def test_generate_images_raises_for_unknown_api(self) -> None:
        model = ImagesModel(
            id="test",
            name="Test",
            api="unknown-api",  # type: ignore[arg-type]
            provider="test",
        )
        context = ImagesContext()

        with pytest.raises(ValueError, match="No API provider registered"):
            await generate_images(model, context)

    async def test_generate_images_with_registered_provider(self) -> None:
        async def mock_generate(
            model: ImagesModel,
            context: ImagesContext,
            options: ImagesOptions | None = None,
        ) -> AssistantImages:
            return AssistantImages(
                api=model.api,
                provider=model.provider,
                model=model.id,
                output=[TextContent(text="Generated image")],
                stop_reason="stop",
            )

        provider = ImagesApiProvider(
            api="openrouter-images",
            generate_images=mock_generate,
        )
        register_images_api_provider(provider)

        model = ImagesModel(
            id="test-model",
            name="Test",
            api="openrouter-images",
            provider="test",
        )
        context = ImagesContext(input=[TextContent(text="Generate")])

        result = await generate_images(model, context)
        assert result.stop_reason == "stop"
        assert len(result.output) == 1
        assert result.output[0].text == "Generated image"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Provider tests
# ---------------------------------------------------------------------------


class TestProvider:
    def test_images_api_provider_creation(self) -> None:
        async def dummy_generate(
            model: ImagesModel,
            context: ImagesContext,
            options: ImagesOptions | None = None,
        ) -> AssistantImages:
            return AssistantImages()

        provider = ImagesApiProvider(
            api="openrouter-images",
            generate_images=dummy_generate,
        )
        assert provider.api == "openrouter-images"
        assert callable(provider.generate_images)
