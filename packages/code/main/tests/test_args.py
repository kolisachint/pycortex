# pyright: reportMissingParameterType=false, reportUnknownParameterType=false
"""Tests for CLI argument parsing.

Tests verify:
- Basic argument parsing
- Flag options
- String options with values
- List options
- Error handling
"""

from __future__ import annotations

from cortex.code.main.args import is_valid_thinking_level, parse_args


class TestIsValidThinkingLevel:
    def test_valid_levels(self):
        assert is_valid_thinking_level("off") is True
        assert is_valid_thinking_level("minimal") is True
        assert is_valid_thinking_level("low") is True
        assert is_valid_thinking_level("medium") is True
        assert is_valid_thinking_level("high") is True
        assert is_valid_thinking_level("xhigh") is True

    def test_invalid_levels(self):
        assert is_valid_thinking_level("invalid") is False
        assert is_valid_thinking_level("") is False
        assert is_valid_thinking_level("HIGH") is False


class TestParseArgs:
    def test_empty_args(self):
        args = parse_args([])
        assert args.help is False
        assert args.version is False
        assert args.messages == []
        assert args.file_args == []

    def test_help_flag(self):
        args = parse_args(["--help"])
        assert args.help is True

    def test_short_help_flag(self):
        args = parse_args(["-h"])
        assert args.help is True

    def test_version_flag(self):
        args = parse_args(["--version"])
        assert args.version is True

    def test_short_version_flag(self):
        args = parse_args(["-v"])
        assert args.version is True

    def test_mode_option(self):
        args = parse_args(["--mode", "json"])
        assert args.mode == "json"

    def test_mode_text(self):
        args = parse_args(["--mode", "text"])
        assert args.mode == "text"

    def test_mode_rpc(self):
        args = parse_args(["--mode", "rpc"])
        assert args.mode == "rpc"

    def test_mode_invalid(self):
        args = parse_args(["--mode", "invalid"])
        assert args.mode == "text"  # default

    def test_provider_option(self):
        args = parse_args(["--provider", "anthropic"])
        assert args.provider == "anthropic"

    def test_model_option(self):
        args = parse_args(["--model", "claude-sonnet-4-5"])
        assert args.model == "claude-sonnet-4-5"

    def test_api_key_option(self):
        args = parse_args(["--api-key", "test-key"])
        assert args.api_key == "test-key"

    def test_system_prompt_option(self):
        args = parse_args(["--system-prompt", "You are helpful"])
        assert args.system_prompt == "You are helpful"

    def test_continue_flag(self):
        args = parse_args(["--continue"])
        assert args.continue_session is True

    def test_short_continue_flag(self):
        args = parse_args(["-c"])
        assert args.continue_session is True

    def test_resume_flag(self):
        args = parse_args(["--resume"])
        assert args.resume is True

    def test_short_resume_flag(self):
        args = parse_args(["-r"])
        assert args.resume is True

    def test_session_option(self):
        args = parse_args(["--session", "abc123"])
        assert args.session == "abc123"

    def test_no_session_flag(self):
        args = parse_args(["--no-session"])
        assert args.no_session is True

    def test_task_id_option(self):
        args = parse_args(["--task-id", "task-123"])
        assert args.task_id == "task-123"

    def test_max_turns_option(self):
        args = parse_args(["--max-turns", "10"])
        assert args.max_turns == 10

    def test_max_turns_invalid(self):
        args = parse_args(["--max-turns", "abc"])
        assert args.max_turns is None

    def test_max_turns_negative(self):
        args = parse_args(["--max-turns", "-1"])
        assert args.max_turns is None

    def test_no_tools_flag(self):
        args = parse_args(["--no-tools"])
        assert args.no_tools is True

    def test_short_no_tools_flag(self):
        args = parse_args(["-nt"])
        assert args.no_tools is True

    def test_tools_option(self):
        args = parse_args(["--tools", "read,bash,edit"])
        assert args.tools == ["read", "bash", "edit"]

    def test_short_tools_option(self):
        args = parse_args(["-t", "read,bash"])
        assert args.tools == ["read", "bash"]

    def test_disallowed_tools_option(self):
        args = parse_args(["--disallowed-tools", "bash,write"])
        assert args.disallowed_tools == ["bash", "write"]

    def test_enable_subagents_flag(self):
        args = parse_args(["--enable-subagents"])
        assert args.subagent is True

    def test_no_subagents_flag(self):
        args = parse_args(["--no-subagents"])
        assert args.subagent is False

    def test_disable_subagents_flag(self):
        args = parse_args(["--disable-subagents"])
        assert args.subagent is False

    def test_enable_todowrite_flag(self):
        args = parse_args(["--enable-todowrite"])
        assert args.todo_write is True

    def test_enable_webtools_flag(self):
        args = parse_args(["--enable-webtools"])
        assert args.enable_web_tools is True

    def test_enable_browsertools_flag(self):
        args = parse_args(["--enable-browsertools"])
        assert args.enable_browser_tools is True

    def test_enable_filetools_flag(self):
        args = parse_args(["--enable-filetools"])
        assert args.enable_file_tools is True

    def test_enable_plugintools_flag(self):
        args = parse_args(["--enable-plugintools"])
        assert args.enable_plugin_tools is True

    def test_enable_search_tool_flag(self):
        args = parse_args(["--enable-search-tool"])
        assert args.enable_embsearch_tools is True

    def test_light_flag(self):
        args = parse_args(["--light"])
        assert args.light is True

    def test_thinking_option(self):
        args = parse_args(["--thinking", "high"])
        assert args.thinking == "high"

    def test_thinking_invalid(self):
        args = parse_args(["--thinking", "invalid"])
        assert args.thinking is None
        assert len(args.diagnostics) == 1
        assert args.diagnostics[0]["type"] == "warning"

    def test_print_flag(self):
        args = parse_args(["--print"])
        assert args.print is True

    def test_short_print_flag(self):
        args = parse_args(["-p"])
        assert args.print is True

    def test_print_with_message(self):
        args = parse_args(["--print", "Hello world"])
        assert args.print is True
        assert args.messages == ["Hello world"]

    def test_print_no_message(self):
        args = parse_args(["--print", "--model", "test"])
        assert args.print is True
        assert args.messages == []

    def test_extension_option(self):
        args = parse_args(["--extension", "path/to/ext"])
        assert args.extensions == ["path/to/ext"]

    def test_short_extension_option(self):
        args = parse_args(["-e", "path/to/ext"])
        assert args.extensions == ["path/to/ext"]

    def test_no_extensions_flag(self):
        args = parse_args(["--no-extensions"])
        assert args.no_extensions is True

    def test_short_no_extensions_flag(self):
        args = parse_args(["-ne"])
        assert args.no_extensions is True

    def test_skill_option(self):
        args = parse_args(["--skill", "path/to/skill"])
        assert args.skills == ["path/to/skill"]

    def test_no_skills_flag(self):
        args = parse_args(["--no-skills"])
        assert args.no_skills is True

    def test_short_no_skills_flag(self):
        args = parse_args(["-ns"])
        assert args.no_skills is True

    def test_agent_option(self):
        args = parse_args(["--agent", "path/to/agent"])
        assert args.agents == ["path/to/agent"]

    def test_prompt_template_option(self):
        args = parse_args(["--prompt-template", "path/to/template"])
        assert args.prompt_templates == ["path/to/template"]

    def test_no_prompt_templates_flag(self):
        args = parse_args(["--no-prompt-templates"])
        assert args.no_prompt_templates is True

    def test_short_no_prompt_templates_flag(self):
        args = parse_args(["-np"])
        assert args.no_prompt_templates is True

    def test_slash_command_option(self):
        args = parse_args(["--slash-command", "path/to/command"])
        assert args.slash_commands == ["path/to/command"]

    def test_no_slash_commands_flag(self):
        args = parse_args(["--no-slash-commands"])
        assert args.no_slash_commands is True

    def test_short_no_slash_commands_flag(self):
        args = parse_args(["-nsc"])
        assert args.no_slash_commands is True

    def test_theme_option(self):
        args = parse_args(["--theme", "path/to/theme"])
        assert args.themes == ["path/to/theme"]

    def test_no_themes_flag(self):
        args = parse_args(["--no-themes"])
        assert args.no_themes is True

    def test_mode_path_option(self):
        args = parse_args(["--mode-path", "path/to/modes"])
        assert args.mode_paths == ["path/to/modes"]

    def test_no_context_files_flag(self):
        args = parse_args(["--no-context-files"])
        assert args.no_context_files is True

    def test_short_no_context_files_flag(self):
        args = parse_args(["-nc"])
        assert args.no_context_files is True

    def test_list_models_flag(self):
        args = parse_args(["--list-models"])
        assert args.list_models is True

    def test_list_models_with_search(self):
        args = parse_args(["--list-models", "claude"])
        assert args.list_models == "claude"

    def test_verbose_flag(self):
        args = parse_args(["--verbose"])
        assert args.verbose is True

    def test_offline_flag(self):
        args = parse_args(["--offline"])
        assert args.offline is True

    def test_ca_cert_option(self):
        args = parse_args(["--ca-cert", "/path/to/cert.pem"])
        assert args.ca_cert == "/path/to/cert.pem"

    def test_use_system_ca_flag(self):
        args = parse_args(["--use-system-ca"])
        assert args.use_system_ca is True

    def test_export_option(self):
        args = parse_args(["--export", "output.html"])
        assert args.export == "output.html"

    def test_print_token_surface_flag(self):
        args = parse_args(["--print-token-surface"])
        assert args.print_token_surface is True

    def test_file_args(self):
        args = parse_args(["@file1.txt", "@file2.md"])
        assert args.file_args == ["file1.txt", "file2.md"]

    def test_messages(self):
        args = parse_args(["Hello", "world"])
        assert args.messages == ["Hello", "world"]

    def test_mixed_args(self):
        args = parse_args(["--model", "test", "Hello", "@file.txt"])
        assert args.model == "test"
        assert args.messages == ["Hello"]
        assert args.file_args == ["file.txt"]

    def test_unknown_long_flag_with_value(self):
        args = parse_args(["--unknown-flag", "value"])
        assert args.unknown_flags == {"unknown-flag": "value"}

    def test_unknown_long_flag_without_value(self):
        args = parse_args(["--unknown-flag"])
        assert args.unknown_flags == {"unknown-flag": True}

    def test_unknown_long_flag_with_equals(self):
        args = parse_args(["--flag=value"])
        assert args.unknown_flags == {"flag": "value"}

    def test_unknown_short_flag(self):
        args = parse_args(["-x"])
        assert len(args.diagnostics) == 1
        assert args.diagnostics[0]["type"] == "error"
        assert "Unknown option" in args.diagnostics[0]["message"]

    def test_multiple_options(self):
        args = parse_args(
            [
                "--provider",
                "anthropic",
                "--model",
                "claude-sonnet-4-5",
                "--thinking",
                "high",
                "--tools",
                "read,bash",
                "Hello world",
            ]
        )
        assert args.provider == "anthropic"
        assert args.model == "claude-sonnet-4-5"
        assert args.thinking == "high"
        assert args.tools == ["read", "bash"]
        assert args.messages == ["Hello world"]

    def test_max_subagent_depth_option(self):
        args = parse_args(["--max-subagent-depth", "3"])
        assert args.max_subagent_depth == 3

    def test_max_subagent_depth_invalid(self):
        args = parse_args(["--max-subagent-depth", "abc"])
        assert args.max_subagent_depth is None

    def test_max_subagent_depth_zero(self):
        args = parse_args(["--max-subagent-depth", "0"])
        assert args.max_subagent_depth is None

    def test_delegate_allow_option(self):
        args = parse_args(["--delegate-allow", "explore,plan"])
        assert args.delegate_allow == ["explore", "plan"]

    def test_support_platform_option(self):
        args = parse_args(["--support-platform", "copilot"])
        assert args.support_platform == ["copilot"]

    def test_support_platform_multiple(self):
        args = parse_args(
            [
                "--support-platform",
                "copilot",
                "--support-platform",
                "agents",
            ]
        )
        assert args.support_platform == ["copilot", "agents"]

    def test_team_option(self):
        args = parse_args(["--team", "auto"])
        assert args.team == "auto"

    def test_fork_option(self):
        args = parse_args(["--fork", "abc123"])
        assert args.fork == "abc123"

    def test_session_dir_option(self):
        args = parse_args(["--session-dir", "/tmp/sessions"])
        assert args.session_dir == "/tmp/sessions"

    def test_models_option(self):
        args = parse_args(["--models", "claude-sonnet,claude-haiku"])
        assert args.models == ["claude-sonnet", "claude-haiku"]

    def test_warm_subagents_flag(self):
        args = parse_args(["--warm-subagents"])
        assert args.warm_subagents is True

    def test_enable_browser_live_preview_flag(self):
        args = parse_args(["--enable-browser-live-preview"])
        assert args.enable_browser_live_preview is True
