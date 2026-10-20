from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from canvas_ally_latex.audit import audit_pdf
from canvas_ally_latex.build import build_project
from canvas_ally_latex.config import ToolConfig
from canvas_ally_latex.project import prepare_project, resolve_project


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "proxitr"
REQUIRED_TOOLS = ("latexmk", "lualatex", "pdfinfo", "pdftotext", "pdffonts", "verapdf")
RUN_E2E = os.environ.get("CANVAS_ALLY_LATEX_E2E") == "1" and all(
    shutil.which(tool) for tool in REQUIRED_TOOLS
)


@unittest.skipUnless(
    RUN_E2E,
    "set CANVAS_ALLY_LATEX_E2E=1 to run the LuaLaTeX/Poppler/veraPDF smoke test",
)
class ProxITRLaTeXIntegrationTests(unittest.TestCase):
    def test_realistic_beamer_fixture_builds_and_passes_pdf_ua_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "proxitr"
            output = root / "proxitr-accessible"
            shutil.copytree(FIXTURE, source)
            original = (source / "main.tex").read_bytes()

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False, beamer_mode="ltx-talk"),
            )
            pdf, _ = build_project(output, result.main_tex)
            audit = audit_pdf(pdf, source_expectations=result.source_expectations)

            self.assertTrue(audit.passed, audit.as_dict())
            expected_formulas = result.source_expectations["formula_minimum"]
            self.assertEqual(expected_formulas, 7)
            self.assertEqual(audit.metrics["formulas"], expected_formulas)
            self.assertEqual(audit.metrics["formulas_with_alt"], expected_formulas)
            self.assertNotIn("\ufffd", _pdf_text(pdf))
            self.assertEqual((source / "main.tex").read_bytes(), original)


@unittest.skipUnless(
    RUN_E2E,
    "set CANVAS_ALLY_LATEX_E2E=1 to run the LuaLaTeX theorem-citation test",
)
class TaggedTheoremCitationIntegrationTests(unittest.TestCase):
    def test_optional_citation_in_theorem_heading_builds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "article"
            output = root / "article-accessible"
            source.mkdir()
            main = source / "main.tex"
            main.write_text(
                r"""\documentclass{article}
\usepackage{amsthm}
\newtheorem{definition}{Definition}
\title{Tagged theorem citation}
\begin{document}
\section{Definitions}
\begin{definition}[{\cite[Definition 2.7]{reference}}]
A protected theorem heading citation.
\end{definition}
\begin{thebibliography}{1}
\bibitem{reference} A. Author, \emph{Reference}.
\end{thebibliography}
\end{document}
""",
                encoding="utf-8",
            )
            original = main.read_bytes()

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False),
            )
            prepared = result.main_tex.read_text(encoding="utf-8")
            pdf, _ = build_project(output, result.main_tex)

            self.assertIn(r"\CanvasAllyTheoremNote", prepared)
            self.assertIn("Definition 2.7", _pdf_text(pdf))
            self.assertEqual(main.read_bytes(), original)


@unittest.skipUnless(
    RUN_E2E,
    "set CANVAS_ALLY_LATEX_E2E=1 to run the LuaLaTeX Beamer-spacing test",
)
class BeamerSpacingIntegrationTests(unittest.TestCase):
    def test_standalone_vspace_linebreak_builds_after_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "slides"
            output = root / "slides-accessible"
            source.mkdir()
            main = source / "main.tex"
            main.write_text(
                r"""\documentclass{beamer}
\title{Vertical spacing}
\begin{document}
\begin{frame}
\frametitle{Spacing after display math}
\begin{equation*}
x = 1
\end{equation*}
\vspace{0.5in}\\
Important note.
\end{frame}
\end{document}
""",
                encoding="utf-8",
            )
            original = main.read_bytes()

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False, beamer_mode="ltx-talk"),
            )
            prepared = result.main_tex.read_text(encoding="utf-8")
            pdf, _ = build_project(output, result.main_tex)

            self.assertIn("\\end{equation*}\n\\vspace{0.5in}\n", prepared)
            self.assertTrue(pdf.is_file())
            self.assertEqual(main.read_bytes(), original)


@unittest.skipUnless(
    RUN_E2E,
    "set CANVAS_ALLY_LATEX_E2E=1 to run the grouped-table tagging test",
)
class GroupedTableIntegrationTests(unittest.TestCase):
    def test_grouped_beamer_table_builds_with_row_and_column_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "slides"
            output = root / "slides-accessible"
            source.mkdir()
            main = source / "main.tex"
            main.write_text(
                r"""\documentclass{beamer}
\title{Grouped table}
\begin{document}
\begin{frame}
\frametitle{Decision outcomes}
\begin{tabular}{ccc}
  & \multicolumn{2}{c}{Truth} \\
  Decision & Null & Alternative \\
  Reject null & Correct & Type I error \\
  Do not reject null & Type II error & Correct \\
\end{tabular}
\end{frame}
\end{document}
""",
                encoding="utf-8",
            )
            original = main.read_bytes()

            result = prepare_project(
                resolve_project(source, output_dir=output),
                ToolConfig(build=False, beamer_mode="ltx-talk"),
            )
            prepared = result.main_tex.read_text(encoding="utf-8")
            pdf, _ = build_project(output, result.main_tex)
            audit = audit_pdf(pdf, source_expectations=result.source_expectations)

            self.assertIn(
                r"\tagpdfsetup{table/header-rows={1,2},table/header-columns={1}}",
                prepared,
            )
            self.assertEqual(result.source_expectations["table_minimum"], 1)
            self.assertTrue(audit.passed, audit.as_dict())
            self.assertEqual(audit.metrics["tables"], 1)
            self.assertEqual(audit.metrics["table_headers"], 7)
            self.assertEqual(audit.metrics["scoped_table_headers"], 7)
            self.assertEqual(main.read_bytes(), original)


def _pdf_text(pdf: Path) -> str:
    import subprocess

    return subprocess.run(
        ["pdftotext", "-enc", "UTF-8", str(pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


if __name__ == "__main__":
    unittest.main()
