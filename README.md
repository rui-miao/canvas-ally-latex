# Canvas Ally LaTeX Cleaner

`canvas-ally-latex` is a standalone command-line tool for preparing LaTeX projects and auditing their generated PDFs for common Canvas/Ally accessibility problems. It is designed for ordinary documents and presentation sources, with explicit handling for Beamer.

The tool follows the useful workflow of [`arxiv_latex_cleaner`](https://github.com/google-research/arxiv-latex-cleaner): point it at a project, receive a separate, ready-to-build copy. It does **not** edit the source project in place. Its goal is an accessible PDF with meaningful document structure—not merely a visually unchanged PDF that happens to compile.

> [!IMPORTANT]
> Canvas/Ally assigns its proprietary accessibility score only after a file is uploaded. This tool reports local **Ally readiness** checks; it does not invent or promise an Ally score. Review the PDF and its Ally report after upload.

## What it does

- Copies a LaTeX project to a sibling output directory (by default, `<project>_ally`).
- Inserts current LaTeX PDF-management and tagging metadata before `\documentclass`.
- Sets document language and PDF title metadata.
- Enables PDF/UA-oriented structure with LuaLaTeX.
- Adds alternatives for supported graphics, TikZ, inline TikZ, and `picture` content, using figure captions or immediately adjacent authored diagram descriptions when available, and leaves numbered review markers for unsupported visual formats.
- Marks configured decorative images as artifacts.
- Adds table-header tagging when requested, including conservative recognition of grouped column headings with a row-label column.
- Preserves tagged math and enables formula alternative text generated from the LaTeX source.
- Omits Beamer navigation controls in migrated static presentations.
- Transforms only the selected main file and its statically reachable `\input`, `\include`, and `\subfile` dependencies.
- Stops at the main file's active `\end{document}`; trailing notes or scratch TeX are copied unchanged but are not treated as rendered content.
- Builds the copied project and audits the resulting PDF with locally available tools.
- Removes a copied, stale same-named PDF before each build so a failed compile cannot be mistaken for a newly generated result.
- Writes machine-readable JSON when requested, making it suitable for CI or batch course-material workflows.

## Why Beamer is migrated

The [current LaTeX tagging status](https://latex3.github.io/tagging-project/tagging-status/full) lists the `beamer` class as unsupported for tagged PDF output because Beamer repeatedly boxes and reuses slide content. Adding `\DocumentMetadata` to a Beamer source is therefore not a reliable accessibility fix.

When the input uses Beamer, the default `beamer_mode = "ltx-talk"` migrates the **copied output** to LaTeX's accessibility-oriented `ltx-talk` class. Common frame, overlay, block, column, and fragile-frame constructs are adapted, including Beamer-only overlay modes mapped to `ltx-talk`'s projector/handout semantics. The original Beamer project remains unchanged.

Beamer's `aspectratio`, font-size, and `handout` class options are mapped to
their `ltx-talk` equivalents. Options supplied through Beamer's `xcolor=...`
class option are forwarded to `xcolor` before `ltx-talk` loads it, preserving
named palettes such as `dvipsnames` in the generated copy.

When a selected Beamer theme has a same-named `beamercolortheme*.sty` file in
the source project, the generated copy loads that local color theme behind the
compatibility shim. This preserves project-defined color names and generic
setup such as `tcolorbox` styles without loading the theme's Beamer-specific
layout and template code. Only theme selections that are statically active in
the target projector or handout mode are considered; commands inside macro
definitions or inactive conditional/mode branches do not cause a package load.

The compatibility layer also accepts Beamer's overlay-aware
`\newcommand<>`/`\renewcommand<>` syntax and restores common implicit support
used by older decks, including theorem declarations, the legacy `\rm`, `\sf`,
`\tt`, `\bf`, `\it`, `\sl`, and `\sc` font switches, and text-font
`\checkmark` idioms. Beamer `\action` wrappers that cross alignment cells are
split into per-cell `\uncover` commands when their overlay can be reused
without changing its step; named, dynamic, multi-step, or state-changing cases
remain explicit blockers. When a display or paragraph boundary proves that a
standalone `\vspace{...}\\` is already in vertical mode, its redundant trailing
line break is removed because `ltx-talk` rejects it. The migration also converts `subcaption` subfigures
to minipages, maps traditional float placement options, and flattens a
provably one-row graphics-only `tabular` so a visual layout is not exposed as
a data table. These rules target static accessible output; custom visual
wrappers that are actually called remain review blockers because their
rendered calls cannot be annotated reliably.

Use `--beamer-mode reject` if a migration should fail explicitly instead of being attempted. The tool never labels an ordinary Beamer build as accessible merely because compilation succeeded.

## Requirements

- Python 3.11 or newer; the Python package has no runtime dependencies.
- A recent TeX Live installation with LuaLaTeX, `latexmk`, LaTeX PDF management/tagging support, and `ltx-talk` for presentation migration.
- Poppler command-line utilities (`pdfinfo`, `pdftotext`, and `pdffonts`) for local PDF checks.
- Optional: veraPDF. If installed, the audit explicitly runs its PDF/UA-2 profile and requires that profile to pass.

LuaLaTeX is required for the generated accessible build. An older TeX distribution may understand the source but lack the tagging features this tool relies on; update TeX Live rather than falling back to pdfLaTeX.

## Install

From this repository:

```sh
cd canvas_ally_latex
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
canvas-ally-latex --help
```

The equivalent compatibility command is `ally-latex-cleaner`. The module entry point also works:

```sh
python -m canvas_ally_latex --help
```

## Basic use

Prepare, build, and audit a project directory:

```sh
canvas-ally-latex path/to/slides --main lecture.tex
```

Prepare a project by naming its main file directly:

```sh
canvas-ally-latex path/to/slides/lecture.tex
```

Choose the output location and replace a previous generated copy explicitly:

```sh
canvas-ally-latex path/to/slides \
  --main lecture.tex \
  --output-dir path/to/lecture_ally \
  --force
```

The output directory is never merged silently. If it already exists, the command stops unless `--force` is supplied. An existing JSON report also requires `--force`. This never authorizes modification of the input project.

The generated copy is made readable and writable by its owner, and copied
directories are made searchable, so read-only source permissions do not block
transformation or compilation. Permissions in the input project and on
symlink targets are left unchanged.

Before compiling, copied file symlinks are materialized as ordinary files so TeX cannot write through them. Directory symlinks that remain in the generated project are rejected at build time; materialize those directories in the source or generated copy first.

Preview the actual transformations and source findings without retaining a
project copy or running a build:

```sh
canvas-ally-latex path/to/slides --main lecture.tex --dry-run
```

The preview transformation runs in a temporary directory and reports the
same source findings as normal preparation. It returns status `1` when those
findings include a blocking error. A requested JSON report is still written
to its explicit destination.

Prepare sources without compiling:

```sh
canvas-ally-latex path/to/slides --main lecture.tex --no-build
```

Compile and audit even when source preparation reports blocking `ERROR`
findings:

```sh
canvas-ally-latex path/to/slides --main lecture.tex --strict --force-build
```

`--force-build` creates the best available PDF for diagnosis and upload
testing. It does not suppress the findings: the report remains failed and the
command exits with status `1` until those accessibility errors are resolved.
It does not override `--no-build`. The separate `--force` option continues to
mean only “replace an existing generated copy or JSON report.”

Audit an existing PDF without transforming a source project:

```sh
canvas-ally-latex --check path/to/lecture.pdf
```

Write a JSON report in addition to the terminal summary:

```sh
canvas-ally-latex path/to/slides --main lecture.tex \
  --report-json ally-report.json
```

Report names must end in `.json`. To uphold the no-in-place-edit guarantee, a
report cannot be written inside the source project, over a checked/generated
PDF, or at the generated output directory path itself.

## Article workflow

For article-like classes, the copied main file keeps its document class. The cleaner inserts a `\DocumentMetadata` declaration before the class, loads its generated support package, instruments figures and tables in the selected document's dependency closure, then builds with LuaLaTeX. Other independent documents copied from the same directory are left alone.

In `first-row` table mode, an ordinary table receives first-row column headers.
The cleaner also recognizes a conservative cross-tab pattern: a blank top-left
cell followed by pure grouped `\multicolumn` headings, a complete second row of
column labels, and simple body rows with labels in column one. That pattern is
tagged with both header rows and the first row-header column. Ambiguous spans or
layouts retain the first-row default and its manual-review warning.

Current tagged-LaTeX theorem hooks can misparse a citation with its own
optional note inside an optional theorem heading, such as
`\begin{definition}[\cite[Definition 2.7]{source}]`. In the generated copy,
the cleaner places that theorem note behind an identity helper so it compiles
without changing the rendered citation; the input source remains unchanged.

```sh
canvas-ally-latex notes/ --main notes.tex \
  --profile article \
  --language en-US \
  --title "Week 5: Sampling Distributions"
```

`--profile auto` is the default and normally preferable. An explicit profile acts as a consistency check; the command stops if it conflicts with the detected document class.

## Beamer workflow

The following prepares a Beamer project by migrating the generated copy to `ltx-talk`:

```sh
canvas-ally-latex slides/ --main lecture.tex \
  --profile beamer \
  --beamer-mode ltx-talk \
  --language en-US
```

Presentation navigation symbols and similar Beamer controls are omitted from the static accessible copy. Ordinary PDF links in the source remain links.

Treat migration warnings as items requiring review. Compile success cannot establish that a visually elaborate Beamer theme retained every intended layout or that every custom macro has correct reading order.

## Visual alternatives and generated placeholders

The cleaner retains existing `alt`, `artifact`, `actualtext`, and nested `tagging-setup` annotations. `actualtext` remains intact because it deliberately gives a symbol-like graphic replacement text rather than turning it into a Figure. For unannotated `\includegraphics`, the cleaner first looks for a configured alternative and then usable nearby source context such as a figure caption. If neither exists, it inserts both `% allytex: alt=graphic1` and the native `alt={graphic1}` option in the copied source.

A TOML configuration can map image paths exactly as they appear in `\includegraphics`:

```toml
[images]
"figures/allocation.png" = "Flowchart showing participants assigned to treatment A or B."
"figures/rule.png" = { decorative = true }
```

For the same image path used differently in two files, qualify the key with the source file:

```toml
[images]
"sections/results.tex::figures/model.png" = "Observed and fitted values by treatment group."
```

An external JSON alternative map may also be supplied:

```json
{
  "images": {
    "figures/allocation.png": "Flowchart showing participants assigned to treatment A or B.",
    "figures/rule.png": null
  }
}
```

```sh
canvas-ally-latex slides/ --main lecture.tex --alt-map image-alts.json
```

`null` means decorative and causes the image to be tagged as an artifact. Do not use `null` merely to silence a warning for an informative image.

For a one-off source annotation, put a directive on the line immediately before the graphic or TikZ picture:

```tex
% allytex: alt=Probability tree with two treatment branches and four outcomes.
\begin{tikzpicture}
  % ...
\end{tikzpicture}
```

When a TikZ picture has no directive, alt-map entry, caption, immediately
following explicit description such as `\small{...}`, or existing annotation,
the cleaner inserts a numbered placeholder in the copied source—for example,
`% allytex: alt=tikz1`—and applies the same text through TikZ's native `alt`
option. The same behavior covers braced inline `\tikz` commands. Standard
LaTeX `picture` environments receive `% allytex: alt=picture1` and their native
`alt` option. Unbraced or dynamically formed inline TikZ remains a blocking
finding because its extent cannot be rewritten safely.

Numbers are project-wide within each kind (`graphic`, `tikz`, `picture`, or `visual`) and pre-existing placeholder numbers are reserved. Placeholders remain warnings on every normal rerun and become blocking findings under `--strict`. Editing a generated directive for a supported visual updates its native option on the next run. Find all remaining placeholders with:

```sh
rg 'allytex: alt=(graphic|tikz|picture|visual)[0-9]+' generated-project
```

Replace them with meaningful descriptions before final publication. Placeholder text may satisfy a mechanical presence check, but it is not an accessible equivalent of the visual; for text-heavy TikZ it also replaces the picture's internal node reading with one Figure alternative.

When alternative text comes from a caption or alt map, the cleaner also records it as a preceding `% allytex: alt=...` directive in the copied source. Existing authored directives and native annotations remain unchanged. A TikZ alt-map key uses `relative/file.tex::tikz:LINE`; a standard picture key uses `relative/file.tex::picture:LINE`. Custom structure selections such as `tag=Formula` are preserved and receive a sibling `alt` when they were otherwise unannotated. If a file sets stateful `Gin` or TikZ accessibility defaults, the cleaner asks for a local directive instead of silently overriding an inherited setting.

For a visual construct without a verified native tagging interface, the cleaner now adds a stable `% allytex: alt=visualN` review marker when no directive exists. This covers imported or merged PDF pages; SVG, Inkscape, standalone, PGF-image, adjusted-image, EPS, and Asymptote inclusion commands; PSTricks and low-level PGF pictures; `forest`, `tikzcd`, `circuitikz`, and Gantt diagrams; overlaid pictures; animations and embedded movies/media; and dynamically formed visual control sequences. These constructs deliberately remain blocking findings: the marker makes remediation trackable, but a comment alone does not alter the PDF tag tree. Convert the construct or add verified manual PDF tagging before release. An unsupported outer visual environment owns its nested content, so it receives one marker and blocker rather than duplicate `graphicN`, `tikzN`, or `visualN` findings for children. Literal examples in verbatim/inline-code commands and URL destinations, visual-looking near names, and statically unreachable literal conditional branches are ignored safely.

Common macro/environment definitions are never rewritten. A definition that
wraps a visual renderer emits `unsupported-visual-in-definition` when it is
statically reachable from rendered source, because the cleaner cannot locate
and annotate its rendered calls safely. A project-wide static call graph keeps
an unused helper definition from blocking the output; dynamically constructed
control-sequence names conservatively disable that exemption. Tables continue
to require real header-cell semantics, and formulas continue to use
MathML/Formula alternatives rather than filler text.

## Formula handling

The generated support package enables LaTeX's tagged-math support and `math/alt/use=true`. This keeps formulas in the document structure, preserves MathML where the current LaTeX engine can generate it, and adds the original LaTeX source as a fallback alternative representation. This is preferable to converting equations into pictures or hiding them from the tag tree.

Formula accessibility is still content-dependent. Authors should review ambiguous notation, custom math macros, alignment-based derivations, and equations embedded in diagrams with a screen reader or a dedicated PDF accessibility checker. A successful build does not prove that every expression will be announced pedagogically.

For static presentation output, top-level inline formulas inside
`\underline{...}` are moved between separately underlined text segments.
This avoids a current tagged-`ltx-talk` behavior that renders the formulas but
omits their Formula structure. The surrounding text stays underlined, while
the formulas themselves do not, so the generated warning requires a visual
and semantic review. Nested, display, unbalanced, or state-dependent cases are
left unchanged and reported for manual remediation rather than guessed.

For an important equation, the generated support package offers a deliberate spoken-text override. Place it immediately before the named environment:

```tex
\CanvasAllyNextMathAlt{equation*}{The risk ratio equals treated risk divided by control risk.}
\begin{equation*}
  RR = \frac{P(Y=1\mid T=1)}{P(Y=1\mid T=0)}
\end{equation*}
```

## Configuration

Pass a TOML file with `--config`. Command-line options override configuration values.

```toml
language = "en-US"
title = "Lecture 2: Probability"
profile = "auto"                  # auto, article, beamer, or ltx-talk
beamer_mode = "ltx-talk"          # ltx-talk or reject
table_headers = "first-row"        # first-row or manual
build = true
keep_build_files = false
strict = false

[images]
"figures/tree.pdf" = "Probability tree for the two-stage experiment."
```

Use it as follows:

```sh
canvas-ally-latex slides/ --main lecture.tex \
  --config canvas-ally.toml
```

The complete annotated example is [`examples/canvas-ally.toml`](examples/canvas-ally.toml).

Useful command-line overrides include:

- `--language TAG` and `--title TEXT` for document metadata.
- `--profile auto|article|beamer|ltx-talk` for source handling.
- `--beamer-mode ltx-talk|reject` for presentation sources.
- `--table-headers first-row|manual`; `first-row` also recognizes the conservative two-tier cross-tab pattern described above, while other non-first-row layouts require `manual` handling.
- `--alt-map FILE` for JSON image alternatives.
- `--no-build` to stop after preparing the copied sources.
- `--force-build` to compile and audit despite source `ERROR` findings; those findings still fail the report and exit status.
- `--keep-build-files` to retain LaTeX auxiliary files.
- `--strict` to turn unresolved graphics, dynamic dependencies, and lossy migration options into blocking findings.
- `--verbose` to print additional build and audit detail.

Run `canvas-ally-latex --help` for the authoritative option list for the installed version.

## Audit results and exit codes

The local audit checks machine-verifiable properties such as PDF metadata, tag trees, Formula/Figure alternatives, heading order, per-table headers, extractable text, embedded-font status, and optional PDF/UA validation. Audit JSON uses `ally_readiness: "pass"` or `"fail"`; a full preparation report uses `pass`, `pass_with_source_review`, `fail`, or `prepared_not_audited`. A dry-run report additionally has `dry_run: true`. Reports always note that the actual proprietary Canvas/Ally score is assigned only after upload.

Exit codes are intended for shell scripts and CI:

| Code | Meaning |
| ---: | --- |
| `0` | The requested preparation, preview, build, or audit completed with no blocking result. A clean `--dry-run` or `--no-build` can return 0 without a PDF. |
| `1` | A blocking source finding or major audit check remains. A PDF need not exist when a preview or source finding stopped the build. |
| `2` | Configuration, source preparation, or LaTeX build failed. |
| `3` | Beamer migration was explicitly rejected with `--beamer-mode reject`. |

Nonblocking migration or content-quality warnings are reported as `pass_with_source_review` in the full JSON report. Strict mode promotes unresolved cases for which the tool has no safe transformation; it does not make every informational migration notice fatal.

Normally, a blocking source finding prevents compilation. `--force-build`
bypasses only that pre-build gate so a PDF and audit can still be produced;
the original errors remain visible, `overall_ally_readiness` remains `fail`,
and the process still returns exit code `1`. A genuine LaTeX compilation
failure still returns exit code `2`.

## What the audit cannot prove

Automated inspection cannot determine whether:

- alternative text teaches the same idea as its image;
- reading and focus order are pedagogically correct on every page;
- headings accurately represent the content hierarchy;
- a table's chosen header cells are semantically correct;
- math is announced clearly for the course's notation;
- color, layout, animations, overlays, or custom widgets are usable by every student; or
- Canvas/Ally will assign a particular score after upload.

After generation, inspect warnings, open the PDF's tag tree in an accessibility checker, test keyboard and screen-reader reading order, and then review the Canvas/Ally feedback after upload.

## Known Beamer migration limits

`ltx-talk` intentionally supports a practical subset of Beamer rather than every theme or macro. Manual adjustment may be required for:

- custom Beamer themes, templates, headline/footline definitions, and theme-specific colors;
- complex overlay specifications or macros that store/replay boxed content;
- TikZ overlays and absolute page-positioned annotations;
- multimedia, JavaScript, forms, and embedded interactive widgets;
- unusual frame options, deeply nested or layout-sensitive columns, or hand-written navigation elements;
- packages that assume the active class is exactly `beamer`; and
- custom commands whose visual order differs from their source order.

The source-to-PDF tag minima deliberately undercount exact, bounded, or
otherwise ambiguous overlay branches because `ltx-talk` tags only the final
generated slide by default. Open-ended ranges that must be active on that
slide are counted. A frame with an explicit `tag-slides` value other than the
default `n` is excluded from source minima; inspect its generated tag tree
manually.

Safe `onlyenv` blocks are rewritten as explicitly scoped `\only` commands in
the generated copy. This prevents a false `onlyenv` from leaking orphan tags
out of its discarded box. An `onlyenv` containing verbatim-like, nested
overlay, or state-changing content is retained and reported as
`unsafe-onlyenv-tagging`; strict mode makes that review item blocking.

The cleaner reports migration warnings instead of concealing these cases. Keep the original project as the visual reference, but make accessibility fixes only in the generated copy or in reusable source macros that work in both classes.

## Tests

Run the dependency-free regression suite from the project root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -q
```

The ProxITR fixture exercises the Beamer migration failures that motivated the
compatibility rules above. A slower, opt-in smoke test also builds that fixture
with LuaLaTeX, audits it with Poppler, and requires veraPDF PDF/UA-2 validation:

```sh
CANVAS_ALLY_LATEX_E2E=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest tests.test_latex_integration -v
```

The smoke test is skipped unless all required command-line tools are present.

## License

MIT. See [`LICENSE`](LICENSE).
