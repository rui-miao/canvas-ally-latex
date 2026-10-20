from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from canvas_ally_latex.audit import _table_details, _verapdf_result, audit_pdf


PDFINFO = """Title:           Accessible probability slides
Pages:           3
Encrypted:       no
Tagged:          yes
Metadata Stream: yes
Suspects:        no
PDF version:     2.0
"""

METADATA = """<?xpacket begin='x'?>
<rdf:RDF>
  <dc:language><rdf:Bag><rdf:li>en-US</rdf:li></rdf:Bag></dc:language>
  <pdfuaid:part>2</pdfuaid:part>
  <pdfuaid:rev>2024</pdfuaid:rev>
</rdf:RDF>
"""

FONTS = """name                                 type              encoding         emb sub uni object ID
------------------------------------ ----------------- ---------------- --- --- --- ---------
CMR10                                Type 1            Custom           yes yes yes      4  0
"""

STRUCTURE = """H1
  Formula [\"x squared\"]
  Figure [\"Bar chart comparing study arms\"]
  Table
    TR
      TH /Scope /Column
"""

VERAPDF_UA2_PASS = """<?xml version="1.0" encoding="utf-8"?>
<report>
  <jobs><job>
    <validationReport jobEndStatus="normal"
      profileName="PDF/UA-2 validation profile" isCompliant="true">
      <details passedRules="1727" failedRules="0"
        passedChecks="3230" failedChecks="0" />
    </validationReport>
  </job></jobs>
</report>
"""

VERAPDF_PDFA_PASS = """<?xml version="1.0" encoding="utf-8"?>
<report>
  <jobs><job>
    <validationReport jobEndStatus="normal"
      profileName="PDF/A-1b validation profile" isCompliant="true">
      <details passedRules="100" failedRules="0"
        passedChecks="200" failedChecks="0" />
    </validationReport>
  </job></jobs>
</report>
"""


def completed(command: list[str], stdout: str, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=stderr)


class PdfAuditTests(unittest.TestCase):
    def test_table_details_accepts_position_implied_grouped_header_scopes(self) -> None:
        structure = """Table
  TR
    TH
    TH
  TR
    TH
    TH:
      /Scope /Column
    TH:
      /Scope /Column
  TR
    TH:
      /Scope /Row
    TD
    TD
  TR
    TH:
      /Scope /Row
    TD
    TD
"""

        self.assertEqual(_table_details(structure), [(7, 7)])

    def test_table_details_accepts_scope_omission_in_the_first_row(self) -> None:
        structure = """Table
  TR
    TH
    TH
  TR
    TD
    TD
"""

        self.assertEqual(_table_details(structure), [(2, 2)])

    def test_table_details_keeps_an_interior_unscoped_header_failing(self) -> None:
        structure = """Table
  TR
    TH
    TH
  TR
    TH
    TH
    TD
"""

        self.assertEqual(_table_details(structure), [(4, 3)])

    def test_table_details_counts_explicit_both_scope(self) -> None:
        structure = "Table\n  TR\n    TD\n    TH /Scope /Both\n"
        self.assertEqual(_table_details(structure), [(1, 1)])

    def test_accessible_pdf_signals_pass_without_claiming_an_ally_score(self) -> None:
        def fake_which(name: str) -> str | None:
            return None if name == "verapdf" else f"/usr/bin/{name}"

        def fake_run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
            if command[0] == "pdfinfo" and "-meta" in command:
                return completed(command, METADATA)
            if command[0] == "pdfinfo" and "-struct" in command:
                return completed(command, STRUCTURE)
            if command[0] == "pdfinfo":
                return completed(command, PDFINFO)
            if command[0] == "pdftotext":
                return completed(command, "Accessible extracted content")
            if command[0] == "pdffonts":
                return completed(command, FONTS)
            raise AssertionError(f"Unexpected command: {command}")

        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "slides.pdf"
            pdf.write_bytes(b"%PDF-2.0\n")
            with (
                patch("canvas_ally_latex.audit.shutil.which", side_effect=fake_which),
                patch("canvas_ally_latex.audit._run", side_effect=fake_run),
            ):
                result = audit_pdf(pdf)

        self.assertTrue(result.passed)
        self.assertEqual(result.metrics["language"], "en-US")
        self.assertEqual(result.metrics["formulas_with_alt"], 1)
        self.assertEqual(result.metrics["figures_with_alt"], 1)
        self.assertEqual(result.metrics["table_headers"], 1)
        self.assertTrue(any("veraPDF" in note for note in result.notes))
        payload = result.as_dict()
        self.assertNotIn("score", payload)
        self.assertIn("Canvas/Ally", payload["disclaimer"])

    def test_source_math_requires_at_least_one_formula_tag(self) -> None:
        def fake_which(name: str) -> str | None:
            return None if name == "verapdf" else f"/usr/bin/{name}"

        def fake_run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
            if command[0] == "pdfinfo" and "-meta" in command:
                return completed(command, METADATA)
            if command[0] == "pdfinfo" and "-struct" in command:
                # This looks superficially well tagged, but deliberately has
                # no Formula node despite source-side math expectations.
                return completed(command, "H1\n  P\n")
            if command[0] == "pdfinfo":
                return completed(command, PDFINFO)
            if command[0] == "pdftotext":
                return completed(command, "The source contained x squared.")
            if command[0] == "pdffonts":
                return completed(command, FONTS)
            raise AssertionError(f"Unexpected command: {command}")

        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "missing-formula-tag.pdf"
            pdf.write_bytes(b"%PDF-2.0\n")
            with (
                patch("canvas_ally_latex.audit.shutil.which", side_effect=fake_which),
                patch("canvas_ally_latex.audit._run", side_effect=fake_run),
            ):
                result = audit_pdf(
                    pdf,
                    source_expectations={"formulas": True, "formula_minimum": 1},
                )

        formula_check = next(
            check for check in result.checks if check.name == "formula-alternatives"
        )
        self.assertFalse(formula_check.passed)
        self.assertFalse(result.passed)
        self.assertIn("at least 1 math expression", formula_check.detail)

    def test_verapdf_is_pinned_to_and_parses_pdf_ua_2(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            if command[0] == "pdfinfo" and "-meta" in command:
                return completed(command, METADATA)
            if command[0] == "pdfinfo" and "-struct" in command:
                return completed(command, STRUCTURE)
            if command[0] == "pdfinfo":
                return completed(command, PDFINFO)
            if command[0] == "pdftotext":
                return completed(command, "Accessible extracted content")
            if command[0] == "pdffonts":
                return completed(command, FONTS)
            if command[0] == "verapdf":
                return completed(command, VERAPDF_UA2_PASS)
            raise AssertionError(f"Unexpected command: {command}")

        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "slides.pdf"
            pdf.write_bytes(b"%PDF-2.0\n")
            with (
                patch("canvas_ally_latex.audit.shutil.which", return_value="/usr/bin/tool"),
                patch("canvas_ally_latex.audit._run", side_effect=fake_run),
            ):
                result = audit_pdf(pdf)

        vera_command = next(command for command in commands if command[0] == "verapdf")
        self.assertEqual(vera_command[1:5], ["--format", "xml", "--flavour", "ua2"])
        vera_check = next(check for check in result.checks if check.name == "verapdf")
        self.assertTrue(vera_check.passed)
        self.assertEqual(result.metrics["verapdf_failed_rules"], 0)
        self.assertEqual(result.metrics["verapdf_failed_checks"], 0)
        self.assertIn("PDF/UA-2", vera_check.detail)

    def test_verapdf_rejects_a_compliant_non_ua_profile(self) -> None:
        def fake_run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
            if command[0] == "pdfinfo" and "-meta" in command:
                return completed(command, METADATA)
            if command[0] == "pdfinfo" and "-struct" in command:
                return completed(command, STRUCTURE)
            if command[0] == "pdfinfo":
                return completed(command, PDFINFO)
            if command[0] == "pdftotext":
                return completed(command, "Accessible extracted content")
            if command[0] == "pdffonts":
                return completed(command, FONTS)
            if command[0] == "verapdf":
                return completed(command, VERAPDF_PDFA_PASS)
            raise AssertionError(f"Unexpected command: {command}")

        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "slides.pdf"
            pdf.write_bytes(b"%PDF-2.0\n")
            with (
                patch("canvas_ally_latex.audit.shutil.which", return_value="/usr/bin/tool"),
                patch("canvas_ally_latex.audit._run", side_effect=fake_run),
            ):
                result = audit_pdf(pdf)

        vera_check = next(check for check in result.checks if check.name == "verapdf")
        self.assertFalse(vera_check.passed)
        self.assertFalse(result.passed)
        self.assertIn("did not return a PDF/UA-2", vera_check.detail)

    def test_verapdf_fails_closed_on_incomplete_ua_report(self) -> None:
        incomplete = """<?xml version="1.0"?>
<report><validationReport
  profileName="PDF/UA-2 validation profile"
  isCompliant="true" /></report>
"""

        passed, detail, metrics = _verapdf_result(incomplete, 0)

        self.assertFalse(passed)
        self.assertFalse(metrics["verapdf_complete_evidence"])
        self.assertIn("incomplete validation evidence", detail)

    def test_verapdf_runs_even_when_poppler_structure_is_missing(self) -> None:
        def fake_run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
            if command[0] == "pdfinfo" and "-meta" in command:
                return completed(command, METADATA)
            if command[0] == "pdfinfo" and "-struct" in command:
                return completed(command, "")
            if command[0] == "pdfinfo":
                return completed(command, PDFINFO)
            if command[0] == "pdftotext":
                return completed(command, "Accessible extracted content")
            if command[0] == "pdffonts":
                return completed(command, FONTS)
            if command[0] == "verapdf":
                return completed(command, VERAPDF_UA2_PASS)
            raise AssertionError(f"Unexpected command: {command}")

        with tempfile.TemporaryDirectory() as temporary:
            pdf = Path(temporary) / "missing-structure.pdf"
            pdf.write_bytes(b"%PDF-2.0\n")
            with (
                patch("canvas_ally_latex.audit.shutil.which", return_value="/usr/bin/tool"),
                patch("canvas_ally_latex.audit._run", side_effect=fake_run),
            ):
                result = audit_pdf(pdf)

        structure_check = next(check for check in result.checks if check.name == "structure-tree")
        vera_check = next(check for check in result.checks if check.name == "verapdf")
        self.assertFalse(structure_check.passed)
        self.assertTrue(vera_check.passed)
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
