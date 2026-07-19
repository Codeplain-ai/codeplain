# Conformance testing improvement — implementation plan

**Status:** Iteration 1 implemented and in review — client PR
[codeplain#252](https://github.com/Codeplain-ai/codeplain/pull/252), backend PR
[plain2code_rest_api#122](https://github.com/Codeplain-ai/plain2code_rest_api/pull/122).
**Iteration 2 implemented (2026-07-19)** on `feature/conformance-single-run-suite` in both
repos (stacked on the Iteration 1 branches), all chunks verified e2e against a local backend:

- 2a (`5a8d4d8`): loop-capable golang + cypress runners (degenerate case preserves old behavior).
- 2b.1 (`4aaa6e4`): failure attribution helpers + tests.
- 2b.2 (`36d09ce`): whole-suite execution flip. Verified: side-by-side per-FRID vs whole-module
  verdicts agree; happy path 2-FRID render = 2 invocations (was 3, grew quadratically);
  forced cross-FRID conflict → failure attributed to the earlier FRID, fix loop scoped to its
  subfolder, backend classified conflicting requirements, render failed with a clear report.
- 2b.3 (`e435de3`): dead FRID-walk machinery removed (~100 lines).
- 2c.1 (backend `40bbf75`): optional module-root-relative output paths behind a request flag.
- 2c.2 (`03c9b0d`): client stores at module root; shared root files included in context; two-tier
  guard (other-suite paths rejected; shared root files insertion-only via difflib) with one retry.
- 2c.3 (client `68ee4a1`, backend `747dfb9`): per-FRID summarization dropped. Evidence: its only
  consumer was the plan-stage dedup section; A/B renders of a 3-FRID project with and without
  summaries produced identical dedup quality (no cross-FRID duplication either way). The
  `folder_name`/`functional_requirement` map stays (attribution + fix routing depend on it);
  the backend endpoint stays for older clients. Saves one LLM call per functionality.

Note from 2c e2e: the render prompt permits shared root helpers, but on small projects the LLM
keeps suites self-contained — sharing is opportunity-based, which is the intended contract.

Feature branch (both repos): `feature/improve-conformance-testing`

- Client worktree: `codeplain/.claude/worktrees/feature+improve-conformance-testing`
- Backend worktree: `plain2code_rest_api/.claude/worktrees/feature+improve-conformance-testing`

## Local environment (verified 2026-07-18)

- Both branches are synced with their `origin/main` and fully green locally.
- **Client (codeplain):** run tests and quality gates with the `plain2code_client` conda env
  (`/opt/miniconda3/envs/plain2code_client/bin/python`). Baseline: 293 tests pass; black, isort,
  flake8, mypy all clean. (Running with the miniconda *base* env produces spurious failures —
  wrong tool versions; don't use it.)
- **Backend (plain2code_rest_api):** run tests with the `plain2code_server` conda env. Baseline:
  225 tests pass. Prerequisites: local Postgres container running
  (`docker-compose -f docker-compose.dev.yml up -d`) and the gitignored `src/.env` present in
  the worktree (copied from the main checkout's `src/.env`; `load_dotenv()` resolves it relative
  to `src/config.py`).

## Background and goal

Today the renderer creates a **separate conformance test suite per FRID**
(`conformance_tests/<module>/<frid_folder>/`), and when generating tests for a new FRID the
backend's plan stage (`DeviseConformanceTestsPlanTemplate`) only sees lossy **text summaries**
of previous FRIDs' tests (`conformance_tests_json`). The implementation stage sees no prior
tests at all and outputs new files only, into a fresh folder.

Consequences, especially when specs build incrementally on one another:

- Duplicated test coverage across suites (each new suite re-tests ground already covered).
- Duplicated setup code (each suite re-implements the fixtures earlier suites already built).
- Inconsistent conventions between suites (naming, structure, helpers drift).

**Long-term direction (see "Roadmap after this iteration"):** one test suite per module that is
run as a whole (one script invocation per module) with shared setup, where rendering a new FRID
only **adds** tests — existing tests are never modified at render time. **This iteration** takes
the first, deliberately small step: keep everything as-is (per-FRID folders, new-files-only
output, folder naming, regression walk, fix loop) and only **send the existing conformance-test
files to the backend** so the render prompts can (a) avoid duplicating existing coverage and
(b) match existing conventions.

## Decisions already made

- Existing test files go to **both** prompt stages (plan + implementation).
- Send the **current module's own suites only** (required modules' copies stay covered by the
  `conformance_tests_json` summaries).
- `conformance_tests_json` summaries **stay in the prompt unchanged** (minimal prompt surgery).
- The new API field is **optional** → fully backward compatible with older clients.

## Delivery model

Implement one step at a time; **STOP after each step for user review** of code and results
before starting the next. Every step leaves both repos fully working: all tests and quality
gates green, old behavior preserved. Backend changes land **before** the client starts sending
the new field, so at no point does the client send something the server doesn't accept.

**Every step cleans up after itself.** Whatever a step makes obsolete (dead code paths, unused
state-machine wiring, scaffolding introduced by an earlier step) is removed in that same step —
there is no deferred "cleanup iteration" at the end.

| Step | Repo | What ships | Why the system still fully works after it |
|------|------|-----------|-------------------------------------------|
| 1 | codeplain | Fetch helper + unit tests (not yet called by the render flow) | Pure addition; render behavior unchanged |
| 2 | rest_api | API plumbing: optional request field threaded to the handler (prompts untouched) | Field accepted and logged but unused; old clients unaffected |
| 3 | rest_api | Prompt changes: inject the files into both templates (empty-safe) | No field sent → empty section → prompts render exactly as today |
| 4 | codeplain | Wire the helper into the render action + API client; feature live | End of feature; verified end-to-end against a local API |

---

## Step 1 — client fetch helper (`codeplain`)

**Goal:** a tested, unused building block that collects all existing conformance-test files of a
module.

### Changes

`render_machine/conformance_tests.py` — add to the `ConformanceTests` class:

```
fetch_all_existing_conformance_test_files(module_name) -> dict[str, str]
```

- Iterate folders from the existing `fetch_existing_conformance_test_folder_names(module_name)`
  (already excludes hidden `.<module>` copies of required-module tests — matches the
  "current module only" decision). Sort folder names for deterministic output.
- Per folder, reuse `file_utils.list_all_text_files` + `file_utils.get_existing_files_content`
  (same primitives the existing `fetch_existing_conformance_test_files` uses,
  `conformance_tests.py:147-166`).
- Key files as `<frid_subfolder>/<relative_path>` so the LLM can tell which suite a file
  belongs to.
- `conformance_tests.json` (the definition file) is naturally excluded — it lives at the module
  folder root and only subfolders are walked; cover this with a test.
- Returns `{}` when no prior suites exist (first FRID).

`tests/test_conformance_tests.py` — new test file covering: empty/missing module folder,
multi-folder collection incl. nested subdirectories, hidden-folder exclusion, definition-file
exclusion, binary-file skipping.

### Done-check (all must be green before review)

- `pytest tests/ -v` (new tests pass; no new failures vs. baseline)
- `black --check`, `isort --check-only`, `flake8`, `mypy . --check-untyped-defs`
  (no new complaints vs. baseline)

**→ STOP for user review.**

---

## Step 2 — backend plumbing (`plain2code_rest_api`)

**Goal:** the API accepts the new optional field end-to-end without using it yet.

### Changes

`src/app.py`:

- `render_conformance_tests_model` (line ~348): add
  `"existing_conformance_tests_files": fields.Nested(file_content_model, required=False,
  description="Content of the module's existing conformance test files, keyed by
  <frid_subfolder>/<relative path>")`.
- Route handler (line ~850):
  `existing_conformance_tests_files = data.get("existing_conformance_tests_files", {})`,
  passed through to `codeplain_instance.render_conformance_tests(...)`.

`src/codeplain.py` — `render_conformance_tests` (line ~1034):

- Accept the new parameter (default `{}`, `None`-safe).
- Log the received file count.
- **Do not use it in prompts yet.**

### Done-check

- Backend test posting to `/render_conformance_tests` **without** the field (backward compat)
  and **with** it — both accepted.
- Backend test suite green (no new failures vs. baseline).

**→ STOP for user review.**

---

## Step 3 — backend prompt changes (`plain2code_rest_api`)

**Goal:** both LLM stages see the existing test files; behavior identical when none are sent.

### Changes

`src/codeplain.py` — `render_conformance_tests`:

- Prefix incoming keys with the existing `CONFORMANCE_TESTS_FOLDER_MARKER` (line 67) the same
  way the fix flow does (line ~1296): `{f"{CONFORMANCE_TESTS_FOLDER_MARKER}/{key}": value ...}`
  and merge into `files_content` so the LLM chain can view file contents. Do **not** add them to
  `applicable_files_content` — they are read-only context, not implementation files.
- Build the prompt section with the existing `_get_conformance_tests_files_prompt_section`
  (line 217) — reuse, don't reimplement.
- Add the rendered section to the input dicts of **both** LLM calls (plan stage at ~line 1114,
  implementation stage at ~line 1183) under a new key, e.g.
  `"previous_conformance_tests_files"`. Empty string when nothing was sent (first FRID or old
  client) so templates degrade gracefully.

`src/prompt_templates/conformance_test_template.py`:

- New template block, e.g. `PREVIOUS_CONFORMANCE_TESTS_FILES_TEXT`: these are the actual source
  files of :ConformanceTests: implemented for previous functionalities; they are **read-only
  reference** — do not modify or re-emit them; use them to (a) avoid duplicating test coverage
  that already exists and (b) follow the same structure, naming conventions, fixtures and
  configuration patterns. (Mirror the phrasing of the acceptance-test template,
  `acceptance_test_template.py:33-43`, which already implements the "extend, don't duplicate"
  stance.)
- `DeviseConformanceTestsPlanTemplate` — extend Task 2 (the dedup task, ~line 175): a planned
  test is also removed if its expectations are **already implemented in the previous
  conformance-test files** (real code, not just summaries). Keep the existing summary-based
  criteria intact.
- `ConformanceTestsImplementationTemplate` — include the new section in
  `get_template_text_list()`; instruct that new tests must follow the conventions visible in the
  previous test files. Output format stays `LLM_SOURCE_OUTPUT_NEW_FILE` — unchanged.
- Both templates render the section conditionally (empty input → empty/omitted section).

### Done-check

- Backend tests green, covering both the with-field and without-field request paths.
- Prompt output with empty input is byte-identical to today's (no accidental drift for old
  clients).

**→ STOP for user review.**

---

## Step 4 — client wiring (`codeplain`)

**Goal:** the feature goes live; the client sends existing tests on every conformance render.

### Changes

`render_machine/actions/render_conformance_tests.py` — in `_render_conformance_tests` (the
full-conformance-render path only, **not** the acceptance path):

- Call the Step 1 helper for `render_context.module_name`.
- Print the files via `console.print_files` (matches the existing "Files sent as input..."
  pattern).
- Pass them as a new argument to `render_context.codeplain_api.render_conformance_tests(...)`.

`codeplain_REST_api.py` (lines ~306-339):

- Add parameter `existing_conformance_tests_files` to `render_conformance_tests`; include it in
  the JSON payload as `"existing_conformance_tests_files"`.

### Done-check

- Client tests + quality gates green.
- **End-to-end verification** (manual, per CLAUDE.md cross-repo workflow):
  1. Start local Postgres + API (`python src/app.py` in the backend worktree, port 5000).
  2. Render a multi-FRID example (e.g. `examples/example_hello_world_python`) with
     `--api http://127.0.0.1:5000`.
  3. Confirm: FRID 1 renders with an empty section; FRID ≥ 2 requests contain
     `existing_conformance_tests_files`; the plan-stage output shows dedup decisions that
     reference actual previous tests; rendered suites still pass their conformance runs.

**→ feature complete, final user review.**

---

## Roadmap after this iteration

### Iteration 2 — single-run suite with shared setup (two-tier immutability)

Decided design:

- **Keep the per-FRID subfolders inside the module suite** (minimal structural change). A
  failing test's path still names its FRID, so attribution stays free — no ownership index
  needed. Storage, folder naming, and `conformance_tests_json` stay as-is.
  - Scope note: the depth-1 "subfolder directly under the module folder" layout holds for all
    three shipped runners (python, golang, cypress) and is what Iteration 2 implements. For
    deep-layout ecosystems it generalizes later — see *Deep-layout ecosystems* below.
- **Whole-suite always — the test-script contract never changes.** The scripts' contract stays
  "run the tests found under `$2`"; the client simply passes the module suite folder
  (`conformance_tests/<module>/`) instead of a per-FRID subfolder. No script in this repo — and,
  critically, no user project's custom script referenced from `config.yaml` — needs editing.
  Consequence: there is no standalone-first run of the current FRID's fresh tests; every
  conformance invocation runs the whole module suite. The client classifies failures
  ("my new test is broken" vs. "new code broke an old test") from the failing tests' subfolder
  paths. Trade-off accepted: fix-loop cycles re-run the full suite (wall-clock cost on modules
  with many FRIDs); revisit a two-path script contract later only if that hurts in practice.
- **Two-tier immutability at render time** (refined from an earlier stricter "append-only"
  rule, which broke on legitimately evolving shared files — build manifests, shared helpers,
  model factories):
  - **Other FRIDs' test files are strictly immutable at render time** — the load-bearing
    guarantee: another functionality's expectations are never weakened or rewritten. Only the
    fix loop may edit them, same as today.
  - **Shared setup files (suite root: manifests, helpers, factories) are extendable at render
    time, insertion-only** — mechanically enforced: every existing line must survive in order;
    the new version may only insert lines (new factory/trait, new dependency line, new import).
    Deletions or edits of existing lines → reject. One diff check covers `pom.xml`,
    `requirements.txt`, and `factories.rb` uniformly.
  - Backstops: the whole-suite run immediately verifies extensions against old tests (an
    insertion that changes existing helper behavior fails fast and the fix loop repairs it),
    and the prompt instructs "add new entries; do not alter the behavior of existing helpers".
    Genuine modifications (e.g. bumping a pinned dependency version) remain fix-loop-only.

**Empirical grounding (verified 2026-07-18 on the Iteration 1 e2e project):** invoking the
unchanged `run_conformance_tests_python.sh` with the module suite folder as `$2` discovered and
ran both FRIDs' suites in one invocation (`Ran 2 tests ... OK`). This works because (a) the
Python template's `***test reqs***` already mandates `__init__.py` in test subfolders
(`python-console-app-template.plain:16`), making each per-FRID subfolder a distinct package, and
(b) `generate_folder_name_from_functional_requirement` already guarantees unique subfolder
names. The whole-suite premise is proven for Python; golang/cypress need the 2a audit.

Delivered in three self-contained chunks, each committed+pushed with the system fully working.

(A shadow-run phase was considered and dropped: shadow phases earn their keep when a change
ships to a fleet you observe, but codeplain renders happen on machines we don't — the only
shadow data would come from our own renders, which 2b's side-by-side verification provides
without building and then deleting renderer machinery.)

---

#### Chunk 2a — discovery-safe suites (loop-capable runners; no behavior change)

##### Audit findings (2026-07-19)

- **python — works as-is.** `unittest discover` recurses; per-FRID subfolders are packages
  (`__init__.py` pinned in the template). Proven empirically on the Iteration 1 e2e project.
- **golang — script change required.** The runner does no discovery at all: it executes one
  hardcoded file, `go run "$2/conformance_tests.go"` (`run_conformance_tests_golang.sh:79`).
  The single-main-file convention is pinned in the golang template
  (`golang-console-app-template.plain:21-23`) and stays. With `$2` = module folder there is no
  root `conformance_tests.go` → immediate failure.
- **cypress — script change required.** Each suite is a standalone Cypress project
  (`cypress.config.ts`, `package.json`, `cypress/e2e/...` — confirmed in
  `examples/example_hello_world_react/harness_tests/hello_world_display/`). The runner copies
  `$2/*` to a scratch dir and runs `npx cypress run` there — with `$2` = module folder there is
  no config at the copied root → hard fail. The runner also builds and starts the React app on
  every invocation — today paid once per FRID, so the flip amortizes it to once per module:
  the biggest single speedup of Iteration 2.
- **Templates need no changes**; suite structures stay as they are.

##### Resolution: subfolder-loop pattern, shipped in 2a

Updated golang/cypress runners implement "run all tests under `$2`" as an internal loop, with a
**degenerate-case check** that keeps today's behavior byte-identical: if `$2` itself looks like
a single suite (root `conformance_tests.go` / root `cypress.config.*`), run it directly exactly
as today. Because the client is untouched in 2a (still passes per-FRID folders → always the
degenerate path), 2a ships with zero behavior change; 2b then only flips `$2`.

##### Step 2a.1 — golang loop runner (`run_conformance_tests_golang.sh` + `.ps1`)

- Keep: arg validation, `/tmp/go_<name>` staging of `$1`, `go get` in the build folder, exit
  codes.
- Degenerate case: `$2/conformance_tests.go` exists → current behavior verbatim (including the
  optional `go get` in `$2` when it has a `go.mod`).
- Loop case: iterate sorted immediate subfolders of `$2` that contain `conformance_tests.go`;
  for each: optional `go get` in the subfolder (mirrors today's per-suite behavior), then
  `go run "<sub>/conformance_tests.go"` from the build dir; print a
  `=== conformance suite: <subfolder name> ===` header before each suite's output (feeds 2b's
  attribution).
- Aggregation: run **all** suites (don't stop at first failure — 2b needs the full implicated
  set); exit with the first failing suite's exit code; zero suites found → exit 1 (the
  "no tests discovered" convention).
- Verify: synthetic two-suite fixture from the golang example's harness artifacts + a
  single-suite degenerate check; golang example still renders green (client unchanged).

##### Step 2a.2 — cypress loop runner (`run_conformance_tests_cypress.sh` + `.ps1`)

- Keep: port-3000 cleanup, `$1` staging, `npm install` + `npm run build` + app start —
  **once per invocation** (Step 1 of the script is untouched).
- Degenerate case: `$2` has a root `cypress.config.*` → current behavior verbatim.
- Loop case: iterate sorted immediate subfolders of `$2` that contain `cypress.config.*`;
  for each: stage into the scratch dir (wipe between suites, preserving `node_modules` /
  `package-lock.json` as today), `npm install cypress` (cheap after first — offline cache),
  `npx cypress run`; same `=== conformance suite: <name> ===` headers.
- Aggregation: same as golang.
- Verify: two-suite fixture built from the react example's harness suite (duplicated with a
  second spec) + degenerate check; react example still renders green.

##### Verification matrix (2026-07-19, chunk implemented)

| Case | golang `.sh` (real go 1.25) | cypress `.sh` (stubbed npm/npx) |
|---|---|---|
| Degenerate single suite, pass | exit 0 ✓ | exit 0 ✓ |
| Degenerate single suite, fail | output printed, exit 1 ✓ | exit 1 ✓ |
| Module folder: pass+fail suites | both run with `=== conformance suite: <name> ===` headers, failure output under its header, exit 1 ✓ | same ✓ |
| Hidden `.module` subfolder | skipped ✓ | skipped ✓ |
| Empty module folder | "No conformance test suites discovered", exit 1 ✓ | same ✓ |
| Missing `$2` | exit 69 ✓ | exit 69 ✓ |

`.ps1` variants mirrored by careful review; not executable on this machine (no pwsh) — same
verification status as the repo's other PowerShell scripts. Python runner untouched.
Latent quirk noted (pre-existing, unchanged): the cypress script's
`npm install | grep -Ev <filter>` under `pipefail` fails if npm's entire output matches the
filter; real npm always emits a surviving line.

A full example render was not repeated: the client is unchanged and still invokes the scripts
with per-FRID folders — exactly the degenerate case verified above.

#### Chunk 2b — flip execution to the single run (client only; the core chunk)

Grounding fact that keeps this chunk small: the fix loop, memory creation, and conflict
detection all key off `conformance_tests_running_context.current_testing_frid` and derive the
suite folder from it (`fix_conformance_test.py:48-147`, `memory_management.py:35`). So the flip
reduces to: run whole suites, and on failure **set `current_testing_frid` via attribution**
before the existing machinery takes over.

Also note: once `TESTING_CURRENT_FRID` runs the whole own-module suite, it already covers every
prior FRID — the own-module regression walk doesn't just shrink, it **disappears**; regression
reduces to running each required module's copied suite.

##### Step 2b.1 — attribution + evidence helpers (pure addition, nothing calls them yet)

New module `render_machine/failure_attribution.py`:

- `attribute_failures(output, conformance_tests_json) -> list[str]`: FRIDs whose
  `folder_name` basename appears in the output, ordered by spec order (json insertion order).
- `extract_frid_failure_evidence(output, folder_basename) -> str`: best-effort per-FRID slice
  (Python unittest blocks are `======`-delimited); returns the full output when slicing fails.
- `format_other_frids_note(implicated_frids, current_frid) -> str`: the one-line "tests of
  functionalities X also failed in this run; handled separately" summary.
- `detect_layout_failure(output) -> bool`: **conservative** migration guard — fires only when
  zero tests ran AND an import-style signature is present ("Start directory is not importable",
  discovery-time `ModuleNotFoundError`). A legit test failure must never trip it.

Unit tests for all four (single/multiple/none implicated; slice + fallback; guard
true/false cases). Commit+push — behavior unchanged.

##### Step 2b.2 — the flip (one behavioral commit)

- `RunConformanceTests.execute` (`run_conformance_tests.py:19`): own module →
  `$2 = get_module_conformance_tests_folder(module_name)`; required module → the module-level
  copy root (`conformance_tests/<module>/.<required>/`, today's
  `get_source_conformance_test_folder_name` logic at module granularity — new small helper in
  `conformance_tests.py`).
- Phase orchestration (`render_context.py`): `TESTING_CURRENT_FRID` = one own-module suite run;
  the regression phase iterates required modules only. Acceptance-test phases unchanged (their
  re-runs are now whole-suite runs). "Code changed while fixing" simplifies from
  "restart the FRID walk" to "re-run the affected module suites".
- Post-failure routing (in `RunConformanceTests.execute`, before returning `FAILED_OUTCOME`):
  `attribute_failures` → set `ctx.current_testing_frid` (and module) to the earliest implicated
  FRID → build the failure payload from `extract_frid_failure_evidence` + `format_other_frids_note`
  instead of the raw output. Everything downstream — fix payload files,
  `is_previous_conformance_tests_issue`, conflict detection, memory keying — works unchanged.
  Order matters: attribution runs **before** `create_conformance_tests_memory`.
- Migration guard: `detect_layout_failure` → dispatch a render error ("regenerate conformance
  tests for this module; if using a custom conformance script, it must run all tests under
  `$2` recursively") instead of entering the fix loop.
- Budget: `ctx.fix_attempts` already lives on the per-implemented-FRID running context → the
  global cap holds with no change.
- Verify: full client suite + gates; **side-by-side verification** — throwaway script runs the
  per-FRID walk and the whole-suite invocation on the same rendered example and diffs verdicts;
  e2e render against the local backend; hand-break an earlier FRID's behavior in the build and
  confirm the fix loop targets that FRID's subfolder with filtered evidence. Commit+push.

##### Step 2b.3 — cleanup (same chunk, separate commit for reviewability)

Remove now-dead machinery + its tests: FRID-iteration branches of
`get_first/next_conformance_tests_running_context`, `_has_reached_implementation_frid`,
`_start_regression_phase`'s FRID bookkeeping, `code_changed_during_regression` restart logic,
and the `MOVE_TO_NEXT_CONFORMANCE_TEST` transitions in `state_machine_config.py:338-399` that
implement the walk (module iteration and acceptance-phase transitions stay). `requires`-chain
copying and `conformance_tests_json` bookkeeping stay. Full suite + gates green; one more e2e
render. Commit+push.

#### Chunk 2c — shared setup at the suite root (backend prompts + client storage)

Depends on 2b. **Scope: flat layouts (python) only** — golang/cypress suites stay standalone
per-suite projects under the loop runners; consolidating them to a shared root belongs to the
deep-layout forward path below.

##### Step 2c.1 — backend: output-path contract + prompts

- Conformance render output paths become **module-suite-root-relative**. The prompt still
  receives the FRID subfolder name and instructs: test files go under
  `<frid_subfolder>/`; shared helpers/fixtures may be placed at the suite root; reuse existing
  root helpers instead of re-implementing setup; other functionalities' test files must never
  be re-emitted or modified; shared setup files at the suite root may be extended by emitting
  the full new version, adding lines only, without altering the behavior of existing helpers.
  Rewrite `CONFORMANCE_TESTS_FOLDER_NAME_HINT` (`conformance_test_folder_names.py`)
  accordingly; extend the Iteration 1 previous-files section with the reuse instruction; same
  treatment for `AcceptanceTestsImplementationTemplate`. Output format stays
  `LLM_SOURCE_OUTPUT_NEW_FILE`.
- No new required API field: the client keeps sending `conformance_tests_folder_name` (the FRID
  subfolder); the module root is its parent by construction.
- Template tests extended. Backend suite green. Commit+push (backward compatible: old clients
  keep old-style paths because the hint text is driven by the same field they already send —
  verify this explicitly in tests).

##### Step 2c.2 — client: storage, context, and the two-tier guard

- Store render/acceptance response files relative to the **module folder**
  (`render_conformance_tests.py:135` and the acceptance path) instead of the FRID subfolder.
- `fetch_all_existing_conformance_test_files` (`conformance_tests.py`): **include root-level
  files** (shared helpers) — today it walks subfolders only; keep excluding
  `conformance_tests.json`. The render-context exclusion of the current FRID's subfolder stays.
- **Two-tier guard** on stored render responses:
  - a response file whose path matches an existing file in another FRID's subfolder → reject
    (retry the render call once with the violation named; then fail the render);
  - a response file whose path matches an existing suite-root shared file → insertion-only
    diff check (every existing line survives, in order — `difflib` opcodes contain no
    `delete`/`replace`); violation → same reject-retry-fail path;
  - new paths → store normally.
- `conformance_tests_json` `folder_name` bookkeeping unchanged. Client suite + gates green;
  e2e: render a multi-FRID python example, confirm FRID ≥ 2 imports a root helper instead of
  duplicating setup, whole suite green. Commit+push.

##### Step 2c.3 — summaries decision (evaluation, then possibly a removal commit)

With real files in prompts (Iteration 1) and one-run suites (2b), evaluate whether the
per-FRID `summarize_finished_conformance_tests` LLM call still earns its cost. The
`folder_name` map in `conformance_tests_json` **must stay** (attribution and fix routing depend
on it) — the question is only the `test_summary` content and its LLM call. Criteria: render the
examples with summaries suppressed from the plan prompt and compare dedup quality; if no
degradation, drop the call (client + backend + prompt cleanup in both repos). Record the
decision and evidence here either way.

#### Deep-layout ecosystems (Java/Maven and similar) — deferred until Iteration 2 works

Decision (2026-07-19): a Java example will be added to `examples/` **after** the first
implementation (chunks 2a-2c) works properly. At that point: audit the example's actual suite
structure (extend 2a's findings), add a Java loop runner as step 2a.3 (degenerate case: root
`pom.xml`; loop case: `mvn test` per subfolder — 2b then covers Java with no further change),
and turn this section into a concrete chunk 2d (single Maven project per module). Recorded here
so Iteration 2's decisions don't paint us into a corner. Theme: **the suite root owns language-specific
structure; a FRID owns only its namespace.**

- **Target shape (Java example):** one Maven project per module —
  `conformance_tests/<module>/pom.xml` at the suite root (shared setup per chunk 2c), shared
  fixtures under `src/test/java/<base_package>/support/`, and one package per FRID
  (`src/test/java/<base_package>/<frid_slug>/`). One `mvn test` runs everything.
- **Folder-name generation is reinterpreted, not removed:**
  `generate_folder_name_from_functional_requirement` keeps producing a unique,
  identifier-safe **namespace slug** per FRID (current outputs are already valid Python and
  Java package names). What changes: the client stops composing the full path from the slug;
  the prompt instead says "this functionality's tests live in a namespace/folder named
  `<slug>`, placed where the suite's layout requires." Depth-1 for Python/golang/cypress
  (byte-identical to Iteration 2 behavior); a package subtree under `src/test/java/` for Java.
- **Recorded path, not assumed path:** when storing response files, the client locates the
  directory matching the slug (`**/<slug>/`) and records the actual path in
  `conformance_tests_json` alongside the slug. Fix-loop payloads, regeneration deletes, and
  attribution all work from the recorded path. Slug uniqueness is checked against the known
  slugs in `conformance_tests_json` rather than a filesystem listing.
- **Attribution is unchanged in mechanism:** the slug appears in fully qualified test names
  (`com.example.conformance.<frid_slug>.FooTest`) and matches against failure output the same
  way a depth-1 folder name does.
- **Build manifests are covered by the two-tier rule:** a shared `pom.xml` / `package.json` /
  `go.mod` at the suite root is a shared setup file — extendable at render time under the
  insertion-only diff check (see the design bullets above), so a later FRID can add a
  dependency without a fix-loop round-trip. Version bumps and other in-place edits stay
  fix-loop-only.

**Migration note:** projects rendered before the flip may have suites that collide in a joint
run. The intended answer for old projects is regenerating conformance tests on the next full
render (surfaced by 2b's migration guard), not compatibility machinery.
Custom user scripts: 2b deepens `$2` (module folder instead of one suite subfolder). Scripts
that genuinely "run all tests under `$2`" recursively are unaffected; scripts that hard-assumed
the old layout fail fast on their first post-flip render — 2b's migration guard should
recognize this shape too and say "your conformance script must run all tests under `$2`
recursively" instead of burning fix attempts. Document the semantic change in the release
notes.

**Logistics (decided 2026-07-19):** Iteration 2 is developed on
`feature/conformance-single-run-suite` in both repos, stacked on the Iteration 1 branches
(`feature/improve-conformance-testing`, PRs #252 / #122), and goes into separate PRs targeting
those branches (retarget to `main` once the Iteration 1 PRs merge).

## Out of scope (removed from the roadmap)

Render-time **modification** of existing tests — and everything it would require (declared
supersession, test→FRID ownership index, fix-diff constraints) — is **completely out of scope
for now**. It may be reconsidered only after Iteration 2 has proven itself in practice.
