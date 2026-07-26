# Migration Notes (pycortex ← hoocode)

Living doc. Update after every step. Speeds up future migrations by recording
conventions, gotchas, and the mechanical mappings that recur.

## Workflow (orchestrator mode)
- `uv run scripts/migrate_next.py --start` → emits next step brief.
- Orchestrator: read source + deps, split code-gen + unit tests to sonnet subagents
  (`general-purpose`), then REVIEW + run integration/gates yourself.
- Gates (all must be green):
  `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyright packages`
- `uv run scripts/migrate_next.py --done <id>` — re-runs gates, flips checkbox,
  commits `migrate: <id> <title>`. ≤1 step per session.

## Repo layout
- Source (TS, never edit): resolved, in order, from `$CORTEX_MIGRATION_SRC`,
  `~/github/hoocode`, then a shallow clone cached at
  `~/.cache/cortex-migration/hoocode`. Do NOT hardcode a laptop path — on CI and in
  containers only the clone cache exists, and only after the driver has run.
- Target (Python): this repo.
- Each leaf = its own package under `packages/<group>/<leaf>/`
  - code: `src/cortex/<group>/<...>/`  (namespace package, no top-level __init__)
  - tests: `<leaf>/tests/test_*.py`
  - `pyproject.toml` with `[tool.uv.sources.*] workspace = true` for internal deps
- Plan: `docs/04-migration-plan.md`; arch + package map: `docs/02-target-architecture.md`

## Package/module mapping (ai group) — IMPORTANT
TS `packages/ai/src/` is split across MULTIPLE python packages:
- `types.ts`                → `cortexcode-ai-types`  → `cortex.ai.types`
- `api-registry.ts`         → `cortexcode-ai-models` → `cortex.ai.models` (api_registry.py)
- `stream.ts`, `utils/event-stream.ts` → `cortexcode-ai-stream` → `cortex.ai.stream`
- `env.ts`                  → `cortex.ai.env`
- `providers/faux.ts`       → `cortexcode-ai-provider-faux` → `cortex.ai.providers.faux`
- So a TS import `../api-registry.js` maps to `from cortex.ai.models import ...`
  and `../utils/event-stream.js` → `from cortex.ai.stream import ...`.

## Type mappings (see docs/02 §4)
- typebox / interface → pydantic v2 model (`from __future__ import annotations`)
- `Promise<T>` → `async def -> T`
- discriminated union w/ `type` field → pydantic models + `Literal` tag; events use `.type`
- `undefined`/`null` → `None`
- camelCase public names → snake_case (`getTokens`→`get_tokens`, `setResponses`→`set_responses`)
- Keep file structure 1:1 with TS for diffability.

## Known building blocks (already ported)
- `create_assistant_message_event_stream()` → `AssistantMessageEventStream`
  (in `cortex.ai.stream`). Producer: `.push(event)`, `.end(result)`. Consumer:
  `async for`, `await s.result()`. Complete events: type in ("done","error").
- Event models in `cortex.ai.types`: StartEvent, TextStartEvent, TextDeltaEvent,
  TextEndEvent, ThinkingStartEvent/Delta/End, ToolCallStartEvent/Delta/End,
  DoneEvent(reason, message), ErrorEvent(reason, error). Access tag via `.type`.
- `register_api_provider(ApiProvider(api, stream, stream_simple), source_id)`,
  `unregister_api_providers(source_id)`, `clear_api_providers()` in `cortex.ai.models`.
  ApiProvider wraps stream fns; wrapper validates `model.api == api`.
- Content types: TextContent(type="text", text), ThinkingContent(type="thinking",
  thinking), ToolCall(type="toolCall", id, name, arguments), ImageContent(type="image",
  mimeType, data). Messages: UserMessage, AssistantMessage(role,content,api,provider,
  model,usage,stopReason,errorMessage?,responseId?,timestamp), ToolResultMessage
  (toolName, content). Usage(input,output,cacheRead,cacheWrite,totalTokens,cost).

## Test conventions
- pytest, `asyncio_mode=auto` (async def tests need no decorator).
- One TS test file → one `test_*.py`. Keep names/order aligned.
- No real API keys; use faux provider / fixtures.
- Reset global registries between tests (`clear_api_providers()` in a fixture).

## Gotchas
- `structuredClone` → deep copy (pydantic `model_copy(deep=True)`).
- `Date.now()` → `time.time()`*1000 or int(time.time()*1000) (ms). Check usage.
- `queueMicrotask` → `asyncio.ensure_future`/`create_task` or `await asyncio.sleep(0)`.
- `Math.random()` streaming chunk sizes → keep behavior; tests must not assume exact splits.
- `JSON.stringify` → `json.dumps` (mind separators/key order for token estimates).

## Orchestration playbook (worked well for 2.6)
- Do infra yourself FIRST (skeleton dirs, pyproject deps) — deterministic, avoids
  subagent guesswork. Then dispatch code-gen + tests to sonnet in parallel.
- CRITICAL: give both subagents the SAME public API contract (exact signatures) or
  they diverge. In 2.6 the module used options-objects while tests used kwargs —
  had to reconcile. Decide kwargs-vs-object up front and state it in BOTH prompts.
- Subagents may "timeout" but still have written their files — always check disk
  (`find <pkg> -type f`) before re-dispatching.
- Orchestrator owns: reconciling API mismatches, pyright strict cleanup, integration.
- Sonnet subagents should NOT be trusted to run full gates; they lint/format their
  own files only. You run pytest+ruff+pyright and the cross-package integration.

## Repo gotchas (confirmed)
- `uv run pytest` (whole repo) FAILS to collect: every leaf has `tests/__init__.py`
  with no unique package name → `ModuleNotFoundError: No module named 'tests.test_x'`
  (duplicate basename collision). This is PRE-EXISTING. The migrate gate runs
  TARGETED pytest per package (`pytest packages/ai/<leaf>`) which works. For
  integration, run each package's pytest separately in a loop.
- pyright is `strict`. Common fixes: `@overload` for get_model() (no-arg → Model,
  arg → Model|None); `# pyright: ignore[reportPrivateUsage]` on the IMPORTED NAME
  line (not the paren) when tests import `_helpers`; `# pyright: ignore[reportUnusedFunction]`
  on autouse fixtures; `cast(T, x)` for callable-step results typed `object`.
- Registered stream-fn options convention: type as `dict[str, Any] | None` (matches
  stream.py + test_stream.py). Faux reads options via a `_get_opt(options, key)`
  helper that accepts BOTH dicts and objects — but tests should pass dicts to stay
  pyright-clean, e.g. `complete(model, ctx, {"session_id": "s1", "cache_retention": "short"})`.
- Abort signal: no AbortSignal type in Python port. Represent as any object with a
  bool `.aborted`; faux checks `getattr(signal, "aborted", False)`. StreamOptions has
  NO on_response field → the TS `options.onResponse?.()` call is OMITTED (no-op).

## Status discipline (added after a status audit found 4 false ticks)
- `uv run scripts/migrate_next.py --status` is the only trustworthy progress view.
  It re-derives counts from the plan and audits every ticked step against the tree.
- `--done` now runs that audit too and REFUSES to tick a box the tree can't back up.
  Gates alone never caught this: an empty leaf passes pytest/ruff/pyright trivially.
- Reverted ticks and why: **1.8** and **2.10** ("umbrella publishable") were ticked
  when only 3 leaves had `publish=true`; **1.5 render** was ticked on a 191-line
  module that is not a port of the 1545-line `tui.ts` at all (different algorithm,
  no Container/overlays/cursor-marker/kitty bookkeeping) whose tests only agree with
  themselves; **1.7 components** was ticked with 4 of 12 components ported.
- Lesson: a step whose evidence is "the file exists" is under-specified. Say in the
  step body what would prove it, and make the driver able to check it.

## TUI porting rule (added with step 1.9)
- **Do not verify a tui port by reading the diff.** `tui.ts` emits cursor moves,
  erases and text; a plausible-looking refactor can produce a completely different
  screen, and a self-written test will happily agree with it. The 1.5 render leaf is
  exactly that failure: 191 lines of invented redraw logic with 6 passing tests.
- The contract is the **cell grid**. `packages/tui/testkit` (`cortex.tui.testkit`,
  never published) provides it: `Surface` (grid + ANSI interpreter), `snapshot()`,
  `diff_surfaces()`, `CaptureTerminal`.
- Authority chain, in order — each link exists because the one below it is not
  self-evidently right:
  1. `@xterm/headless` defines correct ANSI interpretation → `Surface` is held to
     it cell-for-cell over the `ansi/*` corpus (`test_surface_xterm.py`).
  2. The real hoocode TS defines correct rendering → `reference/dump.ts` **imports
     it** and records what it produces. Never a reimplementation.
  3. Parity tests run the same scenario in Python, project both through the
     validated `Surface`, and diff.
- Workflow: add scenarios to `goldens/scenarios.json` → `uv run scripts/tui_goldens.py
  --refresh` (needs bun + TS source) → make the diff go away →
  `uv run scripts/tui_parity.py --write`.
- Comparing raw line strings is the WRONG test: `\x1b[1m\x1b[31m` and `\x1b[31;1m`
  are different strings and the same screen. Compare surfaces.
- Scenarios pycortex can't run yet report as UNPORTED against the plan step that
  closes them — never as a pass against a stand-in.

## Bugs the surface harness found immediately
- **`cortex.tui.util` measured East Asian *Ambiguous* as 2 columns.** TS uses
  `get-east-asian-width` with its default `ambiguousAsWide: false`, so `┌ ─ │ ± ° →
  █` are ONE column there and in every real terminal. The port had `"A": 2` with a
  confident comment saying otherwise. Impact: every box border, arrow and spinner
  measured double → over-truncated lines, and in `tui.ts` it would trip the
  "rendered line exceeds terminal width" crash guard. The 19 existing `tui/util`
  tests passed before and after the fix — they never covered an ambiguous-width
  character. Found by `ansi/box-drawing` disagreeing with xterm.
- **The harness caught a bug in itself.** `ansi/erase-at-pending-wrap` showed the
  Surface erasing the last glyph on `ESC [ K` after a full-width line. Real
  terminals park the cursor at column `cols` (wrap pending) so that erase is a
  no-op. Without the xterm link this would have been logged as a pycortex renderer
  bug. Hence `Surface.cursor_x`, and only cursor-*moves* clear the wrap flag.
- **Step 2.8 shipped 2 pyright errors** (`ai/util/tool_constraints.py`, `_is_record`
  needed to be a `TypeGuard`). The gate ran `pyright packages/ai/provider-openai`
  while the new file landed in `ai/util`. Fixed, and `gates()` now always runs
  pyright over all of `packages` — steps routinely add prerequisites outside
  their own leaf.
- **`diff_surfaces` ignored underline/strike/inverse on blank cells** (found in 1.12
  by mutation testing, not by a failing scenario). The rule was "a blank cell's
  style is unobservable unless it paints a background", which is right for bold,
  italic and foreground colour and wrong for the three attributes that *draw* on
  an empty cell. Deleting `markdown.py`'s trailing-style-prefix strip — the exact
  bug hoocode's own "h1 underline leaks into the padding" test exists to catch —
  passed all 179 markdown scenarios before the fix. Now `_blank_paint()`.
  LESSON: a corpus that passes on the first run has not been shown to work; break
  the port on purpose and count what notices.

## Mutation testing is part of "done" for a TUI step
Run it before ticking the box: patch one behaviour in the ported module, re-run
the parity subset, and count failures (`0 scenarios caught` = a corpus gap, an
equivalent mutant, or dead code — decide which and say so). 1.12 ran 25
mutations; 22 were caught, and every fix was a new *discriminating* scenario
rather than a bigger corpus. The three that stayed uncaught are recorded in the
step log with the reason each is unreachable rather than untested.
**Kill the mutation script with `> file`, never `| head`** — a SIGPIPE at the
wrong moment leaves the module mutated on disk, and the next green test run is
lying to you (this happened; `heading_level >= 2` sat in the tree for two runs).

## A non-rendering TUI module still gets an authority, just not the Surface
`autocomplete.ts` draws nothing, so the parity corpus structurally cannot see
it — but its output is *data* (a suggestion list, and the text
`applyCompletion` writes back), which is diffable more exactly than a frame.
Same authority chain, new harness: `reference/autocomplete_dump.ts` **imports
the real TS module** and records what it produces for
`goldens/autocomplete-corpus.json`; `test_autocomplete_parity.py` replays the
same scenarios in Python and compares. Never write the expectations by hand —
four of them (the `~/..` display form, a query that scores 0, the 20-item cap,
the substring bucket) came out differently from what reading the TS suggested.
- Machine-independence is the whole job for a filesystem module: every scenario
  builds its own tree under `<tmp>/<id>/{cwd,outside,home}`, and `{root}`,
  `{cwd}`, `{home}` are substituted into the line and back out of every
  captured string. Cursor columns are recorded as the length of the
  *placeholder-rendered* prefix, or a temp path's own length leaks into them.
- **bun caches `os.homedir()` at startup**, so setting `process.env.HOME`
  mid-run does nothing (node re-reads it; bun does not). The dumper therefore
  re-execs itself once per scenario with `HOME` set in the child's env. Without
  this every `~` scenario silently captured the *author's* home directory.
- What `fd` reports first among equally-scored entries is not a contract (it
  walks in parallel), so `@` scenarios compare as multisets. The ranking is
  pinned directly instead: the dumper wraps the real private `scoreEntry` and
  records every call and answer, and the test replays those against the port.

## Step log
- 1.8 tui umbrella publishable — DONE. Phase 1 closed. No port work; this is the
  step that turns nine leaves into something a stranger can `pip install`. What it
  took, and what to reuse when 2.10 does the same for `ai`:
  1. **`py.typed` was the whole "pyright strict" story.** In-repo, every tui leaf
     was already type-checked strictly — `[tool.pyright] include = ["packages"]`
     covers the group and the gate runs it. What was missing was the *consumer*
     half: with no PEP 561 marker, an installed leaf is untyped, and a downstream
     strict checker answers `reportMissingTypeStubs` instead of `str | None`.
     Verified both ways, against wheels installed into a clean venv. Every
     published leaf now ships `src/cortex/tui/<leaf>/py.typed`; the root
     `[tool.pyright]` block now says why its four relaxations exist.
  2. **`never_publish = true`** (new `[tool.cortex]` key, on `tui/testkit`). The
     group-published audit demands `publish = true` on *every* leaf in the group,
     which testkit can never satisfy — `publish = false` means "not yet", and the
     audit is right to keep nagging about that. `never_publish` is the permanent
     form: `migrate_next.py` skips those leaves, and `publish_packages.py` hard-
     fails if one is ever also marked `publish = true`.
  3. **The umbrella was an empty shell.** `cortexcode-tui` had `dependencies = []`,
     so `pip install cortexcode-tui` would have installed *nothing* — it now pins
     all eight published leaves (`>=0.0.3,<0.1.0`, the form `bump_versions.py`
     re-pins). It also shipped a stray `cortex/tui/.gitkeep` into site-packages;
     `bypass-selection = true` makes it metadata-only, as a meta-package should be.
  4. **Bare sibling deps do not get re-pinned, ever.** `"cortexcode-tui-render"`
     with no specifier is invisible to `bump_versions.py`'s re-pin regex (it only
     rewrites strings that already carry one), so a published components leaf would
     have accepted render 0.0.1 — which predates half the API it calls. All
     intra-group deps are now pinned. Check this in the ai group before 2.10.
  5. Watch the blast radius of a bulk `sed`/`re.sub` over `pyproject.toml`: a
     naive `"cortexcode-tui-x"` → `"cortexcode-tui-x>=…"` rewrite also hits the
     `name = ` line and produces an unparseable project name. Anchor on the line.
  6. `pytest packages/tui` collects cleanly, so that (not whole-repo pytest, which
     is still broken) is this step's gate — the plan body now says so, since the
     driver reads the gate command out of it. Deleted the one empty
     `tests/__init__.py` left in the group (`terminal/`): those files are what
     makes `pytest packages/ai` fail to collect, and one is enough to break the
     gate.
  7. **Two known-red CI legs, neither fixed here — both need someone with rights
     this session did not have.** The `test` matrix in `ci.yml` lists a
     `coding-agent` package that has never existed (`pytest` exits 4 → red leg);
     it should read `code`. The one-line fix is written and verified locally but
     **cannot be pushed by an agent**: the OAuth app has no `workflow` scope, so
     any commit touching `.github/workflows/` is rejected at the remote. A human
     has to land it. The `ai` leg is red too, from the duplicate-`tests`-basename
     collection failure — pre-existing, and bigger than this step.
- 1.15 images — DONE. `terminal-image.ts` (423) + `components/image.ts` (112) →
  `cortex.tui.images.{terminal_image,image}` (~520), 30 parity scenarios
  (23 component + 7 renderer), 123 unit tests (the whole of
  `terminal-image.test.ts` and `bug-regression-isimageline-*.test.ts`), plus
  `tui-cell-size-input.test.ts` ported into the render leaf. All 273 component
  + 46 renderer scenarios match and the corpus has **no unported scenarios
  left**.
  1. **THE SOFT DEPENDENCY IS GONE, AND IT WAS HIDING A DEAD BRANCH.**
     `cortex.tui.util.is_image_line` was a stub returning `False`, so every
     caller — `tui.ts`'s width guard, `_saw_image_line`, the whole kitty
     delete-on-change bookkeeping 1.5 ported, and `markdown.ts`'s two image
     branches — was unreachable. It is deleted from `util` (it belongs to
     `terminal-image.ts`, not `utils.ts`) and render/markdown now import the
     real thing. The renderer scenarios below are the first to run that code.
  2. LEAF PLACEMENT: `components/image.ts` lives HERE, not in components. It is
     a `Component`-shaped wrapper around `terminal_image` and nothing else, so
     images stays dependency-free — which is what lets render and components
     depend on it. The reverse edge would be a cycle, since `tui.ts` and
     `markdown.ts` both import `terminal-image.ts` directly. `docs/02` updated,
     and the leaf moved T1 → **T0**: a T0 leaf cannot depend on a T1 one, so
     **1.8 must flip `publish = true` here too**.
  3. CONVENTION, stated once and applied: an exported TS *interface* becomes a
     dataclass (`TerminalCapabilities`, `CellDimensions`, `ImageDimensions`,
     `ImageRenderOptions`, `ImageOptions`, `ImageTheme`); an *anonymous inline*
     options object becomes keyword arguments (`encode_kitty`, `encode_iterm2`).
     `render_image` returns a `RenderedImage` dataclass because Python has no
     object literal.
  4. JS TRUTHINESS IS LOAD-BEARING IN `encode_kitty`: `if (options.columns)`
     skips `0`, and `if (options.imageId)` skips `0` — kitty has no image 0, so
     these are not accidents. `encode_iterm2` uses `!== undefined` for width and
     height instead, so `width=0` IS emitted. Both spellings are mutation-tested.
  5. `Buffer.from(x, "base64")` IS NOT `base64.b64decode`. Node ignores
     non-alphabet characters, accepts base64url, stops at the first `=` and
     needs no padding; Python raises. Every dimension parser decides "is this a
     PNG?" from the decoded bytes, so a strict decoder turns an unpadded but
     perfectly readable header into `None`. `_buffer_from_base64` emulates node.
     `buffer.toString("ascii")` masks each byte to 7 bits — `_ascii` does too.
  6. THE SURFACE IS STRUCTURALLY BLIND TO IMAGES. A kitty payload is APC and an
     iTerm2 one is OSC; a terminal paints no cell for either, so `Surface`
     shows the same screen whether an image was drawn, replaced or deleted.
     Two opt-in comparisons close that: `exactLines` on a component scenario
     and `exactStream` on a renderer one compare the captured bytes verbatim.
     Both were shown to be load-bearing rather than assumed: with `exactLines`
     off, dropping the `i=` parameter from every kitty sequence passes all 548
     component parity assertions; with it on, 6 fail.
     Byte equality is only fair here because the renderer is deterministic and
     both sides already agree byte for byte — checked before turning it on, and
     it stays opt-in for that reason.
  7. The corpus now pins `capabilities` AND `cellDimensions` per scenario, on
     both sides (`applyScenarioCapabilities` in `dump.ts`,
     `apply_scenario_capabilities` in `_scene.py`). Both are process-global and
     read at render time, so without this a golden captured inside Ghostty — or
     after a test that called `set_cell_dimensions` — records a different frame.
  8. Kitty scenarios MUST pass an explicit `imageId`: without one the component
     calls `allocate_image_id()`, which is `Math.random()` in the TS, and the
     golden would never reproduce. iTerm2 allocates nothing, so those scenarios
     deliberately leave it out and pin that difference.
  9. THE PARSERS ARE CROSS-VALIDATED THROUGH THE SCREEN. With no image protocol
     the component prints `[Image: <file> [<mime>] WxH]`, so a fallback scenario
     per format (PNG/JPEG/GIF/WebP VP8/VP8L/VP8X) makes the real TS the
     authority on the header maths rather than a hand-written expectation.
 10. MUTATION TESTING, two rounds. Port: 66 mutations, 60 caught on the first
     honest run. All four real misses are now closed by *discriminating* cases:
     `max(1, rows)` is only observable on a **zero-height** image (a 100x1 image
     rounds up to 1 either way); the JPEG segment skip needs a decoy `FF C0`
     planted inside an APP0 payload, or a scan that advances by `length` instead
     of past the segment byte-walks its way to the right answer anyway; the
     lossy-WebP `& 0x3fff` needs the two scaling bits actually set; and the
     component's 60-cell default needs a scenario with no `maxWidthCells` and a
     wide terminal. 64/66 now.
 11. Two mutants stay uncaught and are **equivalent**, not gaps: deleting
     `render_image`'s `if not caps.images: return None` leaves the two protocol
     checks below it, neither of which matches, and the function returns `None`
     anyway; and `Image`'s `if result.image_id: self._image_id = result.image_id`
     can only re-assign the value it just passed in, since the kitty branch
     returns `options.image_id` verbatim.
 12. Renderer round: 8 mutations of 1.5's kitty bookkeeping — code that had
     never run. All 8 caught, but only after two scenarios were added that
     nothing else reached: `_collect_kitty_image_ids` feeds ONLY the
     `full_render(clear=True)` path, so it needs a **resize** after an image
     (`renderer/image-kitty-deleted-on-full-redraw`), and appending
     `SEGMENT_RESET` to an image line is invisible to the screen, so it needs
     `exactStream`.

- 1.14 autocomplete — DONE. `autocomplete.ts` (783) →
  `components/autocomplete.py` (~700), 67 parity scenarios captured from the
  real TS, 90 unit tests (25 ported from `autocomplete.test.ts`), plus the five
  editor tests 1.13 had to defer (`TestAutocompleteBlockedOn114` is gone).
  1. **`posixpath` is not `node:path`, and the difference is load-bearing.**
     `basename("src/")` is `""` in Python and `"src"` in node — every directory
     from `fd` arrives with a trailing slash, so scoring would have compared the
     query against an empty string and un-ranked all of them. `dirname("up")` is
     `""` vs `"."` likewise. `_join`/`_dirname`/`_basename` are ports of node's
     posix implementations; `_dirname` is its exact loop.
  2. `localeCompare` has no Python equivalent. `_locale_compare_key` is an
     approximation of ICU's default collation covering the two places it
     disagrees with code-point order on filenames — case-insensitive first,
     lowercase before uppercase on a tie. `path/sorting-dirs-first-then-locale`
     pins it against the TS, and both directions are mutation-tested.
  3. A quoted directory's value ends in `"`, not `/`, so the "directories
     first" sort — which tests `value`, not `label` — files it with the
     documents. Faithful, and recorded by `path/quoted-sorts-directories-with-files`.
  4. NO ABORT EVENT. The port's `AbortSignal` is the convention from the
     provider leaves (an object with a bool), so there is no
     `addEventListener("abort")` to hang the `SIGKILL` off; the wait polls the
     flag every 5ms and kills `fd` the same way. A test drives it with a stub
     that sleeps — note the stub must `exec` the sleep, or the orphaned
     grandchild keeps stdout open and both implementations wait for it anyway.
  5. TYPES: `apply_completion`'s `item` is annotated with the editing leaf's
     *protocol*, not the dataclass this module defines. TS method parameters
     are bivariant, Python's are not, so a narrower parameter fails to satisfy
     `AutocompleteProvider` at all — pyright caught it, and the editor tests
     were the ones that failed.
  6. `getArgumentCompletions` is `Awaitable<T>` — a value *or* a promise — so
     the port awaits only what `inspect.isawaitable` says to. The TS's
     `Array.isArray` guard is `isinstance(..., list)`, and it is the whole
     point of the "ignores invalid results" test.
  7. Commands are discriminated structurally (`_command_name` reads `name`,
     else `value`), so any object of either shape works, as in the TS — the
     declared type is the union of the two dataclasses this module exports.
  8. MUTATION TESTING: 43 mutations, 38 caught on the first honest run. The
     five misses were all real: the substring score bucket had no scenario that
     reached it, nothing returned a zero-scored entry (a query with a regex
     metacharacter does — `fd` gets the pattern unescaped when it has no
     slash), the 20-item cap was never approached, `.git` filtering is
     unreachable while `fd` is asked to exclude it (a stub `fd` reaches it), and
     applying a `/`-prefix away from the line start had no test. All five are
     now closed by *discriminating* cases rather than more of the same.
  9. Three mutations stay uncaught, each analysed: dropping the trailing slash
     in `_expand_home_path` is **equivalent** (the expansion is only ever used
     as a directory to open); dropping `"./"` from `is_root_prefix` is
     **equivalent by construction** (the `endswith("/")` branch below it is
     character-identical — only `"~"` and `""` distinguish the two, and `"~"` is
     covered); and the `signal.aborted` check in `_get_fuzzy_file_suggestions`
     is **redundant with** the one at the top of `_walk_directory_with_fd` —
     dropping either leaves the other, dropping both is caught.
 10. FALSE MISSES ARE A THING. Two mutations reported "caught" and two reported
     "missed" incorrectly until the runner deleted `__pycache__` between
     mutations: CPython validates a `.pyc` by (mtime, size), and two writes in
     the same second with the same length hand pytest the *previous* mutant.
     Any mutation script that rewrites one file in a loop needs this.

- 1.13 components — editor — DONE. `editor.ts` (2309) → `components/editor.py`
  (~1500), 50 parity scenarios, 184 unit tests (175 ported from `editor.test.ts`).
  All 250 component + 39 renderer scenarios match; the one remaining gap is still
  1.15's OSC 8 link.
  1. **Two prerequisite gaps in `keys`, both found by driving the component.**
     `\x1b[1;3D` (alt+left) parsed as `None`, so every word-motion binding was a
     dead key — that became step **1.19** (`parseKittySequence` had one of its
     four branches, and the kitty codepoint tables were invented: arrows at
     57352-57355, which kitty never sends, `delete` at 57399, which is kitty's
     KP_0, `kpEnter` at 108, which is the letter `l`). Separately,
     `decode_kitty_printable` was missing `KITTY_PRINTABLE_ALLOWED_MODIFIERS`, so
     `\x1b[99;9u` (Super+c) typed a "c"; that one is small and lands here, with
     the ported test that caught it. This is the **third** step to find `keys.ts`
     under-ported — assume more of it is missing until a test says otherwise.
  2. COLUMNS ARE CODE POINTS. Every cursor column in the TS is a UTF-16 code unit
     offset; in Python they are code points. Internal and used consistently, so
     no screen changes — only a caller reading `get_cursor()` across an astral
     character would see it. Recorded at the top of `editor.py`; the ported tests
     assert on ASCII spans, where the two agree.
  3. `Intl.SegmentData` has `.index`; `grapheme_segments` yields bare clusters, so
     `_Segment` re-attaches it. It is a **dataclass, not a NamedTuple** — the TS
     field name `index` shadows `tuple.index` and pyright rejects the override.
  4. AUTOCOMPLETE IS ASYNC AND THE FRAME IS NOT. `requestAutocomplete` fires a
     promise chain and `setTimeout`; with no running event loop there is nothing
     to schedule, so the request is simply not made — the same shape as the
     loader's animation, and it is what keeps the golden capture deterministic on
     both sides (the TS suspends at its first `await` before `render` too).
  5. The `SelectList` the editor builds holds its own `SelectItem`s, so
     `_source_item()` maps the selected row back to the provider's original
     object by identity. Without it `apply_completion` would receive a copy and
     any field a provider attached beyond value/label/description would be lost.
  6. TYPES GAP (same shape as 2.12's): `AutocompleteProvider.get_suggestions` had
     no `signal`, which the TS passes so a provider can drop a superseded
     request. Added, with an `AbortSignal` **Protocol** in the editing leaf
     (`cortex.tui.components.AbortController`'s signal satisfies it structurally)
     — editing cannot import components. `AutocompleteSuggestions.items` became a
     `Sequence`: `list` is invariant, so no provider with its own item type could
     satisfy the protocol at all. The optional `shouldTriggerFileCompletion` has
     no Protocol equivalent, so it is a runtime-checkable protocol the editor
     `isinstance`-checks — which is what `!provider.shouldTriggerFileCompletion ||`
     does in the TS.
  7. MUTATION TESTING: 34 mutations, 24 caught on the first run. The 10 misses
     were all real blind spots, and all but one are now closed by a
     *discriminating* test rather than a bigger corpus:
     - `CURSOR_MARKER` is a zero-width APC string, so **focus is invisible to the
       Surface** — the parity harness structurally cannot see it. Pinned on the
       raw line instead.
     - `_segment_with_markers`' valid-id check only runs once *some* paste
       exists; the ported TS test typed a fake marker into an editor with no
       pastes at all, so the fast path returned first and the check was never
       reached. Now: paste for real, then type a marker with a different id.
     - `<` vs `<=` on a non-last chunk's cursor range needs the cursor **exactly**
       on a wrap boundary; `component/editor-cursor-at-wrap-boundary` is that.
     - The `@`/`#` debounce test asserted `calls == 0` synchronously, which is
       true whether or not the timer exists. It now drains the loop first.
     - `_is_autocomplete_request_current` needed a response that arrives after
       the cursor moved — moving the cursor invalidates the snapshot without
       cancelling the request, which is the only way to reach that branch.
     - Also added: tab expansion, history de-duplication (asserting the text
       after each Up cannot tell one entry from two — walking back down can),
       exact-match-beats-earlier-prefix, and first-line-only slash menus.
  8. One mutation stays uncaught and is an **equivalent mutant**: dropping
     `len(segment) >= 10` from `_is_paste_marker`. `PASTE_MARKER_SINGLE`'s
     shortest possible match is `[paste #1]`, which is exactly 10 characters, so
     the guard can never reject anything the regex accepts. It is a fast path in
     the TS and is kept as one.
  9. `\x1b[13;2~` (one shift+enter encoding) parses as `None` in the TS too —
     `parseKittySequence`'s functional branch has no entry for 13 — which is
     exactly why `handleInput` matches the literal sequence. Pinned by a test in
     1.19 so nobody "fixes" the parser into breaking the editor.

- 1.19 keys — CSI modifier sequences + kitty functional codepoints — DONE
  (prerequisite for 1.13). Ported `parseKittySequence`'s other three branches
  (modified arrows `\x1b[1;<mod>A-D`, modified functional keys `\x1b[<n>;<mod>~`,
  modified Home/End `\x1b[1;<mod>H/F`), the real
  `KITTY_FUNCTIONAL_KEY_EQUIVALENTS` table with the TS's negative sentinels, and
  `formatKeyNameWithModifiers` (LOCK_MASK stripped, unsupported modifier bits
  refused, shift/ctrl/alt/super order). Every value was diffed against the TS
  `parseKey` rather than reasoned about.
  1. The invented tables were worse than missing: `ARROW_CODEPOINTS` used
     57352-57355 (kitty sends 57417-57420), `FUNCTIONAL_CODEPOINTS["delete"]` was
     57399 (kitty's KP_0), and `CODEPOINTS["kpEnter"]` was 108, so `\x1b[108u` —
     a plain letter `l` — parsed as `enter`.
  2. `_format_parsed_key` had to grow a `cp >= 0` guard before `chr(cp)`: the
     sentinels are negative and `chr(-4)` raises.
  3. The legacy `\x1b[3~`/`\x1b[5~`/`\x1b[6~` forms now match the functional
     branch *before* `LEGACY_SEQUENCE_KEY_IDS`, and produce the same ids.

- 1.12 components — markdown — DONE. `markdown.ts` (808) → `components/markdown.py`
  (~700), 110 parity scenarios, 57 unit tests (39 ported from `markdown.test.ts`).
  All 200 component + 39 renderer scenarios match; one scenario is unported by
  design (see 5).
  1. **1.18's adapter was missing four things `markdown.ts` reads**, all found by
     adding corpus samples and diffing against marked, none by reading the TS:
     (a) `table.raw` — the too-narrow-table fallback reprints the table's own
     markdown, and without it the table simply vanished. mistune has no source
     spans, so the table rules are re-registered through `_record_raw`, which
     takes the span from `state.cursor` → the rule's return value;
     (b) tables inside blockquotes and list items needed mistune's
     `table_in_quote`/`table_in_list` — marked parses them, so a quoted table was
     rendering as paragraphs;
     (c) marked's `table` and `html` rules swallow their trailing blank lines
     exactly like `heading` (so no `space` token follows, and the swallowed
     newlines are part of `raw`);
     (d) a list item's prose is a `text` token in marked **even when the list is
     loose** — the adapter emitted `paragraph`, which renders through a branch
     that appends a blank line, so every loose list grew spacing the TS lacks.
  2. **A document ending in a blank line ends in a `space` token**, and the TS
     prints it. 1.18's "drop a trailing space" rule was derived from too few
     samples and deleted that line. mistune agrees with marked at the top level,
     so the rule is gone there; inside a blockquote mistune emits one where marked
     does not, and the blockquote branch pops trailing blanks before drawing
     borders, so it is dropped there and the AST difference is recorded as a
     strict xfail (`KNOWN_DIVERGENCES`) with the scenario that proves the screens
     agree. Lists need the opposite fix: mistune emits no blank line after a list
     at all, so `lex_markdown` appends the `space` when the source ends in one.
  3. **The corpus, not the diff, is the deliverable.** All 179 first-draft
     scenarios passed on the first run; mutation testing then showed 8 of 12
     deliberate bugs went unnoticed. Fixing that meant *discriminating* scenarios,
     e.g. a table narrow enough to actually hit the fallback (3 columns need 10
     columns of border alone — a 2-column table at width 10 never gets there), a
     blockquote whose inner reset is not already followed by the style prefix
     (a bulleted list under a reset-closing theme), a styled table header (plain
     ASCII headers cannot tell `visible_width` from `len`), and padding with text
     long enough to wrap.
  4. Three mutations stay uncaught, each analysed rather than papered over:
     "heading always spaced" and "heading style prefix ignored" are **equivalent
     mutants** (marked never emits a `space` after a heading, and in a heading
     context `applyText` re-styles every text segment, so the prefix only matters
     where `applyText` is identity — i.e. blockquotes, which *is* caught);
     "blockquote keeps trailing blank" is **unreachable by construction** given
     2's decision, and is kept because it is a faithful port of the TS.
  5. **OSC 8 links need terminal capabilities, which are step 1.15.** Same soft
     dependency `cortex.tui.render` takes on the images leaf: `_hyperlinks_supported()`
     resolves `cortex.tui.images.get_capabilities` dynamically and reports "no
     hyperlink support" until it exists — a real runtime state, and the branch the
     TS takes on any terminal it has not positively identified. `hyperlink()` is
     inlined (one format string) exactly as `_delete_kitty_image` was in 1.5.
     `component/markdown-link-osc8` is captured from the TS and reported UNPORTED
     against 1.15; `test_markdown.py` pins the rendering with the capability
     forced, stubbing only where the boolean comes from.
  6. **The goldens now pin the capabilities they were captured under**
     (`setCapabilities` per scenario in `reference/dump.ts`). Without it, running
     `--refresh` inside Ghostty or iTerm would silently recapture every link
     scenario in the OSC 8 branch.
  7. JS-truthiness trap: `if (cached)` is TRUE for an empty array, so blank text
     is a cache *hit* in the TS. `if cached:` in Python re-renders forever;
     `if cached is not None:` is the port.
  8. `bun` resolves `node_modules` by walking up from the *importing file* — a
     probe script in /tmp pulled a different `marked` and disagreed with the
     goldens about table `raw`. Put throwaway probes in `testkit/reference/`.

- 1.18 markdown AST adapter — DONE (prerequisite for 1.12).
  PARSER CHOICE, MEASURED NOT GUESSED: lexed a 20-sample corpus with real `marked`
  under bun, then compared. `markdown-it-py` emits a flat `_open`/`_close` stream
  needing tree reconstruction; `mistune` gives a nested AST with every field
  markdown.ts reads and agreed on 19/20 block-type sequences. Chose mistune.
  New harness, same shape as the surface one: `reference/markdown_ast_dump.ts`
  captures marked's tokens for `goldens/markdown-corpus.json` (32 samples) into
  `goldens/marked-ast.json`; `test_markdown_ast.py` asserts the adapter reproduces
  them token-for-token. Only the fields markdown.ts reads are recorded — pinning
  marked's offsets would fail the port over things nobody can see. (1.12 added
  `raw` for `table` and `html`, which two branches *do* read.)
  Divergences the goldens caught (all would have been invisible by reading):
  1. `space` TOKENS. mistune DOES emit `blank_line`; my first attempt threw them
     away and re-derived spacing from a rule I invented, which was wrong. Correct
     handling: keep mistune's, collapse runs to one, DROP the one after a heading
     (marked's heading rule eats its own trailing blanks — every other block leaves
     them), ADD one after a `list` (mistune omits it), drop a trailing one.
     This is not cosmetic: `markdown.ts` keys spacing off `nextToken.type ===
     "space"`, and `list` is the ONE block that never adds its own trailing blank,
     so a missing space after a list silently loses a line on screen.
     **SUPERSEDED BY 1.12**: "drop a trailing one" was wrong (marked emits it for
     any source ending in a blank line), and `table`/`html` swallow their trailing
     blanks the same way `heading` does. See the 1.12 entry.
  2. SOFT BREAKS. marked yields one text token per contiguous run with `\n` inside;
     mistune splits at every soft break and leaves empty text tokens around
     emphasis. `_inlines` merges adjacent text and drops empties.
  3. `start` is a NUMBER for every ordered list (1 when it starts at 1) and `""`
     for unordered. Not "empty unless non-1".
  4. marked trims the trailing newline off `html` raw; mistune keeps it.
     **SUPERSEDED BY 1.12**: marked's `html` raw is the consumed span, trailing
     blank lines included; it only looked trimmed because the sample ended at EOF.
  5. mistune's strikethrough plugin already rejects the loose `~~ spaced ~~` forms
     that markdown.ts installs a custom tokenizer for — pinned by a golden, so no
     override was needed. Verified rather than assumed.
  Mutation-tested the AST goldens: dropping the list-space, the heading
  suppression, or the ordered `start` each trips 2-3 samples.

- 1.11 components — lists — DONE. `select-list.ts` (229) + `settings-list.ts` (250) →
  `components/{select_list,settings_list}.py`, 33 parity scenarios, 50 unit tests
  (the first five ported from `select-list.test.ts`). All 91 component scenarios match.
  1. `SettingsList` DEPENDS ON `Input` (its search box) and on the fuzzy leaf — hence
     `cortexcode-tui-fuzzy` added to the components leaf. Ordering 1.10 before 1.11
     was load-bearing.
  2. Column-bounds fallback is easy to misread: `min` and `max` each stand in for the
     OTHER when absent, so setting only `max` pins the column rather than leaving
     `min` at the 32 default. Inverted bounds are swapped, not rejected.
  3. `truncate_primary` output is truncated AGAIN by the caller — a custom truncator
     is not trusted to respect the width it was handed.
  4. `settings-list` measures `max_label_width` over ALL items, not the filtered ones,
     so the value column does not jump while you type. It caps the PADDING at 30, it
     does not truncate a longer label; `component/settings-long-label-capped-at-30`
     records that.
  5. `indexOf` returning -1 for an unknown `current_value` means cycling starts at the
     first value. Python's `.index()` raises, so that needs an explicit `except`.
  6. MUTATION TESTING FOUND A WEAK CORPUS: my first pass had `select-exactly-40`, but
     at that width the description does not fit either way, so flipping the `width > 40`
     two-column threshold changed nothing. Added `select-40-short-column` (narrow
     column via layout bounds) and `select-column-width-from-widest`; both mutations
     are now caught. Add the scenario that DISCRIMINATES, not the one that looks
     like a boundary.

- 1.10 components — input — DONE. `input.ts` (503) → `components/input.py` + 24 parity
  scenarios + 46 unit tests. Needed TWO prerequisite steps (1.16, 1.17) that only
  surfaced when the component was driven for real.
  1. `decode_kitty_printable` had to be EXPORTED from the keys leaf. `input.ts` calls
     `decodeKittyPrintable` specifically; the Python public `decode_printable_key` is
     a superset that also decodes modifyOtherKeys. Using the superset would have been
     a silent behaviour widening.
  2. The corpus gained `keys: [...]` and `focused: true` on component scenarios, so
     stateful components are driven to the state under test before the frame is
     captured. Both dumpers apply them in the same order.
  3. Cursor positions are CODE-UNIT indices into the value, but movement/deletion step
     by grapheme cluster — hence `grapheme_segments` from 1.16.
  4. UNDO COALESCING, easy to get wrong: whitespace snapshots *before* inserting
     itself and then still sets `last_action = "type-word"`, so the space and the word
     after it are ONE undo unit. Typing "hello world" then undo gives "hello", not
     "hello ". My first unit test asserted the wrong model; the golden was right.
  5. `_move_word_forwards` uses an index into a materialised grapheme list rather than
     the TS's iterator, which reads better in Python and behaves identically.

- 1.17 keys — legacy escape sequences — DONE (prerequisite for 1.10, found BY the
  parity harness). `parse_key` returned `None` for the whole legacy ESC-prefixed
  family, so `ctrl+-` (undo) and every alt word-motion binding were dead keys.
  Ported the TS branch verbatim: `\x1c`/`\x1d`/`\x1f`, the `ctrl+alt+[\]-` forms,
  `\x08`, `\x1b\r`, `\x1b<space>`, and generic `ESC <char>` → `ctrl+alt+<letter>`
  (1-26) or `alt+<letter|digit>`.
  GOTCHA: `\x1bb`/`\x1bf`/`\x1bp`/`\x1bn` must stay in `LEGACY_SEQUENCE_KEY_IDS`
  mapping to `alt+left`/`alt+right`/`alt+up`/`alt+down`. Falling through to the
  generic rule yields `alt+b`, which matches NO keybinding — the Emacs aliases only
  work because the TS rewrites them to the arrow forms. All values were diffed
  against the TS `parseKey` under bun rather than reasoned about.
  This is the second gap behind 1.3's "minimal" label; assume more of keys.ts is
  missing and check before relying on it.

- 1.16 keybindings + grapheme segmentation — DONE (prerequisite for 1.10).
  1. `TUI_KEYBINDINGS` was 15 of 31 ids, with truncated default key lists. Ported the
     TS table in full, in TS order. The missing Emacs alternates (ctrl+b/f/a/e,
     alt+b/f/d/y, ctrl+w/u/k/y) are how the editor components are actually driven —
     without them `input.ts` cannot be ported at all.
  2. REMOVED three invented ids that were never in the TS:
     `tui.editor.{backspace,deleteChar,deleteWord}` → the real ones carry a direction
     (`deleteCharBackward`/`deleteCharForward`/`deleteWordBackward`/`deleteWordForward`).
     Checked for consumers first; there were none.
  3. `get_segmenter()` returned `None` as a "stub for API compatibility", which is
     worse than absent — every caller reached for the private `_grapheme_segments`.
     Now returns the segmenting callable, and `grapheme_segments()` is exported.
  LESSON: a step titled "minimal <x> port" is a deferred obligation, not a completed
  step. 1.3's own commit message said "minimal" and the box was ticked anyway.

- 1.7 components (simple) — DONE. Added the two missing leaves: `loader.ts` →
  `components/loader.py` and `cancellable-loader.ts` → `components/cancellable_loader.py`.
  text/truncated-text/box/spacer were already correct and now have goldens proving it.
  1. `Loader` SUBCLASSES `Text` (as in the TS) and prepends a blank line in `render`.
  2. `setInterval` → `loop.call_later` re-armed per tick, guarded by a GENERATION
     COUNTER. Without it, `set_indicator` (which stops then restarts) leaves the
     already-queued tick alive and you get two spinners at once —
     `test_restarting_does_not_leave_two_timers_running` pins this.
  3. NO EVENT LOOP → no animation, but the frame still renders. Same shape as the
     renderer's `_soon`; a spinner is only meaningful inside a loop.
  4. `AbortController`/`AbortSignal`: the signal reports the CONTROLLER's flag rather
     than holding its own, so handing a consumer the signal does not hand over the
     ability to trip it. Matches the notes' "any object with a bool `.aborted`".
  5. Indicator semantics worth remembering: passing ANY indicator switches the frame
     to verbatim rendering (the spinner colour fn is skipped); `frames: []` means no
     indicator AND no trailing space; a non-positive `intervalMs` falls back to 120.
     All three have their own golden.
  6. TESTKIT: both dumpers gained a `Loader`/`CancellableLoader` builder constructed
     with `ui=None` and immediately `stop()`ped, so goldens capture frame 0 rather
     than racing a timer. Corpus: 25 → 34 component scenarios, all matching.
  7. A timing test failed first time asserting `_current_frame != 0` after 35ms —
     three frames on a 10ms tick lands back on 0. Sample over time instead of
     checking an index at one instant.

- 1.5 render — DONE (re-port). `tui.ts` (1545) → `cortex.tui.render._render` (~1150),
  one module to stay diffable. Replaced the invented 191-line version wholesale.
  Ported in full: `Container` render memo, root flatten + patch tracking, overlay
  stack/layout/compositing, `CURSOR_MARKER` extraction, kitty image bookkeeping,
  synchronized output, viewport/scroll accounting, the width crash guard.
  1. IDENTITY, NOT EQUALITY. The memo compares child line arrays with `is`, mirroring
     TS `!==` on arrays. Components must return the SAME list object when unchanged
     — a fresh equal list silently disables the whole patch path. `StaticLines` in
     the corpus caches for exactly this reason.
  2. `request_render()` MUST NOT PAINT SYNCHRONOUSLY. My first version fell back to
     rendering inline when no event loop was running; `show_overlay` calls
     `request_render`, so the overlay frame was emitted early and then discarded.
     Three overlay scenarios failed until the fallback became "leave it pending".
     Added `render_now()` as the explicit flush (what the harness drives, matching
     the TS tests reaching for `doRender`).
  3. `_do_render` is the port of `doRender`; the parity driver calls it directly.
  4. `OverlayHandle` is a dataclass OF CLOSURES built inside `show_overlay`, not a
     class holding a `TUI`. That is what the TS returns, and it keeps every mutation
     of the overlay stack inside `TUI` (a sibling class reaching into `_overlay_stack`
     also produced 23 pyright `reportPrivateUsage` errors).
  5. `fullRender` counts the FIRST render in `fullRedraws`. The old fabricated version
     excluded it; do not "fix" that back.
  6. IMAGES ARE A SOFT DEP. `getCapabilities`/`setCellDimensions` live in
     `terminal-image.ts` → step 1.15. Resolved via `importlib` + `getattr`, returning
     "no image support" until then — a real runtime state the TS also has, not a stub.
     `_delete_kitty_image` is inlined (one format string) so the kitty bookkeeping is
     a real port and testable today; 1.15 replaces it with an import.
  7. Env var names kept as `HOOCODE_*`, matching what step 1.4 did in `terminal.py`.
  8. VERIFICATION: corpus grown 11 → 39 renderer scenarios (cursor markers, patch
     paths, child grow/shrink/remove, clearOnShrink, scrolling, height change, ANSI,
     wide chars, OSC-8, and 13 overlay cases). All 39 + all 25 component scenarios
     match the TS. Mutation-tested the harness: an off-by-one in overlay centring
     trips 11 scenarios, dropping the per-line `ESC [ 2K` trips 3 (that mutation
     initially caught only 1, which is why `renderer/*-shrink-in-place` exist).
     `test_renderer_parity.py` is now `STRICT = True`.
  9. Also ported `overlay-non-capturing.test.ts` as `test_overlay_focus.py` — focus
     never reaches the screen, so the surface harness cannot see it.
- 2.9 provider-google — DONE. `google-shared.ts` (350) + `google.ts` (496) →
  `cortex.ai.providers.google.{shared,google}` + 58 tests.
  1. NO `@google/genai` SDK — follows 2.7's httpx decision, which also SETTLES the
     debt 2.8 opened: raw httpx is the convention for provider leaves. The SDK's
     only contributions here are a base-URL builder and an SSE loop, and
     `Content`/`Part`/`FinishReason`/`FunctionCallingConfigMode` are just dicts and
     string constants on the wire. `provider-openai` still carries the `openai`
     dependency — resolve when its tests get backfilled.
  2. VERTEX SPLIT OUT to step 2.16 (separate `plan:` commit). Its 564 lines are
     mostly a second concern — GCP credential resolution — and would have blown
     the one-session contract.
  3. `map_stop_reason` RAISES on an unknown FinishReason. The TS has a compile-time
     `never` exhaustiveness check that throws; Python has no equivalent, so
     silently mapping unknown → "error" would lose the signal. `map_stop_reason_string`
     stays lenient, as in the TS.
  4. OPTIONS: `GoogleOptions` is an open dict (the TS type is an open interface).
     `stream_simple_google` splits caller dicts into the `SimpleStreamOptions`
     fields `build_base_options` needs plus extras that are forwarded — that is what
     lets tests inject `client` without a network.
  5. BUG CAUGHT BY THE PORTED TESTS: my first `_append_tool_result` returned early
     after merging a function response into the previous user turn, which swallowed
     the synthetic "Tool result image:" turn Gemini < 3 needs. Ported test
     `google-shared-image-tool-result-routing` failed on 3 contents vs 5.
  6. TESTS: ported `google-shared-convert-tools` (4), `-image-tool-result-routing`
     (3), `-gemini3-unsigned-tool-call` (4), `google-thinking-signature` (5), plus
     streaming/request-shaping coverage the TS only has e2e. The two
     `google-thinking-disable` suites need a real key and are NOT ported; their
     request-shaping half is covered by `TestThinkingConfig`.

- 2.8 provider-openai — DONE (notes reconstructed after the fact; the step landed
  without them). `openai-completions.ts` (1168), `openai-responses.ts` (273),
  `openai-responses-shared.ts` (561), `openai-codex-responses.ts` (1323) and
  `azure-openai-responses.ts` (281) → `cortex.ai.providers.openai.*` (2181 py lines).
  Also ported `utils/tool-constraints.ts` → `cortex.ai.util.tool_constraints` as a
  prerequisite. Decisions and open debts:
  1. AZURE ABSORBED: `azure-openai-responses.ts` is a thin openai-responses variant,
     so it lives here rather than in its own leaf. The `packages/ai/provider-azure`
     placeholder has been deleted and `docs/02` §3.2 updated.
  2. SDK INCONSISTENCY (debt): this leaf depends on the `openai` SDK, whereas 2.7
     deliberately dropped `@anthropic-ai/sdk` for raw `httpx`. Pick one convention
     before 2.9 — google should not introduce a third.
  3. THIN TESTS (debt): 56 test lines for 2181 lines of module. The TS side has
     ~15 openai test files. Backfill before the ai umbrella is published.
  4. VERSION DRIFT (debt): pyproject says `0.0.1` while every other leaf is `0.0.3`.
  5. Stray `src/cortex/ai/provider-openai/` (hyphen — not a legal module path) was
     shipped empty alongside the real `providers/openai/`; deleted.

- 2.12 provider-common — DONE. New shared leaf `cortexcode-ai-provider-common`
  (`cortex.ai.providers._common`). Ported 4 helpers from `providers/*.ts`:
  `cache_retention.py` (`resolve_cache_retention`), `simple_options.py`
  (`build_base_options`, `clamp_reasoning`, `adjust_max_tokens_for_thinking`),
  `transform_messages.py` (`transform_messages`), `github_copilot_headers.py`
  (`infer_copilot_initiator`, `has_copilot_vision_input`, `build_copilot_dynamic_headers`).
  31 tests (transform-messages ported from
  transform-messages-copilot-openai-to-anthropic.test.ts; cache/simple-options/copilot
  are new unit tests since the TS cache-retention.test.ts is e2e/API-gated).
  TWO PREREQUISITES resolved in this step (both required for faithful port, not
  enhancements):
  1. NAMESPACE: `cortex.ai.providers` was a REGULAR package because faux shipped
     `providers/__init__.py` (a barrel re-exporting faux). That pins the namespace
     `__path__` to faux's dir only, so a second wheel (provider-common) can't add
     `_common` under it. FIX: deleted faux's `providers/__init__.py` → PEP 420
     namespace package. TS has no providers barrel either; all imports already use
     `cortex.ai.providers.faux` directly, so nothing broke. RULE: never ship
     `providers/__init__.py` from any provider leaf — keep `cortex.ai.providers` a
     namespace. Each provider is a submodule (`.faux`, `._common`, later `.anthropic`).
  2. TYPES GAP: pycortex `StreamOptions` was a partial stub missing 8 fields that
     `build_base_options` copies (on_payload, on_response, headers, timeout_ms,
     max_retries, max_retry_delay_ms, metadata, constrain_tool_calls) — added them in
     TS field order. `SimpleStreamOptions` was a WRONG stub (model/api/provider/base_url,
     unused anywhere) → replaced with faithful `class SimpleStreamOptions(StreamOptions)`
     + reasoning/thinking_budgets/thinking_display. Exported `ThinkingBudgets` from
     types `__init__`. Callbacks (onPayload/onResponse/signal) typed `Any | None`.
  `adjust_max_tokens_for_thinking` returns a dict with SNAKE keys
  `{"max_tokens", "thinking_budget"}` (not TS camel). Deps: only `cortexcode-ai-types`
  (the 4 helpers import nothing else), narrower than arch doc §128's list — fine.
  VENV GOTCHA: `uv pip install -e <one pkg>` / plain `uv sync` collapsed the venv to a
  couple editables and broke faux's namespace merge. Use `uv sync --all-packages` to
  restore all workspace editables so `cortex.ai.providers.__path__` lists BOTH dirs.

- 2.11 sanitize-unicode — DONE. utils/sanitize-unicode.ts (`sanitizeSurrogates`) →
  `cortex.ai.util.sanitize_unicode` (`sanitize_surrogates`) + 7 tests. No TS test file
  existed. KEY INSIGHT: Python str is a sequence of Unicode code points (not UTF-16
  units), so valid emoji/BMP+ chars are single code points OUTSIDE the surrogate range
  and lone surrogates are single code points in 0xD800-0xDFFF. So the TS regex
  `[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]` collapses to
  simply stripping any 0xD800-0xDFFF code point. Re-exported from util `__init__`.

- 2.6 provider-faux — DONE. providers/faux.ts (455) + faux-provider.test.ts (597) →
  `cortex.ai.providers.faux` (+ `providers/__init__.py` re-export) + 22 tests, all green.
  Skeleton dir was `src/cortex/ai/provider-faux/` (hyphen, invalid module) → moved to
  `src/cortex/ai/providers/faux.py`. Added `cortexcode-ai-models` dep (faux needs
  api-registry). register_faux_provider + faux_assistant_message use KWARGS (not
  options objects).

- 2.7 provider-anthropic — DONE. providers/anthropic.ts (1177) →
  `cortex.ai.providers.anthropic` + 9 fixture tests. Key design decisions:
  1. NO ANTHROPIC SDK: Replaced `@anthropic-ai/sdk` with `httpx` directly. Defined
     `AnthropicClient` class with `create_message()` method that POSTs to
     `/v1/messages`. SSE parsing ported to Python using `response.aiter_bytes()` with
     incremental UTF-8 decoding. The TS SDK's `.asResponse()` call is eliminated.
  2. CLIENT INJECTION: `options.client` accepts any object with `create_message(params,
     **kwargs)`. Default is `AnthropicClient`. Tests pass mock objects via
     `unittest.mock.patch` on `create_client` or `AnthropicClient`.
  3. SSE PARSER: Custom incremental SSE decoder (`_IncrementalSseDecoder`) mirrors the
     TS `consumeLine`/`decodeSseLine` pattern. Uses `_Utf8Decoder` for incremental
     byte→str conversion.
  4. BLOCK TRACKING: TS uses `Block[]` cast with `.index` property. Python maintains
     `block_map: dict[int, dict]` (event_index → block data) and
     `_find_block_index()` to map event indices to content positions.
  5. OPTIONS: `AnthropicOptions` is a plain `@dataclass` (not a pydantic model or
     StreamOptions subclass) to avoid inheritance conflicts. `build_params()` and
     `convert_messages()` take `AnthropicOptions | None`.
  6. COST BUG FIX: `calculate_cost()` in models.py used snake_case keys
     (`cache_read`) but model cost dicts use camelCase (`cacheRead`). Fixed with
     `.get("cache_read", .get("cacheRead", 0))` fallback.
  7. TESTS: 9 tests ported — SSE parsing (2), thinking disable (4), eager tool input
     (2), copilot auth (1). E2e tests (oauth, long-cache, opus-smoke, tool-name-norm)
     skipped (require real API keys). All tests use mock clients; no network calls.
  Deps: `cortexcode-ai-env`, `cortexcode-ai-provider-common`, `httpx`.
</content>
