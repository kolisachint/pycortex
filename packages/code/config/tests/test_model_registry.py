"""Port of ``test/model-registry.test.ts``, in the TS's order.

``models.json`` keys are snake_case here where the TS's are camelCase — the
port-wide convention for on-disk config, see the module docstring. Everything
else these assert is the TS's, including the built-in model ids they lean on
(``anthropic/claude-sonnet-4`` on openrouter is a real built-in in both).

``TestSchemaValidation`` is this port's own group: the TS gets its schema errors
from typebox, and this port hand-writes them, so the shape of a rejection is
code here and needs covering.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cortex.ai.models import get_api_provider, get_models
from cortex.ai.oauth import get_oauth_provider, reset_oauth_providers
from cortex.ai.types import AnthropicMessagesCompat, Model, OpenAICompletionsCompat
from cortex.code.config import BUILT_IN_PROVIDER_DISPLAY_NAMES
from cortex.code.config.auth_storage import ApiKeyCredential, AuthStorage
from cortex.code.config.model_registry import (
    ModelDefinitionInput,
    ModelRegistry,
    ProviderConfigInput,
    clear_api_key_cache,
)


@pytest.fixture(autouse=True)
def _clean_registries():  # pyright: ignore[reportUnusedFunction]
    yield
    clear_api_key_cache()
    reset_oauth_providers()


@pytest.fixture
def models_json_path(tmp_path: Path) -> str:
    return str(tmp_path / "models.json")


@pytest.fixture
def auth_storage(tmp_path: Path) -> AuthStorage:
    return AuthStorage.create(str(tmp_path / "auth.json"))


def write_models_json(path: str, providers: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps({"providers": providers}), encoding="utf-8")


def provider_config(
    base_url: str, models: list[dict[str, Any]], api: str = "anthropic-messages"
) -> dict[str, Any]:
    """The TS helper: a minimal provider with custom models."""
    return {
        "base_url": base_url,
        "api_key": "TEST_KEY",
        "api": api,
        "models": [
            {
                "id": m["id"],
                "name": m.get("name", m["id"]),
                "reasoning": False,
                "input": ["text"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "context_window": 100000,
                "max_tokens": 8000,
            }
            for m in models
        ],
    }


def override_config(base_url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"base_url": base_url}
    if headers:
        config["headers"] = headers
    return config


def models_for(registry: ModelRegistry, provider: str) -> list[Model]:
    return [m for m in registry.get_all() if m.provider == provider]


def model_definition(model_id: str, **overrides: Any) -> ModelDefinitionInput:
    return ModelDefinitionInput(
        id=model_id,
        name=overrides.pop("name", model_id),
        reasoning=overrides.pop("reasoning", False),
        input=overrides.pop("input", ["text"]),
        cost=overrides.pop("cost", {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}),
        context_window=overrides.pop("context_window", 100000),
        max_tokens=overrides.pop("max_tokens", 8000),
        **overrides,
    )


class TestBaseUrlOverride:
    """A provider block with no ``models``: the built-ins stay, the endpoint moves."""

    def test_overriding_base_url_keeps_all_built_in_models(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path, {"anthropic": override_config("https://my-proxy.example.com/v1")}
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        anthropic_models = models_for(registry, "anthropic")

        assert len(anthropic_models) > 1
        assert any("claude" in m.id for m in anthropic_models)

    def test_overriding_base_url_changes_url_on_all_built_in_models(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path, {"anthropic": override_config("https://my-proxy.example.com/v1")}
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        for model in models_for(registry, "anthropic"):
            assert model.base_url == "https://my-proxy.example.com/v1"

    async def test_overriding_headers_resolves_at_request_time(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "anthropic": override_config(
                    "https://my-proxy.example.com/v1", {"X-Custom-Header": "custom-value"}
                )
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        for model in models_for(registry, "anthropic"):
            auth = await registry.get_api_key_and_headers(model)
            assert auth.ok is True
            assert auth.headers is not None
            assert auth.headers["X-Custom-Header"] == "custom-value"

    async def test_headers_only_override_resolves_at_request_time(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path, {"anthropic": {"headers": {"X-Custom-Header": "custom-value"}}}
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert registry.get_error() is None

        for model in models_for(registry, "anthropic"):
            auth = await registry.get_api_key_and_headers(model)
            assert auth.ok is True
            assert auth.headers is not None
            assert auth.headers["X-Custom-Header"] == "custom-value"

    def test_base_url_only_override_does_not_affect_other_providers(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path, {"anthropic": override_config("https://my-proxy.example.com/v1")}
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        google_models = models_for(registry, "google")

        assert len(google_models) > 0
        assert google_models[0].base_url != "https://my-proxy.example.com/v1"

    def test_can_mix_base_url_override_and_models_merge(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "anthropic": override_config("https://anthropic-proxy.example.com/v1"),
                "google": provider_config(
                    "https://google-proxy.example.com/v1",
                    [{"id": "gemini-custom"}],
                    "google-generative-ai",
                ),
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        anthropic_models = models_for(registry, "anthropic")
        assert len(anthropic_models) > 1
        assert anthropic_models[0].base_url == "https://anthropic-proxy.example.com/v1"

        google_models = models_for(registry, "google")
        assert len(google_models) > 1
        assert any(m.id == "gemini-custom" for m in google_models)

    def test_refresh_picks_up_base_url_override_changes(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path, {"anthropic": override_config("https://first-proxy.example.com/v1")}
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert models_for(registry, "anthropic")[0].base_url == "https://first-proxy.example.com/v1"

        write_models_json(
            models_json_path, {"anthropic": override_config("https://second-proxy.example.com/v1")}
        )
        registry.refresh()

        assert (
            models_for(registry, "anthropic")[0].base_url == "https://second-proxy.example.com/v1"
        )


class TestCustomModelsMerge:
    def test_built_in_provider_models_inherit_api_and_base_url(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        """A built-in provider already has an endpoint and an api on every model,
        so a custom model on one needs neither."""
        write_models_json(
            models_json_path,
            {
                "openrouter": {
                    "models": [
                        {
                            "id": "fake-provider/fake-model",
                            "name": "Fake model",
                            "reasoning": True,
                            "input": ["text"],
                        }
                    ]
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert registry.get_error() is None

        model = registry.find("openrouter", "fake-provider/fake-model")
        assert model is not None
        assert model.api == "openai-completions"
        assert model.base_url == "https://openrouter.ai/api/v1"

    def test_non_built_in_provider_requires_base_url_and_api_key(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "my-custom-provider": {
                    "models": [
                        {
                            "id": "my-model",
                            "api": "openai-completions",
                            "reasoning": False,
                            "input": ["text"],
                        }
                    ]
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        error = registry.get_error()
        assert error is not None
        assert "base_url" in error

    def test_custom_provider_named_like_built_in_merges(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "anthropic": provider_config(
                    "https://my-proxy.example.com/v1", [{"id": "claude-custom"}]
                )
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        anthropic_models = models_for(registry, "anthropic")

        assert len(anthropic_models) > 1
        assert any(m.id == "claude-custom" for m in anthropic_models)
        assert any("claude" in m.id for m in anthropic_models)

    def test_custom_model_with_same_id_replaces_built_in(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "openrouter": provider_config(
                    "https://my-proxy.example.com/v1",
                    [{"id": "anthropic/claude-sonnet-4"}],
                    "openai-completions",
                )
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        matches = [
            m for m in models_for(registry, "openrouter") if m.id == "anthropic/claude-sonnet-4"
        ]

        assert len(matches) == 1
        assert matches[0].base_url == "https://my-proxy.example.com/v1"

    def test_custom_provider_does_not_affect_other_built_ins(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "anthropic": provider_config(
                    "https://my-proxy.example.com/v1", [{"id": "claude-custom"}]
                )
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        assert len(models_for(registry, "google")) > 0
        assert len(models_for(registry, "openai")) > 0

    def test_provider_base_url_applies_to_built_in_and_custom(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "anthropic": provider_config(
                    "https://merged-proxy.example.com/v1", [{"id": "claude-custom"}]
                )
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        for model in models_for(registry, "anthropic"):
            assert model.base_url == "https://merged-proxy.example.com/v1"

    def test_provider_compat_applies_to_custom_models(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "demo": {
                    "base_url": "https://example.com/v1",
                    "api_key": "DEMO_KEY",
                    "api": "openai-completions",
                    "compat": {
                        "supports_usage_in_streaming": False,
                        "max_tokens_field": "max_tokens",
                    },
                    "models": [
                        {
                            "id": "demo-model",
                            "reasoning": False,
                            "input": ["text"],
                            "cost": {
                                "input": 0,
                                "output": 0,
                                "cacheRead": 0,
                                "cacheWrite": 0,
                            },
                            "context_window": 1000,
                            "max_tokens": 100,
                        }
                    ],
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        model = registry.find("demo", "demo-model")
        assert model is not None
        compat = model.compat
        assert isinstance(compat, OpenAICompletionsCompat)
        assert compat.supports_usage_in_streaming is False
        assert compat.max_tokens_field == "max_tokens"

    def test_model_compat_overrides_provider_compat(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "demo": {
                    "base_url": "https://example.com/v1",
                    "api_key": "DEMO_KEY",
                    "api": "openai-completions",
                    "compat": {
                        "supports_usage_in_streaming": False,
                        "max_tokens_field": "max_tokens",
                    },
                    "models": [
                        {
                            "id": "demo-model",
                            "reasoning": False,
                            "input": ["text"],
                            "cost": {
                                "input": 0,
                                "output": 0,
                                "cacheRead": 0,
                                "cacheWrite": 0,
                            },
                            "context_window": 1000,
                            "max_tokens": 100,
                            "compat": {
                                "supports_usage_in_streaming": True,
                                "max_tokens_field": "max_completion_tokens",
                            },
                        }
                    ],
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        model = registry.find("demo", "demo-model")
        assert model is not None
        compat = model.compat
        assert isinstance(compat, OpenAICompletionsCompat)
        assert compat.supports_usage_in_streaming is True
        assert compat.max_tokens_field == "max_completion_tokens"

    def test_provider_compat_applies_to_built_in_models(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "openrouter": {
                    "compat": {
                        "supports_usage_in_streaming": False,
                        "supports_strict_mode": False,
                    }
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        models = models_for(registry, "openrouter")
        assert len(models) > 0
        for model in models:
            compat = model.compat
            assert isinstance(compat, OpenAICompletionsCompat)
            assert compat.supports_usage_in_streaming is False
            assert compat.supports_strict_mode is False

    def test_model_base_url_overrides_provider_base_url(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "demo": {
                    "base_url": "https://provider.example.com/v1",
                    "api_key": "DEMO_KEY",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "demo-model",
                            "base_url": "https://model.example.com/v1",
                            "reasoning": False,
                            "input": ["text"],
                        }
                    ],
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        model = registry.find("demo", "demo-model")
        assert model is not None
        assert model.base_url == "https://model.example.com/v1"

    def test_model_overrides_still_apply_when_provider_defines_models(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "openrouter": {
                    "models": [
                        {"id": "custom/added", "reasoning": False, "input": ["text"]},
                    ],
                    "model_overrides": {"anthropic/claude-sonnet-4": {"name": "Overridden Sonnet"}},
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert registry.get_error() is None

        added = registry.find("openrouter", "custom/added")
        assert added is not None
        overridden = registry.find("openrouter", "anthropic/claude-sonnet-4")
        assert overridden is not None
        assert overridden.name == "Overridden Sonnet"

    def test_refresh_reloads_merged_custom_models(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {"anthropic": provider_config("https://p.example.com/v1", [{"id": "first-custom"}])},
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert registry.find("anthropic", "first-custom") is not None

        write_models_json(
            models_json_path,
            {"anthropic": provider_config("https://p.example.com/v1", [{"id": "second-custom"}])},
        )
        registry.refresh()

        assert registry.find("anthropic", "first-custom") is None
        assert registry.find("anthropic", "second-custom") is not None

    def test_removing_custom_models_keeps_built_ins(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {"anthropic": provider_config("https://p.example.com/v1", [{"id": "claude-custom"}])},
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert registry.find("anthropic", "claude-custom") is not None

        write_models_json(models_json_path, {})
        registry.refresh()

        assert registry.find("anthropic", "claude-custom") is None
        assert len(models_for(registry, "anthropic")) > 1


class TestModelOverrides:
    """Per-model customisation of built-in models."""

    def test_override_applies_to_a_single_built_in_model(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "openrouter": {
                    "model_overrides": {"anthropic/claude-sonnet-4": {"name": "Custom Sonnet Name"}}
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        sonnet = registry.find("openrouter", "anthropic/claude-sonnet-4")
        assert sonnet is not None
        assert sonnet.name == "Custom Sonnet Name"

        opus = registry.find("openrouter", "anthropic/claude-opus-4")
        assert opus is not None
        assert opus.name != "Custom Sonnet Name"

    def test_override_with_open_router_routing(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "openrouter": {
                    "model_overrides": {
                        "anthropic/claude-sonnet-4": {
                            "compat": {"open_router_routing": {"only": ["amazon-bedrock"]}}
                        }
                    }
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        sonnet = registry.find("openrouter", "anthropic/claude-sonnet-4")
        assert sonnet is not None
        compat = sonnet.compat
        assert isinstance(compat, OpenAICompletionsCompat)
        assert compat.open_router_routing == {"only": ["amazon-bedrock"]}

    def test_multiple_overrides_on_same_provider(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "openrouter": {
                    "model_overrides": {
                        "anthropic/claude-sonnet-4": {
                            "compat": {"open_router_routing": {"only": ["amazon-bedrock"]}}
                        },
                        "anthropic/claude-opus-4": {
                            "compat": {"open_router_routing": {"only": ["anthropic"]}}
                        },
                    }
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        sonnet = registry.find("openrouter", "anthropic/claude-sonnet-4")
        opus = registry.find("openrouter", "anthropic/claude-opus-4")
        assert sonnet is not None and opus is not None
        sonnet_compat = sonnet.compat
        opus_compat = opus.compat
        assert isinstance(sonnet_compat, OpenAICompletionsCompat)
        assert isinstance(opus_compat, OpenAICompletionsCompat)
        assert sonnet_compat.open_router_routing == {"only": ["amazon-bedrock"]}
        assert opus_compat.open_router_routing == {"only": ["anthropic"]}

    def test_override_combined_with_base_url_override(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "openrouter": {
                    "base_url": "https://my-proxy.example.com/v1",
                    "model_overrides": {"anthropic/claude-sonnet-4": {"name": "Proxied Sonnet"}},
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        sonnet = registry.find("openrouter", "anthropic/claude-sonnet-4")
        assert sonnet is not None
        assert sonnet.base_url == "https://my-proxy.example.com/v1"
        assert sonnet.name == "Proxied Sonnet"

        opus = registry.find("openrouter", "anthropic/claude-opus-4")
        assert opus is not None
        assert opus.base_url == "https://my-proxy.example.com/v1"
        assert opus.name != "Proxied Sonnet"

    def test_override_for_nonexistent_model_id_is_ignored(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "openrouter": {
                    "model_overrides": {"nonexistent/model-id": {"name": "This should not appear"}}
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        assert registry.find("openrouter", "nonexistent/model-id") is None
        assert registry.get_error() is None

    def test_override_can_change_cost_fields_partially(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "openrouter": {
                    "model_overrides": {"anthropic/claude-sonnet-4": {"cost": {"input": 99}}}
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        sonnet = registry.find("openrouter", "anthropic/claude-sonnet-4")
        assert sonnet is not None
        assert sonnet.cost["input"] == 99
        # The three fields not named keep their built-in values.
        assert sonnet.cost["output"] > 0
        assert sonnet.cost["cacheRead"] > 0

    async def test_override_can_add_headers_at_request_time(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "openrouter": {
                    "model_overrides": {
                        "anthropic/claude-sonnet-4": {"headers": {"X-Custom-Model-Header": "value"}}
                    }
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        sonnet = registry.find("openrouter", "anthropic/claude-sonnet-4")
        assert sonnet is not None

        auth = await registry.get_api_key_and_headers(sonnet)
        assert auth.ok is True
        assert auth.headers is not None
        assert auth.headers["X-Custom-Model-Header"] == "value"

    def test_refresh_picks_up_override_changes(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {"openrouter": {"model_overrides": {"anthropic/claude-sonnet-4": {"name": "First"}}}},
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        first = registry.find("openrouter", "anthropic/claude-sonnet-4")
        assert first is not None and first.name == "First"

        write_models_json(
            models_json_path,
            {"openrouter": {"model_overrides": {"anthropic/claude-sonnet-4": {"name": "Second"}}}},
        )
        registry.refresh()

        second = registry.find("openrouter", "anthropic/claude-sonnet-4")
        assert second is not None and second.name == "Second"

    def test_removing_override_restores_built_in_values(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        built_in_name = next(
            m.name for m in get_models("openrouter") if m.id == "anthropic/claude-sonnet-4"
        )
        write_models_json(
            models_json_path,
            {
                "openrouter": {
                    "model_overrides": {"anthropic/claude-sonnet-4": {"name": "Custom Name"}}
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        custom = registry.find("openrouter", "anthropic/claude-sonnet-4")
        assert custom is not None and custom.name == "Custom Name"

        write_models_json(models_json_path, {})
        registry.refresh()

        restored = registry.find("openrouter", "anthropic/claude-sonnet-4")
        assert restored is not None
        assert restored.name == built_in_name


class TestDynamicProviderLifecycle:
    def test_display_name_resolution_order(self, models_json_path: str, auth_storage: AuthStorage):
        registry = ModelRegistry.create(auth_storage, models_json_path)

        assert registry.get_provider_display_name("openai") == "OpenAI"
        assert registry.get_provider_display_name("unknown-provider") == "unknown-provider"

        # A registered provider's own name wins over everything below it.
        registry.register_provider(
            "my-provider",
            ProviderConfigInput(
                name="My Provider",
                base_url="https://example.com/v1",
                api_key="KEY",
                api="openai-completions",
                models=[model_definition("m1")],
            ),
        )
        assert registry.get_provider_display_name("my-provider") == "My Provider"

    def test_failed_registration_does_not_persist_invalid_stream_simple(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        registry = ModelRegistry.create(auth_storage, models_json_path)

        with pytest.raises(ValueError, match="api"):
            registry.register_provider(
                "bad-provider", ProviderConfigInput(stream_simple=lambda *_a, **_k: None)
            )

        # Nothing was stored, so a refresh has nothing to re-apply.
        registry.refresh()
        assert registry.get_provider_display_name("bad-provider") == "bad-provider"

    def test_failed_registration_does_not_remove_existing_models(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        registry = ModelRegistry.create(auth_storage, models_json_path)
        registry.register_provider(
            "keeper",
            ProviderConfigInput(
                base_url="https://example.com/v1",
                api_key="KEY",
                api="openai-completions",
                models=[model_definition("kept")],
            ),
        )
        assert registry.find("keeper", "kept") is not None

        with pytest.raises(ValueError):
            registry.register_provider(
                "keeper",
                ProviderConfigInput(models=[model_definition("never")]),  # no base_url
            )

        assert registry.find("keeper", "kept") is not None
        assert registry.find("keeper", "never") is None

    def test_register_provider_with_oauth_registers_it_for_login(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        class Stub:
            id = "placeholder"
            name = "Stub OAuth"
            uses_callback_server = False

            async def login(self, callbacks: Any) -> Any: ...  # pragma: no cover
            async def refresh_token(self, credentials: Any) -> Any: ...  # pragma: no cover
            def get_api_key(self, credentials: Any) -> str: ...  # pragma: no cover

        registry = ModelRegistry.create(auth_storage, models_json_path)
        registry.register_provider(
            "stub-oauth",
            ProviderConfigInput(
                base_url="https://example.com/v1",
                api="openai-completions",
                oauth=Stub(),
                models=[model_definition("m1")],
            ),
        )

        registered = get_oauth_provider("stub-oauth")
        assert registered is not None
        # The provider's id is forced to the provider name, or `/login` would
        # store the credential under a key nothing looks up.
        assert registered.id == "stub-oauth"

    def test_unregister_removes_custom_oauth_and_restores_built_in(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        class Stub:
            id = "placeholder"
            name = "Impostor Anthropic"
            uses_callback_server = False

            async def login(self, callbacks: Any) -> Any: ...  # pragma: no cover
            async def refresh_token(self, credentials: Any) -> Any: ...  # pragma: no cover
            def get_api_key(self, credentials: Any) -> str: ...  # pragma: no cover

        registry = ModelRegistry.create(auth_storage, models_json_path)
        built_in = get_oauth_provider("anthropic")
        assert built_in is not None

        registry.register_provider(
            "anthropic",
            ProviderConfigInput(
                base_url="https://example.com/v1", api="anthropic-messages", oauth=Stub()
            ),
        )
        assert get_oauth_provider("anthropic") is not built_in

        registry.unregister_provider("anthropic")
        assert get_oauth_provider("anthropic") is built_in

    def test_unregister_restores_built_in_api_stream_handler(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        registry = ModelRegistry.create(auth_storage, models_json_path)
        built_in = get_api_provider("openai-completions")

        registry.register_provider(
            "custom-stream",
            ProviderConfigInput(api="openai-completions", stream_simple=lambda *_a, **_k: None),
        )
        assert get_api_provider("openai-completions") is not built_in

        registry.unregister_provider("custom-stream")
        restored = get_api_provider("openai-completions")
        assert restored is not None

    def test_unregister_unknown_provider_is_a_no_op(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        registry = ModelRegistry.create(auth_storage, models_json_path)
        before = len(registry.get_all())
        registry.unregister_provider("never-registered")
        assert len(registry.get_all()) == before


class TestDynamicOverridePersistence:
    """A dynamic registration must survive ``refresh()``, which reloads from disk."""

    def test_base_url_only_override_keeps_built_in_models_after_refresh(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        registry = ModelRegistry.create(auth_storage, models_json_path)
        registry.register_provider(
            "anthropic", ProviderConfigInput(base_url="https://dyn.example.com/v1")
        )
        registry.refresh()

        anthropic_models = models_for(registry, "anthropic")
        assert len(anthropic_models) > 1
        for model in anthropic_models:
            assert model.base_url == "https://dyn.example.com/v1"

    def test_models_only_override_replaces_built_in_models_after_refresh(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        registry = ModelRegistry.create(auth_storage, models_json_path)
        registry.register_provider(
            "anthropic",
            ProviderConfigInput(
                base_url="https://dyn.example.com/v1",
                api_key="KEY",
                api="anthropic-messages",
                models=[model_definition("only-model")],
            ),
        )
        registry.refresh()

        anthropic_models = models_for(registry, "anthropic")
        assert [m.id for m in anthropic_models] == ["only-model"]

    def test_custom_provider_registration_survives_refresh(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        registry = ModelRegistry.create(auth_storage, models_json_path)
        registry.register_provider(
            "brand-new",
            ProviderConfigInput(
                base_url="https://dyn.example.com/v1",
                api_key="KEY",
                api="openai-completions",
                models=[model_definition("new-model")],
            ),
        )
        registry.refresh()

        assert registry.find("brand-new", "new-model") is not None

    async def test_headers_only_override_keeps_custom_models_after_refresh(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        registry = ModelRegistry.create(auth_storage, models_json_path)
        registry.register_provider(
            "hdr",
            ProviderConfigInput(
                base_url="https://dyn.example.com/v1",
                api_key="KEY",
                api="openai-completions",
                models=[model_definition("hdr-model")],
            ),
        )
        registry.register_provider("hdr", ProviderConfigInput(headers={"X-H": "v"}))
        registry.refresh()

        model = registry.find("hdr", "hdr-model")
        assert model is not None
        auth = await registry.get_api_key_and_headers(model)
        assert auth.ok is True
        assert auth.headers is not None
        assert auth.headers["X-H"] == "v"

    def test_re_registration_merges_field_by_field(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        """A second registration must not blank the fields it omits."""
        registry = ModelRegistry.create(auth_storage, models_json_path)
        registry.register_provider(
            "merge-me",
            ProviderConfigInput(
                name="Original",
                base_url="https://first.example.com/v1",
                api_key="KEY",
                api="openai-completions",
                models=[model_definition("m1")],
            ),
        )
        registry.register_provider(
            "merge-me", ProviderConfigInput(base_url="https://second.example.com/v1")
        )
        registry.refresh()

        assert registry.get_provider_display_name("merge-me") == "Original"
        model = registry.find("merge-me", "m1")
        assert model is not None
        assert model.base_url == "https://second.example.com/v1"


class TestApiKeyResolution:
    """``models.json`` keys resolve per request, and are never cached."""

    @staticmethod
    def _counting_command(counter_file: Path, tail: str) -> str:
        path = str(counter_file).replace("\\", "/")
        return f'!sh -c \'count=$(cat "{path}"); echo $((count + 1)) > "{path}"; {tail}\''

    def _demo_provider(self, api_key: str) -> dict[str, Any]:
        return {
            "demo": {
                "base_url": "https://example.com/v1",
                "api_key": api_key,
                "api": "openai-completions",
                "models": [{"id": "demo-model", "reasoning": False, "input": ["text"]}],
            }
        }

    async def test_bang_prefix_executes_command(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(models_json_path, self._demo_provider("!echo key-from-command"))
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert await registry.get_api_key_for_provider("demo") == "key-from-command"

    async def test_bang_prefix_trims_whitespace(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(models_json_path, self._demo_provider("!echo '  spaced  '"))
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert await registry.get_api_key_for_provider("demo") == "spaced"

    async def test_bang_prefix_returns_none_on_failure(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(models_json_path, self._demo_provider("!exit 1"))
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert await registry.get_api_key_for_provider("demo") is None

    async def test_env_var_name_resolves(
        self, models_json_path: str, auth_storage: AuthStorage, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("TEST_REGISTRY_KEY_4242", "env-value")
        write_models_json(models_json_path, self._demo_provider("TEST_REGISTRY_KEY_4242"))
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert await registry.get_api_key_for_provider("demo") == "env-value"

    async def test_literal_used_when_not_an_env_var(
        self, models_json_path: str, auth_storage: AuthStorage, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("sk-literal-registry", raising=False)
        write_models_json(models_json_path, self._demo_provider("sk-literal-registry"))
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert await registry.get_api_key_for_provider("demo") == "sk-literal-registry"

    async def test_command_is_executed_on_every_provider_lookup(
        self, models_json_path: str, auth_storage: AuthStorage, tmp_path: Path
    ):
        """Unlike ``auth.json``'s key, a ``models.json`` key is *uncached* — the
        registry reports what a key currently is rather than reusing it."""
        counter = tmp_path / "counter"
        counter.write_text("0")
        command = self._counting_command(counter, 'echo "k"')
        write_models_json(models_json_path, self._demo_provider(command))

        registry = ModelRegistry.create(auth_storage, models_json_path)
        await registry.get_api_key_for_provider("demo")
        await registry.get_api_key_for_provider("demo")

        assert int(counter.read_text().strip()) == 2

    def test_auth_status_reports_env_var_from_models_json(
        self, models_json_path: str, auth_storage: AuthStorage, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("TEST_REGISTRY_ENV_STATUS", "value")
        write_models_json(models_json_path, self._demo_provider("TEST_REGISTRY_ENV_STATUS"))
        registry = ModelRegistry.create(auth_storage, models_json_path)

        status = registry.get_provider_auth_status("demo")
        assert status.configured is True
        assert status.source == "environment"
        assert status.label == "TEST_REGISTRY_ENV_STATUS"

    def test_auth_status_reports_non_env_key_as_config_key(
        self, models_json_path: str, auth_storage: AuthStorage, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("sk-inline-registry", raising=False)
        write_models_json(models_json_path, self._demo_provider("sk-inline-registry"))
        registry = ModelRegistry.create(auth_storage, models_json_path)

        status = registry.get_provider_auth_status("demo")
        assert status.configured is True
        assert status.source == "models_json_key"

    def test_auth_status_reports_command_without_executing_it(
        self, models_json_path: str, auth_storage: AuthStorage, tmp_path: Path
    ):
        """Scrolling a provider list must not shell out per row."""
        counter = tmp_path / "counter"
        counter.write_text("0")
        command = self._counting_command(counter, 'echo "k"')
        write_models_json(models_json_path, self._demo_provider(command))
        registry = ModelRegistry.create(auth_storage, models_json_path)

        status = registry.get_provider_auth_status("demo")
        assert status.configured is True
        assert status.source == "models_json_command"
        assert int(counter.read_text().strip()) == 0

    def test_get_available_does_not_execute_command_backed_keys(
        self, models_json_path: str, auth_storage: AuthStorage, tmp_path: Path
    ):
        counter = tmp_path / "counter"
        counter.write_text("0")
        command = self._counting_command(counter, 'echo "k"')
        write_models_json(models_json_path, self._demo_provider(command))
        registry = ModelRegistry.create(auth_storage, models_json_path)

        available = registry.get_available_sync()

        assert any(m.provider == "demo" for m in available)
        assert int(counter.read_text().strip()) == 0

    async def test_auth_header_is_resolved_on_every_request(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "demo": {
                    "base_url": "https://example.com/v1",
                    "api_key": "!echo bearer-token",
                    "auth_header": True,
                    "api": "openai-completions",
                    "models": [{"id": "demo-model", "reasoning": False, "input": ["text"]}],
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        model = registry.find("demo", "demo-model")
        assert model is not None

        auth = await registry.get_api_key_and_headers(model)
        assert auth.ok is True
        assert auth.headers is not None
        assert auth.headers["Authorization"] == "Bearer bearer-token"

    async def test_auth_header_reports_an_error_when_the_key_fails(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "demo": {
                    "base_url": "https://example.com/v1",
                    "api_key": "!exit 1",
                    "auth_header": True,
                    "api": "openai-completions",
                    "models": [{"id": "demo-model", "reasoning": False, "input": ["text"]}],
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        model = registry.find("demo", "demo-model")
        assert model is not None

        auth = await registry.get_api_key_and_headers(model)
        assert auth.ok is False
        assert auth.error is not None

    async def test_auth_storage_key_wins_over_models_json_key(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        """A key the user typed at ``/login`` beats one sitting in a config file."""
        write_models_json(models_json_path, self._demo_provider("from-models-json"))
        registry = ModelRegistry.create(auth_storage, models_json_path)
        registry.auth_storage.set("demo", ApiKeyCredential(key="from-auth-json"))

        assert await registry.get_api_key_for_provider("demo") == "from-auth-json"


class TestBlankApiKeyIsRejected:
    """A whitespace-only key never reaches the wire.

    ``" "`` is truthy, so it passes every ``if api_key`` on the way out and only
    fails at the transport — h11 raises ``Illegal header value b' '``, naming
    neither the provider nor where the key came from. This port's own group;
    the TS has no equivalent because its header layer accepts the blank value.
    """

    def _demo_provider(self, api_key: str) -> dict[str, Any]:
        return {
            "demo": {
                "base_url": "https://example.com/v1",
                "api_key": api_key,
                "api": "openai-completions",
                "models": [{"id": "demo-model", "reasoning": False, "input": ["text"]}],
            }
        }

    def _demo_model(self, registry: ModelRegistry) -> Model:
        model = registry.find("demo", "demo-model")
        assert model is not None
        return model

    @pytest.mark.parametrize("key", [" ", "   ", "\t", "\n"])
    async def test_blank_key_from_auth_storage_is_reported_as_missing(
        self, models_json_path: str, auth_storage: AuthStorage, key: str
    ):
        write_models_json(models_json_path, self._demo_provider("sk-unused"))
        registry = ModelRegistry.create(auth_storage, models_json_path)
        registry.auth_storage.set("demo", ApiKeyCredential(key=key))

        auth = await registry.get_api_key_and_headers(self._demo_model(registry))
        assert auth.ok is False
        assert auth.error == 'No API key found for "demo"'
        assert auth.api_key is None

    async def test_blank_key_from_an_env_var_is_reported_as_missing(
        self, models_json_path: str, auth_storage: AuthStorage, monkeypatch: pytest.MonkeyPatch
    ):
        """The reported case: ``ANTHROPIC_API_KEY=" "`` in the user's shell."""
        monkeypatch.setenv("TEST_REGISTRY_BLANK_KEY", " ")
        write_models_json(models_json_path, self._demo_provider("TEST_REGISTRY_BLANK_KEY"))
        registry = ModelRegistry.create(auth_storage, models_json_path)

        auth = await registry.get_api_key_and_headers(self._demo_model(registry))
        assert auth.ok is False
        assert auth.error == 'No API key found for "demo"'

    async def test_blank_key_is_rejected_before_it_becomes_an_auth_header(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        """``auth_header`` would otherwise send ``Authorization: Bearer  ``."""
        providers = self._demo_provider("sk-unused")
        providers["demo"]["auth_header"] = True
        write_models_json(models_json_path, providers)
        registry = ModelRegistry.create(auth_storage, models_json_path)
        registry.auth_storage.set("demo", ApiKeyCredential(key=" "))

        auth = await registry.get_api_key_and_headers(self._demo_model(registry))
        assert auth.ok is False
        assert auth.headers is None

    async def test_surrounding_whitespace_is_stripped_off_a_real_key(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(models_json_path, self._demo_provider("sk-unused"))
        registry = ModelRegistry.create(auth_storage, models_json_path)
        registry.auth_storage.set("demo", ApiKeyCredential(key="  sk-real\n"))

        auth = await registry.get_api_key_and_headers(self._demo_model(registry))
        assert auth.ok is True
        assert auth.api_key == "sk-real"

    async def test_a_provider_with_no_key_at_all_is_still_ok(
        self, models_json_path: str, auth_storage: AuthStorage, monkeypatch: pytest.MonkeyPatch
    ):
        """Absent is not blank: an unauthenticated provider still resolves.

        ``ok=True, api_key=None`` is a real answer — the transport just sends no
        key header — so the blank check must reject ``" "`` without catching it.
        """
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
        write_models_json(
            models_json_path, {"anthropic": {"headers": {"X-Custom-Header": "custom-value"}}}
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        model = next(iter(models_for(registry, "anthropic")))

        auth = await registry.get_api_key_and_headers(model)
        assert auth.ok is True
        assert auth.api_key is None


class TestAuthAndAvailability:
    def test_has_configured_auth_via_auth_storage(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        registry = ModelRegistry.create(auth_storage, models_json_path)
        model = registry.find("anthropic", "claude-opus-4-7")
        assert model is not None
        assert registry.has_configured_auth(model) is False

        registry.auth_storage.set("anthropic", ApiKeyCredential(key="sk-test"))
        assert registry.has_configured_auth(model) is True

    def test_available_is_a_subset_of_all(self, models_json_path: str, auth_storage: AuthStorage):
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert registry.get_available_sync() == []
        assert len(registry.get_all()) > 0

        registry.auth_storage.set("anthropic", ApiKeyCredential(key="sk-test"))
        available = registry.get_available_sync()
        assert len(available) > 0
        assert {m.provider for m in available} == {"anthropic"}

    async def test_get_available_matches_the_sync_form(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        registry = ModelRegistry.create(auth_storage, models_json_path)
        registry.auth_storage.set("anthropic", ApiKeyCredential(key="sk-test"))
        assert await registry.get_available() == registry.get_available_sync()

    def test_is_using_oauth_only_for_stored_oauth(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        registry = ModelRegistry.create(auth_storage, models_json_path)
        model = registry.find("anthropic", "claude-opus-4-7")
        assert model is not None

        registry.auth_storage.set("anthropic", ApiKeyCredential(key="sk-test"))
        assert registry.is_using_oauth(model) is False

    def test_models_json_key_counts_as_auth_via_the_registry_not_the_storage(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        """A ``models.json`` key makes its provider available — but the *registry*
        is what knows that, not the storage.

        The storage's fallback resolver is left unwired (see the module
        docstring), so ``has_auth`` says no and ``has_configured_auth`` says yes.
        The asymmetry is the TS's and is load-bearing: it is what keeps
        ``get_auth_status`` reporting a source the provider selector can render.
        """
        write_models_json(
            models_json_path,
            {
                "demo": {
                    "base_url": "https://example.com/v1",
                    "api_key": "sk-models-json",
                    "api": "openai-completions",
                    "models": [{"id": "demo-model", "reasoning": False, "input": ["text"]}],
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        assert registry.auth_storage.has_auth("demo") is False
        model = registry.find("demo", "demo-model")
        assert model is not None
        assert registry.has_configured_auth(model) is True
        assert any(m.provider == "demo" for m in registry.get_available_sync())


class TestSchemaValidation:
    """Rejecting a malformed ``models.json``. Hand-written here, typebox in the TS."""

    def test_missing_providers_key_is_rejected(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        Path(models_json_path).write_text(json.dumps({}), encoding="utf-8")
        registry = ModelRegistry.create(auth_storage, models_json_path)
        error = registry.get_error()
        assert error is not None
        assert "providers" in error

    def test_malformed_json_is_reported_not_raised(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        Path(models_json_path).write_text("{not json", encoding="utf-8")
        registry = ModelRegistry.create(auth_storage, models_json_path)
        error = registry.get_error()
        assert error is not None
        assert "Failed to parse models.json" in error
        # The built-in models survive a broken file.
        assert len(registry.get_all()) > 0

    def test_comments_and_trailing_commas_are_stripped(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        Path(models_json_path).write_text(
            """{
  // a comment
  "providers": {
    "anthropic": {
      "base_url": "https://commented.example.com/v1",
    },
  },
}""",
            encoding="utf-8",
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert registry.get_error() is None
        assert models_for(registry, "anthropic")[0].base_url == "https://commented.example.com/v1"

    def test_a_url_inside_a_string_is_not_treated_as_a_comment(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        """``https://`` contains ``//``; the stripper must match string literals
        first or every base_url in the file would be truncated."""
        write_models_json(
            models_json_path, {"anthropic": {"base_url": "https://keep.example.com/v1"}}
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert registry.get_error() is None
        assert models_for(registry, "anthropic")[0].base_url == "https://keep.example.com/v1"

    def test_empty_provider_block_is_rejected(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(models_json_path, {"anthropic": {}})
        registry = ModelRegistry.create(auth_storage, models_json_path)
        error = registry.get_error()
        assert error is not None
        assert "must specify" in error

    def test_model_missing_id_is_rejected_naming_the_field(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "demo": {
                    "base_url": "https://example.com/v1",
                    "api_key": "K",
                    "api": "openai-completions",
                    "models": [{"name": "no id here"}],
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        error = registry.get_error()
        assert error is not None
        assert "providers.demo.models.0.id" in error

    def test_non_positive_context_window_is_rejected(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "demo": {
                    "base_url": "https://example.com/v1",
                    "api_key": "K",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "m",
                            "reasoning": False,
                            "input": ["text"],
                            "context_window": 0,
                        }
                    ],
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        error = registry.get_error()
        assert error is not None
        assert "invalid context_window" in error

    def test_a_missing_file_is_not_an_error(self, tmp_path: Path, auth_storage: AuthStorage):
        registry = ModelRegistry.create(auth_storage, str(tmp_path / "nope.json"))
        assert registry.get_error() is None
        assert len(registry.get_all()) > 0

    def test_in_memory_reads_no_file_at_all(self, auth_storage: AuthStorage):
        registry = ModelRegistry.in_memory(auth_storage)
        assert registry.get_error() is None
        assert len(registry.get_all()) > 0

    def test_unknown_compat_flag_is_dropped_not_fatal(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        """A file written for a newer build should lose the flag it names, not the
        whole provider."""
        write_models_json(
            models_json_path,
            {"anthropic": {"compat": {"supports_long_cache_retention": True, "from_2027": True}}},
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        assert registry.get_error() is None
        model = models_for(registry, "anthropic")[0]
        compat = model.compat
        assert isinstance(compat, AnthropicMessagesCompat)
        assert compat.supports_long_cache_retention is True


class TestCompatClassSelection:
    """Which compat *class* a merged block becomes, which the providers read back.

    The TS casts and never has to choose. Here the choice is load-bearing:
    ``provider-anthropic`` does ``isinstance(compat, AnthropicMessagesCompat)``
    and ignores anything else, so picking by field coverage alone silently
    disables every anthropic flag set in ``models.json``.
    """

    def test_an_anthropic_model_keeps_an_anthropic_compat(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {"anthropic": {"compat": {"supports_eager_tool_input_streaming": True}}},
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        model = registry.find("anthropic", "claude-opus-4-7")
        assert model is not None
        assert model.api == "anthropic-messages"
        compat = model.compat
        assert isinstance(compat, AnthropicMessagesCompat)
        assert compat.supports_eager_tool_input_streaming is True

    def test_a_shared_flag_still_lands_on_the_api_s_class(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        """``supports_long_cache_retention`` exists on all three classes, so
        coverage alone would hand an anthropic model the openai one."""
        write_models_json(
            models_json_path, {"anthropic": {"compat": {"supports_long_cache_retention": True}}}
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        model = registry.find("anthropic", "claude-opus-4-7")
        assert model is not None
        assert isinstance(model.compat, AnthropicMessagesCompat)

    def test_a_completions_model_keeps_a_completions_compat(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path, {"openrouter": {"compat": {"supports_strict_mode": False}}}
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        model = models_for(registry, "openrouter")[0]
        assert model.api == "openai-completions"
        compat = model.compat
        assert isinstance(compat, OpenAICompletionsCompat)
        assert compat.supports_strict_mode is False

    def test_a_model_override_keeps_the_model_s_own_api_class(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "anthropic": {
                    "model_overrides": {
                        "claude-opus-4-7": {"compat": {"supports_eager_tool_input_streaming": True}}
                    }
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        model = registry.find("anthropic", "claude-opus-4-7")
        assert model is not None
        assert isinstance(model.compat, AnthropicMessagesCompat)

    def test_a_custom_model_gets_the_class_its_declared_api_implies(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "demo": {
                    "base_url": "https://example.com/v1",
                    "api_key": "K",
                    "api": "anthropic-messages",
                    "compat": {"supports_eager_tool_input_streaming": True},
                    "models": [{"id": "m", "reasoning": False, "input": ["text"]}],
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        model = registry.find("demo", "m")
        assert model is not None
        assert isinstance(model.compat, AnthropicMessagesCompat)

    def test_an_openai_only_flag_on_an_anthropic_model_is_dropped_not_promoted(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        """The anthropic shape wins even when a key does not fit it: keeping the
        class the provider can read matters more than keeping a flag it would
        never consult."""
        write_models_json(
            models_json_path,
            {
                "anthropic": {
                    "compat": {
                        "supports_eager_tool_input_streaming": True,
                        "supports_usage_in_streaming": False,
                    }
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        model = registry.find("anthropic", "claude-opus-4-7")
        assert model is not None
        compat = model.compat
        assert isinstance(compat, AnthropicMessagesCompat)
        assert compat.supports_eager_tool_input_streaming is True


class TestGapsFoundByMutationTesting:
    """Cases the tests above passed a mutant on, added rather than broadened."""

    def test_routing_merges_across_provider_and_model_levels(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        """The routing merge only shows when *both* sides carry routing: with one
        side empty, replacing and merging are the same answer."""
        write_models_json(
            models_json_path,
            {
                "demo": {
                    "base_url": "https://example.com/v1",
                    "api_key": "K",
                    "api": "openai-completions",
                    "compat": {"open_router_routing": {"order": ["anthropic"]}},
                    "models": [
                        {
                            "id": "m",
                            "reasoning": False,
                            "input": ["text"],
                            "compat": {"open_router_routing": {"only": ["amazon-bedrock"]}},
                        }
                    ],
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        model = registry.find("demo", "m")
        assert model is not None
        compat = model.compat
        assert isinstance(compat, OpenAICompletionsCompat)
        # Both survive: the model level adds `only` without dropping `order`.
        assert compat.open_router_routing == {
            "order": ["anthropic"],
            "only": ["amazon-bedrock"],
        }

    def test_a_model_override_merges_routing_into_a_provider_level_block(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        write_models_json(
            models_json_path,
            {
                "openrouter": {
                    "compat": {"open_router_routing": {"order": ["anthropic"]}},
                    "model_overrides": {
                        "anthropic/claude-sonnet-4": {
                            "compat": {"open_router_routing": {"only": ["amazon-bedrock"]}}
                        }
                    },
                }
            },
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)

        sonnet = registry.find("openrouter", "anthropic/claude-sonnet-4")
        assert sonnet is not None
        compat = sonnet.compat
        assert isinstance(compat, OpenAICompletionsCompat)
        assert compat.open_router_routing == {
            "order": ["anthropic"],
            "only": ["amazon-bedrock"],
        }

    async def test_auth_header_with_no_key_at_all_is_an_error(
        self, models_json_path: str, auth_storage: AuthStorage, monkeypatch: pytest.MonkeyPatch
    ):
        """`auth_header` needs a key to put in the header. The earlier test used a
        *failing* command, which raises before this branch is reached — so nothing
        covered "there is simply no key"."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        write_models_json(
            models_json_path,
            {"anthropic": {"base_url": "https://example.com/v1", "auth_header": True}},
        )
        registry = ModelRegistry.create(auth_storage, models_json_path)
        model = registry.find("anthropic", "claude-opus-4-7")
        assert model is not None

        auth = await registry.get_api_key_and_headers(model)
        assert auth.ok is False
        assert auth.error is not None
        assert "No API key found" in auth.error
        assert "anthropic" in auth.error

    def test_an_oauth_providers_own_name_beats_the_display_table(
        self, models_json_path: str, auth_storage: AuthStorage
    ):
        """Anthropic is in both, and the OAuth provider's name is the more specific
        one — it names the *subscription*, which is what `/login` is offering."""
        registry = ModelRegistry.create(auth_storage, models_json_path)

        assert BUILT_IN_PROVIDER_DISPLAY_NAMES["anthropic"] == "Anthropic"
        assert registry.get_provider_display_name("anthropic") == "Anthropic (Claude Pro/Max)"
        # And a provider with no OAuth flow falls through to the table.
        assert registry.get_provider_display_name("openai") == "OpenAI"
