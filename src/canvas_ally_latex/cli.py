"""Command-line interface for canvas-ally-latex."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import __version__
from .audit import audit_pdf
from .build import BuildError, build_project
from .config import resolve_config
from .model import AuditResult, Finding, PrepareResult
from .project import prepare_project, resolve_project
from .transform import UnsupportedDocumentError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canvas-ally-latex",
        description=(
            "Copy, prepare, build, and locally audit LaTeX PDFs for Canvas/Ally. "
            "The source project is never edited."
        ),
    )
    parser.add_argument("input", nargs="?", type=Path, help="LaTeX project directory or main .tex file")
    parser.add_argument("--main", type=Path, help="main .tex file relative to a project directory")
    parser.add_argument("-o", "--output-dir", type=Path, help="destination for the prepared project copy")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace the resolved output directory and an existing JSON report",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview transformations and findings without retaining a copy or building",
    )
    parser.add_argument("--check", type=Path, metavar="PDF", help="audit an existing PDF without preparing sources")
    parser.add_argument("--report-json", type=Path, metavar="FILE", help="write a machine-readable report")
    parser.add_argument("--config", type=Path, metavar="FILE", help="TOML configuration file")
    parser.add_argument("--alt-map", type=Path, metavar="FILE", help="JSON map of image paths to alternatives")
    parser.add_argument("--language", metavar="TAG", help="BCP 47 document language, such as en-US")
    parser.add_argument("--title", help="plain-text PDF title override")
    parser.add_argument(
        "--profile",
        choices=("auto", "article", "beamer", "ltx-talk"),
        help="source profile (default: auto)",
    )
    parser.add_argument(
        "--beamer-mode",
        choices=("ltx-talk", "reject"),
        help="migrate Beamer in the copy or reject it",
    )
    parser.add_argument(
        "--table-headers",
        choices=("first-row", "manual"),
        help="automatic table-header policy",
    )
    parser.add_argument("--no-build", action="store_true", help="prepare the copy but do not compile")
    parser.add_argument(
        "--force-build",
        action="store_true",
        help=(
            "compile and audit despite source ERROR findings; errors remain "
            "blocking in the report and exit status"
        ),
    )
    parser.add_argument("--keep-build-files", action="store_true", help="retain LaTeX auxiliary/cache files")
    parser.add_argument("--strict", action="store_true", help="fail on unresolved source accessibility findings")
    parser.add_argument("-v", "--verbose", action="store_true", help="show informational transformations and metrics")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _report_target(path: Path) -> Path:
    lexical = Path(os.path.abspath(path.expanduser()))
    return lexical.parent.resolve() / lexical.name


def _validate_report_target(
    path: Path | None,
    *,
    protected_files: tuple[Path, ...] = (),
    protected_roots: tuple[Path, ...] = (),
    force: bool = False,
) -> None:
    if path is None:
        return
    target = _report_target(path)
    if target.suffix.lower() != ".json":
        raise ValueError("--report-json must name a file ending in .json")
    effective = target.resolve(strict=False)
    if any(effective == item.resolve(strict=False) for item in protected_files):
        raise ValueError(f"JSON report would overwrite a protected input/output file: {target}")
    if target.exists() and any(
        item.exists() and target.samefile(item) for item in protected_files
    ):
        raise ValueError(f"JSON report is a hard link to a protected input/output file: {target}")
    if any(effective.is_relative_to(root.resolve()) for root in protected_roots):
        raise ValueError(f"JSON report cannot be written inside the source project: {target}")
    if (target.exists() or target.is_symlink()) and not force:
        raise FileExistsError(f"JSON report already exists: {target}. Pass --force to replace it.")


def _write_json(
    path: Path | None,
    payload: dict[str, Any],
    *,
    force: bool = False,
) -> None:
    if path is None:
        return
    target = _report_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    exists = target.exists() or target.is_symlink()
    if exists and not force:
        raise FileExistsError(f"JSON report already exists: {target}. Pass --force to replace it.")
    if target.is_dir():
        raise ValueError(f"JSON report path is a directory: {target}")
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if exists and force:
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=".canvas-ally-report-",
                suffix=".json",
                dir=target.parent,
                delete=False,
            ) as stream:
                temporary_name = stream.name
                stream.write(rendered)
            os.replace(temporary_name, target)
            temporary_name = None
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
    else:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    print(f"JSON report: {target}")


def _print_audit(audit: AuditResult, *, verbose: bool) -> None:
    state = "PASS" if audit.passed else "NEEDS REVIEW"
    print(f"Ally readiness: {state}")
    for check in audit.checks:
        marker = "PASS" if check.passed else ("WARN" if check.severity == "minor" else "FAIL")
        print(f"  [{marker}] {check.name}: {check.detail}")
    if verbose and audit.metrics:
        print("Metrics:")
        for name, value in audit.metrics.items():
            print(f"  {name}: {value}")
    for note in audit.notes:
        print(f"  NOTE: {note}")
    print("Actual Canvas/Ally scores are assigned only after upload.")


def _print_preparation(
    result: PrepareResult,
    *,
    verbose: bool,
    dry_run: bool = False,
) -> None:
    label = "Would prepare copy" if dry_run else "Prepared copy"
    print(f"{label}: {result.output}")
    print(f"Main file: {result.main_tex}")
    print(f"Output profile: {result.profile}")
    for finding in result.findings:
        if finding.severity == "info" and not verbose:
            continue
        location = ""
        if finding.file:
            location = finding.file + (f":{finding.line}" if finding.line else "") + ": "
        print(f"  {finding.severity.upper()}: {location}{finding.message}")
    warnings = sum(item.severity == "warning" for item in result.findings)
    errors = sum(item.severity == "error" for item in result.findings)
    if errors:
        print(f"Source review: REQUIRED ({errors} blocking issue(s), {warnings} warning(s)).")
    elif warnings:
        print(f"Source review: recommended ({warnings} warning(s)); the PDF audit cannot judge content quality.")


def _audit_only(args: argparse.Namespace) -> int:
    if args.input is not None:
        raise ValueError("Do not provide a LaTeX input together with --check")
    _validate_report_target(
        args.report_json,
        protected_files=(args.check,),
        force=args.force,
    )
    audit = audit_pdf(args.check)
    _print_audit(audit, verbose=args.verbose)
    _write_json(args.report_json, audit.as_dict(), force=args.force)
    return 0 if audit.passed else 1


def _prepare(args: argparse.Namespace) -> int:
    if args.input is None:
        raise ValueError("Provide a LaTeX project/.tex input, or use --check PDF")
    config = resolve_config(args)
    paths = resolve_project(args.input, main=args.main, output_dir=args.output_dir)
    copied_main = paths.output / paths.main_relative
    _validate_report_target(
        args.report_json,
        protected_files=(
            paths.main,
            paths.output,
            copied_main,
            copied_main.with_suffix(".pdf"),
        ),
        protected_roots=(paths.source,),
        force=args.force,
    )
    if args.dry_run:
        print("Dry-run transformation preview; no project copy will remain and no build will run.")
        with tempfile.TemporaryDirectory(prefix="canvas-ally-dry-run-") as temporary:
            preview_paths = resolve_project(
                paths.source,
                main=paths.main_relative,
                output_dir=Path(temporary) / "preview",
            )
            preview = prepare_project(
                preview_paths,
                replace(config, build=False),
            )
            preview.output = paths.output
            preview.main_tex = copied_main
            for finding in preview.findings:
                if finding.code == "project-copy":
                    finding.message = (
                        f"Would copy {paths.source} to {paths.output}; "
                        "the source would not be modified."
                    )
            _print_preparation(preview, verbose=args.verbose, dry_run=True)
            payload = preview.as_dict()
        payload.update(
            {
                "dry_run": True,
                "settings": {
                    "profile": config.profile,
                    "beamer_mode": config.beamer_mode,
                    "language": config.language,
                    "build": config.build,
                    "force_build": args.force_build,
                },
            }
        )
        _write_json(args.report_json, payload, force=args.force)
        return 1 if any(
            item["severity"] == "error"
            for item in payload["changes_and_warnings"]
        ) else 0

    result = prepare_project(paths, config, force=args.force)
    _print_preparation(result, verbose=args.verbose)
    unresolved = any(item.severity == "error" for item in result.findings)

    if config.build and (not unresolved or args.force_build):
        if unresolved:
            errors = sum(item.severity == "error" for item in result.findings)
            print(
                f"Force build enabled: continuing despite {errors} source ERROR "
                "finding(s). They remain blocking in the report and exit status."
            )
        try:
            pdf, build_log = build_project(
                result.output,
                result.main_tex,
                keep_build_files=config.keep_build_files,
            )
        except BuildError as error:
            result.findings.append(
                Finding("build-failed", str(error), "error", str(result.main_tex))
            )
            _write_json(args.report_json, result.as_dict(), force=args.force)
            raise
        result.pdf = pdf
        print(f"PDF: {pdf}")
        if args.verbose:
            print(f"Build log: {build_log}")
        result.audit = audit_pdf(pdf, source_expectations=result.source_expectations)
        _print_audit(result.audit, verbose=args.verbose)
    elif not config.build:
        print("Build disabled; inspect the prepared source copy before compiling.")
    else:
        print("Build skipped because strict/unresolved source findings require review.")

    _write_json(args.report_json, result.as_dict(), force=args.force)
    if unresolved:
        return 1
    if result.audit is not None and not result.audit.passed:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.check is not None:
            return _audit_only(args)
        return _prepare(args)
    except UnsupportedDocumentError as error:
        print(f"Unsupported: {error}", file=sys.stderr)
        return 3
    except (BuildError, FileExistsError, OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
