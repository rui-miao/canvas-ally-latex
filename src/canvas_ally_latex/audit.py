"""Local PDF checks aligned with common Canvas/Ally findings."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from .model import AuditResult, Check


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=timeout,
    )


def _field(info: str, name: str) -> str:
    match = re.search(r"^" + re.escape(name) + r":\s*(.*)$", info, re.MULTILINE | re.I)
    return match.group(1).strip() if match else ""


def _boolean_field(info: str, name: str) -> bool:
    return _field(info, name).lower() == "yes"


def _font_rows(output: str) -> list[tuple[bool, bool, bool, str]]:
    rows: list[tuple[bool, bool, bool, str]] = []
    for line in output.splitlines():
        match = re.search(
            r"\s+(yes|no)\s+(yes|no)\s+(yes|no)\s+\d+\s+\d+\s*$",
            line,
            re.I,
        )
        if match:
            embedded, subset, unicode_map = (value.lower() == "yes" for value in match.groups())
            rows.append((embedded, subset, unicode_map, line.strip()))
    return rows


def _alt_counts(structure: str, role: str) -> tuple[int, int]:
    lines = [line for line in structure.splitlines() if re.match(r"^\s*" + role + r"\b", line)]
    with_alt = sum(bool(re.search(r"\[\"[^\"\]]", line)) for line in lines)
    return len(lines), with_alt


def _heading_check(
    structure: str,
    pages: int,
    *,
    expected: bool = False,
) -> tuple[bool, str, list[int]]:
    levels = [int(value) for value in re.findall(r"^\s*H([1-6])\b", structure, re.MULTILINE)]
    if not levels:
        if expected:
            return False, "Reachable source has headings, but no H1-H6 tags were found.", levels
        if pages >= 3:
            return False, "No H1-H6 headings found in a document of three or more pages.", levels
        return True, "No headings found; headings are not required by this proxy for fewer than three pages.", levels
    if levels[0] != 1:
        return False, f"The first heading is H{levels[0]}, not H1.", levels
    for previous, current in zip(levels, levels[1:]):
        if current > previous + 1:
            return False, f"Heading sequence skips from H{previous} to H{current}.", levels
    return True, f"Found {len(levels)} headings beginning with H1 and no upward level skips.", levels


def _table_details(structure: str) -> list[tuple[int, int]]:
    """Return (TH count, valid-scope count) for each direct Table subtree."""

    lines = structure.splitlines()
    details: list[tuple[int, int]] = []
    scope_pattern = re.compile(r"/Scope\s+/(Column|Row|Both)")
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)Table\b", line)
        if not match:
            continue
        indentation = len(match.group(1))
        subtree: list[str] = []
        for child in lines[index + 1 :]:
            if child.strip() and len(child) - len(child.lstrip()) <= indentation:
                break
            subtree.append(child)
        row_positions = [
            (position, len(row.group(1)))
            for position, child in enumerate(subtree)
            if (row := re.match(r"^(\s*)TR\b", child))
        ]
        if not row_positions:
            details.append((0, 0))
            continue

        direct_row_indent = min(indent for _, indent in row_positions)
        direct_rows = [
            position
            for position, indent in row_positions
            if indent == direct_row_indent
        ]
        headers = 0
        scopes = 0
        for row_number, row_start in enumerate(direct_rows):
            row_end = (
                direct_rows[row_number + 1]
                if row_number + 1 < len(direct_rows)
                else len(subtree)
            )
            candidates = [
                (position, len(cell.group(1)), cell.group(2))
                for position in range(row_start + 1, row_end)
                if (
                    cell := re.match(
                        r"^(\s*)(TH|TD)\b",
                        subtree[position],
                    )
                )
            ]
            if not candidates:
                continue
            direct_cell_indent = min(indent for _, indent, _ in candidates)
            direct_cells = [
                (position, role)
                for position, indent, role in candidates
                if indent == direct_cell_indent
            ]
            for column_number, (cell_start, role) in enumerate(direct_cells):
                if role != "TH":
                    continue
                headers += 1
                attribute_lines = [subtree[cell_start]]
                cell_end = (
                    direct_cells[column_number + 1][0]
                    if column_number + 1 < len(direct_cells)
                    else row_end
                )
                for child in subtree[cell_start + 1 : cell_end]:
                    if child.lstrip().startswith("/"):
                        attribute_lines.append(child)
                    elif child.strip():
                        break
                explicit_scope = scope_pattern.search("\n".join(attribute_lines))

                # LaTeX's table tagging stores some scopes in attribute-class
                # arrays that Poppler does not display.  Scope is also optional
                # for TH cells in the first row or first column, where the
                # association is positionally unambiguous.
                if explicit_scope or row_number == 0 or column_number == 0:
                    scopes += 1
        details.append((headers, scopes))
    return details


def _list_details(structure: str) -> list[tuple[int, int, int]]:
    """Return (LI, Lbl, LBody counts) for each top-level or nested L subtree."""

    lines = structure.splitlines()
    details: list[tuple[int, int, int]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)L\s+<", line)
        if not match:
            continue
        indentation = len(match.group(1))
        subtree: list[str] = []
        for child in lines[index + 1 :]:
            if child.strip() and len(child) - len(child.lstrip()) <= indentation:
                break
            subtree.append(child)
        li_indents = [
            len(child) - len(child.lstrip())
            for child in subtree
            if re.match(r"^\s*LI\s+<", child)
        ]
        direct_indent = min(li_indents) if li_indents else indentation + 2
        items = sum(
            bool(re.match(r"^\s*LI\s+<", child))
            and len(child) - len(child.lstrip()) == direct_indent
            for child in subtree
        )
        labels = sum(
            bool(re.match(r"^\s*Lbl\s+<", child))
            and len(child) - len(child.lstrip()) == direct_indent + 2
            for child in subtree
        )
        bodies = sum(
            bool(re.match(r"^\s*LBody\s+<", child))
            and len(child) - len(child.lstrip()) == direct_indent + 2
            for child in subtree
        )
        details.append((items, labels, bodies))
    return details


def _verapdf_result(
    xml_output: str,
    returncode: int,
) -> tuple[bool, str, dict[str, object]]:
    """Read veraPDF XML and require the requested PDF/UA-2 report to pass."""

    metrics: dict[str, object] = {"verapdf_returncode": returncode}
    try:
        root = ET.fromstring(xml_output)
    except ET.ParseError as error:
        return (
            False,
            f"veraPDF returned an unreadable XML report: {error}.",
            metrics,
        )

    reports = [
        element
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "validationReport"
    ]
    profiles = [report.get("profileName", "unnamed profile") for report in reports]
    metrics["verapdf_profiles"] = profiles

    ua2_reports = [
        report
        for report in reports
        if "pdf/ua-2" in report.get("profileName", "").casefold()
    ]
    if not ua2_reports:
        found = ", ".join(profiles) if profiles else "no validation profiles"
        return (
            False,
            f"veraPDF did not return a PDF/UA-2 validation report (found {found}).",
            metrics,
        )

    failed_rules = 0
    failed_checks = 0
    complete_evidence = True
    for report in ua2_reports:
        details = next(
            (
                child
                for child in report
                if child.tag.rsplit("}", 1)[-1] == "details"
            ),
            None,
        )
        if details is None:
            complete_evidence = False
            continue
        raw_failed_rules = details.get("failedRules")
        raw_failed_checks = details.get("failedChecks")
        try:
            if raw_failed_rules is None or raw_failed_checks is None:
                raise ValueError
            failed_rules += int(raw_failed_rules)
            failed_checks += int(raw_failed_checks)
        except ValueError:
            complete_evidence = False

    metrics["verapdf_ua2_reports"] = len(ua2_reports)
    metrics["verapdf_failed_rules"] = failed_rules
    metrics["verapdf_failed_checks"] = failed_checks
    metrics["verapdf_complete_evidence"] = complete_evidence
    compliant = all(
        report.get("jobEndStatus", "").casefold() == "normal"
        and report.get("isCompliant", "false").casefold() == "true"
        for report in ua2_reports
    )
    passed = (
        returncode == 0
        and compliant
        and complete_evidence
        and failed_rules == 0
        and failed_checks == 0
    )
    profile_names = ", ".join(
        report.get("profileName", "PDF/UA-2") for report in ua2_reports
    )
    if passed:
        detail = (
            f"veraPDF reports PDF/UA-2 conformance ({profile_names}); "
            "0 failed rules and 0 failed checks."
        )
    else:
        evidence_detail = (
            "complete failure counts"
            if complete_evidence
            else "incomplete validation evidence"
        )
        detail = (
            f"veraPDF did not report PDF/UA-2 conformance ({profile_names}); "
            f"{failed_rules} failed rule(s), {failed_checks} failed check(s), "
            f"{evidence_detail}, exit status {returncode}."
        )
    return passed, detail, metrics


def _append_verapdf_check(result: AuditResult, pdf: Path) -> None:
    """Append formal PDF/UA-2 evidence when veraPDF is installed."""

    if shutil.which("verapdf"):
        vera = _run(
            ["verapdf", "--format", "xml", "--flavour", "ua2", str(pdf)],
            timeout=300,
        )
        passed, detail, vera_metrics = _verapdf_result(vera.stdout, vera.returncode)
        result.metrics.update(vera_metrics)
        result.checks.append(Check("verapdf", passed, detail))
    else:
        result.notes.append(
            "veraPDF was not installed, so formal PDF/UA validation was not run."
        )


def audit_pdf(
    pdf: Path,
    *,
    source_expectations: dict[str, bool | int] | None = None,
) -> AuditResult:
    """Audit one PDF without claiming to reproduce Ally's proprietary score."""

    pdf = pdf.resolve()
    source_expectations = source_expectations or {}
    if not pdf.is_file():
        raise ValueError(f"PDF does not exist: {pdf}")
    required = ("pdfinfo", "pdftotext", "pdffonts")
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        raise RuntimeError(
            "PDF audit requires Poppler utilities; missing: " + ", ".join(missing)
        )

    info_process = _run(["pdfinfo", str(pdf)])
    if info_process.returncode != 0:
        raise RuntimeError(f"pdfinfo could not read {pdf}: {info_process.stderr.strip()}")
    info = info_process.stdout
    result = AuditResult(pdf=pdf)

    pages_text = _field(info, "Pages")
    pages = int(pages_text) if pages_text.isdigit() else 0
    title = _field(info, "Title")
    version = _field(info, "PDF version")
    encrypted = _field(info, "Encrypted").lower()
    suspects = _field(info, "Suspects").lower()
    result.metrics.update({"pages": pages, "title": title, "pdf_version": version})

    result.checks.extend(
        [
            Check("valid-pdf", True, "Poppler parsed the PDF successfully."),
            Check("not-encrypted", encrypted == "no", encrypted or "unknown"),
            Check("tagged", _boolean_field(info, "Tagged"), _field(info, "Tagged") or "unknown"),
            Check(
                "metadata-stream",
                _boolean_field(info, "Metadata Stream"),
                _field(info, "Metadata Stream") or "unknown",
            ),
            Check("no-suspects", suspects == "no", suspects or "unknown"),
            Check(
                "pdf-2",
                version.startswith("2."),
                f"PDF version {version or 'unknown'}; PDF 2.0 is preferred for the generated UA-2 profile.",
                "minor",
            ),
            Check("document-title", bool(title), title or "No PDF title metadata found."),
        ]
    )

    meta_process = _run(["pdfinfo", "-meta", str(pdf)])
    metadata = meta_process.stdout if meta_process.returncode == 0 else ""
    language_match = re.search(
        r"<dc:language>.*?<rdf:li[^>]*>([^<]+)</rdf:li>",
        metadata,
        re.DOTALL | re.I,
    )
    language = language_match.group(1).strip() if language_match else ""
    ua2 = bool(
        re.search(r"<pdfuaid:part>\s*2\s*</pdfuaid:part>", metadata, re.I)
        and re.search(r"<pdfuaid:rev>\s*2024\s*</pdfuaid:rev>", metadata, re.I)
    )
    result.metrics["language"] = language
    result.checks.extend(
        [
            Check("document-language", bool(language), language or "No dc:language value found."),
            Check(
                "pdf-ua-2-claim",
                ua2,
                "XMP declares PDF/UA-2:2024; this is an identification claim, not formal validation."
                if ua2
                else "PDF/UA-2:2024 XMP identification not found; this alone is not an Ally failure.",
                "minor",
            ),
        ]
    )

    text_process = _run(["pdftotext", "-enc", "UTF-8", str(pdf), "-"])
    extracted = text_process.stdout.strip() if text_process.returncode == 0 else ""
    result.metrics["extracted_characters"] = len(extracted)
    result.checks.append(
        Check(
            "extractable-text",
            bool(extracted),
            f"Extracted {len(extracted)} non-whitespace characters." if extracted else "No text could be extracted.",
        )
    )

    fonts_process = _run(["pdffonts", str(pdf)])
    font_rows = _font_rows(fonts_process.stdout) if fonts_process.returncode == 0 else []
    unembedded = [line for embedded, _, _, line in font_rows if not embedded]
    no_unicode = [line for _, _, unicode_map, line in font_rows if not unicode_map]
    result.metrics.update(
        {
            "fonts": len(font_rows),
            "unembedded_fonts": len(unembedded),
            "fonts_without_unicode_map": len(no_unicode),
        }
    )
    result.checks.extend(
        [
            Check(
                "embedded-fonts",
                bool(font_rows) and not unembedded,
                f"All {len(font_rows)} fonts are embedded." if font_rows and not unembedded else f"Found {len(unembedded)} unembedded fonts.",
            ),
            Check(
                "unicode-font-maps",
                bool(font_rows) and not no_unicode,
                f"All {len(font_rows)} fonts have Unicode maps." if font_rows and not no_unicode else f"Found {len(no_unicode)} fonts without Unicode maps.",
                "minor",
            ),
        ]
    )

    structure_process = _run(["pdfinfo", "-struct", str(pdf)])
    structure = structure_process.stdout
    if not structure.strip():
        result.checks.append(Check("structure-tree", False, "Poppler returned no logical structure tree."))
        _append_verapdf_check(result, pdf)
        return result

    formula_total, formula_alt = _alt_counts(structure, "Formula")
    figure_total, figure_alt = _alt_counts(structure, "Figure")
    heading_passed, heading_detail, levels = _heading_check(
        structure,
        pages,
        expected=source_expectations.get("headings", False),
    )
    table_details = _table_details(structure)
    table_total = len(table_details)
    header_total = sum(headers for headers, _ in table_details)
    scoped_headers = sum(scopes for _, scopes in table_details)
    expected_formulas = int(source_expectations.get("formula_minimum", 0))
    expected_figures = int(source_expectations.get("figure_minimum", 0))
    expected_tables = int(source_expectations.get("table_minimum", 0))
    expected_lists = int(source_expectations.get("list_minimum", 0))
    tables_pass = (
        table_total >= expected_tables
        and all(headers > 0 and scopes >= headers for headers, scopes in table_details)
    )
    formulas_pass = formula_total == formula_alt and formula_total >= expected_formulas
    figures_pass = figure_total == figure_alt and figure_total >= expected_figures
    list_details = _list_details(structure)
    list_total = len(list_details)
    list_items = sum(items for items, _, _ in list_details)
    list_labels = sum(labels for _, labels, _ in list_details)
    list_bodies = sum(bodies for _, _, bodies in list_details)
    lists_pass = (
        list_total >= expected_lists
        and all(items > 0 and items == labels == bodies for items, labels, bodies in list_details)
    )
    result.metrics.update(
        {
            "formulas": formula_total,
            "formulas_with_alt": formula_alt,
            "figures": figure_total,
            "figures_with_alt": figure_alt,
            "heading_levels": levels,
            "tables": table_total,
            "table_headers": header_total,
            "scoped_table_headers": scoped_headers,
            "lists": list_total,
            "list_items": list_items,
            "list_labels": list_labels,
            "list_bodies": list_bodies,
            "source_expectations": source_expectations,
        }
    )
    result.checks.extend(
        [
            Check("structure-tree", True, "Poppler returned a logical structure tree."),
            Check(
                "formula-alternatives",
                formulas_pass,
                f"{formula_alt} of {formula_total} Formula tags have alternative text"
                + (f"; reachable source has at least {expected_formulas} math expression(s)." if expected_formulas else "."),
            ),
            Check(
                "figure-alternatives",
                figures_pass,
                f"{figure_alt} of {figure_total} Figure tags have alternative text"
                + (f"; reachable source has at least {expected_figures} informative graphic(s)." if expected_figures else "."),
            ),
            Check("heading-order", heading_passed, heading_detail),
            Check(
                "table-headers",
                tables_pass,
                (
                    f"Found {table_total} tables (source minimum {expected_tables}), "
                    f"{header_total} TH elements, and {scoped_headers} headers "
                    "with explicit or position-implied scope."
                ),
            ),
            Check(
                "list-structure",
                lists_pass,
                f"Found {list_total} lists (source minimum {expected_lists}) with {list_items} LI, "
                f"{list_labels} Lbl, and {list_bodies} LBody tags.",
            ),
        ]
    )

    mathml_errors = len(re.findall(r"wrong type \(math\)", structure_process.stderr, re.I))
    result.metrics["mathml_roots_reported_by_poppler"] = mathml_errors
    if formula_total and not mathml_errors:
        result.notes.append(
            "Poppler did not expose MathML roots. Formula alternatives are present, but run veraPDF for full PDF/UA validation."
        )
    elif mathml_errors:
        result.notes.append(
            "Poppler reported lower-case MathML structure roles as unknown; this is expected for current PDF/UA-2 MathML output."
        )

    _append_verapdf_check(result, pdf)
    return result
