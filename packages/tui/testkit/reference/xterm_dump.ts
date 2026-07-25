/**
 * Cross-validate the Python `Surface` against a real terminal emulator.
 *
 * The Surface is only useful as an authority if it behaves like a terminal.
 * Proving that against a hand-written expectation would just move the guessing
 * one level up, so instead every ANSI scenario in the corpus is fed to
 * `@xterm/headless` — a production emulator — and its resulting grid is stored
 * as the golden. `test_surface_xterm.py` asserts the Python Surface lands on the
 * same grid.
 *
 * Usage: bun reference/xterm_dump.ts <goldens-dir>
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Terminal } from "@xterm/headless";

const [, , GOLDENS_DIR] = process.argv;
if (!GOLDENS_DIR) {
	console.error("usage: bun reference/xterm_dump.ts <goldens-dir>");
	process.exit(2);
}

type Json = Record<string, any>;
const corpus: Json = JSON.parse(readFileSync(join(GOLDENS_DIR, "scenarios.json"), "utf8"));

function writeAndSettle(term: Terminal, data: string): Promise<void> {
	return new Promise((resolve) => term.write(data, () => resolve()));
}

const scenarios: Json[] = [];
for (const spec of corpus.ansi as Json[]) {
	const term = new Terminal({
		cols: spec.cols,
		rows: spec.rows,
		allowProposedApi: true,
		scrollback: 200,
	});
	await writeAndSettle(term, spec.data);

	const buffer = term.buffer.active;
	const rows: Json[] = [];
	for (let y = 0; y < spec.rows; y++) {
		const line = buffer.getLine(buffer.viewportY + y);
		const chars: string[] = [];
		const widths: number[] = [];
		const styles: Json[] = [];
		for (let x = 0; x < spec.cols; x++) {
			const cell = line?.getCell(x);
			chars.push(cell?.getChars() ?? "");
			widths.push(cell?.getWidth() ?? 1);
			styles.push({
				bold: Boolean(cell?.isBold()),
				dim: Boolean(cell?.isDim()),
				italic: Boolean(cell?.isItalic()),
				underline: Boolean(cell?.isUnderline()),
				inverse: Boolean(cell?.isInverse()),
				strike: Boolean(cell?.isStrikethrough()),
				// Colour is reported in xterm's own encoding; normalise to
				// "default" | palette index | #rrggbb so the Python side can
				// compare without knowing xterm internals.
				fg: cell?.isFgDefault() ? null : cell?.isFgRGB() ? rgb(cell.getFgColor()) : (cell?.getFgColor() ?? null),
				bg: cell?.isBgDefault() ? null : cell?.isBgRGB() ? rgb(cell.getBgColor()) : (cell?.getBgColor() ?? null),
			});
		}
		rows.push({ chars, widths, styles, text: line?.translateToString(false) ?? "" });
	}

	scenarios.push({
		id: spec.id,
		cols: spec.cols,
		rows: spec.rows,
		cursor: { row: buffer.cursorY, col: buffer.cursorX },
		grid: rows,
	});
	term.dispose();
}

function rgb(value: number): [number, number, number] {
	return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff];
}

mkdirSync(GOLDENS_DIR, { recursive: true });
writeFileSync(
	join(GOLDENS_DIR, "xterm-grids.json"),
	`${JSON.stringify({ source: "@xterm/headless", scenarios }, null, 2)}\n`,
);
console.log(`captured ${scenarios.length} xterm grids`);
