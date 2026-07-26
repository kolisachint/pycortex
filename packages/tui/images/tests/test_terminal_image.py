"""Tests for terminal image detection and line handling.

Ports `packages/tui/test/terminal-image.test.ts` and
`packages/tui/test/bug-regression-isimageline-startswith-bug.test.ts`, keeping
their names and order. The escape-sequence *shapes* those tests assert on are
also captured from the real TS by the parity corpus
(`component/image-kitty-*`); these cover the branches a rendered frame cannot
reach — env-driven capability detection, and `is_image_line` over lines no
component would produce.
"""

from __future__ import annotations

import base64
import struct
from collections.abc import Iterator

import pytest
from cortex.tui.images import (
    CellDimensions,
    Image,
    ImageDimensions,
    ImageOptions,
    ImageRenderOptions,
    ImageTheme,
    TerminalCapabilities,
    allocate_image_id,
    calculate_image_rows,
    delete_all_kitty_images,
    delete_kitty_image,
    detect_capabilities,
    encode_iterm2,
    encode_kitty,
    get_capabilities,
    get_cell_dimensions,
    get_gif_dimensions,
    get_image_dimensions,
    get_jpeg_dimensions,
    get_png_dimensions,
    get_webp_dimensions,
    hyperlink,
    image_fallback,
    is_image_line,
    render_image,
    reset_capabilities_cache,
    set_capabilities,
    set_cell_dimensions,
)

ENV_KEYS = (
    "TERM",
    "TERM_PROGRAM",
    "COLORTERM",
    "TMUX",
    "KITTY_WINDOW_ID",
    "GHOSTTY_RESOURCES_DIR",
    "WEZTERM_PANE",
    "ITERM_SESSION_ID",
    "CMUX_WORKSPACE_ID",
)


@pytest.fixture(autouse=True)
def _isolated_module_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Capabilities and cell size are process-global, exactly as in the TS.

    Every test starts from a cleared cache and the 9x18 default, and the whole
    `ENV_KEYS` set is cleared so the machine running the suite cannot change a
    detection result. This is the `withEnv` helper of the TS file, applied to
    every test rather than to the detection ones only.
    """
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    reset_capabilities_cache()
    set_cell_dimensions(CellDimensions(width_px=9, height_px=18))
    yield
    reset_capabilities_cache()
    set_cell_dimensions(CellDimensions(width_px=9, height_px=18))


class TestIsImageLineITerm2:
    def test_detects_iterm2_escape_at_start_of_line(self) -> None:
        # iTerm2 image escape sequence: ESC ]1337;File=...
        iterm2_image_line = "\x1b]1337;File=size=100,100;inline=1:base64encodeddata==\x07"
        assert is_image_line(iterm2_image_line) is True

    def test_detects_iterm2_escape_with_text_before_it(self) -> None:
        # Simulating a line that has text then image data (bug scenario)
        line = "Some text \x1b]1337;File=size=100,100;inline=1:base64data==\x07 more text"
        assert is_image_line(line) is True

    def test_detects_iterm2_escape_in_middle_of_long_line(self) -> None:
        line = "Text before image..." + "\x1b]1337;File=inline=1:verylongbase64data==" + "...after"
        assert is_image_line(line) is True

    def test_detects_iterm2_escape_at_end_of_line(self) -> None:
        line = "Regular text ending with \x1b]1337;File=inline=1:base64data==\x07"
        assert is_image_line(line) is True

    def test_detects_minimal_iterm2_escape(self) -> None:
        assert is_image_line("\x1b]1337;File=:\x07") is True


class TestIsImageLineKitty:
    def test_detects_kitty_escape_at_start_of_line(self) -> None:
        # Kitty image escape sequence: ESC _G
        line = "\x1b_Ga=T,f=100,t=f,d=base64data...\x1b\\\x1b_Gm=i=1;\x1b\\"
        assert is_image_line(line) is True

    def test_detects_kitty_escape_with_text_before_it(self) -> None:
        line = "Output: \x1b_Ga=T,f=100;data...\x1b\\\x1b_Gm=i=1;\x1b\\"
        assert is_image_line(line) is True

    def test_detects_kitty_escape_with_padding(self) -> None:
        line = "  \x1b_Ga=T,f=100...\x1b\\\x1b_Gm=i=1;\x1b\\  "
        assert is_image_line(line) is True


class TestIsImageLineRegressions:
    def test_detects_image_sequences_in_very_long_lines(self) -> None:
        # The crash scenario: a 300k+ char line with an image sequence in it.
        long_line = "Text prefix " + "\x1b]1337;File=size=800,600;inline=1:" + "A" * 300_000 + " s"
        assert len(long_line) > 300_000
        assert is_image_line(long_line) is True

    def test_detects_image_sequences_when_terminal_does_not_support_images(self) -> None:
        # The bug: detection used to go through the *active* protocol, so with
        # no image support it reported False and the line hit the width guard.
        set_capabilities(TerminalCapabilities(images=None, true_color=True, hyperlinks=False))
        line = "Read image file [image/jpeg]\x1b]1337;File=inline=1:base64data==\x07"
        assert is_image_line(line) is True

    def test_detects_image_sequences_with_ansi_codes_before_them(self) -> None:
        line = "\x1b[31mError output \x1b]1337;File=inline=1:image==\x07"
        assert is_image_line(line) is True

    def test_detects_image_sequences_with_ansi_codes_after_them(self) -> None:
        line = "\x1b_Ga=T,f=100:data...\x1b\\\x1b_Gm=i=1;\x1b\\\x1b[0m reset"
        assert is_image_line(line) is True

    def test_detects_kitty_sequences_in_any_position(self) -> None:
        scenarios = [
            "At start: \x1b_Ga=T,f=100,data...\x1b\\",
            "Prefix \x1b_Ga=T,data...\x1b\\",
            "Suffix text \x1b_Ga=T,data...\x1b\\ suffix",
            "Middle \x1b_Ga=T,data...\x1b\\ more text",
            f"Text before \x1b_Ga=T,f=100{'A' * 300_000} text after",
        ]
        for line in scenarios:
            assert is_image_line(line) is True, line[:50]

    def test_detects_iterm2_sequences_in_any_position(self) -> None:
        scenarios = [
            "At start: \x1b]1337;File=size=100,100:base64...\x07",
            "Prefix \x1b]1337;File=inline=1:data==\x07",
            "Suffix text \x1b]1337;File=inline=1:data==\x07 suffix",
            "Middle \x1b]1337;File=inline=1:data==\x07 more text",
            f"Text before \x1b]1337;File=size=800,600;inline=1:{'B' * 300_000} text after",
        ]
        for line in scenarios:
            assert is_image_line(line) is True, line[:50]

    def test_handles_line_exactly_matching_the_crash_log_dimensions(self) -> None:
        # Crash log showed a 58649-char line against a terminal width of 115.
        target_width = 58649
        prefix, sequence, suffix = "Text", "\x1b_Ga=T,f=100", "End"
        padding = "A" * (target_width - len(prefix) - len(sequence) - len(suffix))
        line = f"{prefix}{sequence}{padding}{suffix}"
        assert len(line) == 58649
        assert is_image_line(line) is True


class TestIsImageLineNegatives:
    def test_plain_text(self) -> None:
        assert is_image_line("This is just a regular text line without any escape") is False

    def test_only_ansi_codes(self) -> None:
        assert is_image_line("\x1b[31mRed text\x1b[0m and \x1b[32mgreen text\x1b[0m") is False

    def test_cursor_movement_codes(self) -> None:
        assert is_image_line("\x1b[1A\x1b[2KLine cleared and moved up") is False

    def test_partial_iterm2_sequence(self) -> None:
        assert is_image_line("Some text with ]1337;File but missing ESC at start") is False

    def test_partial_kitty_sequence(self) -> None:
        assert is_image_line("Some text with _G but missing ESC at start") is False

    def test_empty_line(self) -> None:
        assert is_image_line("") is False

    def test_newlines_only(self) -> None:
        assert is_image_line("\n") is False
        assert is_image_line("\n\n") is False

    def test_regular_long_text(self) -> None:
        assert is_image_line("A" * 100_000) is False

    def test_file_paths_that_contain_the_keywords(self) -> None:
        for path in (
            "/path/to/1337/image.jpg",
            "/usr/local/bin/File_converter",
            "~/Documents/1337File_backup.png",
            "./_G_test_file.txt",
            "/path/to/File_1337_backup/image.jpg",
        ):
            assert is_image_line(path) is False, path


class TestIsImageLineMixedContent:
    def test_line_with_both_kitty_and_iterm2_sequences(self) -> None:
        line = "Kitty: \x1b_Ga=T...\x1b\\\x1b_Gm=i=1;\x1b\\ iTerm2: \x1b]1337;File=inline=1:d==\x07"
        assert is_image_line(line) is True

    def test_multiple_text_and_image_segments(self) -> None:
        line = "Start \x1b]1337;File=img1==\x07 middle \x1b]1337;File=img2==\x07 end"
        assert is_image_line(line) is True


class TestDetectCapabilities:
    def test_defaults_to_hyperlinks_false_for_unknown_terminals(self) -> None:
        caps = detect_capabilities()
        assert caps.hyperlinks is False
        assert caps.images is None

    def test_forces_hyperlinks_false_under_tmux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")
        monkeypatch.setenv("TERM_PROGRAM", "ghostty")
        caps = detect_capabilities()
        assert caps.hyperlinks is False
        assert caps.images is None

    def test_forces_hyperlinks_false_when_term_starts_with_tmux(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TERM", "tmux-256color")
        monkeypatch.setenv("TERM_PROGRAM", "iterm.app")
        caps = detect_capabilities()
        assert caps.hyperlinks is False
        assert caps.images is None

    def test_forces_hyperlinks_false_when_term_starts_with_screen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TERM", "screen-256color")
        caps = detect_capabilities()
        assert caps.hyperlinks is False
        assert caps.images is None

    def test_enables_hyperlinks_for_ghostty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TERM_PROGRAM", "ghostty")
        assert detect_capabilities().hyperlinks is True

    def test_does_not_disable_ghostty_images_solely_because_cmux_is_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TERM_PROGRAM", "ghostty")
        monkeypatch.setenv("CMUX_WORKSPACE_ID", "workspace")
        caps = detect_capabilities()
        assert caps.images == "kitty"
        assert caps.hyperlinks is True

    def test_enables_hyperlinks_for_kitty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KITTY_WINDOW_ID", "1")
        assert detect_capabilities().hyperlinks is True

    def test_enables_hyperlinks_for_wezterm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEZTERM_PANE", "0")
        assert detect_capabilities().hyperlinks is True

    def test_enables_hyperlinks_for_iterm2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TERM_PROGRAM", "iterm.app")
        assert detect_capabilities().hyperlinks is True

    def test_enables_hyperlinks_for_vscode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TERM_PROGRAM", "vscode")
        assert detect_capabilities().hyperlinks is True

    # --- branches the TS file does not name, one per remaining `if` ---------

    def test_term_program_kitty_without_the_window_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TERM_PROGRAM", "KiTTY")
        assert detect_capabilities().images == "kitty"

    def test_ghostty_recognised_from_term_or_resources_dir(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TERM", "xterm-ghostty")
        assert detect_capabilities().images == "kitty"
        monkeypatch.delenv("TERM")
        monkeypatch.setenv("GHOSTTY_RESOURCES_DIR", "/usr/share/ghostty")
        assert detect_capabilities().images == "kitty"

    def test_wezterm_from_term_program(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TERM_PROGRAM", "wezterm")
        assert detect_capabilities().images == "kitty"

    def test_iterm2_from_session_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ITERM_SESSION_ID", "w0t0p0")
        assert detect_capabilities().images == "iterm2"

    def test_vscode_and_alacritty_get_hyperlinks_but_no_images(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for program in ("vscode", "alacritty"):
            monkeypatch.setenv("TERM_PROGRAM", program)
            caps = detect_capabilities()
            assert caps.images is None, program
            assert caps.true_color is True, program
            assert caps.hyperlinks is True, program

    def test_true_color_comes_from_colorterm_for_unidentified_terminals(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert detect_capabilities().true_color is False
        monkeypatch.setenv("COLORTERM", "truecolor")
        assert detect_capabilities().true_color is True
        monkeypatch.setenv("COLORTERM", "24bit")
        assert detect_capabilities().true_color is True
        monkeypatch.setenv("COLORTERM", "256")
        assert detect_capabilities().true_color is False

    def test_tmux_still_reports_true_color(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TMUX", "/tmp/tmux/default,1,0")
        monkeypatch.setenv("COLORTERM", "truecolor")
        caps = detect_capabilities()
        assert caps.true_color is True
        assert caps.images is None

    def test_detection_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TERM_PROGRAM", "Ghostty")
        assert detect_capabilities().images == "kitty"


class TestCapabilitiesCache:
    def test_get_capabilities_caches_the_first_detection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TERM_PROGRAM", "ghostty")
        assert get_capabilities().images == "kitty"
        monkeypatch.delenv("TERM_PROGRAM")
        # Cached: the environment change is not picked up until the reset.
        assert get_capabilities().images == "kitty"
        reset_capabilities_cache()
        assert get_capabilities().images is None

    def test_set_capabilities_overrides_detection(self) -> None:
        set_capabilities(TerminalCapabilities(images="iterm2", true_color=False, hyperlinks=True))
        caps = get_capabilities()
        assert (caps.images, caps.true_color, caps.hyperlinks) == ("iterm2", False, True)


class TestCellDimensions:
    def test_defaults_to_nine_by_eighteen(self) -> None:
        assert get_cell_dimensions() == CellDimensions(width_px=9, height_px=18)

    def test_set_cell_dimensions_is_read_back(self) -> None:
        set_cell_dimensions(CellDimensions(width_px=10, height_px=20))
        assert get_cell_dimensions() == CellDimensions(width_px=10, height_px=20)


class TestKittyImageCursorMovement:
    def test_can_request_no_terminal_side_cursor_movement(self) -> None:
        sequence = encode_kitty("AAAA", columns=2, rows=2, move_cursor=False)
        assert sequence.startswith("\x1b_Ga=T,f=100,q=2,C=1,c=2,r=2;")

    def test_suppresses_kitty_replies_for_delete_commands(self) -> None:
        assert delete_kitty_image(42) == "\x1b_Ga=d,d=I,i=42,q=2\x1b\\"
        assert delete_all_kitty_images() == "\x1b_Ga=d,d=A,q=2\x1b\\"

    def test_preserves_render_images_default_terminal_side_cursor_movement(self) -> None:
        set_capabilities(TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True))
        set_cell_dimensions(CellDimensions(width_px=10, height_px=10))
        result = render_image(
            "AAAA",
            ImageDimensions(width_px=20, height_px=20),
            ImageRenderOptions(max_width_cells=2),
        )
        assert result is not None
        assert ",C=1," not in result.sequence
        assert result.rows == 2

    def test_can_opt_render_image_into_no_terminal_side_cursor_movement(self) -> None:
        set_capabilities(TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True))
        set_cell_dimensions(CellDimensions(width_px=10, height_px=10))
        result = render_image(
            "AAAA",
            ImageDimensions(width_px=20, height_px=20),
            ImageRenderOptions(max_width_cells=2, move_cursor=False),
        )
        assert result is not None
        assert ",C=1," in result.sequence
        assert result.rows == 2

    def test_restores_the_cursor_to_the_reserved_image_row_after_kitty_rendering(self) -> None:
        set_capabilities(TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True))
        set_cell_dimensions(CellDimensions(width_px=10, height_px=10))
        image = Image(
            "AAAA",
            "image/png",
            ImageTheme(fallback_color=lambda value: value),
            ImageOptions(max_width_cells=2),
            ImageDimensions(width_px=20, height_px=20),
        )
        lines = image.render(4)
        image_id = image.get_image_id()
        assert isinstance(image_id, int)
        assert lines[:-1] == [""]
        assert lines[1].startswith("\x1b[1A\x1b_G")
        assert ",C=1," in lines[1]
        assert f",i={image_id}" in lines[1]
        assert lines[1].endswith("\x1b[1B")


class TestEncodeKitty:
    def test_default_parameters(self) -> None:
        assert encode_kitty("AAAA") == "\x1b_Ga=T,f=100,q=2;AAAA\x1b\\"

    def test_zero_columns_and_rows_are_omitted(self) -> None:
        # `if (options.columns)` in the TS: 0 is falsy, so no `c=0` is emitted.
        assert (
            encode_kitty("AAAA", columns=0, rows=0, image_id=0) == "\x1b_Ga=T,f=100,q=2;AAAA\x1b\\"
        )

    def test_parameter_order_is_c_then_r_then_i(self) -> None:
        assert encode_kitty("AAAA", columns=3, rows=4, image_id=5).startswith(
            "\x1b_Ga=T,f=100,q=2,c=3,r=4,i=5;"
        )

    def test_move_cursor_true_does_not_emit_the_suppression_flag(self) -> None:
        assert ",C=1," not in encode_kitty("AAAA", columns=2, move_cursor=True)

    def test_payload_at_the_chunk_boundary_stays_a_single_sequence(self) -> None:
        data = "A" * 4096
        sequence = encode_kitty(data)
        assert sequence == f"\x1b_Ga=T,f=100,q=2;{data}\x1b\\"
        assert "m=1" not in sequence

    def test_payload_over_the_chunk_boundary_is_split(self) -> None:
        data = "A" * 4097
        sequence = encode_kitty(data, image_id=7)
        assert sequence == (f"\x1b_Ga=T,f=100,q=2,i=7,m=1;{'A' * 4096}\x1b\\\x1b_Gm=0;A\x1b\\")

    def test_three_chunks_carry_a_continuation_in_the_middle(self) -> None:
        data = "A" * (4096 * 2 + 1)
        chunks = encode_kitty(data).split("\x1b\\")[:-1]
        assert len(chunks) == 3
        assert chunks[0].startswith("\x1b_Ga=T,f=100,q=2,m=1;")
        assert chunks[1].startswith("\x1b_Gm=1;")
        assert chunks[2].startswith("\x1b_Gm=0;")


class TestEncodeITerm2:
    def test_inline_defaults_to_one(self) -> None:
        assert encode_iterm2("AAAA") == "\x1b]1337;File=inline=1:AAAA\x07"

    def test_inline_false_is_the_only_way_to_get_zero(self) -> None:
        assert encode_iterm2("AAAA", inline=False).startswith("\x1b]1337;File=inline=0:")
        assert encode_iterm2("AAAA", inline=True).startswith("\x1b]1337;File=inline=1:")

    def test_width_and_height_accept_numbers_and_keywords(self) -> None:
        assert encode_iterm2("AAAA", width=10, height="auto") == (
            "\x1b]1337;File=inline=1;width=10;height=auto:AAAA\x07"
        )

    def test_zero_width_is_still_emitted(self) -> None:
        # `!== undefined` in the TS, not a truthiness check like `columns`.
        assert "width=0" in encode_iterm2("AAAA", width=0)

    def test_name_is_base64_encoded(self) -> None:
        encoded = base64.b64encode(b"cat.png").decode()
        assert f"name={encoded}" in encode_iterm2("AAAA", name="cat.png")

    def test_empty_name_is_omitted(self) -> None:
        assert "name=" not in encode_iterm2("AAAA", name="")

    def test_preserve_aspect_ratio_is_only_emitted_when_disabled(self) -> None:
        assert "preserveAspectRatio" not in encode_iterm2("AAAA", preserve_aspect_ratio=True)
        assert "preserveAspectRatio=0" in encode_iterm2("AAAA", preserve_aspect_ratio=False)


class TestAllocateImageId:
    def test_stays_inside_the_kitty_id_range(self) -> None:
        for _ in range(200):
            assert 1 <= allocate_image_id() <= 0xFFFFFFFE

    def test_ids_are_not_all_the_same(self) -> None:
        assert len({allocate_image_id() for _ in range(50)}) > 1


class TestCalculateImageRows:
    def test_uses_the_default_cell_size_when_none_is_given(self) -> None:
        # 9x18 default: 4 cells wide is 36px, a 36x36 image scales 1:1 -> 2 rows.
        assert calculate_image_rows(ImageDimensions(width_px=36, height_px=36), 4) == 2

    def test_rounds_up(self) -> None:
        cells = CellDimensions(width_px=10, height_px=10)
        assert calculate_image_rows(ImageDimensions(width_px=10, height_px=11), 1, cells) == 2

    def test_never_returns_less_than_one_row(self) -> None:
        # `ceil` only reaches 0 for a zero-height image, so that is the one
        # input the `max(1, ...)` floor is observable on — a 100x1 image still
        # rounds up to 1 and says nothing about the guard.
        cells = CellDimensions(width_px=10, height_px=100)
        assert calculate_image_rows(ImageDimensions(width_px=100, height_px=1), 1, cells) == 1
        assert calculate_image_rows(ImageDimensions(width_px=100, height_px=0), 1, cells) == 1

    def test_scales_with_the_cell_width(self) -> None:
        wide = CellDimensions(width_px=20, height_px=10)
        narrow = CellDimensions(width_px=10, height_px=10)
        image = ImageDimensions(width_px=10, height_px=10)
        assert calculate_image_rows(image, 1, wide) == 2
        assert calculate_image_rows(image, 1, narrow) == 1


def _png(width: int, height: int) -> str:
    header = (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
    )
    return base64.b64encode(header).decode()


def _jpeg(width: int, height: int, marker: int = 0xC0) -> str:
    header = (
        b"\xff\xd8"
        + b"\xff\xe0"
        + struct.pack(">H", 4)
        + b"XY"
        + bytes([0xFF, marker])
        + struct.pack(">H", 11)
        + b"\x08"
        + struct.pack(">HH", height, width)
        + b"\x01\x01\x11\x00"
        + b"\x00" * 8
    )
    return base64.b64encode(header).decode()


def _gif(width: int, height: int, sig: bytes = b"GIF89a") -> str:
    return base64.b64encode(sig + struct.pack("<HH", width, height) + b"\xf7\x00").decode()


def _webp_vp8x(width: int, height: int) -> str:
    def u24le(value: int) -> bytes:
        return bytes([value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF])

    body = (
        b"RIFF"
        + struct.pack("<I", 100)
        + b"WEBP"
        + b"VP8X"
        + struct.pack("<I", 10)
        + b"\x00\x00\x00\x00"
        + u24le(width - 1)
        + u24le(height - 1)
        + b"\x00" * 4
    )
    return base64.b64encode(body).decode()


class TestImageDimensionParsers:
    def test_png(self) -> None:
        assert get_png_dimensions(_png(24, 12)) == ImageDimensions(width_px=24, height_px=12)

    def test_png_rejects_a_wrong_signature(self) -> None:
        broken = base64.b64encode(b"\x89PNQ\r\n\x1a\n" + b"\x00" * 20).decode()
        assert get_png_dimensions(broken) is None

    def test_png_rejects_a_short_buffer(self) -> None:
        assert get_png_dimensions(base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()) is None

    def test_jpeg_reads_the_sof_marker(self) -> None:
        assert get_jpeg_dimensions(_jpeg(40, 20)) == ImageDimensions(width_px=40, height_px=20)

    def test_jpeg_accepts_sof1_and_sof2(self) -> None:
        for marker in (0xC1, 0xC2):
            assert get_jpeg_dimensions(_jpeg(8, 9, marker)) == ImageDimensions(
                width_px=8, height_px=9
            )

    def test_jpeg_rejects_a_wrong_signature(self) -> None:
        assert get_jpeg_dimensions(base64.b64encode(b"\xff\xd9" + b"\x00" * 30).decode()) is None

    def test_jpeg_returns_none_when_no_sof_segment_is_present(self) -> None:
        no_sof = b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"XY" + b"\x00" * 20
        assert get_jpeg_dimensions(base64.b64encode(no_sof).decode()) is None

    def test_jpeg_rejects_a_segment_length_below_two(self) -> None:
        # A zero length would not advance the scan; the TS bails out instead.
        bad = b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 0) + b"\x00" * 30
        assert get_jpeg_dimensions(base64.b64encode(bad).decode()) is None

    def test_jpeg_skips_past_a_segment_rather_than_into_it(self) -> None:
        # A decoy `FF C0` planted in an APP0 payload. Advancing by `length`
        # instead of past the whole segment lands on the decoy and reads
        # 20x2824 from the bytes of the real SOF header.
        planted = "/9j/4AAKESIzRFVm/8D/wAALCAAUACgBAREAAAAAAAAAAAA="
        assert get_jpeg_dimensions(planted) == ImageDimensions(width_px=40, height_px=20)

    def test_gif(self) -> None:
        assert get_gif_dimensions(_gif(33, 11)) == ImageDimensions(width_px=33, height_px=11)

    def test_gif_accepts_the_87a_signature(self) -> None:
        assert get_gif_dimensions(_gif(4, 5, b"GIF87a")) == ImageDimensions(width_px=4, height_px=5)

    def test_gif_rejects_another_signature(self) -> None:
        assert get_gif_dimensions(_gif(4, 5, b"GIF88a")) is None

    def test_webp_vp8x(self) -> None:
        assert get_webp_dimensions(_webp_vp8x(50, 25)) == ImageDimensions(width_px=50, height_px=25)

    def test_webp_lossy_masks_off_the_scaling_bits(self) -> None:
        # A lossy WebP stores 2 scaling bits above each 14-bit dimension.
        # Unmasked these read 32832x16416 instead of 64x32.
        scaled = "UklGRmQAAABXRUJQVlA4IBQAAAAAAAAAAABAgCBAAAAAAAAAAAA="
        assert get_webp_dimensions(scaled) == ImageDimensions(width_px=64, height_px=32)

    def test_webp_rejects_a_non_riff_container(self) -> None:
        assert get_webp_dimensions(base64.b64encode(b"RIFX" + b"\x00" * 40).decode()) is None

    def test_webp_rejects_an_unknown_chunk(self) -> None:
        body = b"RIFF" + struct.pack("<I", 100) + b"WEBP" + b"XXXX" + b"\x00" * 20
        assert get_webp_dimensions(base64.b64encode(body).decode()) is None

    def test_get_image_dimensions_dispatches_on_mime_type(self) -> None:
        assert get_image_dimensions(_png(2, 3), "image/png") == ImageDimensions(
            width_px=2, height_px=3
        )
        assert get_image_dimensions(_jpeg(4, 5), "image/jpeg") == ImageDimensions(
            width_px=4, height_px=5
        )
        assert get_image_dimensions(_gif(6, 7), "image/gif") == ImageDimensions(
            width_px=6, height_px=7
        )
        assert get_image_dimensions(_webp_vp8x(8, 9), "image/webp") == ImageDimensions(
            width_px=8, height_px=9
        )
        assert get_image_dimensions(_png(2, 3), "image/bmp") is None

    def test_a_mismatched_mime_type_parses_as_nothing(self) -> None:
        assert get_image_dimensions(_png(2, 3), "image/gif") is None

    def test_unpadded_base64_still_parses(self) -> None:
        # `Buffer.from(x, "base64")` needs no padding and ignores stray
        # characters; a strict decoder would report "not a PNG" here.
        padded = _png(24, 12)
        assert get_png_dimensions(padded.rstrip("=")) == ImageDimensions(width_px=24, height_px=12)
        assert get_png_dimensions("\n".join(padded)) == ImageDimensions(width_px=24, height_px=12)

    def test_garbage_input_is_none_not_an_exception(self) -> None:
        for parser in (
            get_png_dimensions,
            get_jpeg_dimensions,
            get_gif_dimensions,
            get_webp_dimensions,
        ):
            assert parser("") is None
            assert parser("!!!") is None


class TestRenderImage:
    def test_returns_none_without_an_image_protocol(self) -> None:
        set_capabilities(TerminalCapabilities(images=None, true_color=True, hyperlinks=False))
        assert render_image("AAAA", ImageDimensions(width_px=20, height_px=20)) is None

    def test_defaults_to_eighty_cells_wide(self) -> None:
        set_capabilities(TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True))
        result = render_image("AAAA", ImageDimensions(width_px=20, height_px=20))
        assert result is not None
        assert ",c=80," in result.sequence

    def test_kitty_reports_back_the_image_id_it_was_given(self) -> None:
        set_capabilities(TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True))
        result = render_image(
            "AAAA",
            ImageDimensions(width_px=20, height_px=20),
            ImageRenderOptions(max_width_cells=2, image_id=99),
        )
        assert result is not None
        assert result.image_id == 99

    def test_iterm2_reports_no_image_id(self) -> None:
        set_capabilities(TerminalCapabilities(images="iterm2", true_color=True, hyperlinks=True))
        result = render_image(
            "AAAA",
            ImageDimensions(width_px=20, height_px=20),
            ImageRenderOptions(max_width_cells=2, image_id=99),
        )
        assert result is not None
        assert result.image_id is None
        assert result.sequence.startswith("\x1b]1337;File=inline=1;width=2;height=auto:")

    def test_iterm2_honours_preserve_aspect_ratio_false(self) -> None:
        set_capabilities(TerminalCapabilities(images="iterm2", true_color=True, hyperlinks=True))
        result = render_image(
            "AAAA",
            ImageDimensions(width_px=20, height_px=20),
            ImageRenderOptions(max_width_cells=2, preserve_aspect_ratio=False),
        )
        assert result is not None
        assert "preserveAspectRatio=0" in result.sequence


class TestHyperlink:
    def test_wraps_text_in_osc_8_open_and_close_sequences(self) -> None:
        result = hyperlink("click me", "https://example.com")
        assert result == "\x1b]8;;https://example.com\x1b\\click me\x1b]8;;\x1b\\"

    def test_preserves_ansi_styling_inside_the_hyperlink(self) -> None:
        styled = "\x1b[4m\x1b[34mclick me\x1b[0m"
        result = hyperlink(styled, "https://example.com")
        assert result.startswith("\x1b]8;;https://example.com\x1b\\")
        assert styled in result
        assert result.endswith("\x1b]8;;\x1b\\")

    def test_works_with_empty_text(self) -> None:
        assert hyperlink("", "https://example.com") == (
            "\x1b]8;;https://example.com\x1b\\\x1b]8;;\x1b\\"
        )

    def test_works_with_file_uris(self) -> None:
        result = hyperlink("README.md", "file:///home/user/README.md")
        assert "file:///home/user/README.md" in result
        assert "README.md" in result


class TestImageFallback:
    def test_mime_type_only(self) -> None:
        assert image_fallback("image/png") == "[Image: [image/png]]"

    def test_with_dimensions(self) -> None:
        assert image_fallback("image/png", ImageDimensions(width_px=8, height_px=4)) == (
            "[Image: [image/png] 8x4]"
        )

    def test_filename_comes_first(self) -> None:
        assert (
            image_fallback("image/png", ImageDimensions(width_px=8, height_px=4), "cat.png")
            == "[Image: cat.png [image/png] 8x4]"
        )

    def test_empty_filename_is_omitted(self) -> None:
        assert image_fallback("image/png", None, "") == "[Image: [image/png]]"
