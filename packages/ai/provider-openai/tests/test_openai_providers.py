"""Tests for OpenAI providers."""

from __future__ import annotations


def test_import_openai_responses():
    """Test that openai_responses module can be imported."""
    from cortex.ai.providers.openai.openai_responses import (
        stream_openai_responses,
        stream_simple_openai_responses,
    )

    assert callable(stream_openai_responses)
    assert callable(stream_simple_openai_responses)


def test_import_openai_completions():
    """Test that openai_completions module can be imported."""
    from cortex.ai.providers.openai.openai_completions import (
        stream_openai_completions,
        stream_simple_openai_completions,
    )

    assert callable(stream_openai_completions)
    assert callable(stream_simple_openai_completions)


def test_import_azure_openai_responses():
    """Test that azure_openai_responses module can be imported."""
    from cortex.ai.providers.openai.azure_openai_responses import (
        stream_azure_openai_responses,
    )

    assert callable(stream_azure_openai_responses)


def test_import_openai_codex_responses():
    """Test that openai_codex_responses module can be imported."""
    from cortex.ai.providers.openai.openai_codex_responses import (
        stream_openai_codex_responses,
    )

    assert callable(stream_openai_codex_responses)


def test_import_openai_responses_shared():
    """Test that openai_responses_shared module can be imported."""
    from cortex.ai.providers.openai.openai_responses_shared import (
        convert_responses_messages,
        convert_responses_tools,
        process_responses_stream,
    )

    assert callable(convert_responses_messages)
    assert callable(convert_responses_tools)
    assert callable(process_responses_stream)
