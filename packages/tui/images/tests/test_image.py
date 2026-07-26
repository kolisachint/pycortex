"""Tests for the `Image` component (`components/image.ts`).

The frames themselves are pinned against the real TS by the parity corpus
(`component/image-*`); this file covers what a captured frame cannot show —
the memo, `invalidate`, and which id the component ends up holding.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from cortex.tui.images import (
    CellDimensions,
    Image,
    ImageDimensions,
    ImageOptions,
    ImageTheme,
    TerminalCapabilities,
    reset_capabilities_cache,
    set_capabilities,
    set_cell_dimensions,
)

PLAIN = ImageTheme(fallback_color=lambda value: value)
RED = ImageTheme(fallback_color=lambda value: f"\x1b[31m{value}\x1b[39m")


@pytest.fixture(autouse=True)
def _isolated_module_state() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Capabilities and cell size are process-global, exactly as in the TS."""
    reset_capabilities_cache()
    set_cell_dimensions(CellDimensions(width_px=10, height_px=10))
    yield
    reset_capabilities_cache()
    set_cell_dimensions(CellDimensions(width_px=9, height_px=18))


def _kitty() -> None:
    set_capabilities(TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True))


def _iterm2() -> None:
    set_capabilities(TerminalCapabilities(images="iterm2", true_color=True, hyperlinks=True))


def _no_images() -> None:
    set_capabilities(TerminalCapabilities(images=None, true_color=True, hyperlinks=False))


def _image(
    theme: ImageTheme = PLAIN,
    options: ImageOptions | None = None,
    dimensions: ImageDimensions | None = None,
    data: str = "AAAA",
    mime_type: str = "image/png",
) -> Image:
    return Image(
        data,
        mime_type,
        theme,
        options if options is not None else ImageOptions(max_width_cells=2),
        dimensions if dimensions is not None else ImageDimensions(width_px=20, height_px=20),
    )


class TestImageId:
    def test_kitty_allocates_an_id_on_first_render(self) -> None:
        _kitty()
        image = _image()
        assert image.get_image_id() is None
        image.render(10)
        allocated = image.get_image_id()
        assert isinstance(allocated, int)
        assert 1 <= allocated <= 0xFFFFFFFE

    def test_an_explicit_id_is_used_as_is(self) -> None:
        _kitty()
        image = _image(options=ImageOptions(max_width_cells=2, image_id=123))
        assert image.get_image_id() == 123
        assert ",i=123;" in image.render(10)[-1]
        assert image.get_image_id() == 123

    def test_the_allocated_id_is_reused_across_renders(self) -> None:
        _kitty()
        image = _image()
        image.render(10)
        first = image.get_image_id()
        image.invalidate()
        image.render(10)
        assert image.get_image_id() == first

    def test_iterm2_never_allocates_an_id(self) -> None:
        _iterm2()
        image = _image()
        image.render(10)
        assert image.get_image_id() is None

    def test_no_image_support_never_allocates_an_id(self) -> None:
        _no_images()
        image = _image()
        image.render(10)
        assert image.get_image_id() is None


class TestCaching:
    def test_a_second_render_at_the_same_width_returns_the_same_list(self) -> None:
        _kitty()
        image = _image(options=ImageOptions(max_width_cells=2, image_id=1))
        first = image.render(10)
        assert image.render(10) is first

    def test_a_different_width_re_renders(self) -> None:
        _kitty()
        image = _image(options=ImageOptions(image_id=1))
        # `min(width - 2, 60)`, so the width reaches the sequence as `c=`.
        assert ",c=8," in image.render(10)[-1]
        assert ",c=18," in image.render(20)[-1]

    def test_invalidate_drops_the_memo(self) -> None:
        _kitty()
        image = _image(options=ImageOptions(max_width_cells=2, image_id=1))
        first = image.render(10)
        image.invalidate()
        second = image.render(10)
        assert second == first
        assert second is not first

    def test_the_memo_survives_a_capability_change_until_invalidated(self) -> None:
        # Faithful to the TS: capabilities are read inside `render`, but the
        # cached frame is returned before that happens.
        _kitty()
        image = _image(options=ImageOptions(max_width_cells=2, image_id=1))
        rendered = image.render(10)
        _no_images()
        assert image.render(10) == rendered
        image.invalidate()
        assert image.render(10) == ["[Image: [image/png] 20x20]"]


class TestFallback:
    def test_no_image_protocol_prints_the_fallback(self) -> None:
        _no_images()
        assert _image().render(30) == ["[Image: [image/png] 20x20]"]

    def test_the_theme_colours_the_fallback(self) -> None:
        _no_images()
        assert _image(theme=RED).render(30) == ["\x1b[31m[Image: [image/png] 20x20]\x1b[39m"]

    def test_the_filename_is_included(self) -> None:
        _no_images()
        image = _image(options=ImageOptions(filename="cat.png"))
        assert image.render(40) == ["[Image: cat.png [image/png] 20x20]"]

    def test_unparseable_data_falls_back_to_800_by_600(self) -> None:
        _no_images()
        image = Image("AAAA", "image/png", PLAIN)
        assert image.render(40) == ["[Image: [image/png] 800x600]"]


class TestRenderedLines:
    def test_a_single_row_image_has_no_cursor_movement(self) -> None:
        _kitty()
        image = _image(
            options=ImageOptions(max_width_cells=2, image_id=1),
            dimensions=ImageDimensions(width_px=20, height_px=5),
        )
        lines = image.render(10)
        assert len(lines) == 1
        assert lines[0].startswith("\x1b_G")
        assert not lines[0].endswith("B")

    def test_a_multi_row_image_reserves_blank_lines_above_it(self) -> None:
        _kitty()
        image = _image(
            options=ImageOptions(max_width_cells=2, image_id=1),
            dimensions=ImageDimensions(width_px=20, height_px=40),
        )
        lines = image.render(10)
        assert len(lines) == 4
        assert lines[:3] == ["", "", ""]
        assert lines[3].startswith("\x1b[3A\x1b_G")
        assert lines[3].endswith("\x1b[3B")

    def test_iterm2_moves_up_but_not_back_down(self) -> None:
        # iTerm2 leaves the cursor where the image ends, so only Kitty — whose
        # own cursor movement this component disabled — needs the move back.
        _iterm2()
        image = _image(dimensions=ImageDimensions(width_px=20, height_px=40))
        lines = image.render(10)
        assert len(lines) == 4
        assert lines[3].startswith("\x1b[3A\x1b]1337;File=")
        assert lines[3].endswith("\x07")

    def test_explicit_dimensions_beat_the_parsed_payload(self) -> None:
        _no_images()
        image = Image(
            "AAAA",
            "image/png",
            PLAIN,
            ImageOptions(),
            ImageDimensions(width_px=3, height_px=4),
        )
        assert image.render(40) == ["[Image: [image/png] 3x4]"]

    def test_width_minus_two_caps_the_configured_max(self) -> None:
        _kitty()
        image = _image(options=ImageOptions(max_width_cells=60, image_id=1))
        assert ",c=4," in image.render(6)[-1]

    def test_the_configured_max_caps_the_width(self) -> None:
        _kitty()
        image = _image(options=ImageOptions(max_width_cells=3, image_id=1))
        assert ",c=3," in image.render(60)[-1]
