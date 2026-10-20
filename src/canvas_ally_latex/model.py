"""Shared result models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Finding:
    """A source transformation or accessibility finding."""

    code: str
    message: str
    severity: str = "info"
    file: str | None = None
    line: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Check:
    """One machine-verifiable PDF check."""

    name: str
    passed: bool
    detail: str
    severity: str = "major"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AuditResult:
    """Result of auditing one PDF."""

    pdf: Path
    checks: list[Check] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed or check.severity == "minor" for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pdf": str(self.pdf),
            "ally_readiness": "pass" if self.passed else "fail",
            "checks": [check.as_dict() for check in self.checks],
            "metrics": self.metrics,
            "notes": self.notes,
            "disclaimer": (
                "This is a local structural proxy. The actual proprietary "
                "Canvas/Ally score is assigned only after upload."
            ),
        }


@dataclass(slots=True)
class PrepareResult:
    """Result of copying and instrumenting a project."""

    source: Path
    output: Path
    main_tex: Path
    profile: str
    findings: list[Finding] = field(default_factory=list)
    source_expectations: dict[str, bool | int] = field(default_factory=dict)
    pdf: Path | None = None
    audit: AuditResult | None = None

    def as_dict(self) -> dict[str, Any]:
        errors = sum(item.severity == "error" for item in self.findings)
        warnings = sum(item.severity == "warning" for item in self.findings)
        audit_warnings = bool(
            self.audit
            and any(not check.passed and check.severity == "minor" for check in self.audit.checks)
        )
        if errors or (self.audit is not None and not self.audit.passed):
            overall = "fail"
        elif warnings or audit_warnings:
            overall = "pass_with_source_review"
        elif self.audit is not None:
            overall = "pass"
        else:
            overall = "prepared_not_audited"
        return {
            "source": str(self.source),
            "output": str(self.output),
            "main_tex": str(self.main_tex),
            "profile": self.profile,
            "source_expectations": self.source_expectations,
            "pdf": str(self.pdf) if self.pdf else None,
            "changes_and_warnings": [item.as_dict() for item in self.findings],
            "source_review": {"errors": errors, "warnings": warnings},
            "overall_ally_readiness": overall,
            "audit": self.audit.as_dict() if self.audit else None,
        }
