"""
Assessment report renderer.

Outputs assessment results to terminal (with rich formatting if available),
JSON, or HTML.
"""

import json
import logging
import sys
from typing import List

from assessment.engine import AssessmentReport, CheckResult, CheckStatus

logger = logging.getLogger(__name__)

# Try rich for beautiful terminal output
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


# Status symbols
STATUS_SYMBOLS = {
    CheckStatus.PASS: ("✅", "green"),
    CheckStatus.FAIL: ("❌", "red"),
    CheckStatus.WARN: ("⚠️ ", "yellow"),
    CheckStatus.SKIP: ("⏭️ ", "dim"),
    CheckStatus.ERROR: ("💥", "red"),
    CheckStatus.INFO: ("ℹ️ ", "blue"),
}


def render_terminal(reports: List[AssessmentReport]):
    """Render reports to terminal."""
    if HAS_RICH:
        _render_rich(reports)
    else:
        _render_plain(reports)


def _render_rich(reports: List[AssessmentReport]):
    """Render with rich library."""
    console = Console()

    for report in reports:
        # Header
        total_blockers = len(report.blockers)
        total_warnings = len(report.warnings)
        total_passed = len(report.passed)

        if report.connection_error:
            console.print(Panel(
                f"[red bold]Connection Error:[/] {report.connection_error}",
                title=report.display_name,
                border_style="red",
            ))
            continue

        # Status line
        if report.ready:
            status_text = "[green bold]READY[/green bold]"
        else:
            status_text = f"[red bold]{total_blockers} BLOCKERS[/red bold]"

        header = (
            f"{status_text}  |  "
            f"[green]{total_passed} passed[/]  "
            f"[red]{total_blockers} blockers[/]  "
            f"[yellow]{total_warnings} warnings[/]  "
            f"[blue]{len(report.info)} info[/]"
        )

        # Metadata
        meta_parts = []
        if report.metadata.get("connector"):
            meta_parts.append(f"Connector: {report.metadata['connector']}")
        if report.metadata.get("db_version"):
            meta_parts.append(f"Version: {report.metadata['db_version']}")

        # Results table
        table = Table(show_header=True, header_style="bold", pad_edge=False)
        table.add_column("", width=3)
        table.add_column("Check", min_width=30)
        table.add_column("Status", min_width=15)
        table.add_column("Details", min_width=40)

        # Group by category
        categories = {}
        for r in report.results:
            categories.setdefault(r.category, []).append(r)

        for category, checks in categories.items():
            table.add_row("", f"[bold]{category.replace('_', ' ').title()}[/]", "", "")

            for check in checks:
                symbol, color = STATUS_SYMBOLS.get(check.status, ("?", "white"))
                status_str = f"[{color}]{check.status.value}[/{color}]"

                # Details
                detail_parts = []
                if check.actual_value and check.status != CheckStatus.PASS:
                    val = str(check.actual_value)
                    if len(val) > 60:
                        val = val[:57] + "..."
                    detail_parts.append(val)
                if check.missing_items:
                    n = len(check.missing_items)
                    detail_parts.append(f"{n} missing")
                if check.notes and check.status in (CheckStatus.INFO, CheckStatus.SKIP):
                    detail_parts.append(check.notes[:60])

                detail = " | ".join(detail_parts) if detail_parts else ""

                table.add_row(symbol, check.description, status_str, detail)

        subtitle = " | ".join(meta_parts) if meta_parts else None
        console.print(Panel(table, title=report.display_name, subtitle=subtitle))
        console.print()

        # Show remediation summary for blockers
        if report.blockers:
            console.print("[red bold]Required remediation:[/]")
            for b in report.blockers:
                if b.remediation:
                    first_line = b.remediation.strip().splitlines()[0]
                    console.print(f"  {b.check_id}: {first_line}")
            console.print()

    # Final summary
    all_blockers = sum(len(r.blockers) for r in reports)
    all_warnings = sum(len(r.warnings) for r in reports)
    all_conn_errors = sum(1 for r in reports if r.connection_error)

    if all_conn_errors:
        console.print(f"[red bold]⚠ {all_conn_errors} connection(s) failed[/]")

    if all_blockers == 0 and all_conn_errors == 0:
        console.print("[green bold]✅ All checks passed. Ready for migration.[/]")
    else:
        console.print(
            f"[red bold]❌ {all_blockers} blockers, "
            f"{all_warnings} warnings. "
            f"Run remediation before proceeding.[/]"
        )
        console.print("[dim]Generate fix script: python migrate.py --assess --generate-sql[/]")


def _render_plain(reports: List[AssessmentReport]):
    """Render without rich (plain text fallback)."""
    SYMBOLS_PLAIN = {
        CheckStatus.PASS: "[OK]",
        CheckStatus.FAIL: "[FAIL]",
        CheckStatus.WARN: "[WARN]",
        CheckStatus.SKIP: "[SKIP]",
        CheckStatus.ERROR: "[ERR]",
        CheckStatus.INFO: "[INFO]",
    }

    for report in reports:
        print(f"\n{'=' * 70}")
        print(f"  {report.display_name}")
        print(f"{'=' * 70}")

        if report.connection_error:
            print(f"  CONNECTION ERROR: {report.connection_error}")
            continue

        for check in report.results:
            sym = SYMBOLS_PLAIN.get(check.status, "[???]")
            desc = check.description
            detail = ""
            if check.actual_value and check.status != CheckStatus.PASS:
                detail = f" → {check.actual_value}"
            elif check.missing_items:
                detail = f" → {len(check.missing_items)} missing"
            print(f"  {sym:6s} {desc}{detail}")

        n_block = len(report.blockers)
        n_warn = len(report.warnings)
        n_pass = len(report.passed)
        print(f"\n  Result: {n_pass} passed, {n_block} blockers, {n_warn} warnings")

    # Final
    all_blockers = sum(len(r.blockers) for r in reports)
    if all_blockers == 0:
        print("\n[OK] All checks passed. Ready for migration.")
    else:
        print(f"\n[FAIL] {all_blockers} blockers found. Run remediation.")


def render_json(reports: List[AssessmentReport]) -> str:
    """Render reports as JSON."""
    data = []
    for report in reports:
        data.append({
            "scope": report.scope,
            "display_name": report.display_name,
            "ready": report.ready,
            "connection_error": report.connection_error,
            "metadata": report.metadata,
            "summary": {
                "blockers": len(report.blockers),
                "warnings": len(report.warnings),
                "passed": len(report.passed),
                "info": len(report.info),
            },
            "results": [
                {
                    "check_id": r.check_id,
                    "description": r.description,
                    "category": r.category,
                    "status": r.status.value,
                    "severity": r.severity,
                    "actual_value": str(r.actual_value) if r.actual_value else None,
                    "expected_value": r.expected_value,
                    "remediation": r.remediation,
                    "missing_items": r.missing_items,
                    "doc_url": r.doc_url,
                }
                for r in report.results
            ],
        })
    return json.dumps(data, indent=2)
