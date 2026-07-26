# cortexcode-tui-images

Inline terminal images for `cortex.tui`. Port of hoocode's
`packages/tui/src/terminal-image.ts` and `components/image.ts`.

```python
from cortex.tui.images import (
    Image,
    ImageRenderOptions,
    get_capabilities,
    get_image_dimensions,
    render_image,
)

caps = get_capabilities()  # cached probe: kitty? iTerm2? neither?
caps.protocol  # ImageProtocol — picks the encoding below

dims = get_image_dimensions(data_base64, "image/png")
rendered = render_image(data_base64, dims, ImageRenderOptions(max_width_cells=60))
rendered.sequence  # the escape sequence to write
rendered.rows  # how many terminal rows it occupies

Image(data_base64, "image/png", theme)  # the same thing as a TUI component
```

Two wire protocols are implemented — kitty graphics (`encode_kitty`, plus the
`allocate_image_id` / `delete_kitty_image` bookkeeping the renderer needs to
replace an image in place) and iTerm2 (`encode_iterm2`) — with `image_fallback`
for terminals that support neither. Dimensions are read straight from the
encoded bytes (`get_png_dimensions`, `get_jpeg_dimensions`, `get_gif_dimensions`,
`get_webp_dimensions`), so nothing has to be decoded to lay a frame out.

`is_image_line`, `hyperlink`, `get_cell_dimensions` and `calculate_image_rows`
are here rather than in `util` because `render` and `components/markdown` import
them directly — keeping them here is what stops that edge from becoming a cycle.

The leaf is dependency-free on purpose: every tier above it may depend on it.

> A caveat worth knowing when reading the tests: the parity `Surface` is
> structurally blind to images. Kitty payloads are APC and iTerm2 ones are OSC,
> and a terminal paints no cell for either — so image behaviour is covered by
> unit tests over the encoders, not by screen diffs.

```bash
uv run pytest packages/tui/images
```
