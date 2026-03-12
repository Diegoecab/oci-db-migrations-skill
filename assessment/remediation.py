"""
Remediation generator.

Takes assessment results and produces:
  1. A ready-to-execute SQL script (--generate-sql)
  2. Optionally executes remediation via DB connector (--remediate)
"""

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from assessment.engine import AssessmentReport, CheckResult, CheckStatus
from core.db_connector import BaseConnector

logger = logging.getLogger(__name__)


class RemediationGenerator:
    """Generates and optionally executes remediation scripts."""

    def __init__(self, reports: List[AssessmentReport]):
        self.reports = reports

    def generate_sql(self, output_path: Optional[str] = None) -> str:
        """
        Generate combined SQL remediation script from all reports.
        Returns the SQL string and optionally writes to file.
        """
        lines = [
            "-- =============================================================================",
            "-- OCI DMS Migration Remediation Script",
            f"-- Generated: {datetime.now(timezone.utc).isoformat()}",
            "-- =============================================================================",
            "-- Review each section before executing.",
            "-- Run the assessment again after remediation to verify all issues are resolved:",
            "--   python migrate.py --assess",
            "-- =============================================================================",
            "",
        ]

        blocker_count = 0
        warning_count = 0

        for report in self.reports:
            if not report.results:
                continue

            failed = [
                r for r in report.results
                if r.status in (CheckStatus.FAIL, CheckStatus.WARN)
                and r.remediation
            ]
            if not failed:
                continue

            lines.append(f"-- {'=' * 70}")
            lines.append(f"-- {report.display_name}")
            lines.append(f"-- {'=' * 70}")
            lines.append("")

            # Group by category
            categories = {}
            for r in failed:
                categories.setdefault(r.category, []).append(r)

            for category, checks in categories.items():
                lines.append(f"-- --- {category.replace('_', ' ').title()} ---")
                lines.append("")

                for check in checks:
                    severity_tag = "BLOCKER" if check.is_blocker else "WARNING"
                    if check.is_blocker:
                        blocker_count += 1
                    else:
                        warning_count += 1

                    lines.append(f"-- [{severity_tag}] {check.check_id}: {check.description}")
                    if check.actual_value:
                        lines.append(f"-- Current: {check.actual_value}")
                    if check.missing_items:
                        items_str = ", ".join(check.missing_items[:10])
                        if len(check.missing_items) > 10:
                            items_str += f" ... (+{len(check.missing_items) - 10} more)"
                        lines.append(f"-- Missing: {items_str}")
                    if check.doc_url:
                        lines.append(f"-- Ref: {check.doc_url}")

                    # Write remediation SQL
                    remediation = check.remediation_sql or check.remediation or ""
                    for sql_line in remediation.strip().splitlines():
                        sql_line = sql_line.strip()
                        if sql_line and not sql_line.startswith("--"):
                            lines.append(sql_line)
                        elif sql_line.startswith("--"):
                            lines.append(sql_line)
                    lines.append("")

        # Summary
        lines.insert(5, f"-- Blockers: {blocker_count} | Warnings: {warning_count}")
        lines.insert(6, "")

        # Verification footer
        lines.extend([
            "",
            "-- =============================================================================",
            "-- Post-remediation verification:",
            "--   python migrate.py --assess",
            "-- Expected result: 0 blockers, 0 warnings",
            "-- =============================================================================",
        ])

        sql_text = "\n".join(lines)

        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w") as f:
                f.write(sql_text)
            logger.info(f"Remediation script written to: {output_path}")

        return sql_text

    def execute_remediation(
        self,
        connector: BaseConnector,
        scope: str = "source",
        confirm_each: bool = True,
    ) -> List[dict]:
        """
        Execute remediation SQL against a database.

        Args:
            connector: Connected DB connector (with privileged user)
            scope: "source" or "target" to filter relevant reports
            confirm_each: If True, prompt before each statement

        Returns:
            List of {check_id, sql, success, error} dicts
        """
        execution_log = []

        for report in self.reports:
            if scope == "source" and not report.scope.startswith("source:"):
                continue
            if scope == "target" and not report.scope.startswith("target:"):
                continue

            failed = [
                r for r in report.results
                if r.status == CheckStatus.FAIL and r.remediation_sql
            ]

            for check in failed:
                statements = [
                    s.strip() for s in check.remediation_sql.split("\n")
                    if s.strip() and not s.strip().startswith("--")
                ]

                for stmt in statements:
                    if confirm_each:
                        print(f"\n[{check.check_id}] Execute:")
                        print(f"  {stmt}")
                        answer = input("  Execute? [y/N/q] ").strip().lower()
                        if answer == "q":
                            return execution_log
                        if answer != "y":
                            execution_log.append({
                                "check_id": check.check_id,
                                "sql": stmt,
                                "success": None,
                                "error": "Skipped by user",
                            })
                            continue

                    result = connector.execute(stmt)
                    execution_log.append({
                        "check_id": check.check_id,
                        "sql": stmt,
                        "success": result.success,
                        "error": result.error,
                    })

                    if result.success:
                        logger.info(f"  ✓ {check.check_id}: {stmt[:60]}...")
                    else:
                        logger.error(f"  ✗ {check.check_id}: {result.error}")

        return execution_log

    def generate_oci_remediation(self) -> str:
        """Generate OCI CLI commands for OCI infrastructure remediation."""
        lines = [
            "#!/bin/bash",
            "# =============================================================================",
            "# OCI Infrastructure Remediation Commands",
            f"# Generated: {datetime.now(timezone.utc).isoformat()}",
            "# =============================================================================",
            "",
        ]

        for report in self.reports:
            if report.scope != "oci":
                continue

            failed = [
                r for r in report.results
                if r.status == CheckStatus.FAIL and r.remediation
            ]

            for check in failed:
                lines.append(f"# [{check.severity.upper()}] {check.check_id}: {check.description}")
                for rem_line in check.remediation.strip().splitlines():
                    lines.append(rem_line)
                lines.append("")

        return "\n".join(lines)
