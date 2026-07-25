/**
 * Capture autocomplete results from the REAL hoocode `autocomplete.ts`.
 *
 * `autocomplete.ts` renders nothing, so the surface harness cannot see it; its
 * contract is the suggestion list and the text `applyCompletion` produces. Both
 * are data, so this dumps them for the shared corpus and
 * `test_autocomplete_parity.py` asserts the port reproduces them exactly.
 *
 * Every scenario builds its own tree under <tmp>/<id>/{cwd,outside,home}, with
 * the provider's basePath at `cwd` and HOME at `home`, so nothing depends on the
 * machine it runs on. `{root}`, `{cwd}` and `{home}` are substituted into the
 * scenario's line and back out of every captured string.
 *
 * Each scenario runs in its own child process: `os.homedir()` under bun is read
 * once at startup, so `~` scenarios can only be pinned by setting HOME in the
 * child's environment. The parent builds nothing and only aggregates.
 *
 * Usage: bun reference/autocomplete_dump.ts <hoocode-tui-src> <goldens-dir>
 */

import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

const [, , SRC_DIR, GOLDENS_DIR, SCENARIO_ID, SCENARIO_ROOT] = process.argv;
if (!SRC_DIR || !GOLDENS_DIR) {
	console.error("usage: bun reference/autocomplete_dump.ts <hoocode-tui-src> <goldens-dir>");
	process.exit(2);
}

type Json = Record<string, any>;

type TreeSpec = {
	dirs?: string[];
	files?: Record<string, string>;
	symlinks?: Record<string, string>;
};

type Scenario = {
	id: string;
	line: string;
	cursorFromEnd?: number;
	force?: boolean;
	fd?: boolean;
	commands?: Json[];
	tree?: TreeSpec;
	outsideTree?: TreeSpec;
	homeTree?: TreeSpec;
};

const CORPUS = JSON.parse(readFileSync(join(GOLDENS_DIR, "autocomplete-corpus.json"), "utf-8"));
const SCENARIOS: Scenario[] = CORPUS.scenarios;

function resolveFdPath(): string | null {
	for (const candidate of ["fd", "fdfind"]) {
		const result = spawnSync("which", [candidate], { encoding: "utf-8" });
		if (result.status === 0 && result.stdout) {
			const firstLine = result.stdout.split(/\r?\n/).find(Boolean);
			if (firstLine) return firstLine.trim();
		}
	}
	return null;
}

function buildTree(base: string, spec: TreeSpec = {}): void {
	mkdirSync(base, { recursive: true });
	for (const dir of spec.dirs ?? []) {
		mkdirSync(join(base, dir), { recursive: true });
	}
	for (const [filePath, contents] of Object.entries(spec.files ?? {})) {
		const fullPath = join(base, filePath);
		mkdirSync(dirname(fullPath), { recursive: true });
		writeFileSync(fullPath, contents);
	}
	for (const [linkPath, target] of Object.entries(spec.symlinks ?? {})) {
		const fullPath = join(base, linkPath);
		mkdirSync(dirname(fullPath), { recursive: true });
		symlinkSync(target, fullPath);
	}
}

/** Commands are JSON, so `getArgumentCompletions` is described rather than written. */
function buildCommand(spec: Json): Json {
	const { argumentCompletions, ...rest } = spec;
	if (!argumentCompletions) return rest;

	const kind = argumentCompletions.kind;
	const items = argumentCompletions.items ?? [];
	if (kind === "async") {
		return { ...rest, getArgumentCompletions: async () => items };
	}
	if (kind === "sync") {
		return { ...rest, getArgumentCompletions: () => items };
	}
	if (kind === "null") {
		return { ...rest, getArgumentCompletions: async () => null };
	}
	// "invalid": the TS guards with `Array.isArray`, so this is the value that trips it.
	return { ...rest, getArgumentCompletions: (() => "not-an-array") as any };
}

async function captureOne(scenario: Scenario, root: string, fdPath: string): Promise<Json> {
	const { CombinedAutocompleteProvider } = await import(join(SRC_DIR, "autocomplete.ts"));

	const cwd = join(root, "cwd");
	const outside = join(root, "outside");
	const home = join(root, "home");
	buildTree(cwd, scenario.tree);
	buildTree(outside, scenario.outsideTree);
	buildTree(home, scenario.homeTree);

	const substitute = (value: string): string =>
		value.replaceAll("{cwd}", cwd).replaceAll("{home}", home).replaceAll("{root}", root);
	const unsubstitute = (value: string): string =>
		value.replaceAll(cwd, "{cwd}").replaceAll(home, "{home}").replaceAll(root, "{root}");

	const line = substitute(scenario.line);
	const cursorCol = scenario.cursorFromEnd ? line.length - scenario.cursorFromEnd : line.length;
	const commands = (scenario.commands ?? []).map(buildCommand);
	const provider = new CombinedAutocompleteProvider(commands, cwd, scenario.fd ? fdPath : null);

	// `fd` walks in parallel, so the order of equally-scored entries is its own
	// business and cannot be pinned. Record what the real `scoreEntry` was asked
	// and what it answered instead: that is the part of the ranking that is a
	// contract, and it is observed from the TS rather than recomputed.
	const scoreCalls: Json[] = [];
	const scoreEntry = (provider as any).scoreEntry.bind(provider);
	(provider as any).scoreEntry = (filePath: string, query: string, isDirectory: boolean) => {
		const score = scoreEntry(filePath, query, isDirectory);
		scoreCalls.push({ path: filePath, query, isDirectory, score });
		return score;
	};

	const result = await provider.getSuggestions([line], 0, cursorCol, {
		signal: new AbortController().signal,
		force: scenario.force ?? false,
	});

	const record: Json = {
		id: scenario.id,
		suggestions: null,
		applied: [],
		shouldTriggerFileCompletion: provider.shouldTriggerFileCompletion([line], 0, cursorCol),
		scoreCalls: scoreCalls.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0)),
	};
	if (result) {
		record.suggestions = {
			prefix: unsubstitute(result.prefix),
			items: result.items.map((item: Json) => ({
				value: unsubstitute(item.value),
				label: unsubstitute(item.label),
				description: item.description === undefined ? null : unsubstitute(item.description),
			})),
		};
		record.applied = result.items.map((item: Json) => {
			const applied = provider.applyCompletion([line], 0, cursorCol, item, result.prefix);
			const cursorLineText: string = applied.lines[applied.cursorLine] ?? "";
			return {
				value: unsubstitute(item.value),
				lines: applied.lines.map(unsubstitute),
				cursorLine: applied.cursorLine,
				// Measured in the placeholder-rendered line: the raw column counts
				// the characters of a temp path, which differs between the machine
				// that captured this and the one replaying it.
				cursorCol: unsubstitute(cursorLineText.slice(0, applied.cursorCol)).length,
			};
		});
	}
	return record;
}

const fdPath = resolveFdPath();
if (!fdPath) {
	console.error("fd is required to capture the @-suggestion scenarios");
	process.exit(2);
}

if (SCENARIO_ID && SCENARIO_ROOT) {
	// Child: one scenario, with HOME already pointing at its own tree.
	const scenario = SCENARIOS.find((candidate) => candidate.id === SCENARIO_ID);
	if (!scenario) {
		console.error(`unknown scenario: ${SCENARIO_ID}`);
		process.exit(2);
	}
	process.stdout.write(JSON.stringify(await captureOne(scenario, SCENARIO_ROOT, fdPath)));
} else {
	const tmpRoot = mkdtempSync(join(tmpdir(), "cortex-autocomplete-"));
	const captured: Json[] = [];
	try {
		for (const scenario of SCENARIOS) {
			const root = join(tmpRoot, scenario.id.replaceAll("/", "__"));
			const child = spawnSync(
				process.execPath,
				[process.argv[1]!, SRC_DIR, GOLDENS_DIR, scenario.id, root],
				{ encoding: "utf-8", env: { ...process.env, HOME: join(root, "home") } },
			);
			if (child.status !== 0) {
				console.error(child.stderr);
				throw new Error(`scenario failed: ${scenario.id}`);
			}
			captured.push(JSON.parse(child.stdout));
		}
	} finally {
		rmSync(tmpRoot, { recursive: true, force: true });
	}

	mkdirSync(GOLDENS_DIR, { recursive: true });
	writeFileSync(
		join(GOLDENS_DIR, "ts-autocomplete.json"),
		`${JSON.stringify(
			{
				note: "Captured from the real hoocode autocomplete.ts by reference/autocomplete_dump.ts. Do not edit.",
				scenarios: captured,
			},
			null,
			"\t",
		)}\n`,
	);
	console.log(`captured ${captured.length} autocomplete scenarios`);
}
