"""The provider options classes must inherit their base's fields.

Both option classes carried a `@dataclass` decorator on top of a Pydantic
`StreamOptions` base. A dataclass only inherits fields from *dataclass* bases,
so the synthesized `__init__` accepted the locally declared fields and rejected
every inherited one — `OpenAICompletionsOptions.__init__() got an unexpected
keyword argument 'temperature'` on the first message of any openai-completions
model. `stream_simple_openai_completions` and `stream_simple_openai_responses`
both build their options by splatting a `StreamOptions` in, so the crash was
unconditional rather than only on an explicitly-passed temperature.
"""

from __future__ import annotations

import dataclasses
import importlib
import sys
from pathlib import Path

import pytest
from cortex.ai.providers.openai.openai_completions import OpenAICompletionsOptions
from cortex.ai.providers.openai.openai_responses import OpenAIResponsesOptions
from cortex.ai.types import StreamOptions
from pydantic import BaseModel


class TestOpenAICompletionsOptions:
    def test_inherited_and_local_fields_round_trip(self) -> None:
        options = OpenAICompletionsOptions(temperature=0.5, max_tokens=100, reasoning_effort="low")

        assert options.temperature == 0.5
        assert options.max_tokens == 100
        assert options.reasoning_effort == "low"

    def test_the_crashing_construction_shape_works(self) -> None:
        """The production path: splat a `StreamOptions` in, add the local field."""
        base = StreamOptions(temperature=0.5, max_tokens=100)

        options = OpenAICompletionsOptions(**base.__dict__, reasoning_effort="low")

        assert options.temperature == 0.5
        assert options.max_tokens == 100
        assert options.reasoning_effort == "low"

    def test_local_fields_keep_their_defaults(self) -> None:
        options = OpenAICompletionsOptions(temperature=0.5, max_tokens=100)

        assert options.reasoning_effort is None
        assert options.service_tier is None

    def test_every_base_field_is_accepted(self) -> None:
        """Guards the whole inherited surface, not just the two in the report."""
        for name in StreamOptions.model_fields:
            assert name in OpenAICompletionsOptions.model_fields, name


class TestOpenAIResponsesOptions:
    def test_inherited_and_local_fields_round_trip(self) -> None:
        options = OpenAIResponsesOptions(
            temperature=0.5,
            max_tokens=100,
            reasoning_effort="low",
            reasoning_summary="auto",
        )

        assert options.temperature == 0.5
        assert options.max_tokens == 100
        assert options.reasoning_effort == "low"
        assert options.reasoning_summary == "auto"

    def test_the_crashing_construction_shape_works(self) -> None:
        base = StreamOptions(temperature=0.5, max_tokens=100)

        options = OpenAIResponsesOptions(**base.__dict__, reasoning_effort="low")

        assert options.temperature == 0.5
        assert options.max_tokens == 100
        assert options.reasoning_effort == "low"

    def test_local_fields_keep_their_defaults(self) -> None:
        options = OpenAIResponsesOptions(temperature=0.5, max_tokens=100)

        assert options.reasoning_effort is None
        assert options.reasoning_summary is None
        assert options.service_tier is None

    def test_every_base_field_is_accepted(self) -> None:
        for name in StreamOptions.model_fields:
            assert name in OpenAIResponsesOptions.model_fields, name


def _import_every_cortex_ai_module() -> None:
    """Import all of `cortex.ai.*` so the guard below sees every class.

    Walked off the filesystem rather than with `pkgutil`: `cortex` and its
    subpackages are namespace packages, which `pkgutil.walk_packages` does not
    descend into — it silently yields nothing and the guard passes vacuously.
    """
    packages_root = Path(__file__).resolve().parents[4]
    for cortex_dir in sorted(packages_root.glob("*/*/src/cortex")):
        src_root = cortex_dir.parent
        for py_file in sorted(cortex_dir.rglob("*.py")):
            parts = list(py_file.relative_to(src_root).parts)
            if parts[-1] == "__init__.py":
                parts = parts[:-1]
            else:
                parts[-1] = parts[-1].removesuffix(".py")
            module_name = ".".join(parts)
            if not module_name.startswith("cortex.ai."):
                continue
            importlib.import_module(module_name)


def test_no_cortex_ai_class_is_both_a_dataclass_and_a_base_model() -> None:
    """`@dataclass` on a `BaseModel` subclass silently drops inherited fields.

    Not specific to the two options classes: any such pairing builds an
    `__init__` that ignores the base's fields, and the failure only shows up at
    the first call site that passes one.
    """
    _import_every_cortex_ai_module()

    offenders = sorted(
        f"{obj.__module__}.{obj.__qualname__}"
        for module_name, module in list(sys.modules.items())
        if module_name.startswith("cortex.ai")
        for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and dataclasses.is_dataclass(obj)
    )

    assert offenders == []


@pytest.mark.parametrize("options_class", [OpenAICompletionsOptions, OpenAIResponsesOptions])
def test_options_classes_are_pydantic_not_dataclasses(
    options_class: type[BaseModel],
) -> None:
    assert issubclass(options_class, StreamOptions)
    assert not dataclasses.is_dataclass(options_class)
