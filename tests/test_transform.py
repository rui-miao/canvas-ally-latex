from __future__ import annotations

import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from canvas_ally_latex.config import ToolConfig
from canvas_ally_latex.project import prepare_project, resolve_project
from canvas_ally_latex.transform import (
    UnsupportedDocumentError,
    group_content,
    instrument_main,
    iter_commands,
    mask_tex,
    migrate_beamer_content,
    transform_graphics,
    transform_pictures,
    transform_tables,
    transform_theorem_note_citations,
    transform_tikz,
    transform_underlined_math,
    transform_visual_layout_tables,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class LexicalMaskingTests(unittest.TestCase):
    def test_comments_literal_commands_and_verbatim_are_masked(self) -> None:
        source = r"""
% \includegraphics{commented.pdf}
\verb|\includegraphics{inline.pdf}|
\begin{verbatim}
\includegraphics{verbatim.pdf}
\end{verbatim}
\begin{comment}
\includegraphics{environment.pdf}
\end{comment}
\includegraphics{live.pdf} % \includegraphics{trailing-comment.pdf}
"""

        masked = mask_tex(source)
        commands = iter_commands(source, "includegraphics")
        assets = [group_content(source, command.mandatory) for command in commands]

        self.assertEqual(len(masked), len(source))
        self.assertEqual(masked.count("\n"), source.count("\n"))
        self.assertEqual(assets, ["live.pdf"])

    def test_escaped_percent_does_not_start_a_comment(self) -> None:
        source = r"Text \% remains active. \includegraphics{active.pdf}"
        commands = iter_commands(source, "includegraphics")
        self.assertEqual(len(commands), 1)


class UnderlinedMathTests(unittest.TestCase):
    def test_top_level_inline_math_is_moved_outside_underlined_text(self) -> None:
        source = (
            r"We need to \underline{identify $V(d)$ for all $d\in\mathcal{D}$}. "
            r"Also \underline{compare \(r_1\) and \ensuremath{r_2}}."
        )

        transformed, findings = transform_underlined_math(
            source,
            Path("main.tex"),
            strict=False,
        )
        rerun, rerun_findings = transform_underlined_math(
            transformed,
            Path("main.tex"),
            strict=False,
        )

        self.assertEqual(
            transformed,
            r"We need to \underline{identify }$V(d)$\underline{ for all }"
            r"$d\in\mathcal{D}$. Also \underline{compare }\(r_1\)"
            r"\underline{ and }\ensuremath{r_2}.",
        )
        self.assertEqual(
            [finding.code for finding in findings],
            ["underlined-math-separated", "underlined-math-separated"],
        )
        self.assertEqual(rerun, transformed)
        self.assertEqual(rerun_findings, [])

    def test_nested_or_state_dependent_underlined_math_is_left_for_review(self) -> None:
        for source in (
            r"\underline{nested \textbf{value $x$}}",
            r"\underline{\color{red}value $x$}",
            r"\underline{display \[x\]}",
        ):
            with self.subTest(source=source):
                transformed, findings = transform_underlined_math(
                    source,
                    Path("main.tex"),
                    strict=True,
                )

                self.assertEqual(transformed, source)
                self.assertEqual(len(findings), 1)
                self.assertEqual(
                    findings[0].code,
                    "underlined-math-manual-review",
                )
                self.assertEqual(findings[0].severity, "error")

    def test_definitions_comments_and_near_names_are_not_rewritten(self) -> None:
        source = "\n".join(
            (
                r"\newcommand{\UnderlinedValue}{\underline{value $x$}}",
                r"% \underline{comment $y$}",
                r"\underlined{near name $z$}",
            )
        )

        transformed, findings = transform_underlined_math(
            source,
            Path("main.tex"),
            strict=False,
        )

        self.assertEqual(transformed, source)
        self.assertEqual(findings, [])


class TaggedTheoremCitationTests(unittest.TestCase):
    def test_optional_citation_in_theorem_note_is_wrapped_idempotently(self) -> None:
        source = r"""\newtheorem{definition}{Definition}
\begin{definition}[{\cite[Definition 2.7]{wainwright19}}]
Content.
\end{definition}
"""

        transformed, findings = transform_theorem_note_citations(
            source,
            Path("main.tex"),
        )
        rerun, rerun_findings = transform_theorem_note_citations(
            transformed,
            Path("main.tex"),
        )

        self.assertIn(
            r"\begin{definition}[\CanvasAllyTheoremNote{{\cite[Definition 2.7]{wainwright19}}}]",
            transformed,
        )
        self.assertEqual(
            [finding.code for finding in findings],
            ["tagged-theorem-citation-wrapped"],
        )
        self.assertEqual(rerun, transformed)
        self.assertEqual(rerun_findings, [])

    def test_nonrendered_and_non_theorem_optional_citations_are_unchanged(self) -> None:
        source = r"""\newtheorem{result}{Result}
% \begin{result}[\cite[Comment]{ref}]Comment\end{result}
\newcommand{\saved}{\begin{result}[\cite[Definition]{ref}]Saved\end{result}}
\iffalse
\begin{result}[\cite[Inactive]{ref}]Inactive\end{result}
\fi
\begin{result}[\cite{ref}]Plain citation\end{result}
\begin{figure}[\cite[Placement]{ref}]Figure\end{figure}
"""

        transformed, findings = transform_theorem_note_citations(
            source,
            Path("main.tex"),
        )

        self.assertEqual(transformed, source)
        self.assertEqual(findings, [])


class VisualLayoutTableTests(unittest.TestCase):
    def test_only_active_unescaped_separators_outside_graphics_are_replaced(self) -> None:
        source = r"""\begin{tabular}{cc}
  \includegraphics[alt={Treatment A \& B}]{left.pdf}
  % Preserve R&D in this source comment.
  &
  \includegraphics[alt={Treatment C \& D}]{right.pdf}
\end{tabular}
"""

        transformed, findings = transform_visual_layout_tables(
            source,
            Path("layout.tex"),
        )

        self.assertIn(r"alt={Treatment A \& B}", transformed)
        self.assertIn(r"alt={Treatment C \& D}", transformed)
        self.assertIn("% Preserve R&D in this source comment.", transformed)
        self.assertEqual(transformed.count(r"\hfill"), 1)
        self.assertNotIn("\n  &\n", transformed)
        self.assertEqual(
            [finding.code for finding in findings],
            ["visual-layout-table-flattened"],
        )


class TableHeaderTests(unittest.TestCase):
    def test_grouped_headers_and_row_labels_are_tagged_idempotently(self) -> None:
        source = r"""\begin{tabular}{p{3.5cm}|p{2.5cm}|p{2.5cm}}
  & \multicolumn{2}{c}{Truth (unknown)} \\
  \hline
  Decision (based on sample) & $H_0$ & $H_A$ \\
  \hline
  Reject $H_0$ & & \\
  \hline
  Do not reject $H_0$ & & \\
  \hline
\end{tabular}
"""

        transformed, findings = transform_tables(
            source,
            Path("Set1_class.tex"),
            ToolConfig(),
        )
        rerun, rerun_findings = transform_tables(
            transformed,
            Path("Set1_class.tex"),
            ToolConfig(),
        )

        self.assertIn(
            r"\tagpdfsetup{table/header-rows={1,2},table/header-columns={1}}",
            transformed,
        )
        self.assertEqual([finding.code for finding in findings], ["table-header"])
        self.assertIn("first column as row headers", findings[0].message)
        self.assertEqual(rerun, transformed)
        self.assertEqual(rerun_findings, [])

    def test_commented_row_break_does_not_hide_grouped_headers(self) -> None:
        source = r"""\begin{tabular}{ccc}
  & \multicolumn{2}{c}{Truth} % \\ is inactive
  \\
  Decision & Yes & No \\
  Accept & & \\
\end{tabular}
"""

        transformed, _ = transform_tables(source, Path("table.tex"), ToolConfig())

        self.assertIn(
            r"\tagpdfsetup{table/header-rows={1,2},table/header-columns={1}}",
            transformed,
        )

    def test_ambiguous_grouped_tables_keep_the_first_row_default(self) -> None:
        cases = {
            "non-corner blank": r"""\begin{tabular}{cccc}
Actual & & \multicolumn{2}{c}{Group} \\
A & B & C & D \\
Row & 1 & 2 & 3 \\
\end{tabular}
""",
            "second-row span": r"""\begin{tabular}{ccc}
& \multicolumn{2}{c}{Truth} \\
Decision & \multicolumn{2}{c}{Outcomes} \\
Row & 1 & 2 \\
\end{tabular}
""",
            "blank second-row cell": r"""\begin{tabular}{ccc}
& \multicolumn{2}{c}{Truth} \\
Decision & & No \\
Row & 1 & 2 \\
\end{tabular}
""",
            "width mismatch": r"""\begin{tabular}{cccc}
& \multicolumn{2}{c}{Truth} \\
Decision & Yes & No & Maybe \\
Row & 1 & 2 & 3 \\
\end{tabular}
""",
            "complex body span": r"""\begin{tabular}{ccc}
& \multicolumn{2}{c}{Truth} \\
Decision & Yes & No \\
Row & \multicolumn{2}{c}{Combined} \\
\end{tabular}
""",
        }

        for label, source in cases.items():
            with self.subTest(label=label):
                transformed, _ = transform_tables(
                    source,
                    Path("table.tex"),
                    ToolConfig(),
                )
                self.assertIn(
                    r"\tagpdfsetup{table/header-rows={1}}",
                    transformed,
                )
                self.assertNotIn("table/header-columns", transformed)

    def test_manual_and_existing_table_setups_are_preserved(self) -> None:
        source = r"\begin{tabular}{cc}A & B \\ 1 & 2 \\ \end{tabular}"
        manual, manual_findings = transform_tables(
            source,
            Path("table.tex"),
            ToolConfig(table_headers="manual"),
        )
        prepared = (
            r"\begingroup\tagpdfsetup{table/header-rows={2}}"
            + source
            + r"\endgroup"
        )
        rerun, rerun_findings = transform_tables(
            prepared,
            Path("table.tex"),
            ToolConfig(),
        )

        self.assertEqual(manual, source)
        self.assertEqual(manual_findings, [])
        self.assertEqual(rerun, prepared)
        self.assertEqual(rerun_findings, [])


class GraphicAlternativeTests(unittest.TestCase):
    def test_map_directive_caption_and_existing_alt_are_respected(self) -> None:
        source = r"""
\includegraphics[width=.5\textwidth]{mapped.pdf}
% allytex: decorative
\includegraphics{ornament.pdf}
\begin{figure}
  \includegraphics{captioned.pdf}
  \caption{Observed \textbf{risk} by study arm}
\end{figure}
\includegraphics[alt={Already described},width=2cm]{kept.pdf}
"""
        config = ToolConfig(image_alts={"mapped.pdf": "Allocation flow diagram"})

        transformed, findings = transform_graphics(
            source,
            Path("chapter.tex"),
            config,
        )

        self.assertIn(
            r"\includegraphics[alt={Allocation flow diagram},width=.5\textwidth]{mapped.pdf}",
            transformed,
        )
        self.assertIn(r"\includegraphics[artifact]{ornament.pdf}", transformed)
        self.assertIn(
            r"\includegraphics[alt={Observed risk by study arm}]{captioned.pdf}",
            transformed,
        )
        self.assertEqual(transformed.count("alt={Already described}"), 1)
        self.assertEqual(len(findings), 3)
        self.assertTrue(all(finding.severity == "info" for finding in findings))

    def test_unresolved_graphic_gets_reviewable_fallback(self) -> None:
        transformed, findings = transform_graphics(
            r"\includegraphics{figures/trial_flow.pdf}",
            Path("main.tex"),
            ToolConfig(strict=True),
        )

        self.assertEqual(
            transformed,
            "% allytex: alt=graphic1\n"
            r"\includegraphics[alt={graphic1}]{figures/trial_flow.pdf}",
        )
        self.assertEqual(findings[0].code, "graphic-placeholder-alt")
        self.assertEqual(findings[0].severity, "error")
        self.assertIn("% allytex: alt=graphic1", findings[0].message)

    def test_actualtext_is_preserved_as_intentional_symbol_semantics(self) -> None:
        transformed, findings = transform_graphics(
            r"\includegraphics[width=2cm,actualtext={Allocation diagram}]{flow.pdf}",
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertEqual(
            transformed,
            r"\includegraphics[width=2cm,actualtext={Allocation diagram}]{flow.pdf}",
        )
        self.assertEqual(findings, [])

    def test_beamer_overlay_graphic_keeps_overlay_and_gets_alt(self) -> None:
        transformed, findings = transform_graphics(
            r"\includegraphics<1->[width=2cm]{allocation.pdf}",
            Path("main.tex"),
            ToolConfig(image_alts={"allocation.pdf": "Allocation flow"}),
        )

        self.assertEqual(
            transformed,
            "% allytex: alt=Allocation flow\n"
            r"\includegraphics<1->[alt={Allocation flow},width=2cm]{allocation.pdf}",
        )
        self.assertEqual(findings[0].severity, "info")

    def test_tikz_without_alt_gets_a_numbered_reviewable_placeholder(self) -> None:
        source = r"""\begin{tikzpicture}
  \draw (0,0) -- (1,1);
\end{tikzpicture}
"""

        transformed, findings = transform_tikz(
            source,
            Path("diagram.tex"),
            ToolConfig(),
        )

        self.assertEqual(
            transformed,
            r"""% allytex: alt=tikz1
\begin{tikzpicture}[alt={tikz1}]
  \draw (0,0) -- (1,1);
\end{tikzpicture}
""",
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "tikz-placeholder-alt")
        self.assertEqual(findings[0].severity, "warning")
        self.assertIn("% allytex: alt=tikz1", findings[0].message)

    def test_tikz_placeholders_are_numbered_and_idempotent(self) -> None:
        source = r"""  \begin{tikzpicture}
  \draw (0,0) -- (1,1);
  \end{tikzpicture}
  \begin{tikzpicture}[scale=2]
  \draw (0,0) circle (1);
  \end{tikzpicture}
"""

        transformed, findings = transform_tikz(
            source,
            Path("two-diagrams.tex"),
            ToolConfig(),
        )
        rerun, rerun_findings = transform_tikz(
            transformed,
            Path("two-diagrams.tex"),
            ToolConfig(),
        )

        self.assertIn("  % allytex: alt=tikz1\n  \\begin{tikzpicture}[alt={tikz1}]", transformed)
        self.assertIn(
            "  % allytex: alt=tikz2\n  \\begin{tikzpicture}[alt={tikz2},scale=2]",
            transformed,
        )
        self.assertEqual(
            [item.code for item in findings],
            ["tikz-placeholder-alt", "tikz-placeholder-alt"],
        )
        self.assertEqual(rerun, transformed)
        self.assertEqual(
            [item.code for item in rerun_findings],
            ["tikz-placeholder-alt", "tikz-placeholder-alt"],
        )
        self.assertTrue(all(item.severity == "warning" for item in rerun_findings))

    def test_editing_generated_tikz_directive_updates_native_alt(self) -> None:
        generated, _ = transform_tikz(
            r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}",
            Path("diagram.tex"),
            ToolConfig(),
        )
        edited = generated.replace(
            "% allytex: alt=tikz1",
            "% allytex: alt=Line segment connecting the lower-left and upper-right corners.",
        )

        transformed, findings = transform_tikz(
            edited,
            Path("diagram.tex"),
            ToolConfig(),
        )

        self.assertIn(
            "[alt={Line segment connecting the lower-left and upper-right corners.}]",
            transformed,
        )
        self.assertNotIn("alt={tikz1}", transformed)
        self.assertEqual([item.code for item in findings], ["tikz-directive-synced"])

    def test_new_tikz_after_rerun_uses_next_reserved_number(self) -> None:
        generated, _ = transform_tikz(
            r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}",
            Path("diagram.tex"),
            ToolConfig(),
        )
        extended = generated + "\n\\begin{tikzpicture}\\draw (1,0)--(0,1);\\end{tikzpicture}"

        transformed, findings = transform_tikz(
            extended,
            Path("diagram.tex"),
            ToolConfig(),
        )

        self.assertIn("% allytex: alt=tikz1", transformed)
        self.assertIn("% allytex: alt=tikz2", transformed)
        self.assertEqual(
            [item.code for item in findings],
            ["tikz-placeholder-alt", "tikz-placeholder-alt"],
        )

    def test_generated_graphic_placeholder_persists_and_can_be_edited(self) -> None:
        generated, first_findings = transform_graphics(
            r"\includegraphics{plot.pdf}",
            Path("main.tex"),
            ToolConfig(),
        )
        rerun, rerun_findings = transform_graphics(
            generated,
            Path("main.tex"),
            ToolConfig(),
        )
        edited = generated.replace(
            "% allytex: alt=graphic1",
            "% allytex: alt=Histogram of patient ages by treatment arm.",
        )
        revised, revised_findings = transform_graphics(
            edited,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertEqual(first_findings[0].severity, "warning")
        self.assertEqual(rerun, generated)
        self.assertEqual(rerun_findings[0].code, "graphic-placeholder-alt")
        self.assertIn(
            r"\includegraphics[alt={Histogram of patient ages by treatment arm.}]{plot.pdf}",
            revised,
        )
        self.assertEqual(revised_findings[0].code, "graphic-directive-synced")

    def test_inline_tikz_and_picture_use_native_alt_interfaces(self) -> None:
        tikz_source = r"Inline \tikz[baseline]{\draw (0,0)--(1,0);}."
        picture_source = r"""\setlength{\unitlength}{1pt}
\begin{picture}(20,10)
  \put(0,0){\line(1,0){20}}
\end{picture}
"""

        tikz_transformed, tikz_findings = transform_tikz(
            tikz_source,
            Path("native.tex"),
            ToolConfig(),
        )
        picture_transformed, picture_findings = transform_pictures(
            picture_source,
            Path("native.tex"),
            ToolConfig(),
        )

        self.assertIn(r"\tikz[alt={tikz1},baseline]", tikz_transformed)
        self.assertIn("% allytex: alt=tikz1", tikz_transformed)
        self.assertEqual(tikz_findings[0].code, "tikz-placeholder-alt")
        self.assertIn(r"\begin{picture}[alt={picture1}](20,10)", picture_transformed)
        self.assertIn("% allytex: alt=picture1", picture_transformed)
        self.assertEqual(picture_findings[0].code, "picture-placeholder-alt")

    def test_tikz_nested_tagging_modes_are_not_overridden(self) -> None:
        source = r"""\begin{tikzpicture}[tagging-setup={artifact},scale=2]\draw(0,0)--(1,1);\end{tikzpicture}
\tikz[tagging-setup={actualtext={x}}]{\node{x};}
"""

        transformed, findings = transform_tikz(
            source,
            Path("explicit.tex"),
            ToolConfig(),
        )

        self.assertEqual(transformed, source)
        self.assertEqual(findings, [])

    def test_tikz_caption_is_recorded_as_a_preceding_directive(self) -> None:
        source = r"""\begin{figure}
  \begin{tikzpicture}
    \draw (0,0) -- (1,1);
  \end{tikzpicture}
  \caption{Allocation flow by study arm}
\end{figure}
"""

        transformed, findings = transform_tikz(
            source,
            Path("caption.tex"),
            ToolConfig(),
        )

        self.assertIn(
            "  % allytex: alt=Allocation flow by study arm\n"
            "  \\begin{tikzpicture}[alt={Allocation flow by study arm}]",
            transformed,
        )
        self.assertEqual(findings[0].code, "tikz-alt")
        self.assertEqual(findings[0].severity, "info")

    def test_inline_tikz_and_picture_receive_native_placeholder_options(self) -> None:
        inline, inline_findings = transform_tikz(
            r"Inline \tikz[baseline]{\draw (0,0)--(1,0);}",
            Path("native.tex"),
            ToolConfig(),
        )
        picture, picture_findings = transform_pictures(
            r"  \begin{picture}(20,10)\put(0,0){X}\end{picture}",
            Path("native.tex"),
            ToolConfig(),
        )

        self.assertIn("% allytex: alt=tikz1", inline)
        self.assertIn(r"\tikz[alt={tikz1},baseline]{", inline)
        self.assertEqual(inline_findings[0].code, "tikz-placeholder-alt")
        self.assertIn("  % allytex: alt=picture1\n", picture)
        self.assertIn(r"\begin{picture}[alt={picture1}](20,10)", picture)
        self.assertEqual(picture_findings[0].code, "picture-placeholder-alt")

    def test_nested_tagging_setup_and_actualtext_are_not_overridden(self) -> None:
        tikz_source = (
            r"\begin{tikzpicture}[tagging-setup={actualtext={right arrow}}]"
            r"\draw[->](0,0)--(1,0);\end{tikzpicture}"
        )
        graphic_source = (
            r"\includegraphics[tagging-setup={artifact},width=1cm]{rule.pdf}"
        )

        tikz, tikz_findings = transform_tikz(
            tikz_source,
            Path("native.tex"),
            ToolConfig(),
        )
        graphic, graphic_findings = transform_graphics(
            graphic_source,
            Path("native.tex"),
            ToolConfig(),
        )

        self.assertEqual(tikz, tikz_source)
        self.assertEqual(tikz_findings, [])
        self.assertEqual(graphic, graphic_source)
        self.assertEqual(graphic_findings, [])

    def test_strict_mode_keeps_placeholder_but_makes_it_blocking(self) -> None:
        transformed, findings = transform_pictures(
            r"\begin{picture}(10,10)\end{picture}",
            Path("native.tex"),
            ToolConfig(strict=True),
        )

        self.assertIn("% allytex: alt=picture1", transformed)
        self.assertEqual(findings[0].code, "picture-placeholder-alt")
        self.assertEqual(findings[0].severity, "error")


class ProjectPreparationTests(unittest.TestCase):
    def test_theorem_note_citation_in_dependency_uses_generated_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "project"
            output = root / "prepared"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\usepackage{amsthm}\n"
                "\\newtheorem{result}{Result}\n"
                "\\title{Tagged theorem}\n"
                "\\begin{document}\\input{section}\\end{document}\n",
                encoding="utf-8",
            )
            dependency = source / "section.tex"
            original = (
                "\\begin{result}[{\\cite[Definition 2.7]{reference}}]\n"
                "Content.\n"
                "\\end{result}\n"
            )
            dependency.write_text(original, encoding="utf-8")

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )

            prepared = (output / "section.tex").read_text(encoding="utf-8")
            support = (output / "canvasally.sty").read_text(encoding="utf-8")
            self.assertEqual(dependency.read_text(encoding="utf-8"), original)
            self.assertIn(
                r"\begin{result}[\CanvasAllyTheoremNote{{\cite[Definition 2.7]{reference}}}]",
                prepared,
            )
            self.assertIn(r"\ProvideDocumentCommand\CanvasAllyTheoremNote", support)
            self.assertTrue(
                any(
                    finding.code == "tagged-theorem-citation-wrapped"
                    and finding.file == "section.tex"
                    for finding in result.findings
                )
            )

    def test_read_only_main_is_writable_only_in_prepared_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "project"
            output = root / "prepared"
            source.mkdir()
            main = source / "main.tex"
            copied = output / "main.tex"
            original = (
                "\\documentclass{article}\n"
                "\\title{Read-only main}\n"
                "\\begin{document}Text\\end{document}\n"
            )
            main.write_text(original, encoding="utf-8")
            main.chmod(0o444)
            source_mode = stat.S_IMODE(main.stat().st_mode)

            try:
                prepare_project(
                    resolve_project(source, output_dir=output),
                    ToolConfig(build=False),
                )

                prepared = copied.read_text(encoding="utf-8")
                self.assertEqual(main.read_text(encoding="utf-8"), original)
                self.assertEqual(stat.S_IMODE(main.stat().st_mode), source_mode)
                self.assertFalse(source_mode & stat.S_IWUSR)
                self.assertTrue(stat.S_IMODE(copied.stat().st_mode) & stat.S_IWUSR)
                self.assertNotEqual(prepared, original)
                self.assertIn(r"\DocumentMetadata", prepared)
                self.assertIn(r"\usepackage{canvasally}", prepared)
            finally:
                main.chmod(0o644)
                if copied.exists():
                    copied.chmod(0o644)

    def test_read_only_dependency_tree_is_writable_only_in_prepared_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "project"
            output = root / "prepared"
            dependency_dir = source / "chapters"
            dependency = dependency_dir / "section.tex"
            copied_dir = output / "chapters"
            copied = copied_dir / "section.tex"
            dependency_dir.mkdir(parents=True)
            (source / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\title{Read-only dependency}\n"
                "\\begin{document}\\input{chapters/section}\\end{document}\n",
                encoding="utf-8",
            )
            original = "\\includegraphics{plot.pdf}\n"
            dependency.write_text(original, encoding="utf-8")
            dependency.chmod(0o444)
            dependency_dir.chmod(0o555)
            source_file_mode = stat.S_IMODE(dependency.stat().st_mode)
            source_dir_mode = stat.S_IMODE(dependency_dir.stat().st_mode)

            try:
                prepare_project(
                    resolve_project(source, output_dir=output),
                    ToolConfig(build=False),
                )

                self.assertEqual(dependency.read_text(encoding="utf-8"), original)
                self.assertEqual(
                    stat.S_IMODE(dependency.stat().st_mode),
                    source_file_mode,
                )
                self.assertEqual(
                    stat.S_IMODE(dependency_dir.stat().st_mode),
                    source_dir_mode,
                )
                self.assertFalse(source_file_mode & stat.S_IWUSR)
                self.assertFalse(source_dir_mode & stat.S_IWUSR)
                self.assertTrue(stat.S_IMODE(copied.stat().st_mode) & stat.S_IWUSR)
                self.assertEqual(
                    stat.S_IMODE(copied_dir.stat().st_mode)
                    & (stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR),
                    stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR,
                )
                self.assertIn(
                    r"\includegraphics[alt={graphic1}]{plot.pdf}",
                    copied.read_text(encoding="utf-8"),
                )
            finally:
                dependency_dir.chmod(0o755)
                dependency.chmod(0o644)
                if copied_dir.exists():
                    copied_dir.chmod(0o755)
                if copied.exists():
                    copied.chmod(0o644)

    def test_article_is_prepared_in_an_independent_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "article"
            output = root / "article-accessible"
            shutil.copytree(FIXTURES / "article", source)
            original = (source / "main.tex").read_text(encoding="utf-8")

            paths = resolve_project(source, output_dir=output)
            result = prepare_project(paths, ToolConfig(build=False))

            prepared = (output / "main.tex").read_text(encoding="utf-8")
            self.assertEqual((source / "main.tex").read_text(encoding="utf-8"), original)
            self.assertNotEqual(prepared, original)
            self.assertLess(prepared.index(r"\DocumentMetadata"), prepared.index(r"\documentclass"))
            self.assertIn(r"pdfstandard=UA-2", prepared)
            self.assertIn(r"math/alt/use=true", prepared)
            self.assertIn(r"\usepackage{canvasally}", prepared)
            self.assertIn(r"\tagpdfsetup{table/header-rows={1}}", prepared)
            self.assertIn("% allytex: alt=tikz1", prepared)
            self.assertIn(r"\begin{tikzpicture}[alt={tikz1}]", prepared)
            self.assertIn("% allytex: alt=picture1", prepared)
            self.assertIn(r"\begin{picture}[alt={picture1}](30,12)", prepared)
            self.assertIn("% allytex: alt=tikz2", prepared)
            self.assertIn(r"\tikz[alt={tikz2},baseline]", prepared)
            self.assertTrue((output / "canvasally.sty").is_file())
            self.assertEqual(result.profile, "article")
            self.assertEqual(result.source, source.resolve())
            self.assertEqual(result.output, output.resolve())
            self.assertTrue(any(item.code == "project-copy" for item in result.findings))
            self.assertTrue(any(item.code == "tikz-placeholder-alt" for item in result.findings))

            with self.assertRaises(FileExistsError):
                prepare_project(paths, ToolConfig(build=False))

    def test_only_reachable_tex_dependencies_are_transformed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "project"
            output = root / "prepared"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\title{Dependency closure}\n"
                "\\begin{document}\n"
                "\\input{used}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            used_source = "\\includegraphics{reachable-plot.pdf}\n"
            unused_source = "\\includegraphics{unrelated-plot.pdf}\n"
            (source / "used.tex").write_text(used_source, encoding="utf-8")
            (source / "unused.tex").write_text(unused_source, encoding="utf-8")

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )

            self.assertIn(
                r"\includegraphics[alt={graphic1}]{reachable-plot.pdf}",
                (output / "used.tex").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                (output / "unused.tex").read_text(encoding="utf-8"),
                unused_source,
            )
            self.assertTrue(result.source_expectations["figures"])
            self.assertEqual(result.source_expectations["figure_minimum"], 1)

    def test_placeholder_numbers_are_project_wide_and_reserve_existing_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "project"
            output = root / "prepared"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\usepackage{tikz}\n"
                "\\title{Project placeholders}\n"
                "\\begin{document}\n"
                "\\input{a}\\input{b}\\input{c}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            (source / "a.tex").write_text(
                "% allytex: alt=tikz7\n"
                "\\begin{tikzpicture}[alt={tikz7}]\\draw(0,0)--(1,1);\\end{tikzpicture}\n",
                encoding="utf-8",
            )
            for name in ("b", "c"):
                (source / f"{name}.tex").write_text(
                    "\\begin{tikzpicture}\\draw(0,0)--(1,1);\\end{tikzpicture}\n",
                    encoding="utf-8",
                )

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )

            self.assertIn("% allytex: alt=tikz7", (output / "a.tex").read_text(encoding="utf-8"))
            self.assertIn("% allytex: alt=tikz8", (output / "b.tex").read_text(encoding="utf-8"))
            self.assertIn("% allytex: alt=tikz9", (output / "c.tex").read_text(encoding="utf-8"))
            placeholders = [
                item for item in result.findings if item.code == "tikz-placeholder-alt"
            ]
            self.assertEqual(len(placeholders), 3)
            self.assertEqual(result.source_expectations["figure_minimum"], 3)

    def test_inactive_beamer_visuals_do_not_consume_placeholder_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "slides"
            output = root / "prepared"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{beamer}
\usepackage{tikz}
\title{Static-mode filtering}
\begin{document}
\only<article>{\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}}
\onslide<article>{\includegraphics{hidden.pdf}}
\begin{block}<article>{Hidden block}
  \begin{picture}(10,10)\put(0,0){X}\end{picture}
\end{block}
\begin{frame}{Live visuals}
  \begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}
  \includegraphics{live.pdf}
  \begin{picture}(10,10)\put(0,0){X}\end{picture}
\end{frame}
\end{document}
""",
                encoding="utf-8",
            )

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )
            prepared = (output / "main.tex").read_text(encoding="utf-8")

            self.assertNotIn("hidden.pdf", prepared)
            self.assertNotIn("Hidden block", prepared)
            self.assertIn("% allytex: alt=tikz1", prepared)
            self.assertIn("% allytex: alt=graphic1", prepared)
            self.assertIn("% allytex: alt=picture1", prepared)
            self.assertNotIn("tikz2", prepared)
            self.assertEqual(result.source_expectations["figure_minimum"], 3)

    def test_inactive_beamer_end_document_does_not_truncate_live_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "slides"
            output = root / "prepared"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{beamer}
\usepackage{tikz}
\title{Active boundary}
\begin{document}
\only<article>{\end{document}}
\begin{frame}{Still live}
\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}
\end{frame}
\end{document}
""",
                encoding="utf-8",
            )

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )
            prepared = (output / "main.tex").read_text(encoding="utf-8")

            self.assertIn("Still live", prepared)
            self.assertIn("% allytex: alt=tikz1", prepared)
            self.assertEqual(result.source_expectations["figure_minimum"], 1)

    def test_unused_macro_definition_does_not_raise_formula_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "article"
            output = root / "prepared"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\title{Unused macro}\n"
                "\\begin{document}\\input{macros}Visible text\\end{document}\n",
                encoding="utf-8",
            )
            (source / "macros.tex").write_text(
                "\\newcommand{\\unusedformula}{$x+y$}\n"
                "\\NewDocumentCommand{\\unusedother}{m}{\\ensuremath{#1^2}}\n",
                encoding="utf-8",
            )

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )

            self.assertEqual(result.source_expectations["formula_minimum"], 0)

    def test_symlinked_dependency_parent_is_materialized_before_editing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "project"
            external = root / "external-content"
            output = root / "prepared"
            source.mkdir()
            external.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\title{Safe symlink handling}\n"
                "\\begin{document}\n"
                "\\input{linked/section}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            external_text = "\\includegraphics{external-plot.pdf}\n"
            external_file = external / "section.tex"
            external_file.write_text(external_text, encoding="utf-8")
            external_file.chmod(0o444)
            external_mode = stat.S_IMODE(external_file.stat().st_mode)
            (source / "linked").symlink_to(external, target_is_directory=True)

            try:
                prepare_project(
                    resolve_project(source, output_dir=output),
                    ToolConfig(build=False),
                )

                copied = output / "linked" / "section.tex"
                self.assertEqual(external_file.read_text(encoding="utf-8"), external_text)
                self.assertEqual(
                    stat.S_IMODE(external_file.stat().st_mode),
                    external_mode,
                )
                self.assertTrue((source / "linked").is_symlink())
                self.assertFalse((output / "linked").is_symlink())
                self.assertTrue(stat.S_IMODE(copied.stat().st_mode) & stat.S_IWUSR)
                self.assertIn(
                    r"\includegraphics[alt={graphic1}]{external-plot.pdf}",
                    copied.read_text(encoding="utf-8"),
                )
            finally:
                external_file.chmod(0o644)

    def test_beamer_copy_is_migrated_to_ltx_talk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "beamer"
            output = root / "beamer-accessible"
            shutil.copytree(FIXTURES / "beamer", source)
            original = (source / "main.tex").read_text(encoding="utf-8")

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False, beamer_mode="ltx-talk"),
            )
            prepared = (output / "main.tex").read_text(encoding="utf-8")

            self.assertEqual((source / "main.tex").read_text(encoding="utf-8"), original)
            self.assertIn(
                r"\documentclass[aspect-ratio=16:9,font-size=11pt,mode=handout]{ltx-talk}",
                prepared,
            )
            self.assertIn(r"\begin{frame*}", prepared)
            self.assertIn(r"\end{frame*}", prepared)
            self.assertNotIn(r"\titlepage", prepared)
            self.assertIn(r"\maketitle", prepared)
            self.assertIn(r"\input{canvasally-ltx-talk-shim.tex}", prepared)
            self.assertTrue((output / "canvasally-ltx-talk-shim.tex").is_file())
            self.assertEqual(result.profile, "ltx-talk")
            self.assertTrue(any(item.code == "beamer-migrated" for item in result.findings))
            # Literal content in the migrated frame remains byte-for-byte intact.
            self.assertIn(r"\includegraphics{not-a-real-figure.pdf}", prepared)
            self.assertNotIn(
                r"\includegraphics[alt={Not a real figure}]{not-a-real-figure.pdf}",
                prepared,
            )

    def test_inactive_local_color_themes_are_not_loaded_for_projector(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "slides"
            output = root / "prepared"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{beamer}
\newcommand{\unusedtheme}{\usecolortheme{Defined}}
\iffalse
\usecolortheme{FalseBranch}
\fi
\mode<article>{\usecolortheme{ArticleOnly}}
\mode<projector>{\usetheme{Live}}
\begin{document}
\begin{frame}{Live frame}Text\end{frame}
\end{document}
""",
                encoding="utf-8",
            )
            for name in ("Defined", "FalseBranch", "ArticleOnly", "Live"):
                (source / f"beamercolortheme{name}.sty").write_text(
                    f"\\ProvidesPackage{{beamercolortheme{name}}}\n",
                    encoding="utf-8",
                )

            config = ToolConfig(build=False)
            prepare_project(
                resolve_project(source, output_dir=output),
                config,
            )
            prepared = (output / "main.tex").read_text(encoding="utf-8")

            self.assertEqual(config.local_beamer_color_themes, ["beamercolorthemeLive"])
            self.assertIn(r"\RequirePackage{beamercolorthemeLive}", prepared)
            for name in ("Defined", "FalseBranch", "ArticleOnly"):
                self.assertNotIn(rf"\RequirePackage{{beamercolortheme{name}}}", prepared)

    def test_local_color_theme_selection_respects_handout_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "slides"
            output = root / "prepared"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass[handout]{beamer}
\mode<projector>{\usecolortheme{ProjectorOnly}}
\mode<handout>{\usecolortheme{HandoutOnly}}
\begin{document}
\begin{frame}{Live frame}Text\end{frame}
\end{document}
""",
                encoding="utf-8",
            )
            for name in ("ProjectorOnly", "HandoutOnly"):
                (source / f"beamercolortheme{name}.sty").write_text(
                    f"\\ProvidesPackage{{beamercolortheme{name}}}\n",
                    encoding="utf-8",
                )

            config = ToolConfig(build=False)
            prepare_project(
                resolve_project(source, output_dir=output),
                config,
            )
            prepared = (output / "main.tex").read_text(encoding="utf-8")

            self.assertEqual(
                config.local_beamer_color_themes,
                ["beamercolorthemeHandoutOnly"],
            )
            self.assertIn(r"\RequirePackage{beamercolorthemeHandoutOnly}", prepared)
            self.assertNotIn(r"\RequirePackage{beamercolorthemeProjectorOnly}", prepared)

    def test_reject_mode_refuses_beamer(self) -> None:
        source = (FIXTURES / "beamer" / "main.tex").read_text(encoding="utf-8")
        with self.assertRaisesRegex(UnsupportedDocumentError, "cannot produce tagged PDF"):
            instrument_main(
                source,
                Path("main.tex"),
                ToolConfig(build=False, beamer_mode="reject"),
            )

    def test_beamer_20pt_uses_default_four_by_three_aspect(self) -> None:
        source = r"""\documentclass[20pt]{beamer}
\title{Large type}
\begin{document}
\begin{frame}{Readable title}
Content
\end{frame}
\end{document}
"""

        transformed, profile, _, _ = instrument_main(
            source,
            Path("main.tex"),
            ToolConfig(build=False),
        )

        self.assertEqual(profile, "ltx-talk")
        self.assertIn(
            r"\documentclass[aspect-ratio=4:3,font-size=20pt]{ltx-talk}",
            transformed,
        )

    def test_beamer_xcolor_options_are_forwarded_before_ltx_talk(self) -> None:
        source = r"""\documentclass[aspectratio=1610,xcolor={dvipsnames,table}]{beamer}
\title{Color options}
\begin{document}
\begin{frame}{Named colors}
\textcolor{DarkBlue}{Accessible color-preserving migration}
\end{frame}
\end{document}
"""

        transformed, profile, findings, _ = instrument_main(
            source,
            Path("main.tex"),
            ToolConfig(build=False),
        )

        self.assertEqual(profile, "ltx-talk")
        self.assertLess(
            transformed.index(r"\PassOptionsToPackage{dvipsnames,table}{xcolor}"),
            transformed.index(r"\documentclass[aspect-ratio=16:10]{ltx-talk}"),
        )
        self.assertFalse(any(item.code == "beamer-class-option" for item in findings))

    def test_fragile_frame_title_starts_on_a_new_line(self) -> None:
        source = r"""\begin{frame}[fragile]{Literal example}
\begin{verbatim}
x <- 1
\end{verbatim}
\end{frame}
"""

        transformed, findings = migrate_beamer_content(
            source,
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertIn(
            "\\begin{frame*}\n\\frametitle{Literal example}\n\\begin{verbatim}",
            transformed,
        )
        self.assertIn(r"\end{frame*}", transformed)
        self.assertTrue(any(item.code == "beamer-fragile-frame" for item in findings))

    def test_beamer_default_overlay_spec_and_options_are_parsed(self) -> None:
        source = r"""\begin{frame}[<+->][plain]{Incremental title}
Content
\end{frame}
"""

        transformed, findings = migrate_beamer_content(
            source,
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertIn(
            r"\begin{frame}[action-spec=<+->]" + "\n" + r"\frametitle{Incremental title}",
            transformed,
        )
        self.assertNotIn("[plain]{Incremental title}", transformed)
        self.assertTrue(any(item.code == "beamer-frame-visual-option" for item in findings))

    def test_nested_command_form_columns_are_migrated_without_overlap(self) -> None:
        source = r"""\begin{columns}
\column[t]{0.48\textwidth}
Outer left
\begin{columns}
\column{0.45\linewidth}Inner left
\column{0.45\linewidth}Inner right
\end{columns}
\column[b]{0.48\textwidth}
Outer right
\end{columns}
"""

        transformed, findings = migrate_beamer_content(
            source,
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertNotRegex(transformed, r"\\column(?![A-Za-z@])")
        self.assertEqual(transformed.count(r"\begin{column}"), 4)
        self.assertEqual(transformed.count(r"\end{column}"), 4)
        self.assertEqual(
            sum(item.code == "beamer-column-command" for item in findings),
            4,
        )

    def test_beamer_frame_label_becomes_hyperlink_target(self) -> None:
        transformed, findings = migrate_beamer_content(
            r"\begin{frame}[label=details]{Details}Content\end{frame}",
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertIn(r"\hypertarget{details}{}", transformed)
        self.assertTrue(any(item.code == "beamer-frame-label-mapped" for item in findings))

    def test_beamer_overlay_modes_are_mapped_to_ltx_talk_modes(self) -> None:
        source = r"""\begin{frame}<beamer:1->[<presentation:2->]{Mode title}
\only<article>{Article-only content}
Text <article> and $x<article>y$ remain literal.
\end{frame}
"""

        transformed, findings = migrate_beamer_content(
            source,
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertIn(r"\begin{frame}<1->[action-spec=<2->]", transformed)
        self.assertNotIn("Article-only content", transformed)
        self.assertIn(r"Text <article> and $x<article>y$ remain literal.", transformed)
        self.assertGreaterEqual(
            sum(item.code == "beamer-overlay-mode-mapped" for item in findings),
            3,
        )

    def test_beamer_action_spanning_alignment_cells_uses_per_cell_uncover(self) -> None:
        source = r"""\frame{\frametitle{Aligned actions}
\begin{align*}
\action<+->{G(x) & = E\{I(c \neq Y) \mid X=x\}} \\
\action<2->{& = P(c \neq Y \mid X=x)}
\end{align*}
\action<2->{Ordinary action outside an alignment cell}
}
"""

        transformed, findings = migrate_beamer_content(
            source,
            Path("frames.tex"),
            ToolConfig(build=False),
        )
        rerun, rerun_findings = migrate_beamer_content(
            transformed,
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertIn(
            r"\uncover<+->{G(x) }&\uncover<.->{ = E\{I(c \neq Y) \mid X=x\}}",
            transformed,
        )
        self.assertIn(
            r"\uncover<2->{}&\uncover<2->{ = P(c \neq Y \mid X=x)}",
            transformed,
        )
        self.assertIn(
            r"\action<2->{Ordinary action outside an alignment cell}",
            transformed,
        )
        mapped = [
            item for item in findings
            if item.code == "beamer-action-alignment-mapped"
        ]
        self.assertEqual(len(mapped), 2)
        self.assertTrue(all(item.severity == "info" for item in mapped))
        self.assertEqual(rerun, transformed)
        self.assertFalse(
            any(
                item.code == "beamer-action-alignment-mapped"
                for item in rerun_findings
            )
        )

    def test_standalone_vspace_linebreak_is_paragraph_safe(self) -> None:
        source = r"""\begin{frame}
\begin{equation*}
x = 1
\end{equation*}
\vspace{0.5in}\\
Important note.
\end{frame}
"""

        transformed, findings = migrate_beamer_content(
            source,
            Path("frames.tex"),
            ToolConfig(build=False),
        )
        rerun, rerun_findings = migrate_beamer_content(
            transformed,
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertIn("\\end{equation*}\n\\vspace{0.5in}\n", transformed)
        self.assertNotIn(r"\vspace{0.5in}\\", transformed)
        self.assertEqual(
            sum(
                item.code == "beamer-vspace-linebreak-normalized"
                for item in findings
            ),
            1,
        )
        self.assertEqual(rerun, transformed)
        self.assertFalse(
            any(
                item.code == "beamer-vspace-linebreak-normalized"
                for item in rerun_findings
            )
        )

    def test_structural_and_inline_vspace_linebreaks_are_unchanged(self) -> None:
        source = r"""\vspace{1ex}\\
\begin{frame}{Spacing exceptions}
Inline \vspace{1ex}\\
Paragraph text
\vspace{1ex}\\
continues here.
Paragraph with an intervening comment
% This comment suppresses its own line ending.
\vspace{1ex}\\
also continues here.
\begin{tabular}{c}
\vspace{1ex}\\
\end{tabular}
\end{frame}
"""

        transformed, findings = migrate_beamer_content(
            source,
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertEqual(transformed.count(r"\vspace{1ex}\\"), 5)
        self.assertFalse(
            any(
                item.code == "beamer-vspace-linebreak-normalized"
                for item in findings
            )
        )

    def test_command_form_frame_vspace_linebreak_is_normalized(self) -> None:
        source = "\\frame{\n\\vspace{2ex}\\\\\nText\n}\n"

        transformed, findings = migrate_beamer_content(
            source,
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertIn("\\begin{frame}\\vspace{2ex}\n", transformed)
        self.assertTrue(
            any(
                item.code == "beamer-vspace-linebreak-normalized"
                for item in findings
            )
        )

    def test_named_beamer_action_spanning_alignment_cells_is_blocking(self) -> None:
        source = r"""\begin{frame}{Named aligned action}
\begin{align*}
\action<alert@2>{A & = B}
\end{align*}
\end{frame}
"""

        transformed, findings = migrate_beamer_content(
            source,
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertIn(r"\action<alert@2>{A & = B}", transformed)
        blockers = [
            item for item in findings
            if item.code == "unsupported-beamer-action-alignment"
        ]
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0].severity, "error")

    def test_box_frame_command_inside_slide_is_not_expanded(self) -> None:
        source = r"""\begin{frame}{Box command}
\frame{Boxed text}
\end{frame}
"""

        transformed, _ = migrate_beamer_content(
            source,
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertIn(r"\frame{Boxed text}", transformed)
        self.assertEqual(transformed.count(r"\begin{frame}"), 1)

    def test_never_selected_alt_and_environment_are_omitted(self) -> None:
        source = r"""\begin{frame}{Branches}
\alt<article>{\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}}{Visible text}
\begin{onlyenv}<article>
\includegraphics{hidden.pdf}
\end{onlyenv}
\onslide+<1->{Visible semantics}
\onslide*<2>{Only semantics}
\end{frame}
"""

        transformed, findings = migrate_beamer_content(
            source,
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertIn(r"\begingroup Visible text \endgroup", transformed)
        self.assertNotIn("hidden.pdf", transformed)
        self.assertIn(r"\visible<1->{Visible semantics}", transformed)
        self.assertIn(r"\only<2>{Only semantics}", transformed)
        self.assertTrue(any(item.code == "beamer-nonpresentation-branch-selected" for item in findings))

    def test_unbraced_modified_onslide_is_blocking(self) -> None:
        _, findings = migrate_beamer_content(
            r"\begin{frame}{Stateful}\onslide+<2-> Following text\end{frame}",
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertTrue(
            any(
                item.code == "unsupported-beamer-stateful-onslide"
                and item.severity == "error"
                for item in findings
            )
        )

    def test_swallowed_beamer_hooks_do_not_create_blocking_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "slides"
            output = root / "prepared"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{beamer}
\title{Hooks}
\AtBeginSection[]{\begin{frame}\sectionpage
  \begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}\end{frame}}
\logo{\includegraphics{visual-logo.pdf}}
\begin{document}
\section{Live section}
\begin{frame}{Live frame}Text\end{frame}
\end{document}
""",
                encoding="utf-8",
            )

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False, strict=True),
            )

            self.assertFalse(any(item.severity == "error" for item in result.findings))
            prepared = (output / "main.tex").read_text(encoding="utf-8")
            self.assertNotIn(r"\AtBeginSection", prepared)
            self.assertNotIn(r"visual-logo.pdf", prepared)

    def test_stateful_mode_inside_swallowed_hook_cannot_remove_live_frames(self) -> None:
        source = (
            r"\AtBeginSection{\mode<article>\begin{frame}{Hidden}X\end{frame}}"
            "\n"
            r"\begin{frame}{Live}Visible\end{frame}"
        )

        transformed, _ = migrate_beamer_content(
            source,
            Path("frames.tex"),
            ToolConfig(build=False),
        )

        self.assertNotIn("Hidden", transformed)
        self.assertIn(r"\frametitle{Live}", transformed)
        self.assertIn("Visible", transformed)

    def test_nonrendered_beamer_content_does_not_raise_source_minima(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "slides"
            output = root / "prepared"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{beamer}
\title{Source expectations}
\begin{document}
\begin{frame}{No tagged objects}
Text\\[1ex]
\begin{tikzpicture}[artifact]
  \draw (0,0) -- (1,1);
\end{tikzpicture}
\note<2>[item]{$x$ \includegraphics{private-note.pdf}
  \begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}}
\only<article>{$y$ \includegraphics{article-only.pdf}}
\end{frame}
\end{document}
""",
                encoding="utf-8",
            )

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False, strict=True),
            )

            self.assertEqual(result.source_expectations["formula_minimum"], 0)
            self.assertEqual(result.source_expectations["figure_minimum"], 0)
            self.assertFalse(
                any(
                    item.code in {"graphic-alt", "tikz-alt-required"}
                    for item in result.findings
                )
            )

    def test_main_content_after_end_document_is_preserved_but_not_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "article"
            output = root / "prepared"
            source.mkdir()
            trailing = (
                "\n% Scratch material intentionally below the active document.\n"
                "$x$\n"
                "\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}\n"
                "\\input{scratch}\n"
            )
            (source / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\title{Active document}\n"
                "\\begin{document}Accessible text.\\end{document}"
                + trailing,
                encoding="utf-8",
            )
            scratch = "$y$\\includegraphics{unrendered.pdf}\n"
            (source / "scratch.tex").write_text(scratch, encoding="utf-8")

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False, strict=True),
            )

            prepared = (output / "main.tex").read_text(encoding="utf-8")
            self.assertTrue(prepared.endswith(trailing))
            self.assertEqual((output / "scratch.tex").read_text(encoding="utf-8"), scratch)
            self.assertEqual(result.source_expectations["formula_minimum"], 0)
            self.assertEqual(result.source_expectations["figure_minimum"], 0)
            self.assertFalse(any(item.severity == "error" for item in result.findings))

    def test_handout_ignores_projector_only_visuals_before_strict_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "slides"
            output = root / "prepared"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass[handout]{beamer}
\title{Handout modes}
\begin{document}
\mode<beamer>{\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}}
\begin{frame}{Handout frame}Visible text\end{frame}
\end{document}
""",
                encoding="utf-8",
            )

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False, strict=True),
            )

            self.assertEqual(result.source_expectations["figure_minimum"], 0)
            self.assertFalse(any(item.severity == "error" for item in result.findings))

    def test_existing_support_filename_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "article"
            output = root / "prepared"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\title{Collision}\n"
                "\\begin{document}Text\\end{document}\n",
                encoding="utf-8",
            )
            (source / "canvasally.sty").write_text(
                "% user-owned package\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Refusing to replace"):
                prepare_project(
                    resolve_project(source, output_dir=output),
                    ToolConfig(build=False),
                )


if __name__ == "__main__":
    unittest.main()
