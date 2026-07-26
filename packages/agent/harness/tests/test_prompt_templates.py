"""Tests for prompt template loading and formatting.

Mechanical port of hoocode's ``packages/agent/test/harness/prompt-templates.test.ts``.
"""

from __future__ import annotations

from cortex.agent.harness.prompt_templates import (
    format_prompt_template_invocation,
    parse_command_args,
    substitute_args,
)
from cortex.agent.harness.types import PromptTemplate


class TestParseCommandArgs:
    """Tests for parse_command_args."""

    def test_simple_args(self) -> None:
        """Simple arguments are parsed."""
        result = parse_command_args("arg1 arg2 arg3")
        assert result == ["arg1", "arg2", "arg3"]

    def test_quoted_args(self) -> None:
        """Quoted arguments are parsed."""
        result = parse_command_args('"arg1" "arg2"')
        assert result == ["arg1", "arg2"]

    def test_single_quoted_args(self) -> None:
        """Single-quoted arguments are parsed."""
        result = parse_command_args("'arg1' 'arg2'")
        assert result == ["arg1", "arg2"]

    def test_mixed_quotes(self) -> None:
        """Mixed quote styles are parsed."""
        result = parse_command_args("\"arg1\" 'arg2' arg3")
        assert result == ["arg1", "arg2", "arg3"]

    def test_empty_string(self) -> None:
        """Empty string returns empty list."""
        result = parse_command_args("")
        assert result == []

    def test_whitespace_only(self) -> None:
        """Whitespace-only string returns empty list."""
        result = parse_command_args("   ")
        assert result == []

    def test_tabs(self) -> None:
        """Tabs are treated as separators."""
        result = parse_command_args("arg1\targ2")
        assert result == ["arg1", "arg2"]


class TestSubstituteArgs:
    """Tests for substitute_args."""

    def test_positional_args(self) -> None:
        """Positional args are substituted."""
        result = substitute_args("Hello $1, you are $2", ["World", "great"])
        assert result == "Hello World, you are great"

    def test_all_args(self) -> None:
        """$@ substitutes all args."""
        result = substitute_args("Args: $@", ["a", "b", "c"])
        assert result == "Args: a b c"

    def test_arguments_placeholder(self) -> None:
        """$ARGUMENTS substitutes all args."""
        result = substitute_args("Args: $ARGUMENTS", ["x", "y"])
        assert result == "Args: x y"

    def test_slice_args(self) -> None:
        """${@:N} substitutes args from index N."""
        result = substitute_args("Args: ${@:2}", ["a", "b", "c"])
        assert result == "Args: b c"

    def test_slice_with_length(self) -> None:
        """${@:N:L} substitutes L args from index N."""
        result = substitute_args("Args: ${@:2:1}", ["a", "b", "c"])
        assert result == "Args: b"

    def test_missing_arg(self) -> None:
        """Missing args are replaced with empty string."""
        result = substitute_args("Hello $1 $2", ["World"])
        assert result == "Hello World "

    def test_no_args(self) -> None:
        """No args provided."""
        result = substitute_args("Hello $1", [])
        assert result == "Hello "


class TestFormatPromptTemplateInvocation:
    """Tests for format_prompt_template_invocation."""

    def test_basic_invocation(self) -> None:
        """Basic template invocation."""
        template = PromptTemplate(
            name="test",
            content="Hello $1!",
        )
        result = format_prompt_template_invocation(template, ["World"])
        assert result == "Hello World!"

    def test_no_args(self) -> None:
        """Template invocation without args."""
        template = PromptTemplate(
            name="test",
            content="Hello World!",
        )
        result = format_prompt_template_invocation(template)
        assert result == "Hello World!"

    def test_empty_template(self) -> None:
        """Empty template."""
        template = PromptTemplate(
            name="test",
            content="",
        )
        result = format_prompt_template_invocation(template)
        assert result == ""
