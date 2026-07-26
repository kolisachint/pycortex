"""Tests for mode prompts.

Tests verify:
- Default mode is 'build'
- All four built-in modes have prompts
- Mode prompts contain expected content
"""

from __future__ import annotations

from cortex.code.prompts.mode_prompts import DEFAULT_MODE, DEFAULT_MODE_PROMPTS


class TestModePrompts:
    def test_default_mode_is_build(self):
        assert DEFAULT_MODE == "build"

    def test_all_four_built_in_modes_have_prompts(self):
        assert "ask" in DEFAULT_MODE_PROMPTS
        assert "plan" in DEFAULT_MODE_PROMPTS
        assert "build" in DEFAULT_MODE_PROMPTS
        assert "debug" in DEFAULT_MODE_PROMPTS

    def test_ask_mode_prompt_content(self):
        prompt = DEFAULT_MODE_PROMPTS["ask"]
        assert "ASK mode" in prompt
        assert "read-only Q&A" in prompt
        assert "NEVER write" in prompt

    def test_plan_mode_prompt_content(self):
        prompt = DEFAULT_MODE_PROMPTS["plan"]
        assert "PLAN mode" in prompt
        assert "exploration and planning" in prompt
        assert "PLAN_PATH" in prompt

    def test_build_mode_prompt_content(self):
        prompt = DEFAULT_MODE_PROMPTS["build"]
        assert "BUILD mode" in prompt
        assert "careful implementation" in prompt
        assert "Read files before editing" in prompt

    def test_debug_mode_prompt_content(self):
        prompt = DEFAULT_MODE_PROMPTS["debug"]
        assert "DEBUG mode" in prompt
        assert "root cause analysis" in prompt
        assert "do NOT apply it" in prompt

    def test_mode_prompts_are_strings(self):
        for mode, prompt in DEFAULT_MODE_PROMPTS.items():
            assert isinstance(prompt, str), f"Mode {mode} prompt is not a string"
