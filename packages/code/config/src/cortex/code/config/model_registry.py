"""Which models exist, and which the user can actually reach.

Port of ``core/model-registry.ts``. Built-in models come from
:func:`cortex.ai.models.get_models`; ``models.json`` can add providers, add
models to a provider, override a provider's ``base_url``/``compat``/headers, and
override single models field by field. On top of that,
:meth:`ModelRegistry.register_provider` is the extension-facing door, which is
why the registry keeps its dynamic registrations and re-applies them after every
:meth:`refresh`.

**"Available" is a smaller set than "all", and the difference is the point.**
:meth:`get_all` is every model this build knows about; :meth:`get_available` is
the ones with credentials. The overlays list `available`, so a user with one key
sees one provider's models rather than four hundred they cannot call — while
``--api-key`` and ``resolve_cli_model`` deliberately search `all`, because
first-time setup names a model there is no key for yet.

The registry holds the :class:`~cortex.code.config.auth_storage.AuthStorage` and
asks it for keys; the reverse direction — the storage's *fallback resolver*, which
would let it resolve a ``models.json`` key itself — is left unwired, exactly as in
the TS, where ``setFallbackResolver`` is defined and never called. The registry
therefore answers for its own keys: :meth:`ModelRegistry.has_configured_auth`
checks ``models.json``'s ``api_key`` beside the storage's answer, and
:meth:`ModelRegistry.get_provider_auth_status` inspects it after the storage
declines. Wiring the resolver instead looks tidier and is wrong twice over: it
makes ``get_auth_status`` report every ``models.json`` provider as ``"fallback"``
so the status the selector wants is never reached, and it makes
:meth:`get_available` shell out for every ``!command`` key just to list models.

**``models.json`` keys are snake_case here, where the TS's are camelCase**
(``base_url``, not ``baseUrl``). That is the port's convention for on-disk config
— ``settings.json`` already reads the same way — and it applies to compat flags
too (``supports_usage_in_streaming``). The one exception is the ``cost`` block,
whose keys are not schema fields: they are merged into
:attr:`cortex.ai.types.Model.cost`, which spells them ``input``, ``output``,
``cacheRead``, ``cacheWrite`` throughout ``cortex.ai``, so ``models.json`` must
use those or the merge would not line up.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from cortex.ai.models import (
    ApiProvider,
    get_models,
    get_providers,
    register_api_provider,
    reset_api_providers,
)
from cortex.ai.oauth import register_oauth_provider, reset_oauth_providers
from cortex.ai.types import (
    AnthropicMessagesCompat,
    Model,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
)
from cortex.code.config.auth_storage import AuthStatus, AuthStorage, OAuthCredential
from cortex.code.config.config import get_models_path
from cortex.code.config.provider_display_names import BUILT_IN_PROVIDER_DISPLAY_NAMES
from cortex.code.config.resolve_config_value import (
    clear_config_value_cache,
    resolve_config_value_or_throw,
    resolve_config_value_uncached,
    resolve_headers_or_throw,
)

__all__ = [
    "ModelDefinitionInput",
    "ModelRegistry",
    "ProviderConfigInput",
    "ResolvedRequestAuth",
    "clear_api_key_cache",
]

#: Clear the config-value command cache. Re-exported under the TS's name.
clear_api_key_cache = clear_config_value_cache


# ---------------------------------------------------------------------------
# models.json
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ProviderOverride:
    """A provider-level override of built-in models: endpoint and compat only.

    Request auth (``api_key``, ``headers``, ``auth_header``) is deliberately *not*
    here — it is stored separately and resolved per request, so a key that is a
    ``!command`` is not run while merely listing models.
    """

    base_url: str | None = None
    compat: Any = None


@dataclass(frozen=True)
class _ProviderRequestConfig:
    api_key: str | None = None
    headers: dict[str, str] | None = None
    auth_header: bool | None = None


@dataclass(frozen=True)
class ResolvedRequestAuth:
    """The key and headers to send, or the reason there are none.

    A result type rather than an exception because the caller is a turn in
    progress: "no key for this provider" is a message on screen, not a crash.
    """

    ok: bool
    api_key: str | None = None
    headers: dict[str, str] | None = None
    error: str | None = None


@dataclass(frozen=True)
class _CustomModelsResult:
    models: list[Model] = field(default_factory=list)
    overrides: dict[str, _ProviderOverride] = field(default_factory=dict)
    model_overrides: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    error: str | None = None


_STRING_OR_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*')
_STRING_OR_TRAILING_COMMA = re.compile(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])')


def _strip_json_comments(text: str) -> str:
    """Drop ``//`` comments and trailing commas, leaving string literals alone.

    Port of ``stripJsonComments``. Both passes match a string literal *first* so
    that a ``//`` or a comma inside one is skipped rather than edited — which is
    the only reason this is a regex over alternatives rather than two simple
    substitutions.
    """

    def strip_comment(match: re.Match[str]) -> str:
        return match.group(0) if match.group(0).startswith('"') else ""

    def strip_comma(match: re.Match[str]) -> str:
        tail = match.group(1)
        if tail is not None:
            return tail
        return match.group(0)

    without_comments = _STRING_OR_COMMENT.sub(strip_comment, text)
    return _STRING_OR_TRAILING_COMMA.sub(strip_comma, without_comments)


#: `Model.cost`'s keys, which a `cost` override merges into field by field.
_COST_KEYS = ("input", "output", "cacheRead", "cacheWrite")

#: The two compat sub-objects that merge rather than replace. They are settings
#: *collections* — overriding ``order`` must not drop ``only`` — and the TS
#: singles out exactly these two.
_MERGED_ROUTING_KEYS = ("open_router_routing", "vercel_gateway_routing")

#: Compat classes, widest first — the fallback order when the api does not say.
_COMPAT_CLASSES = (OpenAICompletionsCompat, OpenAIResponsesCompat, AnthropicMessagesCompat)

#: Which compat class an api's requests are shaped by. The TS casts and the
#: distinction never surfaces; here it is load-bearing, because the providers read
#: compat back with ``isinstance`` — ``anthropic.py`` ignores a compat block that
#: is not an ``AnthropicMessagesCompat``, so getting this wrong silently drops
#: every flag a user set in ``models.json``.
_COMPAT_CLASS_BY_API: dict[str, Any] = {
    "openai-completions": OpenAICompletionsCompat,
    "openai-responses": OpenAIResponsesCompat,
    "azure-openai-responses": OpenAIResponsesCompat,
    "openai-codex-responses": OpenAIResponsesCompat,
    "anthropic-messages": AnthropicMessagesCompat,
}


def _merge_compat(base_compat: Any, override_compat: Any, api: str | None = None) -> Any:
    """Shallow-merge two compat blocks, deep-merging the two routing sub-objects.

    Port of ``mergeCompat``. Returns a compat *model*, not a dict, because
    ``Model.compat`` is read as an object everywhere else in the port — and
    ``api`` decides *which* model, for the reason on
    :data:`_COMPAT_CLASS_BY_API`.
    """
    if override_compat is None:
        return base_compat

    base = _compat_dict(base_compat)
    override = _compat_dict(override_compat)
    merged: dict[str, Any] = {**base, **override}

    for routing in _MERGED_ROUTING_KEYS:
        base_routing = base.get(routing)
        override_routing = override.get(routing)
        if base_routing or override_routing:
            merged[routing] = {**(base_routing or {}), **(override_routing or {})}

    return _coerce_compat(merged, api)


def _compat_dict(compat: Any) -> dict[str, Any]:
    """Compat as a plain dict, whether it arrived as one or as a pydantic model."""
    if compat is None:
        return {}
    if isinstance(compat, dict):
        return {key: value for key, value in compat.items() if value is not None}
    dump = getattr(compat, "model_dump", None)
    if dump is not None:
        return dump(exclude_none=True)
    return {}


def _coerce_compat(merged: dict[str, Any], api: str | None = None) -> Any:
    """Build a compat model from the merged keys.

    The api's own class wins when it can hold every key; otherwise the first class
    that covers them, widest first. Coverage matters rather than pydantic's union
    matching because these models *ignore* unknown fields — a dict holding
    ``supports_usage_in_streaming`` validates happily against
    ``OpenAIResponsesCompat`` and silently loses the flag.

    Keys no class knows are dropped rather than raised on, which is
    ``models.json``-appropriate: a file written for a newer build should lose the
    flag it names, not the whole provider.
    """
    if not merged:
        return None

    keys = set(merged)
    preferred = _COMPAT_CLASS_BY_API.get(api) if api else None
    candidates = (preferred, *_COMPAT_CLASSES) if preferred is not None else _COMPAT_CLASSES

    for compat_class in candidates:
        if keys <= set(compat_class.model_fields):
            return compat_class(**merged)

    # Nothing covers every key. Keep the api's shape if it has one — the provider
    # reads compat back by class — and drop what it cannot hold.
    fallback = preferred if preferred is not None else _COMPAT_CLASSES[0]
    return fallback(**{k: v for k, v in merged.items() if k in fallback.model_fields})


def _apply_model_override(model: Model, override: dict[str, Any]) -> Model:
    """Deep-merge one model override. Port of ``applyModelOverride``.

    ``cost``, ``thinking_level_map`` and ``compat`` merge; everything else
    replaces. Cost merges per-field so that overriding only ``input`` does not
    zero the other three.
    """
    updates: dict[str, Any] = {}

    if "name" in override:
        updates["name"] = override["name"]
    if "reasoning" in override:
        updates["reasoning"] = override["reasoning"]
    if "thinking_level_map" in override:
        updates["thinking_level_map"] = {
            **(model.thinking_level_map or {}),
            **override["thinking_level_map"],
        }
    if "input" in override:
        updates["input"] = list(override["input"])
    if "context_window" in override:
        updates["context_window"] = override["context_window"]
    if "max_tokens" in override:
        updates["max_tokens"] = override["max_tokens"]

    if override.get("cost"):
        cost_override = override["cost"]
        updates["cost"] = {key: cost_override.get(key, model.cost.get(key)) for key in _COST_KEYS}

    updates["compat"] = _merge_compat(model.compat, override.get("compat"), model.api)

    return model.model_copy(update=updates)


# ---------------------------------------------------------------------------
# Validation
#
# The TS validates models.json with a typebox schema and then adds a handful of
# cross-field rules on top. There is no typebox here; the schema's job — reject
# a malformed file with a message naming the field — is done by
# `_validate_shape`, and the cross-field rules are `_validate_config`, which is
# a direct port.
# ---------------------------------------------------------------------------

_THINKING_LEVEL_KEYS = ("off", "minimal", "low", "medium", "high", "xhigh")


class _SchemaError(Exception):
    """A models.json that does not match the schema, with the path that failed."""


def _require(condition: bool, path: str, message: str) -> None:
    if not condition:
        raise _SchemaError(f"  - {path}: {message}")


def _validate_shape(parsed: Any) -> dict[str, Any]:
    """The typebox schema, by hand: types and required fields, nothing semantic."""
    _require(isinstance(parsed, dict), "root", "Expected object")
    providers = parsed.get("providers")  # type: ignore[union-attr]
    _require(providers is not None, "providers", "Expected required property")
    _require(isinstance(providers, dict), "providers", "Expected object")

    for provider_name, provider_config in providers.items():  # type: ignore[union-attr]
        base = f"providers.{provider_name}"
        _require(isinstance(provider_config, dict), base, "Expected object")
        for key in ("name", "base_url", "api_key", "api"):
            if key in provider_config:
                _require(
                    isinstance(provider_config[key], str) and provider_config[key] != "",
                    f"{base}.{key}",
                    "Expected string with length greater or equal to 1",
                )
        if "auth_header" in provider_config:
            _require(
                isinstance(provider_config["auth_header"], bool),
                f"{base}.auth_header",
                "Expected boolean",
            )
        if "headers" in provider_config:
            _validate_headers(provider_config["headers"], f"{base}.headers")
        if "models" in provider_config:
            models = provider_config["models"]
            _require(isinstance(models, list), f"{base}.models", "Expected array")
            for index, model_def in enumerate(models):
                _validate_model_definition(model_def, f"{base}.models.{index}")
        if "model_overrides" in provider_config:
            overrides = provider_config["model_overrides"]
            _require(isinstance(overrides, dict), f"{base}.model_overrides", "Expected object")
            for model_id, override in overrides.items():
                _validate_model_override(override, f"{base}.model_overrides.{model_id}")

    return parsed  # type: ignore[return-value]


def _validate_headers(headers: Any, path: str) -> None:
    _require(isinstance(headers, dict), path, "Expected object")
    for key, value in headers.items():
        _require(isinstance(value, str), f"{path}.{key}", "Expected string")


def _validate_thinking_level_map(value: Any, path: str) -> None:
    _require(isinstance(value, dict), path, "Expected object")
    for key, level in value.items():
        _require(key in _THINKING_LEVEL_KEYS, f"{path}.{key}", "Unexpected property")
        _require(level is None or isinstance(level, str), f"{path}.{key}", "Expected string")


def _validate_input(value: Any, path: str) -> None:
    _require(isinstance(value, list), path, "Expected array")
    for index, item in enumerate(value):
        _require(item in ("text", "image"), f"{path}.{index}", "Expected 'text' or 'image'")


def _validate_cost(value: Any, path: str, *, partial: bool) -> None:
    _require(isinstance(value, dict), path, "Expected object")
    for key in ("input", "output", "cacheRead", "cacheWrite"):
        if key in value:
            _require(isinstance(value[key], int | float), f"{path}.{key}", "Expected number")
        elif not partial:
            _require(False, f"{path}.{key}", "Expected required property")


def _validate_model_definition(model_def: Any, path: str) -> None:
    _require(isinstance(model_def, dict), path, "Expected object")
    model_id = model_def.get("id")
    _require(model_id is not None, f"{path}.id", "Expected required property")
    _require(
        isinstance(model_id, str) and model_id != "",
        f"{path}.id",
        "Expected string with length greater or equal to 1",
    )
    for key in ("name", "api", "base_url"):
        if key in model_def:
            _require(
                isinstance(model_def[key], str) and model_def[key] != "",
                f"{path}.{key}",
                "Expected string with length greater or equal to 1",
            )
    _validate_shared_model_fields(model_def, path, partial_cost=False)


def _validate_model_override(override: Any, path: str) -> None:
    _require(isinstance(override, dict), path, "Expected object")
    if "name" in override:
        _require(
            isinstance(override["name"], str) and override["name"] != "",
            f"{path}.name",
            "Expected string with length greater or equal to 1",
        )
    _validate_shared_model_fields(override, path, partial_cost=True)


def _validate_shared_model_fields(value: dict[str, Any], path: str, *, partial_cost: bool) -> None:
    if "reasoning" in value:
        _require(isinstance(value["reasoning"], bool), f"{path}.reasoning", "Expected boolean")
    if "thinking_level_map" in value:
        _validate_thinking_level_map(value["thinking_level_map"], f"{path}.thinking_level_map")
    if "input" in value:
        _validate_input(value["input"], f"{path}.input")
    if "cost" in value:
        _validate_cost(value["cost"], f"{path}.cost", partial=partial_cost)
    for key in ("context_window", "max_tokens"):
        if key in value:
            _require(isinstance(value[key], int | float), f"{path}.{key}", "Expected number")
    if "headers" in value:
        _validate_headers(value["headers"], f"{path}.headers")
    if "compat" in value:
        _require(isinstance(value["compat"], dict), f"{path}.compat", "Expected object")


# ---------------------------------------------------------------------------
# registerProvider input
# ---------------------------------------------------------------------------


@dataclass
class ModelDefinitionInput:
    """One model in a :class:`ProviderConfigInput`."""

    id: str
    name: str
    reasoning: bool
    input: list[Literal["text", "image"]]
    cost: dict[str, float]
    context_window: int
    max_tokens: int
    api: str | None = None
    base_url: str | None = None
    thinking_level_map: dict[str, str | None] | None = None
    headers: dict[str, str] | None = None
    compat: Any = None


@dataclass
class ProviderConfigInput:
    """What :meth:`ModelRegistry.register_provider` takes. Port of the TS interface.

    Three shapes, distinguished by what is set: with ``models`` it *replaces* the
    provider's models; with only ``base_url``/``headers`` it overrides the
    existing ones; with ``oauth`` it also makes the provider appear in ``/login``.
    """

    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api: str | None = None
    stream_simple: Callable[..., Any] | None = None
    headers: dict[str, str] | None = None
    auth_header: bool | None = None
    oauth: Any = None
    models: list[ModelDefinitionInput] | None = None


# ---------------------------------------------------------------------------
# ModelRegistry
# ---------------------------------------------------------------------------


class ModelRegistry:
    """Built-in and custom models, and the auth that decides which are reachable."""

    def __init__(self, auth_storage: AuthStorage, models_json_path: str | None) -> None:
        self.auth_storage = auth_storage
        self._models_json_path = models_json_path
        self._models: list[Model] = []
        self._provider_request_configs: dict[str, _ProviderRequestConfig] = {}
        self._model_request_headers: dict[str, dict[str, str]] = {}
        self._registered_providers: dict[str, ProviderConfigInput] = {}
        self._load_error: str | None = None
        self._load_models()

    @staticmethod
    def create(auth_storage: AuthStorage, models_json_path: str | None = None) -> ModelRegistry:
        return ModelRegistry(
            auth_storage,
            models_json_path if models_json_path is not None else get_models_path(),
        )

    @staticmethod
    def in_memory(auth_storage: AuthStorage) -> ModelRegistry:
        return ModelRegistry(auth_storage, None)

    # -- loading -----------------------------------------------------------

    def refresh(self) -> None:
        """Reload from disk and re-apply every dynamic registration.

        The API and OAuth registries are reset first because a provider that has
        since been unregistered must stop being offered — and re-applying the
        survivors is what puts the rest back.
        """
        self._provider_request_configs.clear()
        self._model_request_headers.clear()
        self._load_error = None

        reset_api_providers()
        reset_oauth_providers()

        self._load_models()

        for provider_name, config in self._registered_providers.items():
            self._apply_provider_config(provider_name, config)

    def get_error(self) -> str | None:
        """Why ``models.json`` did not load, if it did not."""
        return self._load_error

    def _load_models(self) -> None:
        custom = (
            self._load_custom_models(self._models_json_path)
            if self._models_json_path
            else _CustomModelsResult()
        )

        if custom.error:
            # A broken models.json costs the user their custom models, not the
            # built-in ones.
            self._load_error = custom.error

        built_in = self._load_built_in_models(custom.overrides, custom.model_overrides)
        combined = self._merge_custom_models(built_in, custom.models)

        # OAuth providers get to rewrite their own models — GitHub Copilot's
        # base_url depends on the account the token belongs to.
        for oauth_provider in self.auth_storage.get_oauth_providers():
            cred = self.auth_storage.get(oauth_provider.id)
            modify = getattr(oauth_provider, "modify_models", None)
            if isinstance(cred, OAuthCredential) and modify is not None:
                combined = modify(combined, cred.credentials())

        self._models = combined

    def _load_built_in_models(
        self,
        overrides: dict[str, _ProviderOverride],
        model_overrides: dict[str, dict[str, dict[str, Any]]],
    ) -> list[Model]:
        models: list[Model] = []
        for provider in get_providers():
            provider_override = overrides.get(provider)
            per_model_overrides = model_overrides.get(provider)

            for built_in in get_models(provider):
                model = built_in

                if provider_override is not None:
                    model = model.model_copy(
                        update={
                            "base_url": provider_override.base_url or model.base_url,
                            "compat": _merge_compat(
                                model.compat, provider_override.compat, model.api
                            ),
                        }
                    )

                model_override = (
                    per_model_overrides.get(built_in.id) if per_model_overrides else None
                )
                if model_override is not None:
                    model = _apply_model_override(model, model_override)

                models.append(model)
        return models

    def _merge_custom_models(
        self, built_in_models: list[Model], custom_models: list[Model]
    ) -> list[Model]:
        """Custom wins on a provider+id collision, in place rather than appended."""
        merged = list(built_in_models)
        for custom_model in custom_models:
            existing_index = next(
                (
                    index
                    for index, model in enumerate(merged)
                    if model.provider == custom_model.provider and model.id == custom_model.id
                ),
                -1,
            )
            if existing_index >= 0:
                merged[existing_index] = custom_model
            else:
                merged.append(custom_model)
        return merged

    def _load_custom_models(self, models_json_path: str) -> _CustomModelsResult:
        if not os.path.exists(models_json_path):
            return _CustomModelsResult()

        try:
            with open(models_json_path, encoding="utf-8") as handle:
                content = handle.read()
            parsed = json.loads(_strip_json_comments(content))
        except json.JSONDecodeError as error:
            return _CustomModelsResult(
                error=f"Failed to parse models.json: {error}\n\nFile: {models_json_path}"
            )
        except OSError as error:
            return _CustomModelsResult(
                error=f"Failed to load models.json: {error}\n\nFile: {models_json_path}"
            )

        try:
            config = _validate_shape(parsed)
        except _SchemaError as error:
            return _CustomModelsResult(
                error=f"Invalid models.json schema:\n{error}\n\nFile: {models_json_path}"
            )

        try:
            self._validate_config(config)
        except ValueError as error:
            return _CustomModelsResult(
                error=f"Failed to load models.json: {error}\n\nFile: {models_json_path}"
            )

        overrides: dict[str, _ProviderOverride] = {}
        model_overrides: dict[str, dict[str, dict[str, Any]]] = {}

        for provider_name, provider_config in config["providers"].items():
            if provider_config.get("base_url") or provider_config.get("compat"):
                overrides[provider_name] = _ProviderOverride(
                    base_url=provider_config.get("base_url"),
                    compat=provider_config.get("compat"),
                )

            self._store_provider_request_config(
                provider_name,
                api_key=provider_config.get("api_key"),
                headers=provider_config.get("headers"),
                auth_header=provider_config.get("auth_header"),
            )

            if provider_config.get("model_overrides"):
                model_overrides[provider_name] = dict(provider_config["model_overrides"])
                for model_id, model_override in provider_config["model_overrides"].items():
                    self._store_model_headers(
                        provider_name, model_id, model_override.get("headers")
                    )

        return _CustomModelsResult(
            models=self._parse_models(config),
            overrides=overrides,
            model_overrides=model_overrides,
        )

    def _validate_config(self, config: dict[str, Any]) -> None:
        """The cross-field rules the schema cannot express. Port of ``validateConfig``.

        The shape of the rules follows from what can be *inherited*: a built-in
        provider already has an endpoint and an api, so a custom model on one
        needs neither; a provider nobody has heard of needs both, plus a key.
        """
        built_in_providers = set(get_providers())

        for provider_name, provider_config in config["providers"].items():
            is_built_in = provider_name in built_in_providers
            has_provider_api = bool(provider_config.get("api"))
            models = provider_config.get("models") or []
            has_model_overrides = bool(provider_config.get("model_overrides"))

            if not models:
                if not (
                    provider_config.get("base_url")
                    or provider_config.get("headers")
                    or provider_config.get("compat")
                    or has_model_overrides
                ):
                    raise ValueError(
                        f"Provider {provider_name}: must specify "
                        '"base_url", "headers", "compat", "model_overrides", or "models".'
                    )
            elif not is_built_in:
                if not provider_config.get("base_url"):
                    raise ValueError(
                        f'Provider {provider_name}: "base_url" is required '
                        "when defining custom models."
                    )
                if not provider_config.get("api_key"):
                    raise ValueError(
                        f'Provider {provider_name}: "api_key" is required '
                        "when defining custom models."
                    )

            for model_def in models:
                has_model_api = bool(model_def.get("api"))

                if not has_provider_api and not has_model_api and not is_built_in:
                    raise ValueError(
                        f"Provider {provider_name}, model {model_def['id']}: "
                        'no "api" specified. Set at provider or model level.'
                    )

                if not model_def.get("id"):
                    raise ValueError(f'Provider {provider_name}: model missing "id"')
                context_window = model_def.get("context_window")
                if context_window is not None and context_window <= 0:
                    raise ValueError(
                        f"Provider {provider_name}, model {model_def['id']}: invalid context_window"
                    )
                max_tokens = model_def.get("max_tokens")
                if max_tokens is not None and max_tokens <= 0:
                    raise ValueError(
                        f"Provider {provider_name}, model {model_def['id']}: invalid max_tokens"
                    )

    def _parse_models(self, config: dict[str, Any]) -> list[Model]:
        """Turn ``models.json``'s model definitions into models.

        A definition with no resolvable ``api`` or ``base_url`` is *skipped* rather
        than rejected — validation above has already raised for the cases that
        are user error, so what is left is a built-in provider with no built-in
        models to inherit from, which is nothing this can build.
        """
        models: list[Model] = []
        built_in_providers = set(get_providers())
        built_in_defaults_cache: dict[str, tuple[str, str] | None] = {}

        def get_built_in_defaults(provider_name: str) -> tuple[str, str] | None:
            if provider_name not in built_in_providers:
                return None
            if provider_name in built_in_defaults_cache:
                return built_in_defaults_cache[provider_name]
            built_in = get_models(provider_name)
            defaults = (built_in[0].api, built_in[0].base_url) if built_in else None
            built_in_defaults_cache[provider_name] = defaults
            return defaults

        for provider_name, provider_config in config["providers"].items():
            model_defs = provider_config.get("models") or []
            if not model_defs:
                continue

            built_in_defaults = get_built_in_defaults(provider_name)

            for model_def in model_defs:
                api = (
                    model_def.get("api")
                    or provider_config.get("api")
                    or (built_in_defaults[0] if built_in_defaults else None)
                )
                if not api:
                    continue

                base_url = (
                    model_def.get("base_url")
                    or provider_config.get("base_url")
                    or (built_in_defaults[1] if built_in_defaults else None)
                )
                if not base_url:
                    continue

                compat = _merge_compat(provider_config.get("compat"), model_def.get("compat"), api)
                self._store_model_headers(provider_name, model_def["id"], model_def.get("headers"))

                models.append(
                    Model(
                        id=model_def["id"],
                        name=model_def.get("name") or model_def["id"],
                        api=api,
                        provider=provider_name,
                        base_url=base_url,
                        reasoning=model_def.get("reasoning", False),
                        thinking_level_map=model_def.get("thinking_level_map"),
                        input=list(model_def.get("input") or ["text"]),
                        cost=model_def.get("cost")
                        or {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        context_window=model_def.get("context_window", 128000),
                        max_tokens=model_def.get("max_tokens", 16384),
                        headers=None,
                        compat=compat,
                    )
                )

        return models

    # -- reading -----------------------------------------------------------

    def get_all(self) -> list[Model]:
        """Every model this build knows about, with or without credentials."""
        return self._models

    def get_available_sync(self) -> list[Model]:
        """The models with auth configured. Never refreshes a token."""
        return [model for model in self._models if self.has_configured_auth(model)]

    async def get_available(self) -> list[Model]:
        """:meth:`get_available_sync`, as the async
        :class:`~cortex.code.session.ModelRegistryLike` requires.

        The TS's ``getAvailable`` is synchronous and every caller of it in the
        overlays is inside an ``await`` anyway (the TS awaits a non-promise). This
        port's protocol made the async form the contract, so both spellings
        exist and the sync one is the implementation.
        """
        return self.get_available_sync()

    def find(self, provider: str, model_id: str) -> Model | None:
        return next(
            (
                model
                for model in self._models
                if model.provider == provider and model.id == model_id
            ),
            None,
        )

    def has_configured_auth(self, model: Model) -> bool:
        """Whether this model's provider has *some* usable key."""
        return (
            self.auth_storage.has_auth(model.provider)
            or self._provider_request_configs.get(model.provider) is not None
            and self._provider_request_configs[model.provider].api_key is not None
        )

    def is_using_oauth(self, model: Model) -> bool:
        """Whether this model's provider is authenticated by subscription."""
        return isinstance(self.auth_storage.get(model.provider), OAuthCredential)

    # -- request auth ------------------------------------------------------

    def _get_model_request_key(self, provider: str, model_id: str) -> str:
        return f"{provider}:{model_id}"

    def _store_provider_request_config(
        self,
        provider_name: str,
        *,
        api_key: str | None,
        headers: dict[str, str] | None,
        auth_header: bool | None,
    ) -> None:
        if not api_key and not headers and not auth_header:
            return
        self._provider_request_configs[provider_name] = _ProviderRequestConfig(
            api_key=api_key, headers=headers, auth_header=auth_header
        )

    def _store_model_headers(
        self, provider_name: str, model_id: str, headers: dict[str, str] | None
    ) -> None:
        key = self._get_model_request_key(provider_name, model_id)
        if not headers:
            self._model_request_headers.pop(key, None)
            return
        self._model_request_headers[key] = dict(headers)

    async def get_api_key_and_headers(self, model: Model) -> ResolvedRequestAuth:
        """Everything the HTTP call needs, or the reason it cannot be made.

        ``include_fallback=False`` on the storage lookup is not an optimisation:
        the fallback *is* this registry's own ``models.json`` key, and consulting
        it here would resolve the same value twice — once cached and once not.
        """
        try:
            provider_config = self._provider_request_configs.get(model.provider)
            api_key_from_auth_storage = await self.auth_storage.get_api_key(
                model.provider, include_fallback=False
            )
            api_key = api_key_from_auth_storage
            if api_key is None and provider_config is not None and provider_config.api_key:
                api_key = resolve_config_value_or_throw(
                    provider_config.api_key, f'API key for provider "{model.provider}"'
                )

            provider_headers = resolve_headers_or_throw(
                provider_config.headers if provider_config else None,
                f'provider "{model.provider}"',
            )
            model_headers = resolve_headers_or_throw(
                self._model_request_headers.get(
                    self._get_model_request_key(model.provider, model.id)
                ),
                f'model "{model.provider}/{model.id}"',
            )

            headers: dict[str, str] | None = None
            if model.headers or provider_headers or model_headers:
                headers = {
                    **(model.headers or {}),
                    **(provider_headers or {}),
                    **(model_headers or {}),
                }

            if provider_config is not None and provider_config.auth_header:
                if not api_key:
                    return ResolvedRequestAuth(
                        ok=False, error=f'No API key found for "{model.provider}"'
                    )
                headers = {**(headers or {}), "Authorization": f"Bearer {api_key}"}

            return ResolvedRequestAuth(
                ok=True, api_key=api_key, headers=headers if headers else None
            )
        except Exception as error:  # noqa: BLE001 - the TS's catch, one for one
            return ResolvedRequestAuth(ok=False, error=str(error))

    def get_provider_auth_status(self, provider: str) -> AuthStatus:
        """Auth status including what ``models.json`` configures.

        Deliberately does **not** run a command-backed value: this feeds a list
        the user is scrolling, and shelling out per row to find out whether a key
        exists would make the overlay wait on the slowest of them.
        """
        auth_status = self.auth_storage.get_auth_status(provider)
        if auth_status.source is not None:
            return auth_status

        provider_config = self._provider_request_configs.get(provider)
        provider_api_key = provider_config.api_key if provider_config else None
        if not provider_api_key:
            return auth_status

        if provider_api_key.startswith("!"):
            return AuthStatus(configured=True, source="models_json_command")

        if os.environ.get(provider_api_key):
            return AuthStatus(configured=True, source="environment", label=provider_api_key)

        return AuthStatus(configured=True, source="models_json_key")

    def get_provider_display_name(self, provider: str) -> str:
        """How to spell the provider on screen, most specific source first."""
        registered_provider = self._registered_providers.get(provider)
        oauth_provider = next(
            (p for p in self.auth_storage.get_oauth_providers() if p.id == provider), None
        )

        if registered_provider is not None and registered_provider.name:
            return registered_provider.name
        registered_oauth_name = getattr(getattr(registered_provider, "oauth", None), "name", None)
        if registered_oauth_name:
            return str(registered_oauth_name)
        if oauth_provider is not None:
            return oauth_provider.name
        return BUILT_IN_PROVIDER_DISPLAY_NAMES.get(provider, provider)

    async def get_api_key_for_provider(self, provider: str) -> str | None:
        """The provider's key, wherever it comes from."""
        api_key = await self.auth_storage.get_api_key(provider, include_fallback=False)
        if api_key is not None:
            return api_key

        provider_config = self._provider_request_configs.get(provider)
        provider_api_key = provider_config.api_key if provider_config else None
        return resolve_config_value_uncached(provider_api_key) if provider_api_key else None

    # -- dynamic registration ----------------------------------------------

    def register_provider(self, provider_name: str, config: ProviderConfigInput) -> None:
        """Add a provider at runtime (the extension-facing door)."""
        self._validate_provider_config(provider_name, config)
        self._apply_provider_config(provider_name, config)
        self._upsert_registered_provider(provider_name, config)

    def unregister_provider(self, provider_name: str) -> None:
        """Remove a dynamically registered provider and restore what it overrode.

        A full :meth:`refresh` rather than an unwind: the provider may have
        replaced built-in models, and reloading from disk is the only way to get
        the originals back.
        """
        if provider_name not in self._registered_providers:
            return
        del self._registered_providers[provider_name]
        self.refresh()

    def _upsert_registered_provider(self, provider_name: str, config: ProviderConfigInput) -> None:
        """Merge a re-registration field by field, keeping what the new one omits."""
        existing = self._registered_providers.get(provider_name)
        if existing is None:
            self._registered_providers[provider_name] = config
            return
        for key, value in vars(config).items():
            if value is not None:
                setattr(existing, key, value)

    def _validate_provider_config(self, provider_name: str, config: ProviderConfigInput) -> None:
        if config.stream_simple is not None and not config.api:
            raise ValueError(
                f'Provider {provider_name}: "api" is required when registering streamSimple.'
            )

        if not config.models:
            return

        if not config.base_url:
            raise ValueError(
                f'Provider {provider_name}: "base_url" is required when defining models.'
            )
        if not config.api_key and config.oauth is None:
            raise ValueError(
                f'Provider {provider_name}: "api_key" or "oauth" is required when defining models.'
            )

        for model_def in config.models:
            if not (model_def.api or config.api):
                raise ValueError(
                    f'Provider {provider_name}, model {model_def.id}: no "api" specified.'
                )

    def _apply_provider_config(self, provider_name: str, config: ProviderConfigInput) -> None:
        if config.oauth is not None:
            # The OAuth provider's id must be the provider's name, or `/login`
            # would store the credential under a key nothing looks up.
            oauth_provider = config.oauth
            oauth_provider.id = provider_name
            register_oauth_provider(oauth_provider)

        if config.stream_simple is not None:
            # `stream` and `streamSimple` are the same function here, as in the
            # TS: a provider registering only the simple form gets the general
            # one for free, and `register_api_provider` wraps both with the
            # api-match check.
            # `StreamFunction` is a nominal class in `cortex.ai.models`, not a
            # callable protocol, so a plain function needs the cast even though
            # `register_api_provider` only ever calls it.
            stream_simple = cast(Any, config.stream_simple)
            register_api_provider(
                ApiProvider(api=str(config.api), stream=stream_simple, stream_simple=stream_simple),
                f"provider:{provider_name}",
            )

        self._store_provider_request_config(
            provider_name,
            api_key=config.api_key,
            headers=config.headers,
            auth_header=config.auth_header,
        )

        if config.models:
            # Full replacement: this provider's models are the ones given.
            self._models = [model for model in self._models if model.provider != provider_name]

            for model_def in config.models:
                api = model_def.api or config.api
                self._store_model_headers(provider_name, model_def.id, model_def.headers)

                self._models.append(
                    Model(
                        id=model_def.id,
                        name=model_def.name,
                        api=str(api),
                        provider=provider_name,
                        base_url=model_def.base_url or str(config.base_url),
                        reasoning=model_def.reasoning,
                        thinking_level_map=model_def.thinking_level_map,
                        input=list(model_def.input),
                        cost=dict(model_def.cost),
                        context_window=model_def.context_window,
                        max_tokens=model_def.max_tokens,
                        headers=None,
                        compat=model_def.compat,
                    )
                )

            modify = getattr(config.oauth, "modify_models", None)
            if modify is not None:
                cred = self.auth_storage.get(provider_name)
                if isinstance(cred, OAuthCredential):
                    self._models = modify(self._models, cred.credentials())
        elif config.base_url or config.headers:
            # Override-only: the endpoint moves, headers are resolved per request.
            self._models = [
                model.model_copy(update={"base_url": config.base_url or model.base_url})
                if model.provider == provider_name
                else model
                for model in self._models
            ]
