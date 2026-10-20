from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from canvas_ally_latex.config import ToolConfig
from canvas_ally_latex.project import (
    _reduce_static_conditionals,
    prepare_project,
    resolve_project,
)
from canvas_ally_latex.transform import (
    find_unhandled_structural_content,
    instrument_main,
    transform_graphics,
    transform_pictures,
    transform_tikz,
    transform_unhandled_visual_directives,
)


class PlaceholderEdgeCaseTests(unittest.TestCase):
    def test_tikz_text_colour_is_not_mistaken_for_native_text_mode(self) -> None:
        transformed, findings = transform_tikz(
            r"\tikz[text=blue]{\node{x};}",
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertIn(r"\tikz[alt={tikz1},text=blue]", transformed)
        self.assertEqual(findings[0].code, "tikz-placeholder-alt")

    def test_disabled_native_tagging_is_preserved_but_blocking(self) -> None:
        source = r"\begin{tikzpicture}[tagging-setup={false}]\draw(0,0)--(1,1);\end{tikzpicture}"
        transformed, findings = transform_tikz(
            source,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertEqual(transformed, source)
        self.assertEqual(findings[0].code, "tikz-tagging-disabled")
        self.assertEqual(findings[0].severity, "error")

    def test_empty_and_placeholder_actualtext_become_reviewable_alt(self) -> None:
        empty, empty_findings = transform_graphics(
            r"\includegraphics[actualtext={}]{symbol.pdf}",
            Path("main.tex"),
            ToolConfig(),
        )
        placeholder, placeholder_findings = transform_graphics(
            r"\includegraphics[actualtext={graphic1}]{symbol.pdf}",
            Path("main.tex"),
            ToolConfig(strict=True),
        )

        self.assertIn(r"[alt={graphic1}]", empty)
        self.assertIn("% allytex: alt=graphic1", empty)
        self.assertEqual(empty_findings[0].code, "graphic-placeholder-alt")
        self.assertIn(r"[alt={graphic1}]", placeholder)
        self.assertEqual(placeholder_findings[0].severity, "error")

    def test_meaningful_native_edit_wins_over_stale_placeholder_comment(self) -> None:
        source = (
            "% allytex: alt=tikz1\n"
            r"\begin{tikzpicture}[alt={Meaningful allocation flow}]"
            r"\end{tikzpicture}"
        )
        transformed, findings = transform_tikz(
            source,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertIn("% allytex: alt=Meaningful allocation flow", transformed)
        self.assertIn("alt={Meaningful allocation flow}", transformed)
        self.assertNotIn("allytex: alt=tikz1", transformed)
        self.assertEqual(findings[0].code, "tikz-directive-synced")

    def test_meaningful_comment_can_change_generated_artifact_to_alt(self) -> None:
        source = (
            "% allytex: alt=Treatment allocation flow\n"
            r"\begin{tikzpicture}[artifact]\end{tikzpicture}"
        )
        transformed, findings = transform_tikz(
            source,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertIn("[alt={Treatment allocation flow}]", transformed)
        self.assertEqual(findings[0].code, "tikz-directive-synced")

    def test_alt_with_literal_unmatched_bracket_is_idempotent(self) -> None:
        source = r"\begin{tikzpicture}[scale=2]\draw(0,0)--(1,1);\end{tikzpicture}"
        config = ToolConfig(image_alts={"tikz:1": "Values left [ censored"})
        transformed, findings = transform_tikz(source, Path("main.tex"), config)
        rerun, rerun_findings = transform_tikz(
            transformed,
            Path("main.tex"),
            config,
        )

        self.assertEqual(rerun, transformed)
        self.assertEqual(findings[0].severity, "info")
        self.assertEqual(rerun_findings, [])

    def test_inline_tikz_owns_nested_graphic_and_alias_is_not_rewritten(self) -> None:
        source = r"\tikz<2->[baseline]{\node{\includegraphics{x.pdf}};}"
        graphic_pass, graphic_findings = transform_graphics(
            source,
            Path("main.tex"),
            ToolConfig(),
        )
        tikz_pass, tikz_findings = transform_tikz(
            graphic_pass,
            Path("main.tex"),
            ToolConfig(),
        )
        alias = r"\global\let\smalltikz=\tikz"
        alias_transformed, alias_findings = transform_tikz(
            alias,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertEqual(graphic_pass, source)
        self.assertEqual(graphic_findings, [])
        self.assertIn(r"\tikz<2->[alt={tikz1},baseline]", tikz_pass)
        self.assertEqual(tikz_findings[0].code, "tikz-placeholder-alt")
        self.assertEqual(alias_transformed, alias)
        self.assertEqual(alias_findings, [])

    def test_unbraced_inline_tikz_is_an_explicit_blocker(self) -> None:
        source = r"Text \tikz \draw (0,0)--(1,1);"
        findings = find_unhandled_structural_content(
            source,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertTrue(
            any(item.code == "unsupported-inline-tikz-form" for item in findings)
        )

    def test_nested_visual_has_one_owner_but_text_mode_exposes_formula(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{article}
\usepackage{tikz}
\begin{document}
\begin{picture}(10,10)
  \begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}
\end{picture}
\begin{tikzpicture}[tagging-setup={text}]\node {$x$};\end{tikzpicture}
\end{document}
""",
                encoding="utf-8",
            )

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )

            self.assertEqual(result.source_expectations["figure_minimum"], 1)
            self.assertEqual(result.source_expectations["formula_minimum"], 1)
            prepared = (output / "main.tex").read_text(encoding="utf-8")
            self.assertEqual(prepared.count("allytex: alt=picture1"), 1)
            self.assertNotIn("allytex: alt=tikz1", prepared)

    def test_custom_formula_tag_is_not_counted_as_figure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{article}
\usepackage{graphicx}
\begin{document}
\includegraphics[tagging-setup={alt={formula image},tag=Formula}]{formula.pdf}
\end{document}
""",
                encoding="utf-8",
            )

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )

            self.assertEqual(result.source_expectations["figure_minimum"], 0)
            self.assertEqual(result.source_expectations["formula_minimum"], 1)

    def test_unannotated_custom_tags_receive_sibling_placeholders(self) -> None:
        graphic, graphic_findings = transform_graphics(
            r"\includegraphics[tag=Formula]{formula.pdf}",
            Path("main.tex"),
            ToolConfig(),
        )
        picture, picture_findings = transform_pictures(
            r"\begin{picture}[tagging-setup={tag=Formula}](10,10)\end{picture}",
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertIn(r"[alt={graphic1},tag=Formula]", graphic)
        self.assertIn("% allytex: alt=graphic1", graphic)
        self.assertEqual(graphic_findings[0].code, "graphic-placeholder-alt")
        self.assertIn(
            r"[alt={picture1},tagging-setup={tag=Formula}]",
            picture,
        )
        self.assertIn("% allytex: alt=picture1", picture)
        self.assertEqual(picture_findings[0].code, "picture-placeholder-alt")

    def test_graphic_map_replaces_placeholder_in_direct_transform(self) -> None:
        generated, _ = transform_graphics(
            r"\includegraphics{flow.pdf}",
            Path("main.tex"),
            ToolConfig(),
        )
        revised, findings = transform_graphics(
            generated,
            Path("main.tex"),
            ToolConfig(image_alts={"flow.pdf": "Participant allocation flow"}),
        )

        self.assertIn("% allytex: alt=Participant allocation flow", revised)
        self.assertIn("alt={Participant allocation flow}", revised)
        self.assertNotIn("graphic1", revised)
        self.assertEqual(findings[0].code, "graphic-directive-synced")

    def test_pstricks_variants_are_distinct_and_nested_owner_suppresses_them(self) -> None:
        top_level = (
            r"\begin{pspicture}(0,0)(1,1)\end{pspicture}" + "\n"
            r"\begin{pspicture*}(0,0)(1,1)\end{pspicture*}"
        )
        top_findings = find_unhandled_structural_content(
            top_level,
            Path("main.tex"),
            ToolConfig(),
        )
        nested = (
            r"\begin{picture}[alt={Composite visual}](10,10)"
            r"\begin{pspicture*}(0,0)(1,1)\end{pspicture*}"
            r"\end{picture}"
        )
        nested_findings = find_unhandled_structural_content(
            nested,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertEqual(
            [item.code for item in top_findings],
            ["unsupported-environment-pspicture", "unsupported-environment-pspicture*"],
        )
        self.assertFalse(
            any(item.code.startswith("unsupported-environment-pspicture") for item in nested_findings)
        )

    def test_unsupported_visuals_receive_stable_review_markers(self) -> None:
        source = (
            r"\includesvg{diagram}" + "\n"
            r"\begin{pspicture*}(0,0)(1,1)\end{pspicture*}" + "\n"
            "% allytex: alt=Authored animation description\n"
            r"\animategraphics{12}{frame-}{1}{20}"
        )
        transformed, findings = transform_unhandled_visual_directives(
            source,
            Path("main.tex"),
            ToolConfig(),
        )
        rerun, rerun_findings = transform_unhandled_visual_directives(
            transformed,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertIn("% allytex: alt=visual1\n\\includesvg", transformed)
        self.assertIn("% allytex: alt=visual2\n\\begin{pspicture*}", transformed)
        self.assertNotIn("visual3", transformed)
        self.assertEqual(rerun, transformed)
        self.assertEqual(
            [item.code for item in findings],
            ["visual-placeholder-alt", "visual-placeholder-alt"],
        )
        self.assertEqual(
            [item.code for item in rerun_findings],
            ["visual-placeholder-alt", "visual-placeholder-alt"],
        )

    def test_additional_visual_commands_are_exact_marked_and_blocking(self) -> None:
        commands = (
            "pgfpicture",
            "pgfuseimage",
            "multiinclude",
            "includepdfmerge",
            "includemedia",
            "includemovie",
            "movie",
            "adjustimage",
            "adjincludegraphics",
            "asyinclude",
            "includestandalone",
            "includeinkscape",
            "epsfbox",
        )
        source = "\n".join(rf"\{name}{{asset}}" for name in commands)

        marked, marker_findings = transform_unhandled_visual_directives(
            source,
            Path("main.tex"),
            ToolConfig(),
        )
        rerun, rerun_findings = transform_unhandled_visual_directives(
            marked,
            Path("main.tex"),
            ToolConfig(),
        )
        blocking = find_unhandled_structural_content(
            marked,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertEqual(marked.count("allytex: alt=visual"), len(commands))
        self.assertEqual(rerun, marked)
        self.assertEqual(len(marker_findings), len(commands))
        self.assertEqual(len(rerun_findings), len(commands))
        self.assertEqual(
            {item.code for item in blocking},
            {"unsupported-visual-" + name for name in commands},
        )

    def test_additional_visual_environments_are_exact_marked_and_blocking(self) -> None:
        environments = (
            "pgfpicture",
            "overpic",
            "Overpic",
            "animateinline",
            "forest",
            "tikzcd",
            "circuitikz",
            "ganttchart",
        )
        source = "\n".join(
            rf"\begin{{{name}}}content\end{{{name}}}" for name in environments
        )

        marked, marker_findings = transform_unhandled_visual_directives(
            source,
            Path("main.tex"),
            ToolConfig(),
        )
        blocking = find_unhandled_structural_content(
            marked,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertEqual(marked.count("allytex: alt=visual"), len(environments))
        self.assertEqual(len(marker_findings), len(environments))
        self.assertEqual(
            {item.code for item in blocking},
            {"unsupported-environment-" + name for name in environments},
        )

    def test_unsupported_outer_environment_owns_nested_visuals(self) -> None:
        source = r"""\begin{forest}
  \begin{tikzpicture}\includegraphics{plot.pdf}\end{tikzpicture}
  \begin{picture}(10,10)content\end{picture}
  \includesvg{flow}
  \begin{tikzcd}A\end{tikzcd}
\end{forest}
"""

        marked, marker_findings = transform_unhandled_visual_directives(
            source,
            Path("main.tex"),
            ToolConfig(),
        )
        graphics, graphic_findings = transform_graphics(
            marked,
            Path("main.tex"),
            ToolConfig(),
        )
        tikz, tikz_findings = transform_tikz(
            graphics,
            Path("main.tex"),
            ToolConfig(),
        )
        pictures, picture_findings = transform_pictures(
            tikz,
            Path("main.tex"),
            ToolConfig(),
        )
        blocking = find_unhandled_structural_content(
            pictures,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertIn("% allytex: alt=visual1\n\\begin{forest}", pictures)
        self.assertEqual(pictures.count("allytex: alt="), 1)
        self.assertEqual(len(marker_findings), 1)
        self.assertEqual(graphic_findings, [])
        self.assertEqual(tikz_findings, [])
        self.assertEqual(picture_findings, [])
        self.assertEqual(
            [item.code for item in blocking],
            ["unsupported-environment-forest"],
        )

    def test_visual_scanner_ignores_definition_names_and_near_names(self) -> None:
        source = r"""\newcommand{\movie}[2]{plain text}
\NewDocumentCommand{\pgfuseimage}{m}{plain text}
\def\adjustimage#1{plain text}
\let\asyinclude\relax
\futurelet\includemedia\relax
\newenvironment{forest}{plain text}{plain text}
\movieclip{not-a-movie}
\pgfuseimages{not-a-pgf-image}
\begin{forestplot}not-a-forest\end{forestplot}
\includeinkscape{live}
"""

        marked, marker_findings = transform_unhandled_visual_directives(
            source,
            Path("main.tex"),
            ToolConfig(),
        )
        blocking = find_unhandled_structural_content(
            marked,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertEqual(marked.count("allytex: alt=visual"), 1)
        self.assertIn("% allytex: alt=visual1\n\\includeinkscape{live}", marked)
        self.assertEqual(len(marker_findings), 1)
        self.assertEqual(
            [item.code for item in blocking],
            ["unsupported-visual-includeinkscape"],
        )

    def test_visual_wrapper_definition_is_unchanged_but_blocking(self) -> None:
        source = (
            r"\newcommand{\flowdiagram}[1]{\includesvg{#1}}" + "\n"
            r"\newcommand{\hiddenflow}{\iffalse\includesvg{hidden}\fi}" + "\n"
            r"\flowdiagram{allocation}"
        )

        marked, marker_findings = transform_unhandled_visual_directives(
            source,
            Path("main.tex"),
            ToolConfig(),
        )
        graphics, graphic_findings = transform_graphics(
            marked,
            Path("main.tex"),
            ToolConfig(),
        )
        blocking = find_unhandled_structural_content(
            graphics,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertEqual(graphics, source)
        self.assertEqual(marker_findings, [])
        self.assertEqual(graphic_findings, [])
        self.assertEqual(
            [item.code for item in blocking],
            ["unsupported-visual-in-definition"],
        )
        self.assertIn(r"wraps \includesvg", blocking[0].message)

    def test_inactive_literal_conditionals_do_not_create_visual_findings(self) -> None:
        source = r"""\iffalse
\includesvg{hidden}
\includegraphics{hidden.pdf}
\begin{forest}\begin{tikzpicture}hidden\end{tikzpicture}\end{forest}
\fi
\iftrue\includeinkscape{live}\fi
"""

        marked, marker_findings = transform_unhandled_visual_directives(
            source,
            Path("main.tex"),
            ToolConfig(),
        )
        graphics, graphic_findings = transform_graphics(
            marked,
            Path("main.tex"),
            ToolConfig(),
        )
        tikz, tikz_findings = transform_tikz(
            graphics,
            Path("main.tex"),
            ToolConfig(),
        )
        blocking = find_unhandled_structural_content(
            tikz,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertEqual(tikz.count("allytex: alt=visual"), 1)
        self.assertNotIn("allytex: alt=graphic", tikz)
        self.assertNotIn("allytex: alt=tikz", tikz)
        self.assertEqual(len(marker_findings), 1)
        self.assertEqual(graphic_findings, [])
        self.assertEqual(tikz_findings, [])
        self.assertEqual(
            [item.code for item in blocking],
            ["unsupported-visual-includeinkscape"],
        )

    def test_additional_dynamic_visual_commands_are_marked_and_blocking(self) -> None:
        commands = (
            "pgfuseimage",
            "multiinclude",
            "includepdfmerge",
            "includemedia",
            "includemovie",
            "adjustimage",
            "adjincludegraphics",
            "asyinclude",
            "includestandalone",
            "includeinkscape",
        )
        source = "\n".join(
            rf"\csname {name}\endcsname{{asset}}" for name in commands
        )

        marked, marker_findings = transform_unhandled_visual_directives(
            source,
            Path("main.tex"),
            ToolConfig(),
        )
        blocking = find_unhandled_structural_content(
            marked,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertEqual(marked.count("allytex: alt=visual"), len(commands))
        self.assertEqual(len(marker_findings), len(commands))
        self.assertEqual(
            [item.code for item in blocking],
            ["unsupported-dynamic-visual-command"] * len(commands),
        )

    def test_literal_visual_examples_are_never_rewritten(self) -> None:
        source = (
            r"\Verb|x %in% y; \includesvg{fancy}| \includesvg{live-one}" + "\n"
            r"\mintinline{r}|x %in% y; \includesvg{minted}| \includegraphics{live.pdf}" + "\n"
            r"\texttt{\string\includesvg\{stringed\}}" + "\n"
            r"\url{https://example.test/a%20b/\includesvg{url}} \includesvg{live-two}" + "\n"
            r"\Verb|\includegraphics{literal.pdf}|" + "\n"
            r"\includesvg{live-three}"
        )
        marked, findings = transform_unhandled_visual_directives(
            source,
            Path("main.tex"),
            ToolConfig(),
        )
        transformed_graphics, graphic_findings = transform_graphics(
            marked,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertEqual(marked.count("allytex: alt=visual"), 3)
        self.assertIn(r"\includesvg{live-one}", marked)
        self.assertIn(r"\includesvg{live-two}", marked)
        self.assertIn(r"\includesvg{live-three}", marked)
        self.assertIn(r"\Verb|\includegraphics{literal.pdf}|", transformed_graphics)
        self.assertIn("% allytex: alt=graphic1", transformed_graphics)
        self.assertEqual(
            [item.code for item in findings],
            ["visual-placeholder-alt"] * 3,
        )
        self.assertEqual(
            [item.code for item in graphic_findings],
            ["graphic-placeholder-alt"],
        )

    def test_project_marks_but_still_blocks_unsupported_visuals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{article}
\begin{document}
\includesvg{study-flow}
\end{document}
""",
                encoding="utf-8",
            )

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )
            prepared = (output / "main.tex").read_text(encoding="utf-8")
            codes = {item.code for item in result.findings}

            self.assertIn("% allytex: alt=visual1\n\\includesvg", prepared)
            self.assertIn("visual-placeholder-alt", codes)
            self.assertIn("unsupported-visual-includesvg", codes)

    def test_unsupported_marker_does_not_leak_to_later_same_line_graphic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{article}
\begin{document}
\includesvg{study-flow} \includegraphics{outcome.pdf}
\end{document}
""",
                encoding="utf-8",
            )

            prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )
            prepared = (output / "main.tex").read_text(encoding="utf-8")

            self.assertIn("% allytex: alt=visual1", prepared)
            self.assertIn("% allytex: alt=graphic1", prepared)
            self.assertIn(r"\includegraphics[alt={graphic1}]{outcome.pdf}", prepared)
            self.assertNotIn(r"\includegraphics[alt={visual1}]", prepared)

    def test_dynamic_visual_control_sequences_are_marked_and_blocking(self) -> None:
        source = (
            r"\csname includesvg\endcsname{flow}" + "\n"
            r"\csname includegraphics\endcsname{outcome.pdf}"
        )

        marked, marker_findings = transform_unhandled_visual_directives(
            source,
            Path("main.tex"),
            ToolConfig(),
        )
        blocking = find_unhandled_structural_content(
            marked,
            Path("main.tex"),
            ToolConfig(),
        )

        self.assertIn("% allytex: alt=visual1", marked)
        self.assertIn("% allytex: alt=visual2", marked)
        self.assertEqual(len(marker_findings), 2)
        self.assertEqual(
            [item.code for item in blocking],
            [
                "unsupported-dynamic-visual-command",
                "unsupported-dynamic-visual-command",
            ],
        )

    def test_escaped_control_symbols_are_not_rewritten_as_visuals(self) -> None:
        source = (
            r"Text \\includesvg is a word; \\includegraphics is another; "
            r"\\tikz and \\begin{picture} are examples."
        )

        marked, marker_findings = transform_unhandled_visual_directives(
            source,
            Path("main.tex"),
            ToolConfig(strict=True),
        )
        graphics, graphic_findings = transform_graphics(
            marked,
            Path("main.tex"),
            ToolConfig(strict=True),
        )
        tikz, tikz_findings = transform_tikz(
            graphics,
            Path("main.tex"),
            ToolConfig(strict=True),
        )
        pictures, picture_findings = transform_pictures(
            tikz,
            Path("main.tex"),
            ToolConfig(strict=True),
        )

        self.assertEqual(pictures, source)
        self.assertEqual(marker_findings, [])
        self.assertEqual(graphic_findings, [])
        self.assertEqual(tikz_findings, [])
        self.assertEqual(picture_findings, [])
        self.assertEqual(
            find_unhandled_structural_content(
                source,
                Path("main.tex"),
                ToolConfig(strict=True),
            ),
            [],
        )

    def test_line_map_uses_original_line_before_other_insertions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\n"
                "\\usepackage{graphicx,tikz}\n"
                "\\begin{document}\n"
                "\\includegraphics{unmapped.pdf}\n"
                "\\begin{tikzpicture}\\draw(0,0)--(1,1);\\end{tikzpicture}\n"
                "\\end{document}\n",
                encoding="utf-8",
            )
            config = ToolConfig(
                build=False,
                image_alts={"main.tex::tikz:5": "Mapped study flow"},
            )

            prepare_project(resolve_project(source, output_dir=output), config)
            prepared = (output / "main.tex").read_text(encoding="utf-8")

            self.assertIn("% allytex: alt=Mapped study flow", prepared)
            self.assertIn("alt={Mapped study flow}", prepared)
            self.assertNotIn("allytex: alt=tikz1", prepared)

    def test_false_end_document_and_endinput_tail_do_not_hide_live_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{article}
\usepackage{graphicx}
\begin{document}
\iffalse\end{document}\fi
\includegraphics{live.pdf}
\input{section}
\end{document}
""",
                encoding="utf-8",
            )
            (source / "section.tex").write_text(
                "Visible.\\endinput\n\\includegraphics{tail.pdf}\n",
                encoding="utf-8",
            )

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )
            main = (output / "main.tex").read_text(encoding="utf-8")
            section = (output / "section.tex").read_text(encoding="utf-8")

            self.assertIn("allytex: alt=graphic1", main)
            self.assertNotIn("allytex: alt=graphic2", section)
            self.assertIn(r"\includegraphics{tail.pdf}", section)
            self.assertEqual(result.source_expectations["figure_minimum"], 1)

    def test_beamer_hidden_dependency_does_not_consume_placeholder_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{beamer}
\usepackage{tikz}
\begin{document}
\only<article>{\input{hidden}}
\begin{frame}{Live}\begin{tikzpicture}\draw(0,0)--(1,1);\end{tikzpicture}\end{frame}
\end{document}
""",
                encoding="utf-8",
            )
            hidden = r"\begin{tikzpicture}\draw(0,0)--(2,2);\end{tikzpicture}"
            (source / "hidden.tex").write_text(hidden, encoding="utf-8")

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )
            main = (output / "main.tex").read_text(encoding="utf-8")

            self.assertIn("allytex: alt=tikz1", main)
            self.assertEqual((output / "hidden.tex").read_text(encoding="utf-8"), hidden)
            self.assertEqual(result.source_expectations["figure_minimum"], 1)

    def test_includeonly_excludes_nonrendered_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{article}
\includeonly{live}
\begin{document}
\include{live}
\include{excluded}
\end{document}
""",
                encoding="utf-8",
            )
            live = r"\includegraphics{live.pdf}"
            excluded = r"\includegraphics{excluded.pdf}"
            (source / "live.tex").write_text(live, encoding="utf-8")
            (source / "excluded.tex").write_text(excluded, encoding="utf-8")

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )

            self.assertIn("allytex: alt=graphic1", (output / "live.tex").read_text())
            self.assertEqual((output / "excluded.tex").read_text(), excluded)
            self.assertEqual(result.source_expectations["figure_minimum"], 1)

    def test_main_instrumentation_is_byte_idempotent(self) -> None:
        source = r"""% !TeX program = lualatex
\DocumentMetadata{lang=fr}
\documentclass{article}
\title{Idempotence}
\begin{document}Text\end{document}
"""
        first, _, _, _ = instrument_main(
            source,
            Path("main.tex"),
            ToolConfig(build=False),
        )
        second, _, _, _ = instrument_main(
            first,
            Path("main.tex"),
            ToolConfig(build=False),
        )

        self.assertEqual(second, first)

    def test_static_condition_reduction_ignores_definitions_and_string(self) -> None:
        source = (
            r"\def\start{\iffalse}" + "\n"
            r"\string\iffalse" + "\n"
            r"\let\ifdraft=\iffalse" + "\n"
            r"\iffalse hidden \fi LIVE" + "\n"
        )

        reduced = _reduce_static_conditionals(source)

        self.assertIn(r"\def\start{\iffalse}", reduced)
        self.assertIn(r"\string\iffalse", reduced)
        self.assertIn(r"\let\ifdraft=\iffalse", reduced)
        self.assertNotIn("hidden", reduced)
        self.assertIn("LIVE", reduced)

    def test_formula_candidates_ignore_alt_and_url_but_keep_link_body(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{article}
\usepackage{graphicx,hyperref}
\begin{document}
\includegraphics[alt={plot of $x$}]{plot.pdf}
\url{https://example.test/$100$/}
\hyperlink{target}{$y$}
\end{document}
""",
                encoding="utf-8",
            )

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )

            self.assertEqual(result.source_expectations["formula_minimum"], 1)
            self.assertEqual(result.source_expectations["figure_minimum"], 1)

    def test_dependency_lookup_matches_main_build_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            (source / "sub").mkdir(parents=True)
            (source / "main.tex").write_text(
                r"\documentclass{article}\begin{document}\input{sub/a}\end{document}",
                encoding="utf-8",
            )
            (source / "sub" / "a.tex").write_text(r"\input{b}", encoding="utf-8")
            root_b = r"\includegraphics{root.pdf}"
            sub_b = r"\includegraphics{sub.pdf}"
            (source / "b.tex").write_text(root_b, encoding="utf-8")
            (source / "sub" / "b.tex").write_text(sub_b, encoding="utf-8")

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )

            self.assertIn("allytex: alt=graphic1", (output / "b.tex").read_text())
            self.assertEqual((output / "sub" / "b.tex").read_text(), sub_b)
            self.assertEqual(result.source_expectations["figure_minimum"], 1)

    def test_stateful_gin_default_blocks_graphic_and_picture_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{article}
\usepackage{graphicx}
\setkeys{Gin}{artifact}
\begin{document}\input{content}\end{document}
""",
                encoding="utf-8",
            )
            content = (
                r"\includegraphics{x.pdf}" + "\n"
                r"\begin{picture}(10,10)\put(0,0){x}\end{picture}" + "\n"
            )
            (source / "content.tex").write_text(content, encoding="utf-8")

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )
            prepared = (output / "content.tex").read_text()

            self.assertEqual(prepared, content)
            codes = {item.code for item in result.findings}
            self.assertIn("graphic-stateful-annotation", codes)
            self.assertIn("picture-stateful-annotation", codes)

    def test_reachable_dot_directory_is_transformed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            (source / ".parts").mkdir(parents=True)
            (source / "main.tex").write_text(
                r"\documentclass{article}\begin{document}\input{.parts/live}\end{document}",
                encoding="utf-8",
            )
            (source / ".parts" / "live.tex").write_text(
                r"\includegraphics{live.pdf}",
                encoding="utf-8",
            )

            prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )

            self.assertIn(
                "allytex: alt=graphic1",
                (output / ".parts" / "live.tex").read_text(),
            )


if __name__ == "__main__":
    unittest.main()
