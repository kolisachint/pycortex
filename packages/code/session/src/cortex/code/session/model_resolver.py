"""Resolving a model reference to a model. Port of ``core/model-resolver.ts``.

**Half the file, and the half is the point.** ``model-resolver.ts`` does two
different jobs: it turns a *reference the user typed* into a model
(``findExactModelReferenceMatch``, ``parseModelPattern``, ``resolveModelScope``),
and it decides *which model a session starts on* (``resolveCliModel``,
``findInitialModel``, ``restoreModelFromSession``, ``defaultModelPerProvider``).
The second half is startup resolution over stored credentials, which is step
7.11's job along with the auth storage it reads; the first is what ``/model
<name>`` and ``/scoped-models`` call at runtime, which is 7.9's. Only the first
is here, so the file can be finished in 7.11 without unpicking anything.

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
    "ParsedModelResult",
    "ScopedModel",
    "find_exact_model_reference_match",
    "is_alias",
    "parse_model_pattern",
    "resolve_model_scope",
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
