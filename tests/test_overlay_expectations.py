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
from canvas_ally_latex.project import prepare_project, resolve_project


class OverlayExpectationTests(unittest.TestCase):
    def _prepare(self, source_text: str, *, strict: bool = False):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = root / "slides"
        output = root / "prepared"
        source.mkdir()
        (source / "main.tex").write_text(source_text, encoding="utf-8")
        result = prepare_project(
            resolve_project(source, output_dir=output),
            ToolConfig(build=False, strict=strict),
        )
        return temporary, result

    def test_ltx_talk_all_and_default_wrappers_contribute_expected_tags(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Overlay audit}
\begin{document}
\begin{frame}{Known active wrappers}
\only<all>{\includegraphics[alt={Only}]{only.pdf}}
\mode<all>{\includegraphics[alt={Mode}]{mode.pdf}}
\visible{\includegraphics[alt={Visible}]{visible.pdf}}
\uncover < all > {\includegraphics[alt={Uncover}]{uncover.pdf}}
\alt<all>{\includegraphics[alt={Selected alt}]{alt-selected.pdf}}
  {\includegraphics[alt={Unselected alt}]{alt-unselected.pdf}}
\temporal<all>
  {\includegraphics[alt={Before}]{before.pdf}}
  {\includegraphics[alt={During}]{during.pdf}}
  {\includegraphics[alt={After}]{after.pdf}}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result.source_expectations["figure_minimum"], 6)
        self.assertTrue(result.source_expectations["figures"])

    def test_math_inside_an_outer_math_container_is_not_counted_twice(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Nested math audit}
\begin{document}
\begin{frame}{One Formula per outer container}
$x$
\begin{align*}
  f(x) &= \text{measurable $r_1$ on $\mathcal{L}$}
\end{align*}
\(\text{inline $s$}\)
\[\text{display $t$}\]
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result.source_expectations["formula_minimum"], 4)

    def test_ensuremath_owns_nested_math_and_belongs_to_outer_math(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Ensuremath ownership}
\begin{document}
\begin{frame}{One Formula per outer container}
\ensuremath{\text{standalone $x$}}
\ensuremath{\ensuremath{nested}}
\begin{align*}
  f(x) &= \ensuremath{r_1} + \text{re-entered $s$}
\end{align*}
\(\ensuremath{r_2}\)
\[\ensuremath{r_3}\]
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result.source_expectations["formula_minimum"], 5)

    def test_exact_only_and_branching_overlays_are_conservatively_excluded(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Conservative overlay audit}
\begin{document}
\begin{frame}{Unknown selections}
\only<2>{\includegraphics[alt={Unknown only}]{unknown-only.pdf}}
\mode<custom>{\includegraphics[alt={Unknown mode}]{unknown-mode.pdf}}
\visible<2>{\includegraphics[alt={Unknown visible}]{unknown-visible.pdf}}
\uncover<2>{\includegraphics[alt={Unknown uncover}]{unknown-uncover.pdf}}
\alt<all>{$x$}{\includegraphics[alt={Unselected alt}]{alt-unselected.pdf}}
\alt<2>{\includegraphics[alt={Unknown alt one}]{unknown-alt-one.pdf}}
  {\includegraphics[alt={Unknown alt two}]{unknown-alt-two.pdf}}
\temporal<all>
  {\includegraphics[alt={Before}]{before.pdf}}
  {$y$}
  {\includegraphics[alt={After}]{after.pdf}}
\temporal<2>
  {\includegraphics[alt={Unknown before}]{unknown-before.pdf}}
  {\includegraphics[alt={Unknown during}]{unknown-during.pdf}}
  {\includegraphics[alt={Unknown after}]{unknown-after.pdf}}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result.source_expectations["figure_minimum"], 2)
        self.assertEqual(result.source_expectations["formula_minimum"], 2)

    def test_beamer_presentation_modes_are_counted_after_static_mapping(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{beamer}
\title{Beamer overlay audit}
\begin{document}
\begin{frame}{Mapped presentation wrappers}
\only<presentation>{\includegraphics[alt={Only}]{only.pdf}}
\mode<presentation>{\includegraphics[alt={Mode}]{mode.pdf}}
\visible<presentation>{\includegraphics[alt={Visible}]{visible.pdf}}
\uncover<presentation>{\includegraphics[alt={Uncover}]{uncover.pdf}}
\alt<presentation>{\includegraphics[alt={Alt selected}]{alt-selected.pdf}}
  {\includegraphics[alt={Alt omitted}]{alt-omitted.pdf}}
\temporal<presentation>
  {\includegraphics[alt={Before}]{before.pdf}}
  {\includegraphics[alt={During}]{during.pdf}}
  {\includegraphics[alt={After}]{after.pdf}}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result.source_expectations["figure_minimum"], 6)

    def test_nested_selected_wrapper_cannot_escape_unknown_outer_wrapper(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Nested overlay audit}
\begin{document}
\begin{frame}{Nested wrappers}
\only<custom>{
  \only<all>{\includegraphics[alt={Still unknown}]{nested-unknown.pdf}}
}
\only<all>{
  \alt<all>{\includegraphics[alt={Known nested}]{nested-known.pdf}}
    {\includegraphics[alt={Unselected nested}]{nested-unselected.pdf}}
}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result.source_expectations["figure_minimum"], 1)

    def test_numeric_overlay_environments_contribute_expected_tags(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Overlay environments}
\begin{document}
\begin{frame}{Ranges}
\begin{onlyenv}<1>
  \includegraphics[alt={First slide}]{first.pdf}
\end{onlyenv}
\begin{visibleenv}<2-4>
  $x$
\end{visibleenv}
\begin{uncoverenv}<-3>
  \includegraphics[alt={Open range}]{range.pdf}
\end{uncoverenv}
\begin{onlyenv}<custom>
  \includegraphics[alt={Unknown}]{unknown.pdf}
\end{onlyenv}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        prepared = (Path(temporary.name) / "prepared" / "main.tex").read_text()
        self.assertNotIn(r"\begin{onlyenv}", prepared)
        self.assertIn(
            "\\begingroup\\only<1>{\n"
            "  \\includegraphics[alt={First slide}]{first.pdf}\n"
            "}\\endgroup",
            prepared,
        )
        self.assertIn(r"\begingroup\only<custom>{", prepared)
        self.assertEqual(result.source_expectations["figure_minimum"], 1)
        self.assertEqual(result.source_expectations["formula_minimum"], 1)

    def test_common_relative_overlays_contribute_expected_tags(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Relative overlays}
\begin{document}
\begin{frame}{Relative selections}
\only<+->{\includegraphics[alt={Advancing range}]{plus.pdf}}
\visible<.->{\includegraphics[alt={Current range}]{dot.pdf}}
\uncover<+>{\includegraphics[alt={Advancing slide}]{advance.pdf}}
\only<.(1)>{\includegraphics[alt={Positive dot offset}]{dot-offset.pdf}}
\visible<+(2)>{\includegraphics[alt={Positive plus offset}]{plus-offset.pdf}}
\uncover<.(1)-.(2)>{\includegraphics[alt={Bounded relative range}]{bounded.pdf}}
\begin{onlyenv}<+->
  \includegraphics[alt={Environment range}]{environment.pdf}
\end{onlyenv}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result.source_expectations["figure_minimum"], 6)

    def test_final_slide_policy_counts_open_ranges_and_execution_wrappers(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Final tagged slide}
\begin{document}
\begin{frame}{Conservative minima}
\only<2>{\includegraphics[alt={Exact only}]{exact-only.pdf}}
\only<2->{\includegraphics[alt={Open only}]{open-only.pdf}}
\begin{onlyenv}<1>
  \includegraphics[alt={Exact environment}]{exact-environment.pdf}
\end{onlyenv}
\begin{onlyenv}<3->
  \includegraphics[alt={Open environment}]{open-environment.pdf}
\end{onlyenv}
\visible<2>{\includegraphics[alt={Exact visible}]{exact-visible.pdf}}
\uncover<2-4>{\includegraphics[alt={Bounded uncover}]{bounded-uncover.pdf}}
\alt<2>
  {\includegraphics[alt={Uncertain selected}]{uncertain-selected.pdf}}
  {\includegraphics[alt={Uncertain fallback}]{uncertain-fallback.pdf}}
\alt<2->
  {\includegraphics[alt={Open selected}]{open-selected.pdf}}
  {\includegraphics[alt={Open fallback}]{open-fallback.pdf}}
\temporal<2>
  {\includegraphics[alt={Uncertain before}]{uncertain-before.pdf}}
  {\includegraphics[alt={Uncertain during}]{uncertain-during.pdf}}
  {\includegraphics[alt={Uncertain after}]{uncertain-after.pdf}}
\temporal<2->
  {\includegraphics[alt={Open before}]{open-before.pdf}}
  {\includegraphics[alt={Open during}]{open-during.pdf}}
  {\includegraphics[alt={Open after}]{open-after.pdf}}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        # Open-ended only/onlyenv and branching selections are active on the
        # final tagged slide. visible/uncover execute their content there even
        # when it is visually hidden. Exact branching selection is uncertain.
        self.assertEqual(result.source_expectations["figure_minimum"], 6)

    def test_unsafe_onlyenv_is_preserved_and_strict_blocking(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Unsafe only environment}
\begin{document}
\begin{frame*}{Literal body}
\begin{onlyenv}<1>
\begin{verbatim}
literal { content }
\end{verbatim}
\end{onlyenv}
\end{frame*}
\end{document}
""",
            strict=True,
        )
        self.addCleanup(temporary.cleanup)

        prepared = (Path(temporary.name) / "prepared" / "main.tex").read_text()
        unsafe = [
            item for item in result.findings if item.code == "unsafe-onlyenv-tagging"
        ]
        self.assertIn(r"\begin{onlyenv}<1>", prepared)
        self.assertEqual(len(unsafe), 1)
        self.assertEqual(unsafe[0].severity, "error")

    def test_visible_and_uncover_keep_dynamic_and_dot_overlay_content(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Opacity wrappers}
\begin{document}
\begin{frame}{Always executed bodies}
\visible<.>{\includegraphics[alt={Dot visible}]{dot-visible.pdf}}
\uncover<custom>{\includegraphics[alt={Custom uncover}]{custom-uncover.pdf}}
\begin{visibleenv}<\dynamicOverlay>
  \includegraphics[alt={Dynamic visible environment}]{dynamic-visible.pdf}
\end{visibleenv}
\begin{uncoverenv}<.>
  \includegraphics[alt={Dot uncover environment}]{dot-uncover.pdf}
\end{uncoverenv}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result.source_expectations["figure_minimum"], 4)
        self.assertNotIn(
            "ambiguous-overlay-tag-minimum",
            [item.code for item in result.findings],
        )

    def test_false_alert_and_structure_keep_their_unstyled_bodies(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Styling overlays}
\begin{document}
\begin{frame}{Content remains}
\alert<0-0>{\includegraphics[alt={Unalerted image}]{unalerted.pdf}}
\structure<2-1>{$x$ remains ordinary text}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        prepared = (Path(temporary.name) / "prepared" / "main.tex").read_text()
        self.assertIn(r"\alert<0-0>", prepared)
        self.assertIn("unalerted.pdf", prepared)
        self.assertIn(r"\structure<2-1>", prepared)
        self.assertIn("$x$ remains ordinary text", prepared)
        self.assertEqual(result.source_expectations["figure_minimum"], 1)
        self.assertEqual(result.source_expectations["formula_minimum"], 1)

    def test_exact_overlay_includegraphics_is_not_a_final_slide_minimum(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Graphic overlay}
\begin{document}
\begin{frame}{Exact graphic}
\includegraphics<1>[alt={First only}]{first.pdf}
\only<2>{Later slide}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result.source_expectations["figure_minimum"], 0)
        ambiguity = [
            item
            for item in result.findings
            if item.code == "ambiguous-overlay-tag-minimum"
        ]
        self.assertEqual(len(ambiguity), 1)
        self.assertEqual(ambiguity[0].severity, "warning")

    def test_ambiguous_overlay_warning_is_deduplicated_and_strict_blocking(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Ambiguous overlays}
\begin{document}
\begin{frame}{Several uncertain branches}
\only<1>{\includegraphics[alt={First}]{first.pdf}}
\alt<2>{Second}{Fallback}
\temporal<3>{Before}{During}{After}
\end{frame}
\end{document}
""",
            strict=True,
        )
        self.addCleanup(temporary.cleanup)

        ambiguity = [
            item
            for item in result.findings
            if item.code == "ambiguous-overlay-tag-minimum"
        ]
        self.assertEqual(len(ambiguity), 1)
        self.assertEqual(ambiguity[0].severity, "error")

    def test_open_overlay_includegraphics_is_a_final_slide_minimum(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Graphic overlay}
\begin{document}
\begin{frame}{Open graphic}
\includegraphics<2->[alt={Final graphic}]{final.pdf}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result.source_expectations["figure_minimum"], 1)

    def test_uncertain_frame_action_and_custom_tag_slides_are_excluded(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Frame overlay}
\begin{document}
\begin{frame}<1>{Uncertain frame}
\includegraphics[alt={First frame}]{first-frame.pdf}
\only<2>{Later slide}
\end{frame}
\begin{frame}<1->[tag-slides=1]{Custom tagged slide}
\only<2->{\includegraphics[alt={Not on tagged slide}]{custom-tag.pdf}}
\end{frame}
\begin{frame}<1->{Guaranteed final frame}
\only<2->{\includegraphics[alt={Final slide}]{final-frame.pdf}}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        self.assertEqual(result.source_expectations["figure_minimum"], 1)

    def test_statically_empty_ranges_are_removed_and_fallbacks_retained(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Empty overlays}
\begin{document}
\begin{frame}{No invisible reading-order content}
\visible<0-0>{\includegraphics[alt={Hidden visible}]{hidden-visible.pdf}}
\uncover<2-1>{$hidden_uncover$}
\only<0,2-1>{\includegraphics[alt={Hidden only}]{hidden-only.pdf}}
\begin{visibleenv}<0-0>
  \includegraphics[alt={Hidden environment}]{hidden-environment.pdf}
\end{visibleenv}
\begin{uncoverenv}<3-2>
  $hidden_environment_math$
\end{uncoverenv}
\alt<0-0>
  {\includegraphics[alt={Hidden alt}]{hidden-alt.pdf}}
  {\includegraphics[alt={Visible fallback}]{visible-fallback.pdf}}
\temporal<2-1>
  {\includegraphics[alt={Visible before}]{visible-before.pdf}}
  {\includegraphics[alt={Hidden during}]{hidden-during.pdf}}
  {\includegraphics[alt={Visible after}]{visible-after.pdf}}
\temporal<0-0>
  {\includegraphics[alt={Zero before}]{zero-before.pdf}}
  {\includegraphics[alt={Zero during}]{zero-during.pdf}}
  {\includegraphics[alt={Zero after}]{zero-after.pdf}}
\temporal<2-0>
  {\includegraphics[alt={Past before}]{past-before.pdf}}
  {\includegraphics[alt={Past during}]{past-during.pdf}}
  {\includegraphics[alt={Past after}]{past-after.pdf}}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        prepared = (Path(temporary.name) / "prepared" / "main.tex").read_text()
        self.assertNotIn("hidden-visible.pdf", prepared)
        self.assertNotIn("hidden_uncover", prepared)
        self.assertNotIn("hidden-only.pdf", prepared)
        self.assertNotIn("hidden-environment.pdf", prepared)
        self.assertNotIn("hidden_environment_math", prepared)
        self.assertNotIn("hidden-alt.pdf", prepared)
        self.assertIn("visible-before.pdf", prepared)
        # Preserve the native temporal wrapper plus its first and third
        # branches. Only the provably unreachable middle content is blanked.
        self.assertIn(r"\temporal<2-1>", prepared)
        self.assertNotIn("hidden-during.pdf", prepared)
        self.assertIn("visible-fallback.pdf", prepared)
        self.assertIn("visible-after.pdf", prepared)
        self.assertIn("zero-before.pdf", prepared)
        self.assertNotIn("zero-during.pdf", prepared)
        self.assertIn("zero-after.pdf", prepared)
        self.assertIn("past-before.pdf", prepared)
        self.assertNotIn("past-during.pdf", prepared)
        self.assertIn("past-after.pdf", prepared)
        self.assertEqual(result.source_expectations["figure_minimum"], 4)
        self.assertEqual(result.source_expectations["formula_minimum"], 0)

    def test_empty_temporal_middle_visual_is_neither_marked_nor_blocking(self) -> None:
        temporary, result = self._prepare(
            r"""\documentclass{ltx-talk}
\title{Empty temporal visual}
\begin{document}
\begin{frame}{Unreachable middle}
\temporal<2-1>
  {\includegraphics[alt={Before}]{before.pdf}}
  {\includesvg{unreachable-middle}}
  {\includegraphics[alt={After}]{after.pdf}}
\end{frame}
\end{document}
"""
        )
        self.addCleanup(temporary.cleanup)

        prepared = (Path(temporary.name) / "prepared" / "main.tex").read_text()
        codes = [item.code for item in result.findings]

        self.assertIn(r"\temporal<2-1>", prepared)
        self.assertIn("before.pdf", prepared)
        self.assertIn("after.pdf", prepared)
        self.assertNotIn("unreachable-middle", prepared)
        self.assertNotIn("allytex: alt=visual", prepared)
        self.assertNotIn("visual-placeholder-alt", codes)
        self.assertNotIn("unsupported-visual-includesvg", codes)
        self.assertEqual(result.source_expectations["figure_minimum"], 1)


if __name__ == "__main__":
    unittest.main()
