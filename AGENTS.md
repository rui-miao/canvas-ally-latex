# Agent guide for canvas_ally_latex

This file applies to the entire `canvas_ally_latex/` subtree. Treat this
directory as an independent Python project. Work from this directory unless a
command explicitly says otherwise.

## Mission

`canvas-ally-latex` is a Python 3.11+, standard-library-only command-line
tool. It copies a LaTeX project, transforms only the selected document's
statically reachable sources, builds the copy with LuaLaTeX, and audits the
resulting PDF for accessibility signals relevant to Canvas/Ally.

The tool is a local readiness aid, not Canvas/Ally. Never claim, estimate, or
encode a proprietary Ally score. A passing local audit or PDF/UA validator is
strong preflight evidence, but the actual Ally result exists only after upload.

Keep these user-facing surfaces synchronized when behavior changes:

- `README.md`
- `--help` text in `src/canvas_ally_latex/cli.py`
- JSON fields and status semantics in `model.py`
- the example configuration in `examples/canvas-ally.toml`
- focused tests and end-to-end fixtures

## Start here

Read `README.md`, `pyproject.toml`, and the relevant source and test
files before editing. The package deliberately has no Python runtime
dependencies.

Run the baseline checks from this directory:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -q
ally_pyc_dir="$(mktemp -d /tmp/canvas-ally-pyc.XXXXXX)"
PYTHONPYCACHEPREFIX="$ally_pyc_dir" python3 -m compileall -q src tests
PYTHONPATH=src python3 -m canvas_ally_latex --help
```

The temporary bytecode prefix keeps `compileall` artifacts out of the project.

Do not use a direct path such as `python3 tests/test_cli.py` as the only
test gate. Use discovery or the module commands below so imports behave the
same way as the full suite.

## Architecture

| File | Responsibility |
| --- | --- |
| `src/canvas_ally_latex/cli.py` | Argument parsing, report-path safety, orchestration, terminal output, build gating, and exit codes. |
| `src/canvas_ally_latex/config.py` | Defaults, TOML and JSON alt-map loading, validation, and CLI precedence. |
| `src/canvas_ally_latex/project.py` | Project resolution/copying, reachable dependency closure, project-wide placeholder allocation, transformation coordination, support-file emission, and conservative source expectations. |
| `src/canvas_ally_latex/transform.py` | TeX masking/parsing, visual annotations, table handling, Beamer-to-`ltx-talk` migration, and generated LaTeX support strings. |
| `src/canvas_ally_latex/build.py` | Symlink hardening, safe TeX caches, LuaLaTeX/`latexmk` execution, timeouts, logging, and cleanup. |
| `src/canvas_ally_latex/audit.py` | Poppler structure/font/text checks, source-to-PDF minimum checks, and veraPDF XML parsing. |
| `src/canvas_ally_latex/model.py` | Findings, checks, audit results, preparation results, JSON representation, and readiness aggregation. |
| `tests/fixtures/` | Small real article and Beamer projects for transformation and build smoke tests. |

The main flow is:

1. `resolve_project()` validates the source, main file, and output
   relationship.
2. `prepare_project()` copies the source and resolves the reachable TeX
   dependency closure.
3. One shared `PlaceholderAllocator` is seeded from all active reachable
   sources.
4. `transform_tex_file()` runs its ordered rewrite pipeline on the copied
   files.
5. `build_project()` compiles only the copied project.
6. `audit_pdf()` checks the generated PDF, including conservative
   source-derived tag minima.
7. `PrepareResult.as_dict()` produces the complete machine-readable
   result.

## Non-negotiable safety contracts

### Source and output isolation

- Never edit the input project. All transformed TeX, generated support files,
  logs, auxiliary files, PDFs, and reports belong in validated output
  locations.
- Do not call `copy_project()` with paths that bypass
  `resolve_project()`.
- The output may not equal the source, be nested inside the source, or contain
  the source.
- `--force` authorizes replacement only of the resolved generated output
  directory or JSON report. It never authorizes source mutation.
- `--force-build` is separate: it bypasses the source-finding build gate,
  not path safety and not a real LaTeX compilation failure.
- A JSON report must end in `.json`, remain outside the source tree, and
  never overwrite or alias an input, checked PDF, generated main file, or
  generated PDF. Preserve the hard-link, symlink, and atomic-replacement
  protections in `cli.py`.

### Symlinks, subprocesses, and generated files

- Materialize copied file symlinks before writing or building so a generated
  write cannot reach the source target.
- Reject copied directory symlinks at build time.
- Unlink symlinked TeX output names before compilation.
- Preserve the owner-only TeX-cache fallback and build/audit timeouts.
- Invoke external tools with argument lists, not shell-built command strings.
- Do not add shell escape to the LaTeX invocation.
- Do not overwrite conflicting user-owned content in `canvasally.sty`,
  `canvasally-ltx-talk-shim.tex`, or
  `canvasally-ltx-talk-content-shim.tex`. A byte-identical existing ordinary
  file is accepted, and a byte-identical symlink is materialized safely;
  differing content must remain an explicit failure.
- TeX is executable input. This tool prevents accidental source-tree writes;
  it is not a sandbox for hostile TeX.

## Findings, reports, and exit codes

Findings use `info`, `warning`, or `error`. Audit checks are
blocking by default; a failed `minor` check is nonblocking. Do not change
severity or aggregation casually because CI behavior and report meaning depend
on it.

| Exit | Contract |
| ---: | --- |
| `0` | Requested work completed with no blocking source or audit result. A clean `--no-build` preparation may therefore return 0 without a PDF. |
| `1` | At least one source `error` or major audit check remains. Without `--force-build`, a source error skips compilation. With it, build and audit proceed, but the report remains failed and exit 1 is preserved. |
| `2` | Configuration, input, report safety, preparation, required audit tooling, or LaTeX compilation failed. |
| `3` | Beamer migration was explicitly rejected with `--beamer-mode reject`. |

Warnings normally yield `pass_with_source_review`, not a blocking exit.
`--strict` promotes unresolved cases for which the tool cannot prove a
safe accessible rewrite. `--force-build` never downgrades or suppresses
those errors. `--no-build` remains authoritative.

## TeX rewriting rules

This is a conservative lexer/rewriter, not a TeX expansion engine.

- Use `mask_tex()`, the visual scan masks, balanced-group parsing, and
  reverse non-overlapping replacements. Do not replace them with broad regular
  expression rewrites.
- Masks must preserve string length and newlines. Offsets, original line
  numbers, directive ownership, and later passes rely on that property.
- Ignore comments, protected/verbatim environments, inline-code commands, URL
  and path destinations, escaped control symbols, definition syntax, and
  statically unreachable literal branches. Active `\iftrue` content
  must remain visible to transforms.
- Do not rewrite common macro or environment definitions. If a definition
  wraps a visual renderer and calls cannot be resolved safely, preserve it and
  emit `unsupported-visual-in-definition`.
- Transform only statically reachable `\input`, `\include`, and
  `\subfile` dependencies. Dynamic or missing dependencies produce
  findings rather than guessed paths.
- Preserve all bytes after the first active `\end{document}` in the
  main file and after an active `\endinput` in a dependency.
- The order inside `transform_tex_file()` is intentional. Marker
  placement, native annotations, nested ownership, and idempotence depend on
  it. Any reordering requires focused regression tests.
- A second transformation pass must be byte-idempotent. Review findings for
  unresolved placeholders must still be emitted on every run.

## Visual alternatives and placeholder invariants

The placeholder namespaces are `graphicN`, `tikzN`,
`pictureN`, and `visualN`.

- IDs are deterministic and project-wide within each kind. Seed one allocator
  from every active reachable source and configured alternative value, and
  reserve all existing IDs.
- Excluded files, inactive conditionals, presentation notes, omitted overlays,
  and source tails after active end boundaries must not consume IDs.
- For a supported visual, prefer an authored native annotation/directive, an
  alt-map entry, a usable caption, or a decorative map entry. Only the safe
  final fallback receives both an immediately preceding
  `% allytex: alt=<kind>N` directive and the corresponding native
  `alt={...}` option. Stateful defaults or syntax whose extent cannot be
  proved produce findings instead of a guessed rewrite.
- A decorative/null map entry produces `% allytex: decorative` plus the native
  artifact annotation instead of a placeholder or alternative text.
- Supported native targets include `\includegraphics`, balanced
  `tikzpicture`, braced inline `\tikz`, and standard
  `picture` environments.
- A directive belongs only to the first non-whitespace construct that follows
  it. It must not leak to a later visual on the same line.
- Placeholder findings persist: normally a warning and an error under
  `--strict`. The generated words are review markers, not meaningful
  descriptions.
- Preserve authored `alt`, `artifact`, meaningful
  `actualtext`, `tagging-setup`, and custom structure tags.
  `actualtext` is symbol replacement, not automatically a Figure
  alternative. Empty or generated-placeholder `actualtext` may be
  repaired; intentional text must not be overwritten.
- If a generated directive and native option differ, a meaningful authored
  value may replace a placeholder. Two conflicting meaningful values are a
  blocking conflict.
- Do not override possible stateful `Gin` or TikZ accessibility defaults.
  Require a local directive/native annotation and emit the relevant finding.
- Resolve line-qualified alt-map entries before any transformation that shifts
  source lines. Keys such as `file.tex::tikz:LINE` refer to original
  copied-source lines.

### Visual ownership and unsupported constructs

- Ownership is outermost-first. An outer visual normally suppresses nested
  visuals so a composite receives one alternative and one expectation.
- Native TikZ or standard-picture `text` mode exposes its children; otherwise
  the outer drawing's alternative owns the nested content.
- An unsupported outer visual environment owns nested graphics, TikZ,
  pictures, and other unsupported visuals. An unannotated environment owner
  gets one stable `visualN` marker/finding, not duplicate child placeholders.
- Unsupported commands do not automatically own every visual found in their
  arguments because command signatures differ. Preserve current independent
  child detection unless the command's extent and ownership are explicitly
  parsed and covered by focused tests.
- Every unsupported command or environment remains blocking because a comment
  or authored directive alone does not create verified PDF structure. An
  existing meaningful or decorative directive suppresses a generated
  `visualN` placeholder, but never suppresses the hard unsupported blocker.
- Braceless or dynamically bounded inline TikZ is a blocker, not a safe rewrite
  target.
- When adding renderer coverage, keep
  `UNSUPPORTED_VISUAL_COMMANDS`,
  `UNSUPPORTED_VISUAL_ENVIRONMENTS`, dynamic/direct detection, ownership,
  source expectations, tests, and README documentation synchronized.
- Match exact control-sequence and environment boundaries; near names and
  literal examples must not be treated as renderers.

## Source-expectation contract

`project._source_expectations()` records conservative lower bounds for
structure tags that the generated PDF must contain. It is not a raw token
count.

- Transformation and expectation calculation must share reachability,
  conditional reduction, masking, annotation, ownership, Beamer-mode, and
  overlay semantics.
- A false-positive minimum makes an accessible PDF fail. Exclude content whose
  emission cannot be guaranteed and add an explicit review finding instead.
- Do not count artifacts, meaningful `actualtext`, native text-mode
  drawings, or tagging-disabled visuals as Figures.
- Count a custom `tag=Formula` as Formula, not Figure.
- Math owned by a diagram alternative is not independently required. Math
  exposed by native text mode may contribute Formula structure.
- Any parser or transform change that changes rendered reachability requires a
  paired source-expectation test.

## Beamer and overlay contract

Do not tag ordinary Beamer directly. Current Beamer boxing/reuse does not
provide a trustworthy tagged PDF. In the copied output, either migrate to
`ltx-talk` or reject the source explicitly.

- Preserve the original Beamer project.
- Map supported frames, blocks, columns, modes, and overlays statically.
- Omit Beamer navigation controls, presentation-only notes/hooks, and
  theme-only machinery from the static accessible copy where intended.
- Keep unsupported or lossy constructs visible as findings.
- `ltx-talk` normally uses `tag-slides=n`; only the final generated
  slide is tagged. Never inflate audit minima with content that may appear only
  on an earlier untagged slide.
- For only-like branch selection and direct overlay-aware graphics, count
  default/all, star, and provably active positive open-ended ranges on the
  final slide. Exclude exact, bounded, relative-only, or dynamic selections
  unless final-slide emission can be proved. This exclusion does not apply to
  `\visible`, `\uncover`, or their environments: after preprocessing, their
  bodies execute and remain tagged even when their visibility overlay is
  exact or bounded.
- Preserve the `ambiguous-overlay-tag-minimum` review path for uncertain
  selection or nondefault frame-level `tag-slides`.
- Normalize safe balanced `onlyenv` blocks to scoped `\only`
  constructs so discarded boxes cannot leak orphan tags. Preserve unsafe
  verbatim, nested-overlay, or state-changing bodies and report
  `unsafe-onlyenv-tagging`.
- After mode/empty preprocessing, `\visible`, `\uncover`, and
  their environments retain and tag their bodies. False `\alert` and
  `\structure` overlays retain their body and omit only styling.
- For `\alt`, a selected final branch and a statically empty fallback
  must follow the established tests. For `\temporal`, preserve native
  execution while excluding branches that cannot contribute final tags.
- Overlay changes require both unit coverage and a real compiled tag-tree
  inspection. A visually correct PDF can still have orphaned or duplicated
  structure.

## Build and audit contracts

### Build

- Use LuaLaTeX through `latexmk -norc -lualatex`. Do not silently fall
  back to pdfLaTeX.
- Keep `-interaction=nonstopmode`, `-halt-on-error`,
  `-file-line-error`, `-synctex=0`, the 15-minute build timeout,
  and the captured `canvas-ally-build.log` unless a tested change
  explicitly revises the contract.
- Root `TEXINPUTS` in the copied project and keep TeX caches out of user
  source.
- A compile success alone never establishes accessibility.

### Audit

- Poppler's `pdfinfo`, `pdftotext`, and `pdffonts` are
  required for a PDF audit.
- veraPDF is optional. Its absence adds a note and must never be described as
  formal validation.
- When available, invoke veraPDF as
  `verapdf --format xml --flavour ua2 PDF` with the existing timeout.
  Do not use automatic flavour detection; an unclaimed PDF can silently fall
  back to PDF/A.
- Parse XML from stdout and ignore unrelated Java warnings on stderr. Accept a
  PDF/UA-2 report only when the process exits 0, the job ends normally,
  `isCompliant=true`, and failed-rule and failed-check counts are zero.
- A passing PDF/A or WTPDF-only profile is not a PDF/UA-2 pass.
- A PDF/UA XMP identification claim is a minor metadata signal, not validation.
- Preserve the distinction between the standalone PDF audit result and the
  full preparation result. A forced PDF may pass PDF/UA while source placeholder
  errors correctly keep `overall_ally_readiness=fail`.

## Test map

| Suite | Change areas |
| --- | --- |
| `tests/test_transform.py` | Main instrumentation, graphics/TikZ/picture alternatives, migration, dependency selection, and idempotence. |
| `tests/test_placeholder_edge_cases.py` | Lexical masking, exact-name safety, unsupported visuals, ownership, directives, line mapping, definitions, and stateful annotation. |
| `tests/test_overlay_expectations.py` | Final-slide overlay selection, modes, temporal/alt behavior, `onlyenv`, and conservative minima. |
| `tests/test_audit.py` | Poppler parsing, source minima, formula/figure/table checks, and PDF/UA-2 profile parsing. |
| `tests/test_build.py` | Cache and symlink-safe build behavior. |
| `tests/test_cli.py` | Report-path safety, orchestration, force-build behavior, JSON, and exit status. |

Useful focused commands:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest \
  tests.test_cli tests.test_build tests.test_audit -q

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest \
  tests.test_transform tests.test_placeholder_edge_cases \
  tests.test_overlay_expectations -q
```

Every new visual or annotation mode needs focused tests for:

- unannotated insertion;
- authored annotation preservation;
- normal and strict severity;
- byte-idempotent reruns with persistent findings;
- exact-name, near-name, literal, and inactive-content safety;
- nested ownership;
- definition wrapping;
- project-wide placeholder numbering; and
- matching conservative source minima.

## Real end-to-end verification

Mocked unit tests are insufficient for changes to generated LaTeX, Beamer
migration, build behavior, tag trees, audit parsing, or veraPDF.

Use fresh scratch output outside the source:

```sh
ally_test_dir="$(mktemp -d /tmp/canvas-ally-smoke.XXXXXX)"

PYTHONPATH=src python3 -m canvas_ally_latex \
  tests/fixtures/article/main.tex \
  -o "$ally_test_dir/article" \
  --report-json "$ally_test_dir/article.json"

PYTHONPATH=src python3 -m canvas_ally_latex \
  tests/fixtures/beamer/main.tex \
  -o "$ally_test_dir/beamer" \
  --report-json "$ally_test_dir/beamer.json"

PYTHONPATH=src python3 -m canvas_ally_latex \
  tests/fixtures/beamer_modes/main.tex \
  -o "$ally_test_dir/beamer-modes" \
  --report-json "$ally_test_dir/beamer-modes.json"
```

For the force-build path, the article fixture deliberately contains generated
placeholders:

```sh
PYTHONPATH=src python3 -m canvas_ally_latex \
  tests/fixtures/article/main.tex \
  -o "$ally_test_dir/article-strict" \
  --strict --force-build \
  --report-json "$ally_test_dir/article-strict.json"
```

That final command is expected to build and audit a PDF but exit 1 because
source `ERROR` findings remain.

For every end-to-end run, inspect more than the process status:

- the transformed main and dependency `.tex` files;
- `canvas-ally-build.log`;
- the JSON report and its overall status;
- extracted text;
- `pdfinfo -struct` Formula, Figure, Table, List, and heading structure;
- font embedding and Unicode maps; and
- the veraPDF profile and failed-rule/check counts when veraPDF is installed.

For overlay changes, inspect actual structure ownership and tag counts, not
only the rendered page. For accessibility output changes, a real PDF/UA-2 pass
is required when veraPDF is available.

## Change checklist

### CLI or configuration

- Update parser help, precedence, dry-run JSON, README, and CLI tests.
- Preserve path-safety validation before writes.
- Verify all four exit-code meanings.
- If adding a persistent configuration key, update `ToolConfig`, allowed
  keys, type validation, example TOML, and precedence tests.

### TeX transform or new visual

- Update masking/detection, rewrite logic, ownership, placeholder allocation,
  source expectations, documentation, and all edge-case tests together.
- Test meaningful authored annotations and conflicts, not only placeholders.
- Verify original line numbers and a byte-identical second pass.

### Beamer or overlay

- Test projector and handout modes.
- Test selected, unselected, ambiguous, empty, relative, bounded, and
  open-ended overlays.
- Compile a real fixture and inspect the structure tree.

### Audit

- Add parser fixtures for both a pass and a misleading/nonconforming result.
- Ensure malformed or missing evidence fails closed rather than claiming a
  pass.
- Keep external-tool warnings separate from machine-readable stdout.

### Release metadata

- Keep the version in `pyproject.toml` and
  `src/canvas_ally_latex/__init__.py` synchronized when intentionally
  releasing a new version.
- Do not add generated PDFs, TeX auxiliary files, caches, virtual environments,
  or scratch reports to the source package.

## Definition of done

A change is complete only when:

1. the source project remains untouched;
2. the transformation is conservative and idempotent;
3. findings, JSON, terminal output, help, and README agree;
4. the full unit suite and compile check pass;
5. relevant real LaTeX fixtures build in fresh output directories;
6. the generated PDF's structure and text were inspected;
7. veraPDF PDF/UA-2 passes when the change affects accessible output and the
   validator is available; and
8. unresolved content-quality limitations remain visible instead of being
   converted into a false success.
