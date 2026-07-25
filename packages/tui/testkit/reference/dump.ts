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
const { Loader } = await import(join(SRC_DIR, "components/loader.ts"));
const { CancellableLoader } = await import(join(SRC_DIR, "components/cancellable-loader.ts"));
const { Input } = await import(join(SRC_DIR, "components/input.ts"));
const { SelectList } = await import(join(SRC_DIR, "components/select-list.ts"));
const { SettingsList } = await import(join(SRC_DIR, "components/settings-list.ts"));
const { Markdown } = await import(join(SRC_DIR, "components/markdown.ts"));
const { Editor } = await import(join(SRC_DIR, "components/editor.ts"));
const { setCapabilities } = await import(join(SRC_DIR, "terminal-image.ts"));

type Json = Record<string, any>;

const corpus: Json = JSON.parse(readFileSync(join(GOLDENS_DIR, "scenarios.json"), "utf8"));

/** Serialisable background function: `open + text + close`. */
function makeBgFn(spec: Json | undefined): ((text: string) => string) | undefined {
	if (!spec) return undefined;
	return (text: string) => `${spec.open}${text}${spec.close}`;
}

/** Same idea for the loader's colour functions; identity when unspecified. */
function makeWrapFn(spec: Json | undefined): (text: string) => string {
	if (!spec) return (text: string) => text;
	return (text: string) => `${spec.open}${text}${spec.close}`;
}

/**
 * Serialisable stand-in for the app's markdown theme, mirroring the chalk
 * colours `test/test-themes.ts` uses at chalk level 3. Kept as data so the
 * Python builder can construct the identical functions; every element gets a
 * *distinct* code so a mis-applied style shows up on the surface instead of
 * blending in.
 */
const DEFAULT_MARKDOWN_THEME: Json = {
	heading: { open: "\x1b[1m\x1b[36m", close: "\x1b[39m\x1b[22m" },
	link: { open: "\x1b[34m", close: "\x1b[39m" },
	linkUrl: { open: "\x1b[2m", close: "\x1b[22m" },
	code: { open: "\x1b[33m", close: "\x1b[39m" },
	codeBlock: { open: "\x1b[32m", close: "\x1b[39m" },
	codeBlockBorder: { open: "\x1b[2m", close: "\x1b[22m" },
	quote: { open: "\x1b[35m", close: "\x1b[39m" },
	quoteBorder: { open: "\x1b[2m", close: "\x1b[22m" },
	hr: { open: "\x1b[2m", close: "\x1b[22m" },
	listBullet: { open: "\x1b[36m", close: "\x1b[39m" },
	bold: { open: "\x1b[1m", close: "\x1b[22m" },
	italic: { open: "\x1b[3m", close: "\x1b[23m" },
	strikethrough: { open: "\x1b[9m", close: "\x1b[29m" },
	underline: { open: "\x1b[4m", close: "\x1b[24m" },
};

/** Capabilities the goldens are captured under, so the capture machine's
 * TERM_PROGRAM cannot change what is recorded. */
const DEFAULT_CAPABILITIES: Json = { images: null, trueColor: true, hyperlinks: false };

function makeMarkdownTheme(args: Json): Json {
	const spec: Json = { ...DEFAULT_MARKDOWN_THEME, ...(args.theme ?? {}) };
	const theme: Json = {};
	for (const key of Object.keys(DEFAULT_MARKDOWN_THEME)) theme[key] = makeWrapFn(spec[key]);
	if (args.highlightCode) {
		const hl = args.highlightCode;
		// Test double for a syntax highlighter: one styled line per code line,
		// tagged with the language so a dropped `lang` argument is visible.
		theme.highlightCode = (code: string, lang?: string) =>
			code.split("\n").map((line: string) => `${hl.open}${lang ?? ""}|${line}${hl.close}`);
	}
	if (args.codeBlockIndent !== undefined) theme.codeBlockIndent = args.codeBlockIndent;
	return theme;
}

function makeDefaultTextStyle(spec: Json | undefined): Json | undefined {
	if (!spec) return undefined;
	return {
		color: spec.color ? makeWrapFn(spec.color) : undefined,
		bgColor: spec.bgColor ? makeWrapFn(spec.bgColor) : undefined,
		bold: spec.bold ?? false,
		italic: spec.italic ?? false,
		strikethrough: spec.strikethrough ?? false,
		underline: spec.underline ?? false,
	};
}

/** The select-list theme, shared by `SelectList` and the editor's autocomplete. */
function makeSelectListTheme(spec: Json | undefined): Json {
	const theme: Json = spec ?? {};
	return {
		selectedPrefix: makeWrapFn(theme.selectedPrefix),
		selectedText: makeWrapFn(theme.selectedText),
		description: makeWrapFn(theme.description),
		scrollInfo: makeWrapFn(theme.scrollInfo),
		noMatch: makeWrapFn(theme.noMatch),
	};
}

/** `test-themes.ts` builds the editor border from `chalk.dim`. */
const DEFAULT_EDITOR_BORDER: Json = { open: "\x1b[2m", close: "\x1b[22m" };

/** Theme fn taking (text, selected) — the settings-list label/value shape. */
function makeThemePairFn(spec: Json | undefined): (text: string, selected: boolean) => string {
	if (!spec) return (text: string) => text;
	return (text: string, selected: boolean) =>
		selected ? `${spec.open}${text}${spec.close}` : text;
}

/**
 * Fixed-line component, the counterpart of hoocode's own `StaticOverlay` test
 * double. Lets scenarios exercise the renderer (cursor markers, ANSI runs, wide
 * characters, exact-width lines) without waiting on components that are still
 * unported. Cached so repeated renders stay reference-stable — the root's
 * memoization keys on array identity, so a fresh array every frame would
 * silently disable the patch path this is meant to test.
 */
class StaticLines {
	private cached: string[];
	constructor(lines: string[]) {
		this.cached = lines;
	}
	setLines(lines: string[]): void {
		this.cached = lines;
	}
	render(_width: number): string[] {
		return this.cached;
	}
	invalidate(): void {}
}

function build(spec: Json): any {
	const args = spec.args ?? {};
	const bg = makeBgFn(spec.bgFn);
	switch (spec.component) {
		case "StaticLines":
			return new StaticLines(args.lines ?? []);
		case "SelectList": {
			const list = new SelectList(
				args.items ?? [],
				args.maxVisible ?? 5,
				makeSelectListTheme(args.theme),
				args.layout ?? {},
			);
			if (args.filter !== undefined) list.setFilter(args.filter);
			if (args.selectedIndex !== undefined) list.setSelectedIndex(args.selectedIndex);
			return list;
		}
		case "SettingsList": {
			const list = new SettingsList(
				args.items ?? [],
				args.maxVisible ?? 5,
				{
					label: makeThemePairFn(args.theme?.label),
					value: makeThemePairFn(args.theme?.value),
					description: makeWrapFn(args.theme?.description),
					cursor: args.theme?.cursor ?? "> ",
					hint: makeWrapFn(args.theme?.hint),
				},
				() => {},
				() => {},
				args.options ?? {},
			);
			return list;
		}
		case "Editor": {
			// The editor reads `tui.terminal.rows` (30% of it caps the visible
			// lines) and calls `requestRender`, so it needs a real TUI. The
			// terminal is the same recording stub the renderer scenarios use;
			// nothing it writes is part of a component golden.
			const terminal = new CaptureTerminal(args.cols ?? spec.width, args.rows ?? 24);
			const tui = new TUI(terminal as any, false);
			const editor = new Editor(
				tui,
				{
					borderColor: makeWrapFn(args.theme?.borderColor ?? DEFAULT_EDITOR_BORDER),
					selectList: makeSelectListTheme(args.theme?.selectList),
				},
				{ paddingX: args.paddingX, autocompleteMaxVisible: args.autocompleteMaxVisible },
			);
			if (args.text !== undefined) editor.setText(args.text);
			if (args.promptPrefix !== undefined) editor.promptPrefix = args.promptPrefix;
			if (args.promptColor !== undefined) editor.promptColor = makeWrapFn(args.promptColor);
			if (args.disableSubmit) editor.disableSubmit = true;
			return editor;
		}
		case "Input": {
			const input = new Input();
			if (args.value !== undefined) input.setValue(args.value);
			return input;
		}
		case "Loader":
		case "CancellableLoader": {
			// `ui: null` keeps the component out of the render loop — the frame
			// is what is under test, not the animation timer, and the corpus
			// pins `currentFrame` at 0 by never letting the interval fire.
			const Ctor = spec.component === "Loader" ? Loader : CancellableLoader;
			const loader = new Ctor(
				null,
				makeWrapFn(args.spinnerColor),
				makeWrapFn(args.messageColor),
				args.message ?? "Loading...",
				args.indicator,
			);
			loader.stop();
			return loader;
		}
		case "Markdown":
			return new Markdown(
				args.text ?? "",
				args.paddingX ?? 1,
				args.paddingY ?? 1,
				makeMarkdownTheme(args),
				makeDefaultTextStyle(args.defaultTextStyle),
			);
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
	// Terminal capabilities are read at render time (markdown links pick OSC 8
	// over `text (url)` from them), so pin them per scenario instead of
	// inheriting whatever terminal the capture ran in.
	setCapabilities({ ...DEFAULT_CAPABILITIES, ...(spec.capabilities ?? {}) });
	const component = build(spec);
	// Stateful components (Input) are driven to the state under test before the
	// frame is captured.
	if (spec.focused) component.focused = true;
	for (const key of spec.keys ?? []) component.handleInput(key);
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
setCapabilities({ ...DEFAULT_CAPABILITIES });
for (const spec of corpus.renderer ?? []) {
	const terminal = new CaptureTerminal(spec.cols, spec.rows);
	const tui = new TUI(terminal as any, false);
	const children: any[] = [];
	const frames: Json[] = [];
	const overlayHandles: any[] = [];
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
			case "setLines":
				children[step.target].setLines(step.lines);
				break;
			case "removeChild":
				tui.removeChild(children[step.target]);
				break;
			case "clearOnShrink":
				tui.setClearOnShrink(step.enabled);
				break;
			case "overlayHandle": {
				const handle = overlayHandles[step.target];
				if (step.action === "setHidden") handle.setHidden(step.hidden);
				else if (step.action === "hide") handle.hide();
				else if (step.action === "focus") handle.focus();
				terminal.take();
				break;
			}
			case "resize":
				terminal.columns = step.cols;
				terminal.rows = step.rows;
				break;
			case "overlay":
				overlayHandle = tui.showOverlay(build(step), step.options ?? {});
				overlayHandles.push(overlayHandle);
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
