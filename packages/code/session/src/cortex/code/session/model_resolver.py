"""Resolving a model reference to a model. Port of ``core/model-resolver.ts``.

**Two jobs, and the split is the point.** ``model-resolver.ts`` turns a
*reference the user typed* into a model (``findExactModelReferenceMatch``,
``parseModelPattern``, ``resolveModelScope``) and decides *which model a session
starts on* (``resolveCliModel``, ``findInitialModel``,
``restoreModelFromSession``, ``defaultModelPerProvider``). The first half landed
with 7.9, which is when ``/model`` and ``/scoped-models`` started needing it; the
second is startup resolution over stored credentials and arrives with 7.11,
which is when there is a
:class:`~cortex.code.config.ModelRegistry` holding those credentials to resolve
against. Both halves are here now.

Two Python-side notes:

* the TS matches globs with ``minimatch``, whose ``*`` stops at a ``/``. That is
  load-bearing here — it is why the caller tries the pattern against both
  ``provider/id`` and a bare ``id`` — so this uses
  :func:`cortex.code.tools.native_search.minimatch`, the port's own translation
  of those semantics, rather than :mod:`fnmatch` (whose ``*`` would swallow the
  separator and make the second attempt redundant);
* ``isValidThinkingLevel`` lives in ``cli/args.ts`` there and in
  :mod:`cortex.code.main.args` here, which this leaf cannot import (``main``
  depends on ``session``, not the other way round). The check goes through
  :data:`~cortex.ai.models.EXTENDED_THINKING_LEVELS` instead — the same six
  levels, from the package that defines them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from cortex.ai.models import EXTENDED_THINKING_LEVELS, models_are_equal
from cortex.ai.types import Model
from cortex.code.tools.native_search import minimatch

__all__ = [
    "DEFAULT_MODEL_PER_PROVIDER",
    "DEFAULT_THINKING_LEVEL",
    "InitialModelResult",
    "ParsedModelResult",
    "ResolveCliModelResult",
    "RestoredModelResult",
    "ScopedModel",
    "StartupModelRegistry",
    "find_exact_model_reference_match",
    "find_initial_model",
    "is_alias",
    "parse_model_pattern",
    "resolve_cli_model",
    "resolve_model_scope",
    "restore_model_from_session",
]

#: A date suffix on a model id, as the TS's ``/-\d{8}$/``.
_DATE_SUFFIX = re.compile(r"-\d{8}$")


class AvailableModels(Protocol):
    """The one thing scope resolution asks a model registry for."""

    async def get_available(self) -> list[Model]: ...


@dataclass(frozen=True)
class ScopedModel:
    """One model in the cycling scope, with the level its pattern pinned."""

    model: Model
    #: Set only when the pattern said so (``sonnet:high``); ``None`` inherits.
    thinking_level: str | None = None


@dataclass(frozen=True)
class ParsedModelResult:
    """What one ``pattern`` resolved to, plus anything worth warning about."""

    model: Model | None
    thinking_level: str | None
    warning: str | None


def is_alias(model_id: str) -> bool:
    """Whether a model id is an alias rather than a dated release.

    ``claude-sonnet-4-5`` is an alias; ``claude-sonnet-4-5-20250929`` is not.
    """
    if model_id.endswith("-latest"):
        return True
    return _DATE_SUFFIX.search(model_id) is None


def find_exact_model_reference_match(
    model_reference: str, available_models: list[Model]
) -> Model | None:
    """Match a bare id or a ``provider/id`` reference, exactly.

    Ambiguity is answered with ``None`` rather than a guess: two providers can
    serve the same model id, and picking one of them silently is how a user ends
    up billed by a provider they did not name.
    """
    trimmed_reference = model_reference.strip()
    if not trimmed_reference:
        return None

    normalized_reference = trimmed_reference.lower()

    canonical_matches = [
        model
        for model in available_models
        if f"{model.provider}/{model.id}".lower() == normalized_reference
    ]
    if len(canonical_matches) == 1:
        return canonical_matches[0]
    if len(canonical_matches) > 1:
        return None

    slash_index = trimmed_reference.find("/")
    if slash_index != -1:
        provider = trimmed_reference[:slash_index].strip()
        model_id = trimmed_reference[slash_index + 1 :].strip()
        if provider and model_id:
            provider_matches = [
                model
                for model in available_models
                if model.provider.lower() == provider.lower()
                and model.id.lower() == model_id.lower()
            ]
            if len(provider_matches) == 1:
                return provider_matches[0]
            if len(provider_matches) > 1:
                return None

    id_matches = [model for model in available_models if model.id.lower() == normalized_reference]
    return id_matches[0] if len(id_matches) == 1 else None


def _normalize_separators(text: str) -> str:
    """Lower-case, with ``.`` and ``-`` collapsed onto ``-``.

    Providers disagree about separators for the same model (anthropic's
    ``claude-haiku-4-5`` is github-copilot's ``claude-haiku-4.5``), which defeats
    plain substring matching.
    """
    return re.sub(r"[.-]", "-", text.lower())


def _try_match_model(model_pattern: str, available_models: list[Model]) -> Model | None:
    """Exact match, then substring, then separator-normalised substring.

    Ties are broken towards an alias, and between dated versions towards the one
    that sorts highest — i.e. the newest, since the suffix is a date.
    """
    exact_match = find_exact_model_reference_match(model_pattern, available_models)
    if exact_match is not None:
        return exact_match

    lowered = model_pattern.lower()
    matches = [
        model
        for model in available_models
        if lowered in model.id.lower() or lowered in model.name.lower()
    ]

    if not matches:
        normalized_pattern = _normalize_separators(model_pattern)
        matches = [
            model
            for model in available_models
            if normalized_pattern in _normalize_separators(model.id)
            or normalized_pattern in _normalize_separators(model.name)
        ]

    if not matches:
        return None

    aliases = [model for model in matches if is_alias(model.id)]
    dated_versions = [model for model in matches if not is_alias(model.id)]

    if aliases:
        return sorted(aliases, key=lambda model: model.id, reverse=True)[0]
    return sorted(dated_versions, key=lambda model: model.id, reverse=True)[0]


def parse_model_pattern(
    pattern: str,
    available_models: list[Model],
    allow_invalid_thinking_level_fallback: bool = True,
) -> ParsedModelResult:
    """Split ``pattern`` into a model and an optional ``:level`` suffix.

    The full pattern is tried as a model id first, because ids can contain
    colons themselves (OpenRouter's ``…:exacto``); only when that fails is the
    last colon treated as a separator, and the prefix retried.
    """
    exact_match = _try_match_model(pattern, available_models)
    if exact_match is not None:
        return ParsedModelResult(exact_match, None, None)

    last_colon_index = pattern.rfind(":")
    if last_colon_index == -1:
        return ParsedModelResult(None, None, None)

    prefix = pattern[:last_colon_index]
    suffix = pattern[last_colon_index + 1 :]

    if suffix in EXTENDED_THINKING_LEVELS:
        result = parse_model_pattern(
            prefix, available_models, allow_invalid_thinking_level_fallback
        )
        if result.model is not None:
            # A warning from the inner call means the level it found is not
            # trustworthy, so this one is dropped rather than layered on top.
            return ParsedModelResult(
                result.model, None if result.warning else suffix, result.warning
            )
        return result

    if not allow_invalid_thinking_level_fallback:
        # Strict mode (CLI ``--model``): treat the suffix as part of the id and
        # fail, rather than quietly resolving to a different model.
        return ParsedModelResult(None, None, None)

    result = parse_model_pattern(prefix, available_models, allow_invalid_thinking_level_fallback)
    if result.model is not None:
        return ParsedModelResult(
            result.model,
            None,
            f'Invalid thinking level "{suffix}" in pattern "{pattern}". Using default instead.',
        )
    return result


def _is_glob(pattern: str) -> bool:
    return "*" in pattern or "?" in pattern or "[" in pattern


async def resolve_model_scope(
    patterns: list[str], model_registry: AvailableModels
) -> list[ScopedModel]:
    """Resolve ``--models``/``/scoped-models`` patterns to models, in order.

    Warnings the TS prints to the console are dropped rather than redirected:
    every caller here is inside a running TUI, where a ``console.warn`` would
    land in the middle of the frame. The pattern is skipped, exactly as there.
    """
    available_models = await model_registry.get_available()
    scoped_models: list[ScopedModel] = []

    def already_scoped(model: Model) -> bool:
        return any(models_are_equal(scoped.model, model) for scoped in scoped_models)

    for pattern in patterns:
        if _is_glob(pattern):
            colon_index = pattern.rfind(":")
            glob_pattern = pattern
            thinking_level: str | None = None

            if colon_index != -1:
                suffix = pattern[colon_index + 1 :]
                if suffix in EXTENDED_THINKING_LEVELS:
                    thinking_level = suffix
                    glob_pattern = pattern[:colon_index]

            # Both spellings, because minimatch's ``*`` does not cross a ``/``:
            # ``*sonnet*`` has to match a bare id for the user not to have to
            # write the provider out.
            matching_models = [
                model
                for model in available_models
                if minimatch(f"{model.provider}/{model.id}", glob_pattern, nocase=True)
                or minimatch(model.id, glob_pattern, nocase=True)
            ]

            for model in matching_models:
                if not already_scoped(model):
                    scoped_models.append(ScopedModel(model, thinking_level))
            continue

        parsed = parse_model_pattern(pattern, available_models)
        if parsed.model is None:
            continue
        if not already_scoped(parsed.model):
            scoped_models.append(ScopedModel(parsed.model, parsed.thinking_level))

    return scoped_models


def scoped_model_ids(scoped_models: list[Any]) -> list[str]:
    """``provider/id`` for each scoped model — the id the selectors key on."""
    return [f"{scoped.model.provider}/{scoped.model.id}" for scoped in scoped_models]


# ===========================================================================
# Startup resolution — which model a session begins on
#
# Everything above turns a reference the *user typed at runtime* into a model.
# Everything below decides what the session starts on before there is a user to
# type anything, which is a different question because it has to consult stored
# credentials: a default naming a provider with no key must not wedge startup,
# it must fall through to a provider that works.
# ===========================================================================

#: The model each known provider starts on. Port of ``defaultModelPerProvider``.
#:
#: Insertion order is load-bearing — :func:`find_initial_model` walks it in order
#: looking for the first available default, so this is also the preference list
#: for "the user has keys for three providers, which one answers".
DEFAULT_MODEL_PER_PROVIDER: dict[str, str] = {
    "anthropic": "claude-opus-4-7",
    "openai": "gpt-5.4",
    "azure-openai-responses": "gpt-5.4",
    "openai-codex": "gpt-5.5",
    "deepseek": "deepseek-v4-pro",
    "google": "gemini-3.1-pro-preview",
    "google-vertex": "gemini-3.1-pro-preview",
    "github-copilot": "gpt-5.4",
    "openrouter": "moonshotai/kimi-k2.6",
    "vercel-ai-gateway": "zai/glm-5.1",
    "xai": "grok-4.20-0309-reasoning",
    "groq": "openai/gpt-oss-120b",
    "cerebras": "zai-glm-4.7",
    "zai": "glm-5.1",
    "minimax": "MiniMax-M2.7",
    "minimax-cn": "MiniMax-M2.7",
    "moonshotai": "kimi-k2.6",
    "moonshotai-cn": "kimi-k2.6",
    "huggingface": "moonshotai/Kimi-K2.6",
    "fireworks": "accounts/fireworks/models/kimi-k2p6",
    "together": "moonshotai/Kimi-K2.6",
    "opencode": "kimi-k2.6",
    "opencode-go": "kimi-k2.6",
    "kimi-coding": "kimi-for-coding",
    "xiaomi": "mimo-v2.5-pro",
    "xiaomi-token-plan-cn": "mimo-v2.5-pro",
    "xiaomi-token-plan-ams": "mimo-v2.5-pro",
    "xiaomi-token-plan-sgp": "mimo-v2.5-pro",
    "nvidia": "meta/llama-3.3-70b-instruct",
}

#: ``core/defaults.ts``'s ``DEFAULT_THINKING_LEVEL``. One constant, ported where
#: it is used rather than as a module of its own.
DEFAULT_THINKING_LEVEL = "off"


class StartupModelRegistry(Protocol):
    """What startup resolution asks a registry for.

    Wider than :class:`AvailableModels` above and narrower than
    :class:`cortex.code.session.ModelRegistryLike`: these functions need the
    *unfiltered* list (``get_all``) as well as the credentialled one, because
    ``--model`` names a model the user may not have a key for yet.
    """

    def get_all(self) -> list[Model]: ...

    def find(self, provider: str, model_id: str) -> Model | None: ...

    def has_configured_auth(self, model: Model) -> bool: ...

    async def get_available(self) -> list[Model]: ...


@dataclass(frozen=True)
class ResolveCliModelResult:
    """What ``--provider``/``--model`` resolved to.

    ``model`` and ``error`` are mutually exclusive; a ``warning`` can accompany
    either (a fuzzy match that worked, but not the way the user may have meant).
    """

    model: Model | None
    thinking_level: str | None = None
    warning: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class InitialModelResult:
    """The model a session starts on, and why it is not the one that was asked for.

    ``error`` is this port's, not the TS's: ``findInitialModel`` prints and calls
    ``process.exit(1)`` on an unresolvable ``--model``, which a leaf that other
    modes import must not do. The caller in ``code/main`` prints it and exits,
    so the user-visible behaviour is the TS's.
    """

    model: Model | None
    thinking_level: str
    fallback_message: str | None = None
    error: str | None = None


def _build_fallback_model(
    provider: str, model_id: str, available_models: list[Model]
) -> Model | None:
    """A model the registry has never heard of, shaped like one it has.

    Port of ``buildFallbackModel``. This is what makes ``--model
    my-local-llama`` work against an OpenAI-compatible endpoint: the id is the
    user's, and every other field is borrowed from a model on the same provider
    so the request is formed the same way.
    """
    provider_models = [model for model in available_models if model.provider == provider]
    if not provider_models:
        return None

    default_id = DEFAULT_MODEL_PER_PROVIDER.get(provider)
    base_model = (
        next((m for m in provider_models if m.id == default_id), provider_models[0])
        if default_id
        else provider_models[0]
    )

    return base_model.model_copy(update={"id": model_id, "name": model_id})


def resolve_cli_model(
    *,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    model_registry: StartupModelRegistry,
) -> ResolveCliModelResult:
    """Resolve ``--provider``/``--model``. Port of ``resolveCliModel``.

    The awkward shape of this function is earned by one ambiguity: a slash means
    ``provider/model`` *and* appears inside real model ids
    (``moonshotai/kimi-k2.6`` on openrouter). So the order is: a known provider
    prefix wins, then a literal id match across everything, then fuzzy matching
    inside the provider, then — if a provider *was* inferred from the slash and
    nothing matched inside it — the whole string as an id again, because
    ``openai/gpt-4o:extended`` looks like a provider prefix and is not one.

    The search is over :meth:`get_all` rather than the available models on
    purpose: ``--api-key`` exists to set up a provider that has no key yet, and
    filtering by credentials here would hide the model it is being passed for.
    """
    if not cli_model:
        return ResolveCliModelResult(model=None)

    available_models = model_registry.get_all()
    if not available_models:
        return ResolveCliModelResult(
            model=None,
            error="No models available. Check your installation or add models to models.json.",
        )

    provider_map = {model.provider.lower(): model.provider for model in available_models}

    provider = provider_map.get(cli_provider.lower()) if cli_provider else None
    if cli_provider and not provider:
        return ResolveCliModelResult(
            model=None,
            error=(
                f'Unknown provider "{cli_provider}". '
                "Use --list-models to see available providers/models."
            ),
        )

    pattern = cli_model
    inferred_provider = False

    if not provider:
        slash_index = cli_model.find("/")
        if slash_index != -1:
            maybe_provider = cli_model[:slash_index]
            canonical = provider_map.get(maybe_provider.lower())
            if canonical:
                provider = canonical
                pattern = cli_model[slash_index + 1 :]
                inferred_provider = True

    if not provider:
        exact = _find_literal_model(cli_model, available_models)
        if exact is not None:
            return ResolveCliModelResult(model=exact)

    if cli_provider and provider:
        # Tolerate `--provider openai --model openai/gpt-5.4`.
        prefix = f"{provider}/"
        if cli_model.lower().startswith(prefix.lower()):
            pattern = cli_model[len(prefix) :]

    candidates = (
        [model for model in available_models if model.provider == provider]
        if provider
        else available_models
    )
    parsed = parse_model_pattern(pattern, candidates, allow_invalid_thinking_level_fallback=False)

    if parsed.model is not None:
        return ResolveCliModelResult(
            model=parsed.model, thinking_level=parsed.thinking_level, warning=parsed.warning
        )

    if inferred_provider:
        exact = _find_literal_model(cli_model, available_models)
        if exact is not None:
            return ResolveCliModelResult(model=exact)
        fallback = parse_model_pattern(
            cli_model, available_models, allow_invalid_thinking_level_fallback=False
        )
        if fallback.model is not None:
            return ResolveCliModelResult(
                model=fallback.model,
                thinking_level=fallback.thinking_level,
                warning=fallback.warning,
            )

    if provider:
        fallback_model = _build_fallback_model(provider, pattern, available_models)
        if fallback_model is not None:
            not_found = (
                f'Model "{pattern}" not found for provider "{provider}". Using custom model id.'
            )
            return ResolveCliModelResult(
                model=fallback_model,
                warning=f"{parsed.warning} {not_found}" if parsed.warning else not_found,
            )

    display = f"{provider}/{pattern}" if provider else cli_model
    return ResolveCliModelResult(
        model=None,
        warning=parsed.warning,
        error=f'Model "{display}" not found. Use --list-models to see available models.',
    )


def _find_literal_model(reference: str, models: list[Model]) -> Model | None:
    """The first model whose id — or ``provider/id`` — equals ``reference``."""
    lower = reference.lower()
    return next(
        (
            model
            for model in models
            if model.id.lower() == lower or f"{model.provider}/{model.id}".lower() == lower
        ),
        None,
    )


def _first_default_available(available_models: list[Model]) -> Model | None:
    """The first model in :data:`DEFAULT_MODEL_PER_PROVIDER` order that is available."""
    for provider, default_id in DEFAULT_MODEL_PER_PROVIDER.items():
        match = next(
            (m for m in available_models if m.provider == provider and m.id == default_id),
            None,
        )
        if match is not None:
            return match
    return None


async def find_initial_model(
    *,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    scoped_models: list[ScopedModel] | None = None,
    is_continuing: bool = False,
    default_provider: str | None = None,
    default_model_id: str | None = None,
    default_thinking_level: str | None = None,
    model_registry: StartupModelRegistry,
) -> InitialModelResult:
    """Which model to start on. Port of ``findInitialModel``.

    In priority order: the CLI flags, the first scoped model (unless resuming, in
    which case the session's own model wins), the saved default, then the first
    available model — preferring each provider's default in
    :data:`DEFAULT_MODEL_PER_PROVIDER` order.

    **The saved default is only honoured when its provider has auth**, which is
    the one branch here worth reading twice. A settings file naming
    ``anthropic/claude-opus-4-7`` on a machine with only an OpenAI key would
    otherwise pin a model that cannot answer, and the user would see "no API key"
    on every turn instead of the working provider they do have.
    """
    scoped = list(scoped_models or [])
    thinking_level: str = DEFAULT_THINKING_LEVEL

    # 1. CLI flags.
    if cli_provider and cli_model:
        resolved = resolve_cli_model(
            cli_provider=cli_provider, cli_model=cli_model, model_registry=model_registry
        )
        if resolved.error:
            return InitialModelResult(
                model=None, thinking_level=DEFAULT_THINKING_LEVEL, error=resolved.error
            )
        if resolved.model is not None:
            return InitialModelResult(model=resolved.model, thinking_level=DEFAULT_THINKING_LEVEL)

    # 2. The scope's first model — but not when resuming, where the session's
    #    own model is the one the user left off on.
    if scoped and not is_continuing:
        return InitialModelResult(
            model=scoped[0].model,
            thinking_level=(
                scoped[0].thinking_level or default_thinking_level or DEFAULT_THINKING_LEVEL
            ),
        )

    # 3. The saved default, if its provider can actually be reached.
    if default_provider and default_model_id:
        found = model_registry.find(default_provider, default_model_id)
        if found is not None and model_registry.has_configured_auth(found):
            if default_thinking_level:
                thinking_level = default_thinking_level
            return InitialModelResult(model=found, thinking_level=thinking_level)

    # 4. Anything with a key, best default first.
    available_models = await model_registry.get_available()
    if available_models:
        match = _first_default_available(available_models)
        return InitialModelResult(
            model=match if match is not None else available_models[0],
            thinking_level=DEFAULT_THINKING_LEVEL,
        )

    # 5. Nothing to start on. `auth_guidance` is what the user is shown instead.
    return InitialModelResult(model=None, thinking_level=DEFAULT_THINKING_LEVEL)


@dataclass(frozen=True)
class RestoredModelResult:
    """The model a resumed session runs on, and what to say if it changed."""

    model: Model | None
    fallback_message: str | None = None


async def restore_model_from_session(
    saved_provider: str,
    saved_model_id: str,
    current_model: Model | None,
    should_print_messages: bool,
    model_registry: StartupModelRegistry,
) -> RestoredModelResult:
    """Put a resumed session back on the model it was saved with, or explain.

    Port of ``restoreModelFromSession``. Two ways it can fail — the model is gone
    from the registry, or its provider's credentials are — and the message names
    which, because they need different fixes (``/model`` versus ``/login``).

    The TS's ``chalk`` colouring of these lines is dropped: they are printed
    before the TUI starts and this port has no styling helper for that path, so
    they are plain text.
    """
    restored_model = model_registry.find(saved_provider, saved_model_id)
    has_configured_auth = (
        model_registry.has_configured_auth(restored_model) if restored_model else False
    )

    if restored_model is not None and has_configured_auth:
        if should_print_messages:
            print(f"Restored model: {saved_provider}/{saved_model_id}")
        return RestoredModelResult(model=restored_model)

    reason = "model no longer exists" if restored_model is None else "no auth configured"

    if should_print_messages:
        print(f"Warning: Could not restore model {saved_provider}/{saved_model_id} ({reason}).")

    if current_model is not None:
        if should_print_messages:
            print(f"Falling back to: {current_model.provider}/{current_model.id}")
        return RestoredModelResult(
            model=current_model,
            fallback_message=(
                f"Could not restore model {saved_provider}/{saved_model_id} ({reason}). "
                f"Using {current_model.provider}/{current_model.id}."
            ),
        )

    available_models = await model_registry.get_available()
    if available_models:
        fallback_model = _first_default_available(available_models) or available_models[0]

        if should_print_messages:
            print(f"Falling back to: {fallback_model.provider}/{fallback_model.id}")

        return RestoredModelResult(
            model=fallback_model,
            fallback_message=(
                f"Could not restore model {saved_provider}/{saved_model_id} ({reason}). "
                f"Using {fallback_model.provider}/{fallback_model.id}."
            ),
        )

    return RestoredModelResult(model=None)
