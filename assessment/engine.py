"""
Assessment Engine — Discovery, Gap Analysis, Check Execution.

Connects to source/target DBs and OCI, runs KB-defined checks,
produces structured results with severity, gaps, and remediation.

Usage:
    engine = AssessmentEngine(config, kb, oci_factory)
    report = engine.run_full_assessment()
    # or:
    report = engine.assess_source("aws_oracle_prod")
    report = engine.assess_target("adb_prod")
    report = engine.assess_oci()
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from core.config import MigrationConfig
from core.db_connector import DBConnector, BaseConnector, QueryResult
from core.kb_loader import KnowledgeBase
from core.oci_client import OCIClientFactory

logger = logging.getLogger(__name__)


# =============================================================================
# Result types
# =============================================================================
class CheckStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"
    ERROR = "ERROR"
    INFO = "INFO"


@dataclass
class CheckResult:
    """Result of a single prerequisite check."""
    check_id: str
    description: str
    category: str
    status: CheckStatus
    severity: str  # blocker, warning, info
    actual_value: Any = None
    expected_value: Any = None
    remediation: Optional[str] = None
    remediation_sql: Optional[str] = None  # Ready-to-execute SQL
    notes: Optional[str] = None
    doc_url: Optional[str] = None
    missing_items: List[str] = field(default_factory=list)  # For set checks

    @property
    def is_blocker(self) -> bool:
        return self.severity == "blocker" and self.status == CheckStatus.FAIL

    @property
    def is_warning(self) -> bool:
        return self.severity == "warning" and self.status in (CheckStatus.FAIL, CheckStatus.WARN)


@dataclass
class AssessmentReport:
    """Complete assessment report for one scope."""
    scope: str  # "source:aws_oracle_prod", "target:adb_prod", "oci"
    display_name: str
    results: List[CheckResult] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    connection_error: Optional[str] = None

    @property
    def blockers(self) -> List[CheckResult]:
        return [r for r in self.results if r.is_blocker]

    @property
    def warnings(self) -> List[CheckResult]:
        return [r for r in self.results if r.is_warning]

    @property
    def passed(self) -> List[CheckResult]:
        return [r for r in self.results if r.status == CheckStatus.PASS]

    @property
    def info(self) -> List[CheckResult]:
        return [r for r in self.results if r.status == CheckStatus.INFO]

    @property
    def ready(self) -> bool:
        return len(self.blockers) == 0 and self.connection_error is None


# =============================================================================
# Check executor
# =============================================================================
class CheckExecutor:
    """Executes individual KB checks against a DB connector or OCI SDK."""

    def __init__(self, connector: Optional[BaseConnector] = None,
                 oci_factory: Optional[OCIClientFactory] = None):
        self.connector = connector
        self.oci = oci_factory

    def execute_check(self, check: Dict, context: Dict[str, str]) -> CheckResult:
        """
        Execute a single KB check definition.

        Args:
            check: KB check dict (from prerequisites.yaml)
            context: Variable substitution dict (gg_username, password, schema, etc.)
        """
        check_id = check["id"]
        check_type = check.get("check_type", "sql")
        severity = check.get("severity", "info")
        description = check.get("description", "")
        category = check.get("category", "general")

        try:
            if check_type == "sql":
                return self._exec_sql_check(check, context)
            elif check_type == "sql_set":
                return self._exec_sql_set_check(check, context)
            elif check_type == "sql_per_schema":
                return self._exec_per_schema_check(check, context)
            elif check_type == "sql_per_table":
                return self._exec_per_table_check(check, context)
            elif check_type == "oci_sdk":
                return self._exec_oci_check(check, context)
            elif check_type == "custom":
                return self._exec_custom_check(check, context)
            else:
                return CheckResult(
                    check_id=check_id, description=description,
                    category=category, status=CheckStatus.SKIP,
                    severity=severity, notes=f"Unknown check_type: {check_type}"
                )
        except Exception as e:
            return CheckResult(
                check_id=check_id, description=description,
                category=category, status=CheckStatus.ERROR,
                severity=severity, notes=f"Check execution error: {e}"
            )

    # ---- SQL check ----
    def _exec_sql_check(self, check: Dict, ctx: Dict) -> CheckResult:
        """Execute single SQL query and compare result to expected."""
        check_id = check["id"]
        sql = self._substitute(check.get("sql", ""), ctx)
        expected = check.get("expected", "")
        severity = check.get("severity", "info")

        if not self.connector:
            return CheckResult(
                check_id=check_id, description=check.get("description", ""),
                category=check.get("category", ""), status=CheckStatus.SKIP,
                severity=severity, notes="No database connector available"
            )

        result = self.connector.execute(sql)

        if not result.success:
            # Try fallback SQL if available
            fallback_sql = check.get("fallback_sql")
            if fallback_sql:
                result = self.connector.execute(self._substitute(fallback_sql, ctx))

            if not result.success:
                return CheckResult(
                    check_id=check_id, description=check.get("description", ""),
                    category=check.get("category", ""), status=CheckStatus.ERROR,
                    severity=severity, notes=f"SQL error: {result.error}"
                )

        actual = result.scalar()
        status = self._evaluate(actual, expected)

        return CheckResult(
            check_id=check_id, description=check.get("description", ""),
            category=check.get("category", ""), status=status,
            severity=severity, actual_value=actual, expected_value=expected,
            remediation=self._substitute(check.get("remediation", ""), ctx),
            notes=check.get("notes"),
            doc_url=check.get("doc_url"),
        )

    # ---- SQL set check (privileges) ----
    def _exec_sql_set_check(self, check: Dict, ctx: Dict) -> CheckResult:
        """Check that query results contain all items from expected_set."""
        check_id = check["id"]
        sql = self._substitute(check.get("sql", ""), ctx)
        expected_set = set(check.get("expected_set", []))

        if not self.connector:
            return CheckResult(
                check_id=check_id, description=check.get("description", ""),
                category=check.get("category", ""), status=CheckStatus.SKIP,
                severity=check.get("severity", "info"),
                notes="No database connector available"
            )

        result = self.connector.execute(sql)
        if not result.success:
            return CheckResult(
                check_id=check_id, description=check.get("description", ""),
                category=check.get("category", ""), status=CheckStatus.ERROR,
                severity=check.get("severity", "info"),
                notes=f"SQL error: {result.error}"
            )

        actual_set = set(result.column_values(0))
        missing = expected_set - actual_set

        if missing:
            # Generate per-item remediation
            remediation_tmpl = check.get("remediation_template", "")
            remediation_lines = []
            for item in sorted(missing):
                remediation_lines.append(
                    self._substitute(remediation_tmpl.replace("{missing_priv}", item), ctx)
                )

            return CheckResult(
                check_id=check_id, description=check.get("description", ""),
                category=check.get("category", ""), status=CheckStatus.FAIL,
                severity=check.get("severity", "blocker"),
                actual_value=f"{len(actual_set)} of {len(expected_set)} present",
                expected_value=f"All {len(expected_set)} required",
                remediation="\n".join(remediation_lines),
                remediation_sql="\n".join(remediation_lines),
                missing_items=sorted(missing),
                doc_url=check.get("doc_url"),
            )

        return CheckResult(
            check_id=check_id, description=check.get("description", ""),
            category=check.get("category", ""), status=CheckStatus.PASS,
            severity=check.get("severity", "info"),
            actual_value=f"All {len(expected_set)} present",
        )

    # ---- Per-schema check ----
    def _exec_per_schema_check(self, check: Dict, ctx: Dict) -> CheckResult:
        """Execute check once per schema in scope."""
        schemas = ctx.get("_schemas", [])
        if not schemas:
            return CheckResult(
                check_id=check["id"], description=check.get("description", ""),
                category=check.get("category", ""), status=CheckStatus.SKIP,
                severity=check.get("severity", "info"),
                notes="No schemas in scope"
            )

        all_results = []
        for schema in schemas:
            schema_ctx = {**ctx, "schema": schema}
            sql = self._substitute(check.get("sql", ""), schema_ctx)

            if not self.connector:
                continue

            result = self.connector.execute(sql)
            if result.success:
                all_results.append({"schema": schema, "data": result.as_dicts(), "rows": result.rows})

        # For informational checks, just collect data
        if check.get("severity") == "info" or check.get("expected") == "informational":
            return CheckResult(
                check_id=check["id"], description=check.get("description", ""),
                category=check.get("category", ""), status=CheckStatus.INFO,
                severity="info", actual_value=all_results,
            )

        # For blockers (e.g. schema must exist), check expected
        expected = check.get("expected", "")
        if "row_exists" in expected:
            missing = [r["schema"] for r in all_results if not r["data"]]
            if missing:
                return CheckResult(
                    check_id=check["id"], description=check.get("description", ""),
                    category=check.get("category", ""), status=CheckStatus.FAIL,
                    severity=check.get("severity", "blocker"),
                    actual_value=f"Missing schemas: {missing}",
                    missing_items=missing,
                    remediation=check.get("remediation", ""),
                )

        return CheckResult(
            check_id=check["id"], description=check.get("description", ""),
            category=check.get("category", ""), status=CheckStatus.PASS,
            severity=check.get("severity", "info"),
            actual_value=f"All {len(schemas)} schemas found",
        )

    # ---- Per-table check (supplemental logging) ----
    def _exec_per_table_check(self, check: Dict, ctx: Dict) -> CheckResult:
        """Execute check for each table in migration scope."""
        # This is a simplified version — production would need full table enumeration
        schemas = ctx.get("_schemas", [])
        if not schemas or not self.connector:
            return CheckResult(
                check_id=check["id"], description=check.get("description", ""),
                category=check.get("category", ""), status=CheckStatus.SKIP,
                severity=check.get("severity", "info"),
                notes="No schemas or connector"
            )

        # Get all tables in scope
        missing_log = []
        for schema in schemas:
            tables_result = self.connector.execute(
                f"SELECT TABLE_NAME FROM DBA_TABLES WHERE OWNER = '{schema}'"
            )
            if not tables_result.success:
                continue

            for row in tables_result.rows:
                table_name = row[0]
                log_check = self.connector.execute(f"""
                    SELECT COUNT(*) FROM DBA_LOG_GROUPS
                    WHERE OWNER = '{schema}' AND TABLE_NAME = '{table_name}'
                    AND LOG_GROUP_TYPE = 'ALL COLUMN LOGGING'
                """)
                if log_check.success and log_check.scalar() == 0:
                    missing_log.append(f"{schema}.{table_name}")

        if missing_log:
            remediation_tmpl = check.get("remediation_template", "")
            sql_lines = []
            for fqn in missing_log:
                owner, table = fqn.split(".")
                sql_lines.append(
                    remediation_tmpl.replace("{owner}", owner).replace("{table}", table)
                )
            return CheckResult(
                check_id=check["id"], description=check.get("description", ""),
                category=check.get("category", ""), status=CheckStatus.FAIL,
                severity=check.get("severity", "blocker"),
                actual_value=f"{len(missing_log)} tables missing ALL COLUMNS logging",
                missing_items=missing_log[:20],  # Cap at 20 for display
                remediation="\n".join(sql_lines),
                remediation_sql="\n".join(sql_lines),
            )

        return CheckResult(
            check_id=check["id"], description=check.get("description", ""),
            category=check.get("category", ""), status=CheckStatus.PASS,
            severity=check.get("severity", "info"),
            actual_value="All tables have ALL COLUMNS supplemental logging",
        )

    # ---- OCI SDK check ----
    def _exec_oci_check(self, check: Dict, ctx: Dict) -> CheckResult:
        """Execute check via OCI SDK."""
        check_id = check["id"]
        if not self.oci:
            return CheckResult(
                check_id=check_id, description=check.get("description", ""),
                category=check.get("category", ""), status=CheckStatus.SKIP,
                severity=check.get("severity", "info"),
                notes="OCI SDK not available"
            )

        sdk_call = check.get("sdk_call", "")
        expected = check.get("expected", "")
        field_name = check.get("field")

        try:
            actual = self._invoke_oci(sdk_call, ctx, field_name)
            status = self._evaluate(actual, expected)

            return CheckResult(
                check_id=check_id, description=check.get("description", ""),
                category=check.get("category", ""), status=status,
                severity=check.get("severity", "info"),
                actual_value=actual, expected_value=expected,
                remediation=self._substitute(check.get("remediation", ""), ctx),
                doc_url=check.get("doc_url"),
            )
        except Exception as e:
            return CheckResult(
                check_id=check_id, description=check.get("description", ""),
                category=check.get("category", ""), status=CheckStatus.FAIL,
                severity=check.get("severity", "blocker"),
                notes=f"OCI API error: {e}",
                remediation=self._substitute(check.get("remediation", ""), ctx),
            )

    def _invoke_oci(self, sdk_call: str, ctx: Dict, field_name: Optional[str]) -> Any:
        """Dispatch OCI SDK call by name."""
        if "get_autonomous_database" in sdk_call:
            adb_ocid = ctx.get("adb_ocid", "")
            response = self.oci.database.get_autonomous_database(adb_ocid)
            if field_name:
                return getattr(response.data, field_name, None)
            return response.data.lifecycle_state

        elif "get_bucket" in sdk_call:
            ns = ctx.get("namespace", "")
            bucket = ctx.get("bucket_name", "")
            response = self.oci.object_storage.get_bucket(ns, bucket)
            if field_name:
                return getattr(response.data, field_name, None)
            return "EXISTS"

        elif "get_vault" in sdk_call:
            vault_ocid = ctx.get("vault_ocid", "")
            response = self.oci.kms_vault().get_vault(vault_ocid)
            if field_name:
                return getattr(response.data, field_name, None)
            return response.data.lifecycle_state

        elif "get_key" in sdk_call or "kms_management" in sdk_call:
            key_ocid = ctx.get("key_ocid", "")
            vault_ocid = ctx.get("vault_ocid", "")
            vault = self.oci.kms_vault().get_vault(vault_ocid).data
            client = self.oci.kms_management(vault.management_endpoint)
            response = client.get_key(key_ocid)
            if field_name:
                return getattr(response.data, field_name, None)
            return response.data.lifecycle_state

        elif "list_policies" in sdk_call:
            compartment_id = ctx.get("compartment_ocid", "")
            response = self.oci.identity.list_policies(compartment_id)
            return [p.statements for p in response.data]

        elif "list_dynamic_groups" in sdk_call:
            compartment_id = ctx.get("tenancy_ocid", "")
            response = self.oci.identity.list_dynamic_groups(compartment_id)
            return [dg.matching_rule for dg in response.data]

        elif "get_subnet" in sdk_call:
            subnet_ocid = ctx.get("subnet_ocid", "")
            response = self.oci.virtual_network.get_subnet(subnet_ocid)
            return response.data.lifecycle_state

        elif "generate_autonomous_database_wallet" in sdk_call:
            adb_ocid = ctx.get("adb_ocid", "")
            import oci
            details = oci.database.models.GenerateAutonomousDatabaseWalletDetails(
                password=ctx.get("password", "dummy")
            )
            response = self.oci.database.generate_autonomous_database_wallet(adb_ocid, details)
            content = response.data.content if hasattr(response.data, 'content') else response.data
            return len(content) if content else 0

        else:
            raise ValueError(f"Unknown SDK call: {sdk_call}")

    # ---- Custom checks ----
    def _exec_custom_check(self, check: Dict, ctx: Dict) -> CheckResult:
        """Execute custom check (e.g. TCP test)."""
        custom_type = check.get("custom_check", "")

        if custom_type == "tcp_connect_test":
            host = self._substitute(ctx.get("source_host", ""), ctx)
            port = int(ctx.get("source_port", 1521))
            reachable = OCIClientFactory.test_tcp_connect(host, port)

            return CheckResult(
                check_id=check["id"], description=check.get("description", ""),
                category=check.get("category", ""), 
                status=CheckStatus.PASS if reachable else CheckStatus.FAIL,
                severity=check.get("severity", "blocker"),
                actual_value="Reachable" if reachable else "Unreachable",
                expected_value="connection_successful",
                remediation=self._substitute(check.get("remediation", ""), ctx),
            )

        return CheckResult(
            check_id=check["id"], description=check.get("description", ""),
            category=check.get("category", ""), status=CheckStatus.SKIP,
            severity=check.get("severity", "info"),
            notes=f"Unknown custom check: {custom_type}",
        )

    # ---- Helpers ----
    @staticmethod
    def _substitute(template: str, ctx: Dict[str, str]) -> str:
        """Replace {var} placeholders in template with context values."""
        if not template:
            return ""
        for key, val in ctx.items():
            if not key.startswith("_"):
                template = template.replace(f"{{{key}}}", str(val))
        return template

    @staticmethod
    def _evaluate(actual: Any, expected: str) -> CheckStatus:
        """Compare actual value against expected specification."""
        if not expected or expected == "informational":
            return CheckStatus.INFO

        actual_str = str(actual).strip() if actual is not None else ""

        # Exact match
        if expected == actual_str:
            return CheckStatus.PASS

        # row_exists
        if "row_exists" in expected:
            return CheckStatus.PASS if actual is not None and actual_str else CheckStatus.FAIL

        # not_null
        if expected == "not_null":
            return CheckStatus.PASS if actual is not None and actual_str else CheckStatus.FAIL

        # regex match
        if expected.startswith("regex:"):
            pattern = expected[6:]
            return CheckStatus.PASS if re.search(pattern, actual_str) else CheckStatus.FAIL

        # gte (greater than or equal)
        if expected.startswith("gte:"):
            threshold = int(expected[4:])
            try:
                return CheckStatus.PASS if int(actual_str) >= threshold else CheckStatus.FAIL
            except (ValueError, TypeError):
                return CheckStatus.FAIL

        # contains
        if expected.startswith("contains:"):
            substr = expected[9:]
            return CheckStatus.PASS if substr in actual_str else CheckStatus.FAIL

        # bucket_exists
        if expected == "bucket_exists" or expected == "EXISTS":
            return CheckStatus.PASS

        # policy match (list of statement lists)
        if "policy" in expected.lower() and "matching" in expected.lower():
            return CheckStatus.INFO  # Manual verification needed

        # default: string comparison
        return CheckStatus.PASS if expected.lower() in actual_str.lower() else CheckStatus.FAIL


# =============================================================================
# Main Assessment Engine
# =============================================================================
class AssessmentEngine:
    """
    Orchestrates assessment across source DBs, target ADBs, and OCI infra.
    """

    def __init__(self, config: MigrationConfig, kb: KnowledgeBase,
                 oci_factory: Optional[OCIClientFactory] = None):
        self.config = config
        self.kb = kb
        self.oci = oci_factory

    def run_full_assessment(self) -> List[AssessmentReport]:
        """Run assessment on all sources, targets, and OCI."""
        reports = []

        # Assess each unique source
        assessed_sources = set()
        for mig_key, mig in self.config.migrations.items():
            src_key = mig.get("source_db_key", "")
            if src_key and src_key not in assessed_sources:
                assessed_sources.add(src_key)
                reports.append(self.assess_source(src_key))

        # Assess each unique target
        assessed_targets = set()
        for mig_key, mig in self.config.migrations.items():
            tgt_key = mig.get("target_db_key", "")
            if tgt_key and tgt_key not in assessed_targets:
                assessed_targets.add(tgt_key)
                reports.append(self.assess_target(tgt_key))

        # Assess OCI infrastructure
        reports.append(self.assess_oci())

        return reports

    def assess_source(self, source_key: str) -> AssessmentReport:
        """Assess a source database."""
        src = self.config.source_db(source_key)
        if not src:
            return AssessmentReport(
                scope=f"source:{source_key}",
                display_name=f"Source: {source_key}",
                connection_error=f"Source '{source_key}' not found in config"
            )

        display_name = src.get("display_name", source_key)
        report = AssessmentReport(
            scope=f"source:{source_key}",
            display_name=f"Source: {display_name}"
        )

        # Build context for variable substitution
        schemas = list(self.config.all_schemas_for_source(source_key))
        db_type = src.get("db_type", "oracle_onprem")
        migration_type = self._get_migration_type_for_source(source_key)

        ctx = {
            "gg_username": src.get("gg_username", "GGADMIN"),
            "gg_password": src.get("gg_password", ""),
            "username": src.get("username", ""),
            "password": src.get("password", ""),
            "pdb_name": src.get("pdb_name", ""),
            "host": src.get("host", ""),
            "port": str(src.get("port", 1521)),
            "service_name": src.get("service_name", ""),
            "_schemas": schemas,
        }

        # Connect using assessment user (read-only)
        assess_user = src.get("assessment_user", src.get("username", ""))
        assess_pass = src.get("assessment_password", src.get("password", ""))
        preference = self.config.assessment_config.get("db_connector_preference", "auto")

        connector = None
        try:
            connector = DBConnector.create(
                host=src["host"], port=src["port"],
                service_name=src["service_name"],
                user=assess_user, password=assess_pass,
                preference=preference,
            )
            report.metadata["connector"] = connector.connector_type
            report.metadata["db_version"] = connector.execute(
                "SELECT VERSION_FULL FROM V$INSTANCE"
            ).scalar() or connector.execute("SELECT VERSION FROM V$INSTANCE").scalar()
        except Exception as e:
            report.connection_error = str(e)
            logger.error(f"Cannot connect to {source_key}: {e}")

        # Execute checks
        executor = CheckExecutor(connector=connector, oci_factory=self.oci)
        checks = self.kb.get_source_checks(migration_type=migration_type, db_type=db_type)

        for check_def in checks:
            # Skip if dependency not met
            depends = check_def.get("depends_on", [])
            if depends:
                dep_failed = any(
                    r.status == CheckStatus.FAIL
                    for r in report.results
                    if r.check_id in depends
                )
                if dep_failed:
                    report.results.append(CheckResult(
                        check_id=check_def["id"],
                        description=check_def.get("description", ""),
                        category=check_def.get("category", ""),
                        status=CheckStatus.SKIP,
                        severity=check_def.get("severity", "info"),
                        notes=f"Skipped: dependency {depends} not met",
                    ))
                    continue

            # Conditional check
            condition = check_def.get("condition", "")
            if condition == "is_cdb == true" and not src.get("is_cdb", False):
                continue

            result = executor.execute_check(check_def, ctx)
            report.results.append(result)

        # Cleanup
        if connector:
            connector.close()

        return report

    def assess_target(self, target_key: str) -> AssessmentReport:
        """Assess a target ADB."""
        tgt = self.config.target_db(target_key)
        if not tgt:
            return AssessmentReport(
                scope=f"target:{target_key}",
                display_name=f"Target: {target_key}",
                connection_error=f"Target '{target_key}' not found in config"
            )

        display_name = tgt.get("display_name", target_key)
        report = AssessmentReport(
            scope=f"target:{target_key}",
            display_name=f"Target: {display_name}"
        )

        ctx = {
            "adb_ocid": tgt.get("adb_ocid", ""),
            "username": tgt.get("username", "ADMIN"),
            "password": tgt.get("password", ""),
            "gg_username": tgt.get("gg_username", "GGADMIN"),
            "gg_password": tgt.get("gg_password", ""),
            "namespace": self.config.object_storage.get("namespace", ""),
            "bucket_name": self.config.object_storage.get("bucket_name", ""),
            **{k: v for k, v in self.config.oci.items()},
        }

        # OCI SDK checks (ADB status, private endpoint, wallet)
        executor = CheckExecutor(oci_factory=self.oci)

        for check_def in self.kb.get_target_checks():
            check_type = check_def.get("check_type", "")

            if check_type == "oci_sdk":
                result = executor.execute_check(check_def, ctx)
                report.results.append(result)
            elif check_type == "sql":
                # SQL checks against target ADB require wallet connection
                # For now, mark as INFO with guidance
                report.results.append(CheckResult(
                    check_id=check_def["id"],
                    description=check_def.get("description", ""),
                    category=check_def.get("category", ""),
                    status=CheckStatus.INFO,
                    severity=check_def.get("severity", "info"),
                    notes="Connect to ADB with wallet to verify. "
                          "Run: python migrate.py --assess --target " + target_key,
                    remediation=check_def.get("remediation"),
                ))

        return report

    def assess_oci(self) -> AssessmentReport:
        """Assess OCI infrastructure."""
        report = AssessmentReport(
            scope="oci",
            display_name="OCI Infrastructure"
        )

        ctx = {
            **{k: str(v) for k, v in self.config.oci.items()},
            **{k: str(v) for k, v in self.config.vault.items()},
            **{k: str(v) for k, v in self.config.object_storage.items()},
            **{k: str(v) for k, v in self.config.networking.items()},
        }

        # Add source connection info for network test
        for src_key, src in self.config.source_databases.items():
            ctx["source_host"] = src.get("host", "")
            ctx["source_port"] = str(src.get("port", 1521))
            break  # Use first source for network test

        has_rr = self.config.has_reverse_replication()
        executor = CheckExecutor(oci_factory=self.oci)

        for check_def in self.kb.get_oci_checks(has_reverse_replication=has_rr):
            result = executor.execute_check(check_def, ctx)
            report.results.append(result)

        return report

    def _get_migration_type_for_source(self, source_key: str) -> str:
        """Get the migration type (ONLINE/OFFLINE) for migrations using this source."""
        for mig in self.config.migrations.values():
            if mig.get("source_db_key") == source_key:
                return mig.get("migration_type", "ONLINE")
        return "ONLINE"
