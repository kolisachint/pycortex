/**
 * Capture reference frames from the REAL hoocode TS TUI.
 *
 * This is the authority the Python port is checked against. It never
 * reimplements anything: it imports hoocode's own components and renderer,
 * drives them through the shared scenario corpus, and records what they
 * produce. Two artefacts per scenario:
 *
 *   - `lines`  — what a component's render(width) returned, verbatim
 *   - `stream` — every byte the renderer wrote to the terminal, per frame
 *
 * Both are replayed through the Python `Surface` at test time, so a golden
 * captures behaviour rather than a particular way of spelling it.
 *
 * Usage:  bun reference/dump.ts <hoocode-src-dir> <goldens-dir>
 * Driven by `uv run scripts/tui_goldens.py --refresh`.
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const [, , SRC_DIR, GOLDENS_DIR] = process.argv;
if (!SRC_DIR || !GOLDENS_DIR) {
	console.error("usage: bun reference/dump.ts <hoocode-tui-src> <goldens-dir>");
	process.exit(2);
}

const { Text, TruncatedText, Box, Spacer } = await import(join(SRC_DIR, "index.ts"));
const { TUI } = await import(join(SRC_DIR, "tui.ts"));

type Json = Record<string, any>;

const corpus: Json = JSON.parse(readFileSync(join(GOLDENS_DIR, "scenarios.json"), "utf8"));

/** Serialisable background function: `open + text + close`. */
function makeBgFn(spec: Json | undefined): ((text: string) => string) | undefined {
	if (!spec) return undefined;
	return (text: string) => `${spec.open}${text}${spec.close}`;
}

function build(spec: Json): any {
	const args = spec.args ?? {};
	const bg = makeBgFn(spec.bgFn);
	switch (spec.component) {
		case "Text":
			return new Text(args.text ?? "", args.paddingX ?? 1, args.paddingY ?? 1, bg);
		case "TruncatedText":
			return new TruncatedText(args.text ?? "", args.paddingX ?? 0, args.paddingY ?? 0);
		case "Spacer":
			return new Spacer(args.lines ?? 1);
		case "Box": {
			const box = new Box(args.paddingX ?? 1, args.paddingY ?? 1, bg);
			for (const child of spec.children ?? []) box.addChild(build(child));
			return box;
		}
		default:
			throw new Error(`unknown component: ${spec.component}`);
	}
}

/** Terminal stub that records writes and nothing else — same contract as the Python CaptureTerminal. */
class CaptureTerminal {
	writes: string[] = [];
	cursorHidden: boolean | null = null;
	constructor(
		public columns: number,
		public rows: number,
	) {}
	write(data: string): void {
		this.writes.push(data);
	}
	start(): void {}
	stop(): void {}
	hideCursor(): void {
		this.cursorHidden = true;
	}
	showCursor(): void {
		this.cursorHidden = false;
	}
	clearLine(): void {}
	clearFromCursor(): void {}
	clearScreen(): void {}
	moveBy(): void {}
	setTitle(): void {}
	setProgress(): void {}
	drainInput(): void {}
	get kittyProtocolActive(): boolean {
		return false;
	}
	take(): string {
		const out = this.writes.join("");
		this.writes = [];
		return out;
	}
}

// `doRender` is private and the public path is debounced through timers, which
// would make captures non-deterministic. Reach for it directly: the scenarios
// are about what a frame looks like, not about when it is scheduled.
function renderNow(tui: any): void {
	tui.doRender();
}

const componentGoldens: Json[] = [];
for (const spec of corpus.components ?? []) {
	const component = build(spec);
	const lines: string[] = component.render(spec.width);
	// Render twice: components memoize, and a stale cache is a real bug class.
	const second: string[] = component.render(spec.width);
	componentGoldens.push({
		id: spec.id,
		width: spec.width,
		lines,
		stable: JSON.stringify(lines) === JSON.stringify(second),
		referenceIdentical: lines === second,
	});
}

const rendererGoldens: Json[] = [];
for (const spec of corpus.renderer ?? []) {
	const terminal = new CaptureTerminal(spec.cols, spec.rows);
	const tui = new TUI(terminal as any, false);
	const children: any[] = [];
	const frames: Json[] = [];
	let overlayHandle: any = null;
	let frameIndex = 0;

	for (const step of spec.steps as Json[]) {
		switch (step.op) {
			case "add": {
				const component = build(step);
				children.push(component);
				tui.addChild(component);
				break;
			}
			case "setText":
				children[step.target].setText(step.text);
				break;
			case "resize":
				terminal.columns = step.cols;
				terminal.rows = step.rows;
				break;
			case "overlay":
				overlayHandle = tui.showOverlay(build(step), step.options ?? {});
				terminal.take(); // showOverlay hides the cursor; not part of the frame
				break;
			case "hideOverlay":
				if (overlayHandle) overlayHandle.hide();
				overlayHandle = null;
				terminal.take();
				break;
			case "render": {
				renderNow(tui);
				frames.push({
					index: frameIndex++,
					cols: terminal.columns,
					rows: terminal.rows,
					stream: terminal.take(),
					lines: tui.render(terminal.columns),
				});
				break;
			}
			default:
				throw new Error(`unknown renderer op: ${step.op}`);
		}
	}

	rendererGoldens.push({ id: spec.id, cols: spec.cols, rows: spec.rows, frames });
}

mkdirSync(GOLDENS_DIR, { recursive: true });
writeFileSync(
	join(GOLDENS_DIR, "ts-components.json"),
	`${JSON.stringify({ source: "hoocode TS", scenarios: componentGoldens }, null, 2)}\n`,
);
writeFileSync(
	join(GOLDENS_DIR, "ts-renderer.json"),
	`${JSON.stringify({ source: "hoocode TS", scenarios: rendererGoldens }, null, 2)}\n`,
);
console.log(
	`captured ${componentGoldens.length} component + ${rendererGoldens.length} renderer goldens`,
);
