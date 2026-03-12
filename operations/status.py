"""
Status command — provides a full state snapshot of all migration resources.

Returns structured JSON that the AI skill consumes to make decisions.
This replaces the traditional interactive menu: instead of showing
12 static options, the skill reads this state and presents only
the actions that make sense right now.

Usage:
    python migrate.py status                    # Full status
    python migrate.py status --migration hr     # Specific migration
    python migrate.py status --json             # Raw JSON (for skill consumption)
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.config import MigrationConfig
from core.oci_client import OCIClientFactory

logger = logging.getLogger(__name__)


@dataclass
class ResourceState:
    """State of a single OCI resource."""
    resource_type: str
    name: str
    ocid: Optional[str] = None
    state: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    console_url: Optional[str] = None


@dataclass
class MigrationState:
    """Complete state of a migration and its dependencies."""
    migration_key: str
    display_name: str
    migration_type: str
    source_key: str
    target_key: str

    # DMS state
    dms_migration: Optional[ResourceState] = None
    dms_source_connection: Optional[ResourceState] = None
    dms_target_connection: Optional[ResourceState] = None
    dms_job: Optional[ResourceState] = None

    # GoldenGate state (if reverse replication)
    gg_deployment: Optional[ResourceState] = None
    gg_extract: Optional[ResourceState] = None
    gg_replicat: Optional[ResourceState] = None

    # Computed
    has_reverse_replication: bool = False
    recommended_actions: List[str] = field(default_factory=list)


@dataclass
class FullStatus:
    """Complete status snapshot for the skill."""
    timestamp: str = ""
    config_valid: bool = False
    config_errors: List[str] = field(default_factory=list)

    # OCI infrastructure
    vault: Optional[ResourceState] = None
    nsg: Optional[ResourceState] = None
    bucket: Optional[ResourceState] = None

    # Migrations
    migrations: List[MigrationState] = field(default_factory=list)

    # Summary
    total_migrations: int = 0
    active_migrations: int = 0
    completed_migrations: int = 0
    failed_migrations: int = 0

    # Next recommended action (computed by analyze())
    next_action: str = ""
    next_action_command: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


class StatusCollector:
    """Collects current state from OCI APIs."""

    def __init__(self, config: MigrationConfig, oci: OCIClientFactory):
        self.config = config
        self.oci = oci
        self.region = config.oci.get("region", "")
        self.compartment = config.oci.get("compartment_ocid", "")

    def collect(self, migration_key: Optional[str] = None) -> FullStatus:
        """Collect full status snapshot."""
        status = FullStatus(
            timestamp=datetime.now(timezone.utc).isoformat(),
            config_valid=True,
        )

        # Infrastructure
        status.vault = self._check_vault()
        status.nsg = self._check_nsg()
        status.bucket = self._check_bucket()

        # Migrations
        dms_connections = self._list_dms_connections()
        dms_migrations = self._list_dms_migrations()
        gg_deployment = self._check_gg_deployment()

        for mig_key, mig in self.config.migrations.items():
            if migration_key and mig_key != migration_key:
                continue

            mig_state = MigrationState(
                migration_key=mig_key,
                display_name=mig.get("display_name", mig_key),
                migration_type=mig.get("migration_type", "ONLINE"),
                source_key=mig.get("source_db_key", ""),
                target_key=mig.get("target_db_key", ""),
                has_reverse_replication=mig.get("enable_reverse_replication", False),
            )

            # DMS connections
            src_conn_name = f"dms-src-{mig['source_db_key']}"
            tgt_conn_name = f"dms-tgt-{mig['target_db_key']}"
            mig_state.dms_source_connection = dms_connections.get(src_conn_name)
            mig_state.dms_target_connection = dms_connections.get(tgt_conn_name)

            # DMS migration
            display_name = mig.get("display_name", mig_key)
            mig_state.dms_migration = dms_migrations.get(display_name)

            # DMS job (if migration exists)
            if mig_state.dms_migration and mig_state.dms_migration.ocid:
                mig_state.dms_job = self._check_dms_job(mig_state.dms_migration.ocid)

            # GoldenGate
            if mig.get("enable_reverse_replication"):
                mig_state.gg_deployment = gg_deployment

            # Compute recommended actions
            mig_state.recommended_actions = self._compute_actions(mig_state)

            status.migrations.append(mig_state)

        # Summary
        status.total_migrations = len(status.migrations)
        status.active_migrations = sum(
            1 for m in status.migrations
            if m.dms_migration and m.dms_migration.state in ("ACTIVE", "IN_PROGRESS")
        )
        status.completed_migrations = sum(
            1 for m in status.migrations
            if m.dms_migration and m.dms_migration.state == "SUCCEEDED"
        )
        status.failed_migrations = sum(
            1 for m in status.migrations
            if m.dms_migration and m.dms_migration.state == "FAILED"
        )

        # Overall next action
        status.next_action, status.next_action_command = self._compute_next_global(status)

        return status

    # ---- Resource checks ----

    def _check_vault(self) -> ResourceState:
        try:
            vault_ocid = self.config.vault.get("vault_ocid", "")
            vault = self.oci.kms_vault().get_vault(vault_ocid).data
            return ResourceState(
                resource_type="vault", name=vault.display_name,
                ocid=vault_ocid, state=vault.lifecycle_state,
            )
        except Exception as e:
            return ResourceState(
                resource_type="vault", name="vault",
                error=str(e),
            )

    def _check_nsg(self) -> ResourceState:
        try:
            vcn_ocid = self.config.networking.get("vcn_ocid", "")
            nsgs = self.oci.virtual_network.list_network_security_groups(
                compartment_id=self.compartment, vcn_id=vcn_ocid,
            ).data
            for nsg in nsgs:
                if nsg.display_name == "dms-migration-nsg":
                    return ResourceState(
                        resource_type="nsg", name=nsg.display_name,
                        ocid=nsg.id, state=nsg.lifecycle_state,
                    )
            return ResourceState(
                resource_type="nsg", name="dms-migration-nsg",
                state="NOT_FOUND",
            )
        except Exception as e:
            return ResourceState(resource_type="nsg", name="nsg", error=str(e))

    def _check_bucket(self) -> ResourceState:
        try:
            ns = self.config.object_storage.get("namespace", "")
            bucket_name = self.config.object_storage.get("bucket_name", "")
            bucket = self.oci.object_storage.get_bucket(ns, bucket_name).data
            return ResourceState(
                resource_type="bucket", name=bucket.name,
                state="ACTIVE",
                details={"approximate_count": getattr(bucket, 'approximate_count', None)},
            )
        except Exception as e:
            return ResourceState(resource_type="bucket", name="bucket", error=str(e))

    def _list_dms_connections(self) -> Dict[str, ResourceState]:
        result = {}
        try:
            connections = self.oci.dms.list_connections(
                compartment_id=self.compartment).data.items
            for c in connections:
                result[c.display_name] = ResourceState(
                    resource_type="dms_connection", name=c.display_name,
                    ocid=c.id, state=c.lifecycle_state,
                )
        except Exception as e:
            logger.debug(f"Cannot list DMS connections: {e}")
        return result

    def _list_dms_migrations(self) -> Dict[str, ResourceState]:
        result = {}
        try:
            migrations = self.oci.dms.list_migrations(
                compartment_id=self.compartment).data.items
            for m in migrations:
                if m.lifecycle_state not in ("DELETED", "DELETING"):
                    result[m.display_name] = ResourceState(
                        resource_type="dms_migration", name=m.display_name,
                        ocid=m.id, state=m.lifecycle_state,
                        details={"type": getattr(m, 'type', None)},
                    )
        except Exception as e:
            logger.debug(f"Cannot list DMS migrations: {e}")
        return result

    def _check_dms_job(self, migration_ocid: str) -> Optional[ResourceState]:
        try:
            jobs = self.oci.dms.list_migration_jobs(migration_id=migration_ocid).data.items
            if jobs:
                latest = jobs[0]  # Most recent
                return ResourceState(
                    resource_type="dms_job", name=latest.display_name or "job",
                    ocid=latest.id, state=latest.lifecycle_state,
                    details={
                        "type": getattr(latest, 'type', None),
                        "progress": getattr(latest, 'progress', None),
                    },
                )
        except Exception as e:
            logger.debug(f"Cannot check DMS job: {e}")
        return None

    def _check_gg_deployment(self) -> Optional[ResourceState]:
        try:
            deployments = self.oci.goldengate().list_deployments(
                compartment_id=self.compartment).data.items
            gg_name = self.config.goldengate.get("deployment_name", "gg-migration-fallback")
            for d in deployments:
                if d.display_name == gg_name and d.lifecycle_state != "DELETED":
                    return ResourceState(
                        resource_type="gg_deployment", name=d.display_name,
                        ocid=d.id, state=d.lifecycle_state,
                        details={"url": getattr(d, 'deployment_url', None)},
                    )
        except Exception as e:
            logger.debug(f"Cannot check GG deployment: {e}")
        return None

    # ---- Action computation ----

    def _compute_actions(self, mig: MigrationState) -> List[str]:
        """Compute recommended actions based on current state."""
        actions = []

        # No connections yet
        if not mig.dms_source_connection or mig.dms_source_connection.state == "NOT_FOUND":
            actions.append("deploy --step 3  # Create DMS connections")
            return actions

        # No migration yet
        if not mig.dms_migration:
            actions.append("deploy --step 4  # Create DMS migration")
            return actions

        state = mig.dms_migration.state if mig.dms_migration else ""

        if state == "ACTIVE" and not mig.dms_job:
            actions.append(f"assess --source {mig.source_key}  # Pre-flight check")
            actions.append("# Then: start migration via deploy --step 4")

        if state == "IN_PROGRESS":
            if mig.dms_job and mig.dms_job.state == "WAITING":
                actions.append("# Migration at cutover point — replication caught up")
                if mig.has_reverse_replication:
                    actions.append("# Activate GG fallback before cutover")
                actions.append(f"cutover --migration {mig.migration_key}")
            else:
                actions.append("# Migration in progress — monitor lag")
                actions.append(f"status --migration {mig.migration_key}")

        if state == "SUCCEEDED":
            actions.append("# Migration complete")
            if mig.has_reverse_replication:
                actions.append("# GG reverse replication available for rollback if needed")

        if state == "FAILED":
            actions.append(f"diagnose  # Check error in KB")
            actions.append(f"assess --source {mig.source_key}  # Re-validate source")

        return actions

    def _compute_next_global(self, status: FullStatus) -> tuple:
        """Compute the single most important next action."""
        # No infrastructure
        if status.vault and status.vault.error:
            return "Fix OCI connectivity", "python migrate.py probe"

        if status.nsg and status.nsg.state == "NOT_FOUND":
            return "Create infrastructure", "python migrate.py deploy --step 1"

        # Check migrations
        for m in status.migrations:
            if not m.dms_migration:
                return f"Create migration '{m.display_name}'", "python migrate.py deploy"

            if m.dms_migration.state == "FAILED":
                return f"Investigate failed migration '{m.display_name}'", \
                       f"python migrate.py diagnose"

            if m.dms_job and m.dms_job.state == "WAITING":
                return f"Cutover ready for '{m.display_name}'", \
                       f"python migrate.py cutover --migration {m.migration_key}"

        if all(m.dms_migration and m.dms_migration.state == "SUCCEEDED"
               for m in status.migrations if m.dms_migration):
            return "All migrations complete", ""

        return "Check status of active migrations", "python migrate.py status"
