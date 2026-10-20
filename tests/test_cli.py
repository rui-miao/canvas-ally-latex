from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from canvas_ally_latex.cli import main
from canvas_ally_latex.model import AuditResult, Check, Finding, PrepareResult


class ReportSafetyTests(unittest.TestCase):
    def test_report_cannot_replace_checked_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "lecture.pdf"
            original = b"%PDF-2.0\n"
            pdf.write_bytes(original)

            status = main(["--check", str(pdf), "--report-json", str(pdf)])

            self.assertEqual(status, 2)
            self.assertEqual(pdf.read_bytes(), original)

    def test_report_cannot_be_written_into_source_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "course"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\\begin{document}Text\\end{document}\n",
                encoding="utf-8",
            )
            report = source / "report.json"

            status = main(
                [
                    str(source),
                    "--dry-run",
                    "--report-json",
                    str(report),
                ]
            )

            self.assertEqual(status, 2)
            self.assertFalse(report.exists())

    def test_forced_report_breaks_hard_link_without_touching_checked_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "lecture.pdf"
            report = root / "report.json"
            original = b"%PDF-2.0\n"
            pdf.write_bytes(original)
            report.hardlink_to(pdf)

            status = main(
                [
                    "--check",
                    str(pdf),
                    "--report-json",
                    str(report),
                    "--force",
                ]
            )

            self.assertEqual(status, 2)
            self.assertEqual(pdf.read_bytes(), original)

    def test_dry_run_report_cannot_alias_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "course"
            output_and_report = root / "prepared.json"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\\begin{document}Text\\end{document}\n",
                encoding="utf-8",
            )

            status = main(
                [
                    str(source),
                    "--dry-run",
                    "--output-dir",
                    str(output_and_report),
                    "--report-json",
                    str(output_and_report),
                ]
            )

            self.assertEqual(status, 2)
            self.assertFalse(output_and_report.exists())

    def test_normal_report_cannot_alias_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "course"
            output_and_report = root / "prepared.json"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\\begin{document}Text\\end{document}\n",
                encoding="utf-8",
            )

            status = main(
                [
                    str(source),
                    "--no-build",
                    "--output-dir",
                    str(output_and_report),
                    "--report-json",
                    str(output_and_report),
                ]
            )

            self.assertEqual(status, 2)
            self.assertFalse(output_and_report.exists())


class ForceBuildTests(unittest.TestCase):
    def _prepared_result(self, source: Path, output: Path) -> PrepareResult:
        return PrepareResult(
            source=source.resolve(),
            output=output.resolve(),
            main_tex=(output / "main.tex").resolve(),
            profile="article",
            findings=[
                Finding(
                    "unresolved-test-visual",
                    "A visual still requires manual accessibility work.",
                    "error",
                    "main.tex",
                    3,
                )
            ],
        )

    def test_source_error_skips_build_without_force_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\\begin{document}Text\\end{document}\n",
                encoding="utf-8",
            )
            prepared = self._prepared_result(source, output)

            with (
                patch("canvas_ally_latex.cli.prepare_project", return_value=prepared),
                patch("canvas_ally_latex.cli.build_project") as build,
                patch("canvas_ally_latex.cli.audit_pdf") as audit,
            ):
                status = main([str(source), "-o", str(output)])

            self.assertEqual(status, 1)
            build.assert_not_called()
            audit.assert_not_called()

    def test_force_build_produces_and_audits_pdf_but_keeps_failure_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            report = root / "report.json"
            pdf = output / "main.pdf"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\\begin{document}Text\\end{document}\n",
                encoding="utf-8",
            )
            prepared = self._prepared_result(source, output)
            audit_result = AuditResult(
                pdf=pdf,
                checks=[Check("verapdf", True, "PDF/UA-2 passed.")],
            )

            with (
                patch("canvas_ally_latex.cli.prepare_project", return_value=prepared),
                patch(
                    "canvas_ally_latex.cli.build_project",
                    return_value=(pdf, output / "canvas-ally-build.log"),
                ) as build,
                patch("canvas_ally_latex.cli.audit_pdf", return_value=audit_result) as audit,
            ):
                status = main(
                    [
                        str(source),
                        "-o",
                        str(output),
                        "--force-build",
                        "--report-json",
                        str(report),
                    ]
                )

            self.assertEqual(status, 1)
            build.assert_called_once()
            audit.assert_called_once()
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["pdf"], str(pdf))
            self.assertIsNotNone(payload["audit"])
            self.assertEqual(payload["overall_ally_readiness"], "fail")

    def test_no_build_remains_authoritative_with_force_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            (source / "main.tex").write_text(
                "\\documentclass{article}\\begin{document}Text\\end{document}\n",
                encoding="utf-8",
            )
            prepared = self._prepared_result(source, output)

            with (
                patch("canvas_ally_latex.cli.prepare_project", return_value=prepared),
                patch("canvas_ally_latex.cli.build_project") as build,
                patch("canvas_ally_latex.cli.audit_pdf") as audit,
            ):
                status = main(
                    [str(source), "-o", str(output), "--no-build", "--force-build"]
                )

            self.assertEqual(status, 1)
            build.assert_not_called()
            audit.assert_not_called()


class DryRunPreviewTests(unittest.TestCase):
    def test_dry_run_reports_transform_findings_without_leaving_a_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "slides"
            output = root / "preview-output"
            report = root / "preview.json"
            source.mkdir()
            (source / "main.tex").write_text(
                r"""\documentclass{beamer}
\title{Dry-run findings}
\begin{document}
\begin{frame}{Unsupported visual}
\includesvg{diagram.svg}
\end{frame}
\end{document}
""",
                encoding="utf-8",
            )

            status = main(
                [
                    str(source),
                    "--dry-run",
                    "--output-dir",
                    str(output),
                    "--report-json",
                    str(report),
                ]
            )

            self.assertEqual(status, 1)
            self.assertFalse(output.exists())
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["output"], str(output.resolve()))
            self.assertTrue(
                any(
                    finding["code"] == "unsupported-visual-includesvg"
                    for finding in payload["changes_and_warnings"]
                )
            )

if __name__ == "__main__":
    unittest.main()
