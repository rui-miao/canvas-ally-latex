from __future__ import annotations

import re
import shutil
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
from canvas_ally_latex.transform import group_content, iter_graphic_commands


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "proxitr"


class ProxITRRegressionTests(unittest.TestCase):
    """Regression coverage distilled from the real ProxITR Beamer deck."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.source = root / "proxitr"
        self.output = root / "proxitr-accessible"
        shutil.copytree(FIXTURE, self.source)
        self.original = (self.source / "main.tex").read_text(encoding="utf-8")

        self.result = prepare_project(
            resolve_project(self.source, output_dir=self.output),
            ToolConfig(build=False, beamer_mode="ltx-talk"),
        )
        self.prepared = (self.output / "main.tex").read_text(encoding="utf-8")

    def _source_line(self, needle: str) -> int:
        offset = self.original.index(needle)
        return self.original.count("\n", 0, offset) + 1

    def _graphic_options(self, asset: str) -> str:
        for command in iter_graphic_commands(self.prepared):
            if (group_content(self.prepared, command.mandatory) or "").strip() != asset:
                continue
            return ",".join(
                group_content(self.prepared, optional) or ""
                for optional in command.optionals
            )
        self.fail(f"generated source does not contain graphic {asset!r}")

    def test_xcolor_options_are_forwarded_before_ltx_talk(self) -> None:
        forwarded = r"\PassOptionsToPackage{dvipsnames}{xcolor}"
        document_class = r"\documentclass[aspect-ratio=16:10]{ltx-talk}"

        self.assertIn(forwarded, self.prepared)
        self.assertIn(document_class, self.prepared)
        self.assertLess(self.prepared.index(forwarded), self.prepared.index(document_class))
        self.assertFalse(
            any(
                finding.code == "beamer-class-option" and "xcolor" in finding.message
                for finding in self.result.findings
            )
        )

    def test_local_color_theme_keeps_tcolorbox_available(self) -> None:
        shim = r"\input{canvasally-ltx-talk-shim.tex}"
        local_theme = r"\RequirePackage{beamercolorthemeSimplePlus}"

        self.assertIn(shim, self.prepared)
        self.assertIn(local_theme, self.prepared)
        self.assertLess(self.prepared.index(shim), self.prepared.index(local_theme))
        self.assertIn(r"\begin{tcolorbox}[green,title={Key result}]", self.prepared)
        copied_theme = self.output / "beamercolorthemeSimplePlus.sty"
        self.assertIn(r"\RequirePackage{tcolorbox}", copied_theme.read_text(encoding="utf-8"))
        self.assertTrue(
            any(finding.code == "beamer-local-color-theme" for finding in self.result.findings)
        )

    def test_overlay_aware_newcommand_is_supported_by_the_early_shim(self) -> None:
        self.assertIn(r"\newcommand<>{\NormalBox}[1]", self.prepared)
        shim = (self.output / "canvasally-ltx-talk-shim.tex").read_text(
            encoding="utf-8"
        )
        self.assertIn(r"\def\newcommand", shim)
        self.assertIn(r"\CanvasAllyOverlayStart", shim)
        self.assertFalse(
            any(
                finding.code == "unsupported-inline-tikz-form"
                for finding in self.result.findings
            )
        )

    def test_legacy_beamer_font_switches_are_restored_by_the_early_shim(self) -> None:
        shim = (self.output / "canvasally-ltx-talk-shim.tex").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            r"\DeclareOldFontCommand{\rm}{\normalfont\rmfamily}{\mathrm}",
            shim,
        )
        self.assertIn(
            r"\DeclareOldFontCommand{\tt}{\normalfont\ttfamily}{\mathtt}",
            shim,
        )
        self.assertIn(
            r"\DeclareOldFontCommand{\bf}{\normalfont\bfseries}{\mathbf}",
            shim,
        )
        self.assertIn(r"{\bf Legacy bold text}", self.prepared)
        self.assertIn(r"{\tt legacy monospaced text}", self.prepared)

    def test_alignment_actions_are_mapped_without_changing_formula_minimum(self) -> None:
        self.assertIn(
            r"\uncover<+->{R(d_1) }&\uncover<.->{ = h(W,1,L)-h(W,-1,L)}",
            self.prepared,
        )
        self.assertIn(
            r"\uncover<+->{}&\uncover<.->{ = r_1(L,Z)",
            self.prepared,
        )
        mapped = [
            finding
            for finding in self.result.findings
            if finding.code == "beamer-action-alignment-mapped"
        ]
        self.assertEqual(len(mapped), 2)
        self.assertEqual(self.result.source_expectations["formula_minimum"], 7)

    def test_figure_placement_and_subfigure_syntax_are_migrated(self) -> None:
        self.assertNotIn(r"\usepackage{subcaption}", self.prepared)
        self.assertNotRegex(self.prepared, r"\\begin\{figure\}\s*\[[Hh]\]")
        self.assertNotIn(r"\begin{subfigure}", self.prepared)
        self.assertNotIn(r"\end{subfigure}", self.prepared)
        risky = [
            finding
            for finding in self.result.findings
            if finding.severity == "error"
            and any(
                term in (finding.code + " " + finding.message).lower()
                for term in ("subfigure", "figure placement", "float placement")
            )
        ]
        self.assertEqual(risky, [])

    def test_panel_alts_combine_parent_and_child_captions(self) -> None:
        first = self._graphic_options("example-image-a.pdf")
        second = self._graphic_options("example-image-b.pdf")

        for options, child_caption in (
            (first, "Sample size 2,000"),
            (second, "Sample size 5,000"),
        ):
            self.assertIn("alt=", options)
            self.assertIn("Boxplots of values for Scenario L1", options)
            self.assertIn(child_caption, options)

    def test_layout_tabular_is_not_given_false_header_cells(self) -> None:
        start = self.prepared.index("% BEGIN layout table")
        end = self.prepared.index("% END layout table", start)
        layout_table = self.prepared[start:end]

        self.assertNotIn("table/header-rows", layout_table)
        self.assertFalse(any(finding.code == "table-header" for finding in self.result.findings))
        self.assertEqual(self.result.source_expectations["table_minimum"], 0)

    def test_unused_visual_macro_does_not_create_a_blocker(self) -> None:
        unused_line = self._source_line(r"\newcommand{\ShowPutAtGrid}")
        called_line = self._source_line(r"\newcommand<>{\NormalBox}")
        errors = [finding for finding in self.result.findings if finding.severity == "error"]

        self.assertFalse(
            any(finding.line == unused_line for finding in errors),
            "an uncalled visual macro must not block the generated static deck",
        )
        unexpected = [
            finding
            for finding in errors
            if not (
                finding.code == "unsupported-visual-in-definition"
                and finding.line == called_line
            )
        ]
        self.assertEqual(unexpected, [])
        self.assertLessEqual(len(errors), 1)

    def test_direct_tikz_uses_its_adjacent_authored_description(self) -> None:
        description = "Causal DAG with observed treatment and outcome nodes"

        self.assertIn(r"\begin{tikzpicture}[alt={" + description + "}]", self.prepared)
        self.assertFalse(
            any(finding.code == "tikz-placeholder-alt" for finding in self.result.findings)
        )

    def test_underlined_math_is_separated_and_nested_math_is_counted_once(self) -> None:
        self.assertIn(
            r"\underline{identify }$V(d)$\underline{ for all }$d\in\mathcal{D}$",
            self.prepared,
        )
        self.assertIn(
            r"\text{ for some measurable $r_1$ on $\mathcal{L}\times\mathcal{Z}$}",
            self.prepared,
        )
        separated = [
            finding
            for finding in self.result.findings
            if finding.code == "underlined-math-separated"
        ]
        self.assertEqual(len(separated), 1)
        self.assertEqual(separated[0].severity, "warning")
        self.assertEqual(self.result.source_expectations["formula_minimum"], 7)


if __name__ == "__main__":
    unittest.main()
