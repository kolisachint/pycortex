/**
 * Capture reference Markdown ASTs from the REAL `marked`.
 *
 * `markdown.ts` is written against marked's token tree; pycortex parses with
 * mistune and normalises. That normalisation is only trustworthy if it is
 * checked against marked itself, so this dumps marked's tokens for the shared
 * corpus and `test_markdown_ast.py` asserts the adapter reproduces them.
 *
 * Only the fields `markdown.ts` actually reads are recorded — `raw` and offsets
 * are marked-internal bookkeeping that no renderer branch consults, and pinning
 * them would fail the port for differences nobody can see.
 *
 * Usage: bun reference/markdown_ast_dump.ts <hoocode-tui-src> <goldens-dir>
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Marked, Tokenizer } from "marked";

const [, , SRC_DIR, GOLDENS_DIR] = process.argv;
if (!SRC_DIR || !GOLDENS_DIR) {
	console.error("usage: bun reference/markdown_ast_dump.ts <hoocode-tui-src> <goldens-dir>");
	process.exit(2);
}

type Json = Record<string, any>;

// markdown.ts installs this tokenizer, so the goldens must be captured with it:
// stock marked accepts `~~ x ~~` and other loose forms this rejects.
const STRICT_STRIKETHROUGH_REGEX = /^(~~)(?=[^\s~])((?:\\.|[^\\])*?(?:\\.|[^\s~\\]))\1(?=[^~]|$)/;

class StrictStrikethroughTokenizer extends Tokenizer {
	override del(src: string): any {
		const match = STRICT_STRIKETHROUGH_REGEX.exec(src);
		if (!match) return undefined;
		const text = match[2];
		return { type: "del", raw: match[0], text, tokens: (this as any).lexer.inlineTokens(text) };
	}
}

const markdownParser = new Marked();
markdownParser.setOptions({ tokenizer: new StrictStrikethroughTokenizer() });

/** Keep only what a renderer branch in markdown.ts reads. */
function normalize(token: Json): Json {
	const out: Json = { type: token.type };

	switch (token.type) {
		case "heading":
			out.depth = token.depth;
			out.tokens = (token.tokens ?? []).map(normalize);
			break;
		case "code":
			out.lang = token.lang ?? "";
			out.text = token.text;
			break;
		case "list":
			out.ordered = Boolean(token.ordered);
			out.start = token.start === "" ? "" : token.start;
			out.items = (token.items ?? []).map((item: Json) => ({
				type: "list_item",
				task: Boolean(item.task),
				checked: item.task ? Boolean(item.checked) : undefined,
				tokens: (item.tokens ?? []).map(normalize),
			}));
			break;
		case "table":
			out.align = token.align ?? [];
			out.header = (token.header ?? []).map((cell: Json) => ({
				tokens: (cell.tokens ?? []).map(normalize),
			}));
			out.rows = (token.rows ?? []).map((row: Json[]) =>
				row.map((cell: Json) => ({ tokens: (cell.tokens ?? []).map(normalize) })),
			);
			break;
		case "link":
			out.href = token.href;
			out.text = token.text;
			out.tokens = (token.tokens ?? []).map(normalize);
			break;
		case "codespan":
		case "text":
			out.text = token.text;
			if (token.tokens) out.tokens = token.tokens.map(normalize);
			break;
		case "html":
			out.raw = token.raw;
			break;
		case "br":
		case "hr":
		case "space":
			break;
		default:
			// paragraph, blockquote, strong, em, del: children only.
			if (token.tokens) out.tokens = token.tokens.map(normalize);
			if (token.text !== undefined && !token.tokens) out.text = token.text;
	}
	return out;
}

const corpus: Json = JSON.parse(readFileSync(join(GOLDENS_DIR, "markdown-corpus.json"), "utf8"));
const scenarios = (corpus.samples as Json[]).map((sample) => ({
	id: sample.id,
	source: sample.source,
	// markdown.ts normalises tabs before lexing; the goldens must match.
	tokens: markdownParser.lexer(sample.source.replace(/\t/g, "   ")).map(normalize),
}));

mkdirSync(GOLDENS_DIR, { recursive: true });
writeFileSync(
	join(GOLDENS_DIR, "marked-ast.json"),
	`${JSON.stringify({ source: "marked (hoocode's StrictStrikethroughTokenizer)", scenarios }, null, 2)}\n`,
);
console.log(`captured ${scenarios.length} marked ASTs`);
