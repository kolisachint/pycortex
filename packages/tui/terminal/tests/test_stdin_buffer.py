"""
Tests for StdinBuffer

Based on code from OpenTUI (https://github.com/anomalyco/opentui)
MIT License - Copyright (c) 2025 opentui
"""

import asyncio

import pytest
from cortex.tui.terminal.stdin_buffer import StdinBuffer


class TestStdinBuffer:
    """Tests for StdinBuffer functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.buffer = StdinBuffer(timeout=10)
        self.emitted_sequences: list[str] = []

        # Collect emitted sequences
        self.buffer.on_data(lambda seq: self.emitted_sequences.append(seq))

    def process_input(self, data: str | bytes) -> None:
        """Helper to process data through the buffer."""
        self.buffer.process(data)

    # ------------------------------------------------------------------ #
    # Regular Characters
    # ------------------------------------------------------------------ #

    def test_pass_through_regular_characters(self):
        """Should pass through regular characters immediately."""
        self.process_input("a")
        assert self.emitted_sequences == ["a"]

    def test_pass_through_multiple_regular_characters(self):
        """Should pass through multiple regular characters."""
        self.process_input("abc")
        assert self.emitted_sequences == ["a", "b", "c"]

    def test_handle_unicode_characters(self):
        """Should handle unicode characters."""
        self.process_input("hello 世界")
        assert self.emitted_sequences == ["h", "e", "l", "l", "o", " ", "世", "界"]

    # ------------------------------------------------------------------ #
    # Complete Escape Sequences
    # ------------------------------------------------------------------ #

    def test_pass_through_complete_mouse_sgr_sequences(self):
        """Should pass through complete mouse SGR sequences."""
        mouse_seq = "\x1b[<35;20;5m"
        self.process_input(mouse_seq)
        assert self.emitted_sequences == [mouse_seq]

    def test_pass_through_complete_arrow_key_sequences(self):
        """Should pass through complete arrow key sequences."""
        up_arrow = "\x1b[A"
        self.process_input(up_arrow)
        assert self.emitted_sequences == [up_arrow]

    def test_pass_through_complete_function_key_sequences(self):
        """Should pass through complete function key sequences."""
        f1 = "\x1b[11~"
        self.process_input(f1)
        assert self.emitted_sequences == [f1]

    def test_pass_through_meta_key_sequences(self):
        """Should pass through meta key sequences."""
        meta_a = "\x1ba"
        self.process_input(meta_a)
        assert self.emitted_sequences == [meta_a]

    def test_pass_through_ss3_sequences(self):
        """Should pass through SS3 sequences."""
        ss3 = "\x1bOA"
        self.process_input(ss3)
        assert self.emitted_sequences == [ss3]

    # ------------------------------------------------------------------ #
    # Partial Escape Sequences
    # ------------------------------------------------------------------ #

    def test_buffer_incomplete_mouse_sgr_sequence(self):
        """Should buffer incomplete mouse SGR sequence."""
        self.process_input("\x1b")
        assert self.emitted_sequences == []
        assert self.buffer.get_buffer() == "\x1b"

        self.process_input("[<35")
        assert self.emitted_sequences == []
        assert self.buffer.get_buffer() == "\x1b[<35"

        self.process_input(";20;5m")
        assert self.emitted_sequences == ["\x1b[<35;20;5m"]
        assert self.buffer.get_buffer() == ""

    def test_buffer_incomplete_csi_sequence(self):
        """Should buffer incomplete CSI sequence."""
        self.process_input("\x1b[")
        assert self.emitted_sequences == []

        self.process_input("1;")
        assert self.emitted_sequences == []

        self.process_input("5H")
        assert self.emitted_sequences == ["\x1b[1;5H"]

    def test_buffer_split_across_many_chunks(self):
        """Should buffer split across many chunks."""
        self.process_input("\x1b")
        self.process_input("[")
        self.process_input("<")
        self.process_input("3")
        self.process_input("5")
        self.process_input(";")
        self.process_input("2")
        self.process_input("0")
        self.process_input(";")
        self.process_input("5")
        self.process_input("m")

        assert self.emitted_sequences == ["\x1b[<35;20;5m"]

    @pytest.mark.asyncio
    async def test_flush_incomplete_sequence_after_timeout(self):
        """Should flush incomplete sequence after timeout."""
        self.process_input("\x1b[<35")
        assert self.emitted_sequences == []

        # Wait for timeout
        await asyncio.sleep(0.015)

        assert self.emitted_sequences == ["\x1b[<35"]

    # ------------------------------------------------------------------ #
    # Mixed Content
    # ------------------------------------------------------------------ #

    def test_handle_characters_followed_by_escape_sequence(self):
        """Should handle characters followed by escape sequence."""
        self.process_input("abc\x1b[A")
        assert self.emitted_sequences == ["a", "b", "c", "\x1b[A"]

    def test_handle_escape_sequence_followed_by_characters(self):
        """Should handle escape sequence followed by characters."""
        self.process_input("\x1b[Aabc")
        assert self.emitted_sequences == ["\x1b[A", "a", "b", "c"]

    def test_handle_multiple_complete_sequences(self):
        """Should handle multiple complete sequences."""
        self.process_input("\x1b[A\x1b[B\x1b[C")
        assert self.emitted_sequences == ["\x1b[A", "\x1b[B", "\x1b[C"]

    def test_handle_partial_sequence_with_preceding_characters(self):
        """Should handle partial sequence with preceding characters."""
        self.process_input("abc\x1b[<35")
        assert self.emitted_sequences == ["a", "b", "c"]
        assert self.buffer.get_buffer() == "\x1b[<35"

        self.process_input(";20;5m")
        assert self.emitted_sequences == ["a", "b", "c", "\x1b[<35;20;5m"]

    # ------------------------------------------------------------------ #
    # Kitty Keyboard Protocol
    # ------------------------------------------------------------------ #

    def test_handle_kitty_csi_u_press_events(self):
        """Should handle Kitty CSI u press events."""
        # Press 'a' in Kitty protocol
        self.process_input("\x1b[97u")
        assert self.emitted_sequences == ["\x1b[97u"]

    def test_handle_kitty_csi_u_release_events(self):
        """Should handle Kitty CSI u release events."""
        # Release 'a' in Kitty protocol
        self.process_input("\x1b[97;1:3u")
        assert self.emitted_sequences == ["\x1b[97;1:3u"]

    def test_handle_batched_kitty_press_and_release(self):
        """Should handle batched Kitty press and release."""
        # Press 'a', release 'a' batched together (common over SSH)
        self.process_input("\x1b[97u\x1b[97;1:3u")
        assert self.emitted_sequences == ["\x1b[97u", "\x1b[97;1:3u"]

    def test_handle_multiple_batched_kitty_events(self):
        """Should handle multiple batched Kitty events."""
        # Press 'a', release 'a', press 'b', release 'b'
        self.process_input("\x1b[97u\x1b[97;1:3u\x1b[98u\x1b[98;1:3u")
        assert self.emitted_sequences == [
            "\x1b[97u",
            "\x1b[97;1:3u",
            "\x1b[98u",
            "\x1b[98;1:3u",
        ]

    def test_handle_kitty_arrow_keys_with_event_type(self):
        """Should handle Kitty arrow keys with event type."""
        # Up arrow press with event type
        self.process_input("\x1b[1;1:1A")
        assert self.emitted_sequences == ["\x1b[1;1:1A"]

    def test_handle_kitty_functional_keys_with_event_type(self):
        """Should handle Kitty functional keys with event type."""
        # Delete key release
        self.process_input("\x1b[3;1:3~")
        assert self.emitted_sequences == ["\x1b[3;1:3~"]

    def test_handle_plain_characters_mixed_with_kitty_sequences(self):
        """Should handle plain characters mixed with Kitty sequences."""
        # Plain 'a' followed by Kitty release
        self.process_input("a\x1b[97;1:3u")
        assert self.emitted_sequences == ["a", "\x1b[97;1:3u"]

    def test_drop_raw_duplicate_character_after_matching_kitty_printable_sequence(self):
        """Should drop raw duplicate character after matching Kitty printable sequence."""
        self.process_input("\x1b[224uà")
        assert self.emitted_sequences == ["\x1b[224u"]

    def test_drop_raw_duplicate_after_matching_kitty_printable(self):
        """Should drop raw duplicate after matching Kitty printable."""
        self.process_input("\x1b[64u")
        self.process_input("@")
        assert self.emitted_sequences == ["\x1b[64u"]

    def test_keep_non_matching_plain_character_after_kitty_printable_sequence(self):
        """Should keep non-matching plain character after Kitty printable sequence."""
        self.process_input("\x1b[97ub")
        assert self.emitted_sequences == ["\x1b[97u", "b"]

    def test_keep_raw_character_after_modified_kitty_printable_sequence(self):
        """Should keep raw character after modified Kitty printable sequence."""
        self.process_input("\x1b[64;3u@")
        assert self.emitted_sequences == ["\x1b[64;3u", "@"]

    def test_handle_rapid_typing_simulation_with_kitty_protocol(self):
        """Should handle rapid typing simulation with Kitty protocol."""
        # Simulates typing "hi" quickly with releases interleaved
        self.process_input("\x1b[104u\x1b[104;1:3u\x1b[105u\x1b[105;1:3u")
        assert self.emitted_sequences == [
            "\x1b[104u",
            "\x1b[104;1:3u",
            "\x1b[105u",
            "\x1b[105;1:3u",
        ]

    # ------------------------------------------------------------------ #
    # Mouse Events
    # ------------------------------------------------------------------ #

    def test_handle_mouse_press_event(self):
        """Should handle mouse press event."""
        self.process_input("\x1b[<0;10;5M")
        assert self.emitted_sequences == ["\x1b[<0;10;5M"]

    def test_handle_mouse_release_event(self):
        """Should handle mouse release event."""
        self.process_input("\x1b[<0;10;5m")
        assert self.emitted_sequences == ["\x1b[<0;10;5m"]

    def test_handle_mouse_move_event(self):
        """Should handle mouse move event."""
        self.process_input("\x1b[<35;20;5m")
        assert self.emitted_sequences == ["\x1b[<35;20;5m"]

    def test_handle_split_mouse_events(self):
        """Should handle split mouse events."""
        self.process_input("\x1b[<3")
        self.process_input("5;1")
        self.process_input("5;")
        self.process_input("10m")
        assert self.emitted_sequences == ["\x1b[<35;15;10m"]

    def test_handle_multiple_mouse_events(self):
        """Should handle multiple mouse events."""
        self.process_input("\x1b[<35;1;1m\x1b[<35;2;2m\x1b[<35;3;3m")
        assert self.emitted_sequences == [
            "\x1b[<35;1;1m",
            "\x1b[<35;2;2m",
            "\x1b[<35;3;3m",
        ]

    def test_handle_old_style_mouse_sequence(self):
        """Should handle old-style mouse sequence (ESC[M + 3 bytes)."""
        self.process_input("\x1b[M abc")
        assert self.emitted_sequences == ["\x1b[M ab", "c"]

    def test_buffer_incomplete_old_style_mouse_sequence(self):
        """Should buffer incomplete old-style mouse sequence."""
        self.process_input("\x1b[M")
        assert self.buffer.get_buffer() == "\x1b[M"

        self.process_input(" a")
        assert self.buffer.get_buffer() == "\x1b[M a"

        self.process_input("b")
        assert self.emitted_sequences == ["\x1b[M ab"]

    # ------------------------------------------------------------------ #
    # Edge Cases
    # ------------------------------------------------------------------ #

    def test_handle_empty_input(self):
        """Should handle empty input."""
        self.process_input("")
        # Empty string emits an empty data event
        assert self.emitted_sequences == [""]

    @pytest.mark.asyncio
    async def test_handle_lone_escape_character_with_timeout(self):
        """Should handle lone escape character with timeout."""
        self.process_input("\x1b")
        assert self.emitted_sequences == []

        # After timeout, should emit
        await asyncio.sleep(0.015)
        assert self.emitted_sequences == ["\x1b"]

    def test_handle_lone_escape_character_with_explicit_flush(self):
        """Should handle lone escape character with explicit flush."""
        self.process_input("\x1b")
        assert self.emitted_sequences == []

        flushed = self.buffer.flush()
        assert flushed == ["\x1b"]

    def test_handle_buffer_input(self):
        """Should handle buffer input."""
        self.process_input(b"\x1b[A")
        assert self.emitted_sequences == ["\x1b[A"]

    def test_handle_very_long_sequences(self):
        """Should handle very long sequences."""
        long_seq = f"\x1b[{'1;' * 50}H"
        self.process_input(long_seq)
        assert self.emitted_sequences == [long_seq]

    # ------------------------------------------------------------------ #
    # Flush
    # ------------------------------------------------------------------ #

    def test_flush_incomplete_sequences(self):
        """Should flush incomplete sequences."""
        self.process_input("\x1b[<35")
        flushed = self.buffer.flush()
        assert flushed == ["\x1b[<35"]
        assert self.buffer.get_buffer() == ""

    def test_flush_return_empty_array_if_nothing_to_flush(self):
        """Should return empty array if nothing to flush."""
        flushed = self.buffer.flush()
        assert flushed == []

    @pytest.mark.asyncio
    async def test_flush_emit_flushed_data_via_timeout(self):
        """Should emit flushed data via timeout."""
        self.process_input("\x1b[<35")
        assert self.emitted_sequences == []

        # Wait for timeout to flush
        await asyncio.sleep(0.015)

        assert self.emitted_sequences == ["\x1b[<35"]

    # ------------------------------------------------------------------ #
    # Clear
    # ------------------------------------------------------------------ #

    def test_clear_buffered_content_without_emitting(self):
        """Should clear buffered content without emitting."""
        self.process_input("\x1b[<35")
        assert self.buffer.get_buffer() == "\x1b[<35"

        self.buffer.clear()
        assert self.buffer.get_buffer() == ""
        assert self.emitted_sequences == []

    # ------------------------------------------------------------------ #
    # Bracketed Paste
    # ------------------------------------------------------------------ #

    def test_emit_paste_event_for_complete_bracketed_paste(self):
        """Should emit paste event for complete bracketed paste."""
        paste_start = "\x1b[200~"
        paste_end = "\x1b[201~"
        content = "hello world"

        self.buffer.clear()
        emitted_paste: list[str] = []
        self.buffer.on_paste(lambda data: emitted_paste.append(data))

        self.process_input(paste_start + content + paste_end)

        assert emitted_paste == ["hello world"]
        assert self.emitted_sequences == []  # No data events during paste

    def test_handle_paste_arriving_in_chunks(self):
        """Should handle paste arriving in chunks."""
        self.buffer.clear()
        emitted_paste: list[str] = []
        self.buffer.on_paste(lambda data: emitted_paste.append(data))

        self.process_input("\x1b[200~")
        assert emitted_paste == []

        self.process_input("hello ")
        assert emitted_paste == []

        self.process_input("world\x1b[201~")
        assert emitted_paste == ["hello world"]
        assert self.emitted_sequences == []

    def test_handle_paste_with_input_before_and_after(self):
        """Should handle paste with input before and after."""
        self.buffer.clear()
        emitted_paste: list[str] = []
        self.buffer.on_paste(lambda data: emitted_paste.append(data))

        self.process_input("a")
        self.process_input("\x1b[200~pasted\x1b[201~")
        self.process_input("b")

        assert self.emitted_sequences == ["a", "b"]
        assert emitted_paste == ["pasted"]

    def test_handle_paste_with_newlines(self):
        """Should handle paste with newlines."""
        self.buffer.clear()
        emitted_paste: list[str] = []
        self.buffer.on_paste(lambda data: emitted_paste.append(data))

        self.process_input("\x1b[200~line1\nline2\nline3\x1b[201~")

        assert emitted_paste == ["line1\nline2\nline3"]
        assert self.emitted_sequences == []

    def test_handle_paste_with_unicode(self):
        """Should handle paste with unicode."""
        self.buffer.clear()
        emitted_paste: list[str] = []
        self.buffer.on_paste(lambda data: emitted_paste.append(data))

        self.process_input("\x1b[200~Hello 世界 🎉\x1b[201~")

        assert emitted_paste == ["Hello 世界 🎉"]
        assert self.emitted_sequences == []

    # ------------------------------------------------------------------ #
    # Destroy
    # ------------------------------------------------------------------ #

    def test_clear_buffer_on_destroy(self):
        """Should clear buffer on destroy."""
        self.process_input("\x1b[<35")
        assert self.buffer.get_buffer() == "\x1b[<35"

        self.buffer.destroy()
        assert self.buffer.get_buffer() == ""

    @pytest.mark.asyncio
    async def test_clear_pending_timeouts_on_destroy(self):
        """Should clear pending timeouts on destroy."""
        self.process_input("\x1b[<35")
        self.buffer.destroy()

        # Wait longer than timeout
        await asyncio.sleep(0.015)

        # Should not have emitted anything
        assert self.emitted_sequences == []
