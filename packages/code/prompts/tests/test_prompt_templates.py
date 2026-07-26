# pyright: reportMissingParameterType=false, reportUnknownParameterType=false
"""Tests for prompt template argument parsing and substitution.

Tests verify:
- Argument parsing with quotes and special characters
- Placeholder substitution ($1, $2, $@, $ARGUMENTS)
- No recursive substitution of patterns in argument values
- Edge cases and integration between parsing and substitution
"""

from __future__ import annotations

import pytest
from cortex.code.prompts.prompt_templates import (
    LoadPromptTemplatesOptions,
    load_prompt_templates,
    parse_command_args,
    substitute_args,
    try_expand_prompt_template,
)
from cortex.code.prompts.types import PromptTemplate

# ============================================================================
# substituteArgs
# ============================================================================


class TestSubstituteArgs:
    def test_replace_arguments_with_all_args_joined(self):
        assert substitute_args("Test: $ARGUMENTS", ["a", "b", "c"]) == "Test: a b c"

    def test_replace_at_with_all_args_joined(self):
        assert substitute_args("Test: $@", ["a", "b", "c"]) == "Test: a b c"

    def test_replace_at_and_arguments_identically(self):
        args = ["foo", "bar", "baz"]
        assert substitute_args("Test: $@", args) == substitute_args("Test: $ARGUMENTS", args)

    def test_no_recursive_substitution_in_argument_values(self):
        assert substitute_args("$ARGUMENTS", ["$1", "$ARGUMENTS"]) == "$1 $ARGUMENTS"
        assert substitute_args("$@", ["$100", "$1"]) == "$100 $1"
        assert substitute_args("$ARGUMENTS", ["$100", "$1"]) == "$100 $1"

    def test_mixed_numbered_and_arguments(self):
        assert substitute_args("$1: $ARGUMENTS", ["prefix", "a", "b"]) == "prefix: prefix a b"

    def test_mixed_numbered_and_at(self):
        assert substitute_args("$1: $@", ["prefix", "a", "b"]) == "prefix: prefix a b"

    def test_empty_arguments_with_arguments(self):
        assert substitute_args("Test: $ARGUMENTS", []) == "Test: "

    def test_empty_arguments_with_at(self):
        assert substitute_args("Test: $@", []) == "Test: "

    def test_empty_arguments_with_numbered(self):
        assert substitute_args("Test: $1", []) == "Test: "

    def test_multiple_occurrences_of_arguments(self):
        assert substitute_args("$ARGUMENTS and $ARGUMENTS", ["a", "b"]) == "a b and a b"

    def test_multiple_occurrences_of_at(self):
        assert substitute_args("$@ and $@", ["a", "b"]) == "a b and a b"

    def test_mixed_occurrences_of_at_and_arguments(self):
        assert substitute_args("$@ and $ARGUMENTS", ["a", "b"]) == "a b and a b"

    def test_special_characters_in_arguments(self):
        assert (
            substitute_args("$1 $2: $ARGUMENTS", ["arg100", "@user"])
            == "arg100 @user: arg100 @user"
        )

    def test_out_of_range_numbered_placeholders(self):
        assert substitute_args("$1 $2 $3 $4 $5", ["a", "b"]) == "a b   "

    def test_unicode_characters(self):
        assert substitute_args("$ARGUMENTS", ["日本語", "🎉", "café"]) == "日本語 🎉 café"

    def test_preserve_newlines_and_tabs(self):
        assert substitute_args("$1 $2", ["line1\nline2", "tab\tthere"]) == "line1\nline2 tab\tthere"

    def test_consecutive_dollar_patterns(self):
        assert substitute_args("$1$2", ["a", "b"]) == "ab"

    def test_quoted_arguments_with_spaces(self):
        assert substitute_args("$ARGUMENTS", ["first arg", "second arg"]) == "first arg second arg"

    def test_single_argument_with_arguments(self):
        assert substitute_args("Test: $ARGUMENTS", ["only"]) == "Test: only"

    def test_single_argument_with_at(self):
        assert substitute_args("Test: $@", ["only"]) == "Test: only"

    def test_zero_index(self):
        assert substitute_args("$0", ["a", "b"]) == ""

    def test_decimal_number_in_pattern(self):
        assert substitute_args("$1.5", ["a"]) == "a.5"

    def test_arguments_as_part_of_word(self):
        assert substitute_args("pre$ARGUMENTS", ["a", "b"]) == "prea b"

    def test_at_as_part_of_word(self):
        assert substitute_args("pre$@", ["a", "b"]) == "prea b"

    def test_empty_arguments_in_middle(self):
        assert substitute_args("$ARGUMENTS", ["a", "", "c"]) == "a  c"

    def test_trailing_and_leading_spaces(self):
        assert (
            substitute_args("$ARGUMENTS", ["  leading  ", "trailing  "]) == "  leading   trailing  "
        )

    def test_argument_containing_pattern_partially(self):
        assert (
            substitute_args("Prefix $ARGUMENTS suffix", ["ARGUMENTS"]) == "Prefix ARGUMENTS suffix"
        )

    def test_non_matching_patterns(self):
        assert substitute_args("$A $$ $ $ARGS", ["a"]) == "$A $$ $ $ARGS"

    def test_case_variations(self):
        assert (
            substitute_args("$arguments $Arguments $ARGUMENTS", ["a", "b"])
            == "$arguments $Arguments a b"
        )

    def test_both_syntaxes_same_result(self):
        args = ["x", "y", "z"]
        result1 = substitute_args("$@ and $ARGUMENTS", args)
        result2 = substitute_args("$ARGUMENTS and $@", args)
        assert result1 == result2
        assert result1 == "x y z and x y z"

    def test_very_long_argument_lists(self):
        args = [f"arg{i}" for i in range(100)]
        assert substitute_args("$ARGUMENTS", args) == " ".join(args)

    def test_numbered_placeholders_single_digit(self):
        assert substitute_args("$1 $2 $3", ["a", "b", "c"]) == "a b c"

    def test_numbered_placeholders_multiple_digits(self):
        args = [f"val{i}" for i in range(15)]
        assert substitute_args("$10 $12 $15", args) == "val9 val11 val14"

    def test_escaped_dollar_signs(self):
        assert substitute_args("Price: \\$100", []) == "Price: \\"

    def test_mixed_numbered_and_wildcard(self):
        assert substitute_args("$1: $@ ($ARGUMENTS)", ["first", "second", "third"]) == (
            "first: first second third (first second third)"
        )

    def test_no_placeholders(self):
        assert substitute_args("Just plain text", ["a", "b"]) == "Just plain text"

    def test_only_placeholders(self):
        assert substitute_args("$1 $2 $@", ["a", "b", "c"]) == "a b a b c"


# ============================================================================
# substituteArgs - Array Slicing (Bash-Style)
# ============================================================================


class TestSubstituteArgsSlicing:
    def test_slice_from_index(self):
        assert substitute_args("${@:2}", ["a", "b", "c", "d"]) == "b c d"
        assert substitute_args("${@:1}", ["a", "b", "c"]) == "a b c"
        assert substitute_args("${@:3}", ["a", "b", "c", "d"]) == "c d"

    def test_slice_with_length(self):
        assert substitute_args("${@:2:2}", ["a", "b", "c", "d"]) == "b c"
        assert substitute_args("${@:1:1}", ["a", "b", "c"]) == "a"
        assert substitute_args("${@:3:1}", ["a", "b", "c", "d"]) == "c"
        assert substitute_args("${@:2:3}", ["a", "b", "c", "d", "e"]) == "b c d"

    def test_out_of_range_slices(self):
        assert substitute_args("${@:99}", ["a", "b"]) == ""
        assert substitute_args("${@:5}", ["a", "b"]) == ""
        assert substitute_args("${@:10:5}", ["a", "b"]) == ""

    def test_zero_length_slices(self):
        assert substitute_args("${@:2:0}", ["a", "b", "c"]) == ""
        assert substitute_args("${@:1:0}", ["a", "b", "c"]) == ""

    def test_length_exceeding_array(self):
        assert substitute_args("${@:2:99}", ["a", "b", "c"]) == "b c"
        assert substitute_args("${@:1:10}", ["a", "b"]) == "a b"

    def test_process_slice_before_simple_at(self):
        assert substitute_args("${@:2} vs $@", ["a", "b", "c"]) == "b c vs a b c"
        assert (
            substitute_args("First: ${@:1:1}, All: $@", ["x", "y", "z"]) == "First: x, All: x y z"
        )

    def test_no_recursive_substitution_in_slice_args(self):
        assert substitute_args("${@:1}", ["${@:2}", "test"]) == "${@:2} test"
        assert substitute_args("${@:2}", ["a", "${@:3}", "c"]) == "${@:3} c"

    def test_mixed_with_positional_args(self):
        assert substitute_args("$1: ${@:2}", ["cmd", "arg1", "arg2"]) == "cmd: arg1 arg2"
        assert substitute_args("$1 $2 ${@:3}", ["a", "b", "c", "d"]) == "a b c d"

    def test_slice_zero_as_all_args(self):
        assert substitute_args("${@:0}", ["a", "b", "c"]) == "a b c"

    def test_empty_args_array(self):
        assert substitute_args("${@:2}", []) == ""
        assert substitute_args("${@:1}", []) == ""

    def test_single_arg_array(self):
        assert substitute_args("${@:1}", ["only"]) == "only"
        assert substitute_args("${@:2}", ["only"]) == ""

    def test_slice_in_middle_of_text(self):
        assert substitute_args("Process ${@:2} with $1", ["tool", "file1", "file2"]) == (
            "Process file1 file2 with tool"
        )

    def test_multiple_slices_in_one_template(self):
        assert substitute_args("${@:1:1} and ${@:2}", ["a", "b", "c"]) == "a and b c"
        assert substitute_args("${@:1:2} vs ${@:3:2}", ["a", "b", "c", "d", "e"]) == "a b vs c d"

    def test_quoted_arguments_in_slices(self):
        assert (
            substitute_args("${@:2}", ["cmd", "first arg", "second arg"]) == "first arg second arg"
        )

    def test_special_characters_in_sliced_args(self):
        assert substitute_args("${@:2}", ["cmd", "$100", "@user", "#tag"]) == "$100 @user #tag"

    def test_unicode_in_sliced_args(self):
        assert substitute_args("${@:1}", ["日本語", "🎉", "café"]) == "日本語 🎉 café"

    def test_combine_positional_slice_and_wildcard(self):
        template = "Run $1 on ${@:2:2}, then process $@"
        args = ["eslint", "file1.ts", "file2.ts", "file3.ts"]
        assert substitute_args(template, args) == (
            "Run eslint on file1.ts file2.ts, then process eslint file1.ts file2.ts file3.ts"
        )

    def test_slice_with_no_spacing(self):
        assert substitute_args("prefix${@:2}suffix", ["a", "b", "c"]) == "prefixb csuffix"

    def test_large_slice_lengths(self):
        args = [f"arg{i + 1}" for i in range(10)]
        assert substitute_args("${@:5:100}", args) == "arg5 arg6 arg7 arg8 arg9 arg10"


# ============================================================================
# parseCommandArgs
# ============================================================================


class TestParseCommandArgs:
    def test_simple_space_separated(self):
        assert parse_command_args("a b c") == ["a", "b", "c"]

    def test_quoted_arguments_with_spaces(self):
        assert parse_command_args('"first arg" second') == ["first arg", "second"]

    def test_single_quoted_arguments(self):
        assert parse_command_args("'first arg' second") == ["first arg", "second"]

    def test_mixed_quote_styles(self):
        assert parse_command_args('"double" \'single\' "double again"') == [
            "double",
            "single",
            "double again",
        ]

    def test_empty_string(self):
        assert parse_command_args("") == []

    def test_extra_spaces(self):
        assert parse_command_args("a  b   c") == ["a", "b", "c"]

    def test_tabs_as_separators(self):
        assert parse_command_args("a\tb\tc") == ["a", "b", "c"]

    def test_special_characters(self):
        assert parse_command_args("$100 @user #tag") == ["$100", "@user", "#tag"]

    def test_unicode_characters(self):
        assert parse_command_args("日本語 🎉 café") == ["日本語", "🎉", "café"]

    def test_newlines_in_arguments(self):
        assert parse_command_args('"line1\nline2" second') == ["line1\nline2", "second"]

    def test_escaped_quotes_inside_quoted_strings(self):
        # Note: This implementation doesn't handle escaped quotes - backslash is literal
        assert parse_command_args('"quoted \\"text\\""') == ["quoted \\text\\"]

    def test_trailing_spaces(self):
        assert parse_command_args("a b c   ") == ["a", "b", "c"]

    def test_leading_spaces(self):
        assert parse_command_args("   a b c") == ["a", "b", "c"]


# ============================================================================
# Integration
# ============================================================================


class TestParseAndSubstituteIntegration:
    def test_parse_and_substitute_together(self):
        input_str = 'Button "onClick handler" "disabled support"'
        args = parse_command_args(input_str)
        template = "Create component $1 with features: $ARGUMENTS"
        result = substitute_args(template, args)
        assert (
            result
            == "Create component Button with features: Button onClick handler disabled support"
        )

    def test_example_from_readme(self):
        input_str = 'Button "onClick handler" "disabled support"'
        args = parse_command_args(input_str)
        template = "Create a React component named $1 with features: $ARGUMENTS"
        result = substitute_args(template, args)
        assert result == (
            "Create a React component named Button with features: "
            "Button onClick handler disabled support"
        )

    def test_same_result_with_at_and_arguments(self):
        args = parse_command_args("feature1 feature2 feature3")
        template1 = "Implement: $@"
        template2 = "Implement: $ARGUMENTS"
        assert substitute_args(template1, args) == substitute_args(template2, args)


# ============================================================================
# loadPromptTemplates - argument-hint frontmatter
# ============================================================================


class TestLoadPromptTemplatesArgumentHint:
    def test_parse_required_argument_hint(self, tmp_path):
        pr_file = tmp_path / "pr.md"
        pr_file.write_text(
            "---\n"
            "description: Review PRs from URLs with structured issue and code analysis\n"
            'argument-hint: "<PR-URL>"\n'
            "---\n"
            "You are given one or more GitHub PR URLs: $@"
        )

        templates = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=str(tmp_path),
                prompt_paths=[str(tmp_path)],
                include_defaults=False,
            )
        )

        pr = next((t for t in templates if t.name == "pr"), None)
        assert pr is not None
        assert pr.argument_hint == "<PR-URL>"
        assert pr.description == "Review PRs from URLs with structured issue and code analysis"

    def test_parse_optional_argument_hint(self, tmp_path):
        wr_file = tmp_path / "wr.md"
        wr_file.write_text(
            "---\n"
            "description: Finish the current task end-to-end with changelog, commit, and push\n"
            'argument-hint: "[instructions]"\n'
            "---\n"
            "Wrap it. Additional instructions: $ARGUMENTS"
        )

        templates = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=str(tmp_path),
                prompt_paths=[str(tmp_path)],
                include_defaults=False,
            )
        )

        wr = next((t for t in templates if t.name == "wr"), None)
        assert wr is not None
        assert wr.argument_hint == "[instructions]"
        assert (
            wr.description == "Finish the current task end-to-end with changelog, commit, and push"
        )

    def test_leave_argument_hint_undefined_when_not_specified(self, tmp_path):
        cl_file = tmp_path / "cl.md"
        cl_file.write_text(
            "---\n"
            "description: Audit changelog entries before release\n"
            "---\n"
            "Audit changelog entries for all commits since the last release."
        )

        templates = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=str(tmp_path),
                prompt_paths=[str(tmp_path)],
                include_defaults=False,
            )
        )

        cl = next((t for t in templates if t.name == "cl"), None)
        assert cl is not None
        assert cl.argument_hint is None

    def test_ignore_empty_argument_hint(self, tmp_path):
        empty_hint_file = tmp_path / "empty-hint.md"
        empty_hint_file.write_text(
            '---\ndescription: A command with empty hint\nargument-hint: ""\n---\nDo something'
        )

        templates = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=str(tmp_path),
                prompt_paths=[str(tmp_path)],
                include_defaults=False,
            )
        )

        tmpl = next((t for t in templates if t.name == "empty-hint"), None)
        assert tmpl is not None
        assert tmpl.argument_hint is None

    def test_preserve_argument_hint_with_special_characters(self, tmp_path):
        is_file = tmp_path / "is.md"
        is_file.write_text(
            "---\n"
            "description: Analyze GitHub issues (bugs or feature requests)\n"
            'argument-hint: "<issue>"\n'
            "---\n"
            "Analyze GitHub issue(s): $ARGUMENTS"
        )

        templates = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=str(tmp_path),
                prompt_paths=[str(tmp_path)],
                include_defaults=False,
            )
        )

        is_tmpl = next((t for t in templates if t.name == "is"), None)
        assert is_tmpl is not None
        assert is_tmpl.argument_hint == "<issue>"


# ============================================================================
# loadPromptTemplates - type frontmatter
# ============================================================================


class TestLoadPromptTemplatesType:
    def test_default_type_to_user(self, tmp_path):
        default_file = tmp_path / "default.md"
        default_file.write_text("---\ndescription: Default type\n---\nContent")

        templates = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=str(tmp_path),
                prompt_paths=[str(tmp_path)],
                include_defaults=False,
            )
        )

        tmpl = next((t for t in templates if t.name == "default"), None)
        assert tmpl is not None
        assert tmpl.type == "user"

    def test_parse_system_type(self, tmp_path):
        system_file = tmp_path / "system.md"
        system_file.write_text(
            "---\ndescription: System type\ntype: system\n---\nThink step by step"
        )

        templates = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=str(tmp_path),
                prompt_paths=[str(tmp_path)],
                include_defaults=False,
            )
        )

        tmpl = next((t for t in templates if t.name == "system"), None)
        assert tmpl is not None
        assert tmpl.type == "system"

    def test_parse_context_type(self, tmp_path):
        context_file = tmp_path / "context.md"
        context_file.write_text("---\ndescription: Context type\ntype: context\n---\nContext info")

        templates = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=str(tmp_path),
                prompt_paths=[str(tmp_path)],
                include_defaults=False,
            )
        )

        tmpl = next((t for t in templates if t.name == "context"), None)
        assert tmpl is not None
        assert tmpl.type == "context"

    def test_ignore_invalid_type(self, tmp_path):
        invalid_file = tmp_path / "invalid.md"
        invalid_file.write_text("---\ndescription: Invalid type\ntype: banana\n---\nContent")

        templates = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=str(tmp_path),
                prompt_paths=[str(tmp_path)],
                include_defaults=False,
            )
        )

        tmpl = next((t for t in templates if t.name == "invalid"), None)
        assert tmpl is not None
        assert tmpl.type == "user"


# ============================================================================
# loadPromptTemplates - slash-command paths
# ============================================================================


class TestLoadPromptTemplatesSlashCommandPaths:
    def test_load_from_explicit_slash_command_paths(self, tmp_path):
        slash_dir = tmp_path / "commands"
        slash_dir.mkdir()
        deploy_file = slash_dir / "deploy.md"
        deploy_file.write_text("---\ndescription: Deploy the app\n---\nRun deploy script")

        templates = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=str(tmp_path),
                slash_command_paths=[str(slash_dir)],
                include_defaults=False,
            )
        )

        tmpl = next((t for t in templates if t.name == "deploy"), None)
        assert tmpl is not None
        assert tmpl.description == "Deploy the app"

    def test_merge_prompt_paths_and_slash_command_paths(self, tmp_path):
        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        slash_dir = tmp_path / "commands"
        slash_dir.mkdir()

        (prompt_dir / "review.md").write_text("Review code")
        (slash_dir / "deploy.md").write_text("Deploy app")

        templates = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=str(tmp_path),
                prompt_paths=[str(prompt_dir)],
                slash_command_paths=[str(slash_dir)],
                include_defaults=False,
            )
        )

        assert any(t.name == "review" for t in templates)
        assert any(t.name == "deploy" for t in templates)

    def test_strip_copilot_prompt_md_suffix(self, tmp_path):
        slash_dir = tmp_path / "commands"
        slash_dir.mkdir()
        greet_file = slash_dir / "greet.prompt.md"
        greet_file.write_text("---\ndescription: Say hi\n---\nSay hello")

        templates = load_prompt_templates(
            LoadPromptTemplatesOptions(
                cwd=str(tmp_path),
                slash_command_paths=[str(slash_dir)],
                include_defaults=False,
            )
        )

        assert any(t.name == "greet" for t in templates)
        assert not any(t.name == "greet.prompt" for t in templates)


# ============================================================================
# tryExpandPromptTemplate
# ============================================================================


class TestTryExpandPromptTemplate:
    @pytest.fixture
    def templates(self):
        return [
            PromptTemplate(
                name="review",
                description="Review",
                type="user",
                content="Review: $ARGUMENTS",
                file_path="/virtual/review.md",
            ),
            PromptTemplate(
                name="system",
                description="System",
                type="system",
                content="Think step by step: $ARGUMENTS",
                file_path="/virtual/system.md",
            ),
        ]

    def test_return_template_metadata_when_matched(self, templates):
        expansion = try_expand_prompt_template("/review some code", templates)
        assert expansion.text == "Review: some code"
        assert expansion.template is not None
        assert expansion.template.name == "review"
        assert expansion.template.type == "user"
        assert expansion.args == ["some", "code"]
        assert expansion.args_string == "some code"

    def test_return_system_template_type(self, templates):
        expansion = try_expand_prompt_template("/system hello", templates)
        assert expansion.text == "Think step by step: hello"
        assert expansion.template is not None
        assert expansion.template.type == "system"

    def test_return_empty_template_when_not_matched(self, templates):
        expansion = try_expand_prompt_template("/unknown", templates)
        assert expansion.text == "/unknown"
        assert expansion.template is None
        assert expansion.args == []

    def test_return_empty_args_for_non_slash_input(self, templates):
        expansion = try_expand_prompt_template("hello world", templates)
        assert expansion.text == "hello world"
        assert expansion.template is None
        assert expansion.args_string == ""
