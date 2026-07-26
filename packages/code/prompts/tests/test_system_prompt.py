"""Tests for system prompt construction.

Tests verify:
- Empty tools handling
- Output constraints
- Default tools inclusion
- Custom tool snippets
- Prompt guidelines
- Search/grep routing
"""

from __future__ import annotations

from cortex.code.prompts.system_prompt import BuildSystemPromptOptions, build_system_prompt


class TestBuildSystemPrompt:
    class TestEmptyTools:
        def test_shows_none_for_empty_tools_list(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    selected_tools=[],
                    context_files=[],
                    skills=[],
                    cwd="/test",
                )
            )
            assert "Available tools:\n(none)" in prompt

        def test_shows_file_paths_guideline_even_with_no_tools(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    selected_tools=[],
                    context_files=[],
                    skills=[],
                    cwd="/test",
                )
            )
            assert "Show file paths clearly" in prompt

    class TestOutputConstraints:
        def test_includes_default_output_constraint_guidelines(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    selected_tools=[],
                    context_files=[],
                    skills=[],
                    cwd="/test",
                )
            )
            assert "do not restate the task" in prompt
            assert '"Let me know"' in prompt
            assert "Do not narrate routine tool calls or results" in prompt
            assert "Match the surrounding code's conventions" in prompt

    class TestDefaultTools:
        def test_includes_all_default_tools_when_snippets_provided(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    tool_snippets={
                        "read": "Read file contents",
                        "bash": "Execute bash commands",
                        "edit": "Make surgical edits",
                        "write": "Create or overwrite files",
                    },
                    context_files=[],
                    skills=[],
                    cwd="/test",
                )
            )
            assert "- read:" in prompt
            assert "- bash:" in prompt
            assert "- edit:" in prompt
            assert "- write:" in prompt

    class TestCustomToolSnippets:
        def test_includes_custom_tools_when_snippet_provided(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    selected_tools=["read", "dynamic_tool"],
                    tool_snippets={
                        "dynamic_tool": "Run dynamic test behavior",
                    },
                    context_files=[],
                    skills=[],
                    cwd="/test",
                )
            )
            assert "- dynamic_tool: Run dynamic test behavior" in prompt

        def test_omits_custom_tools_when_snippet_not_provided(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    selected_tools=["read", "dynamic_tool"],
                    context_files=[],
                    skills=[],
                    cwd="/test",
                )
            )
            assert "dynamic_tool" not in prompt

    class TestPromptGuidelines:
        def test_appends_prompt_guidelines_to_default(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    selected_tools=["read", "dynamic_tool"],
                    prompt_guidelines=["Use dynamic_tool for project summaries."],
                    context_files=[],
                    skills=[],
                    cwd="/test",
                )
            )
            assert "- Use dynamic_tool for project summaries." in prompt

        def test_deduplicates_and_trims_prompt_guidelines(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    selected_tools=["read", "dynamic_tool"],
                    prompt_guidelines=[
                        "Use dynamic_tool for summaries.",
                        "  Use dynamic_tool for summaries.  ",
                        "   ",
                    ],
                    context_files=[],
                    skills=[],
                    cwd="/test",
                )
            )
            assert prompt.count("- Use dynamic_tool for summaries.") == 1

    class TestSearchGrepRouting:
        def test_emits_routing_guideline_when_both_active(self):
            both = build_system_prompt(
                BuildSystemPromptOptions(
                    selected_tools=["search", "grep"],
                    context_files=[],
                    skills=[],
                    cwd="/test",
                )
            )
            assert "Between search and grep:" in both

        def test_does_not_emit_routing_guideline_when_only_grep(self):
            grep_only = build_system_prompt(
                BuildSystemPromptOptions(
                    selected_tools=["grep"],
                    context_files=[],
                    skills=[],
                    cwd="/test",
                )
            )
            assert "Between search and grep:" not in grep_only

    class TestCustomPrompt:
        def test_uses_custom_prompt_when_provided(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    custom_prompt="Custom system prompt",
                    cwd="/test",
                )
            )
            # Custom prompt still includes date and working directory
            assert prompt.startswith("Custom system prompt")
            assert "Current date:" in prompt
            assert "Current working directory: /test" in prompt

        def test_appends_append_system_prompt_to_custom(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    custom_prompt="Custom system prompt",
                    append_system_prompt="Additional content",
                    cwd="/test",
                )
            )
            assert "Custom system prompt\n\nAdditional content" in prompt

    class TestContextFiles:
        def test_appends_context_files_to_prompt(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    context_files=[
                        {"path": "AGENTS.md", "content": "Agent instructions"},
                        {"path": "README.md", "content": "Readme content"},
                    ],
                    cwd="/test",
                )
            )
            assert "# Project Context" in prompt
            assert "## AGENTS.md" in prompt
            assert "Agent instructions" in prompt
            assert "## README.md" in prompt
            assert "Readme content" in prompt

    class TestDateAndCwd:
        def test_includes_current_date(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    cwd="/test",
                )
            )
            assert "Current date:" in prompt

        def test_includes_working_directory(self):
            prompt = build_system_prompt(
                BuildSystemPromptOptions(
                    cwd="/test/path",
                )
            )
            assert "Current working directory: /test/path" in prompt
