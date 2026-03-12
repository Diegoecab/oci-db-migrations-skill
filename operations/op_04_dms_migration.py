"""
Operation 04: Create DMS migrations, validate, and optionally start.

For each migration in config:
  1. Create migration with object mappings
  2. Auto-validate (if enabled)
  3. Auto-start (if enabled)

Handles ONLINE and OFFLINE types.

Compatible with both SDK versions:
  - New SDK: CreateOracleMigrationDetails + OracleDatabaseObject
  - Old SDK: CreateMigrationDetails + DatabaseObject
"""

import logging
import re
from typing import Dict, List, Optional

from operations.base import BaseOperation, OpResult, OpStatus

logger = logging.getLogger(__name__)


def _get_migration_model(oci_models):
    """Get the correct migration creation model for the SDK version."""
    if hasattr(oci_models, 'CreateOracleMigrationDetails'):
        return oci_models.CreateOracleMigrationDetails
    return oci_models.CreateMigrationDetails


def _get_db_object_model(oci_models):
    """Get the correct database object model for the SDK version."""
    if hasattr(oci_models, 'OracleDatabaseObject'):
        return oci_models.OracleDatabaseObject
    return oci_models.DatabaseObject


class DMSMigrationOperation(BaseOperation):

    @property
    def name(self) -> str:
        return "dms-migrations"

    def check_exists(self, **kwargs) -> Optional[str]:
        """Check if all migrations exist."""
        try:
            compartment = self.config.oci["compartment_ocid"]
            migrations = self.oci.dms.list_migrations(
                compartment_id=compartment,
            ).data.items

            existing = {m.display_name: m for m in migrations
                        if m.lifecycle_state not in ("DELETED", "DELETING")}

            expected = {m.get("display_name", k) for k, m in self.config.migrations.items()}

            if expected.issubset(existing.keys()):
                return "all-migrations-exist"
            return None

        except Exception as e:
            logger.debug(f"Cannot check migrations: {e}")
            return None

    def execute(self, **kwargs) -> OpResult:
        """Create migrations."""
        import oci

        compartment = self.config.oci["compartment_ocid"]

        # Resolve DMS connection OCIDs
        connections = self.oci.dms.list_connections(
            compartment_id=compartment).data.items
        conn_map = {c.display_name: c.id for c in connections
                    if c.lifecycle_state == "ACTIVE"}

        # Get existing migrations
        existing_migrations = {}
        try:
            migs = self.oci.dms.list_migrations(
                compartment_id=compartment).data.items
            existing_migrations = {
                m.display_name: m for m in migs
                if m.lifecycle_state not in ("DELETED", "DELETING")
            }
        except Exception:
            pass

        created = []
        validated = []
        started = []
        errors = []

        for mig_key, mig in self.config.migrations.items():
            display_name = mig.get("display_name", mig_key)

            if display_name in existing_migrations:
                logger.info(f"  Migration exists: {display_name}")
                continue

            src_conn_name = f"dms-src-{mig['source_db_key']}"
            tgt_conn_name = f"dms-tgt-{mig['target_db_key']}"

            src_conn_id = conn_map.get(src_conn_name)
            tgt_conn_id = conn_map.get(tgt_conn_name)

            if not src_conn_id:
                errors.append(f"{mig_key}: source connection '{src_conn_name}' not found")
                continue
            if not tgt_conn_id:
                errors.append(f"{mig_key}: target connection '{tgt_conn_name}' not found")
                continue

            # Build object mapping
            include_objects = self._parse_object_list(mig.get("include_allow_objects", []))
            exclude_objects = self._parse_object_list(mig.get("exclude_objects", []))

            # Data transfer config
            dp_params = mig.get("datapump_parameters", {})
            tablespace_remap = mig.get("tablespace_remap", {})

            try:
                # Build migration details
                migration_kwargs = {
                    "compartment_id": compartment,
                    "display_name": display_name,
                    "type": mig["migration_type"],
                    "source_database_connection_id": src_conn_id,
                    "target_database_connection_id": tgt_conn_id,
                }

                # Add GoldenGate settings for ONLINE
                if mig["migration_type"] == "ONLINE":
                    gg_settings = {
                        "acceptable_lag": self.config.monitoring.get("thresholds", {}).get(
                            "lag_critical_seconds", 300
                        ),
                    }

                    # Source GG config
                    src_db = self.config.source_db(mig["source_db_key"])
                    if src_db.get("gg_username"):
                        gg_settings["extract"] = {
                            "performance_profile": "MEDIUM",
                        }

                # Include/exclude objects
                DbObjectModel = _get_db_object_model(oci.database_migration.models)
                if include_objects:
                    migration_kwargs["include_objects"] = [
                        DbObjectModel(**obj)
                        for obj in include_objects
                    ]
                elif exclude_objects:
                    migration_kwargs["exclude_objects"] = [
                        DbObjectModel(**obj)
                        for obj in exclude_objects
                    ]

                # CDB connection (for multitenant source)
                src_cdb_key = mig.get("source_cdb_key")
                if src_cdb_key:
                    cdb_conn_name = f"dms-src-{src_cdb_key}"
                    cdb_conn_id = conn_map.get(cdb_conn_name)
                    if cdb_conn_id:
                        migration_kwargs["source_container_database_connection_id"] = cdb_conn_id

                MigrationModel = _get_migration_model(oci.database_migration.models)
                details = MigrationModel(**migration_kwargs)

                response = self.oci.dms.create_migration(details)
                mig_id = response.data.id
                created.append(mig_key)
                logger.info(f"  Created migration: {display_name} ({mig_id})")

                # Wait for ACTIVE/READY
                self.wait_for_state(
                    self.oci.dms.get_migration, mig_id, "ACTIVE",
                    max_wait=600, interval=15
                )

                # Auto-validate
                if mig.get("auto_validate", False):
                    try:
                        logger.info(f"  Validating: {display_name}...")
                        self.oci.dms.evaluate_migration(mig_id)
                        validated.append(mig_key)

                        # Wait for validation work request
                        self.wait_for_state(
                            self.oci.dms.get_migration, mig_id, "ACTIVE",
                            max_wait=1200, interval=20
                        )
                    except Exception as e:
                        logger.warning(f"  Validation failed: {e}")

                # Auto-start
                if mig.get("auto_start", False):
                    try:
                        logger.info(f"  Starting: {display_name}...")
                        self.oci.dms.start_migration(mig_id)
                        started.append(mig_key)
                    except Exception as e:
                        logger.warning(f"  Start failed: {e}")

            except Exception as e:
                errors.append(f"{mig_key}: {e}")
                logger.error(f"  Failed: {mig_key}: {e}")

        if errors:
            return OpResult(
                operation=self.name, resource_type="dms_migration",
                status=OpStatus.FAILED, error="; ".join(errors),
                details={"created": created, "validated": validated,
                         "started": started, "errors": errors},
            )

        return OpResult(
            operation=self.name, resource_type="dms_migration",
            status=OpStatus.CREATED if created else OpStatus.SKIPPED,
            message=f"Created {len(created)}, validated {len(validated)}, started {len(started)}",
            details={"created": created, "validated": validated, "started": started},
        )

    @staticmethod
    def _parse_object_list(objects: List[str]) -> List[Dict]:
        """Parse 'SCHEMA.*' and 'SCHEMA.TABLE' format into DMS object dicts."""
        parsed = []
        for obj in objects:
            parts = obj.split(".", 1)
            if len(parts) != 2:
                continue

            owner, obj_name = parts[0].upper(), parts[1]

            if obj_name == "*":
                parsed.append({
                    "owner": owner,
                    "object_name": ".*",
                    "type": "ALL",
                })
            else:
                parsed.append({
                    "owner": owner,
                    "object_name": obj_name.upper(),
                    "type": "TABLE",
                })

        return parsed
