"""
Operation 03: Create DMS connections for source and target databases.

Creates:
  - Source connection (Oracle DB with private endpoint)
  - Target connection (ADB with private endpoint)

Uses OCI Vault secrets for credentials (verified in step 01).

Compatible with both old and new OCI SDK versions:
  - New SDK (>=2.120): CreateOracleConnectionDetails with technology_type, connection_string
  - Old SDK: CreateConnectionDetails with database_type + manual_database_sub_type
"""

import logging
from typing import Optional

from operations.base import BaseOperation, OpResult, OpStatus

logger = logging.getLogger(__name__)


def _create_oracle_connection_details(oci_models, **kwargs):
    """Create Oracle connection details compatible with both SDK versions."""
    # Try new-style subclass first (SDK >= 2.100+)
    if hasattr(oci_models, 'CreateOracleConnectionDetails'):
        return oci_models.CreateOracleConnectionDetails(**kwargs)

    # Fallback to old-style base class
    old_kwargs = dict(kwargs)
    old_kwargs.pop('technology_type', None)
    old_kwargs['database_type'] = 'ORACLE'
    return oci_models.CreateConnectionDetails(**old_kwargs)


def _delete_and_wait(dms_client, conn_id, conn_name, max_wait=300, interval=10):
    """Delete a DMS connection and wait for it to be gone."""
    import time
    dms_client.delete_connection(conn_id)
    elapsed = 0
    while elapsed < max_wait:
        try:
            c = dms_client.get_connection(conn_id).data
            if c.lifecycle_state in ("DELETED", "FAILED"):
                return True
        except Exception:
            # 404 = deleted
            return True
        time.sleep(interval)
        elapsed += interval
    logger.error(f"  Timeout waiting for {conn_name} deletion")
    return False


class DMSConnectionsOperation(BaseOperation):

    @property
    def name(self) -> str:
        return "dms-connections"

    def check_exists(self, **kwargs) -> Optional[str]:
        """Check if all required DMS connections exist and are properly configured."""
        try:
            compartment = self.config.oci["compartment_ocid"]
            connections = self.oci.dms.list_connections(
                compartment_id=compartment,
            ).data.items

            existing = {c.display_name: c.id for c in connections
                        if c.lifecycle_state in ("ACTIVE", "CREATING")}

            expected_names = set()
            for key in self.config.source_databases:
                expected_names.add(f"dms-src-{key}")
            for key in self.config.target_databases:
                expected_names.add(f"dms-tgt-{key}")
            # Include CDB container connections
            source_containers = getattr(self.config, '_raw', {}).get("source_container_databases", {})
            for key in source_containers:
                expected_names.add(f"dms-src-{key}")

            if not expected_names.issubset(existing.keys()):
                return None

            # All connections exist — check if any need replication_username update
            for key, src in self.config.source_databases.items():
                conn_name = f"dms-src-{key}"
                if conn_name in existing and src.get("gg_username"):
                    conn = self.oci.dms.get_connection(existing[conn_name]).data
                    if getattr(conn, "replication_username", None) != src["gg_username"]:
                        logger.info(f"  {conn_name} needs replication_username update")
                        return None  # Force execute to run

            return "all-connections-exist"

        except Exception as e:
            logger.debug(f"Cannot check connections: {e}")
            return None

    def execute(self, **kwargs) -> OpResult:
        """Create missing DMS connections."""
        import oci

        compartment = self.config.oci["compartment_ocid"]
        subnet_ocid = self.config.networking["subnet_ocid"]
        vault_ocid = self.config.vault["vault_ocid"]
        key_ocid = self.config.vault["key_ocid"]

        # Get existing connections
        existing_connections = {}
        try:
            connections = self.oci.dms.list_connections(
                compartment_id=compartment).data.items
            existing_connections = {
                c.display_name: c.id for c in connections
                if c.lifecycle_state in ("ACTIVE", "CREATING")
            }
        except Exception:
            pass

        # Resolve secret OCIDs
        secrets_client = oci.vault.VaultsClient(self.oci.config)
        secrets_list = secrets_client.list_secrets(
            compartment_id=compartment,
            vault_id=vault_ocid,
            lifecycle_state="ACTIVE",
        ).data
        secret_map = {s.secret_name: s.id for s in secrets_list}

        created = []
        errors = []

        # Source connections
        for key, src in self.config.source_databases.items():
            conn_name = f"dms-src-{key}"
            if conn_name in existing_connections:
                # Check if replication_username needs to be set
                existing_id = existing_connections[conn_name]
                try:
                    existing_conn = self.oci.dms.get_connection(existing_id).data
                    needs_recreate = (
                        src.get("gg_username")
                        and getattr(existing_conn, "replication_username", None) != src["gg_username"]
                    )
                    if needs_recreate:
                        # DMS API silently ignores replication_username on update,
                        # so we must delete and recreate the connection.
                        logger.info(f"  {conn_name} needs replication_username — deleting to recreate...")
                        if not _delete_and_wait(self.oci.dms, existing_id, conn_name):
                            errors.append(f"{conn_name}: timeout deleting for recreate")
                            continue
                        del existing_connections[conn_name]
                        # Fall through to creation below
                    else:
                        logger.info(f"  Source connection exists: {conn_name}")
                        continue
                except Exception as e:
                    logger.warning(f"  Could not check {conn_name}: {e}")
                    continue

            password_secret = secret_map.get(f"dms-src-{key}-password")
            if not password_secret:
                errors.append(f"{conn_name}: password secret not found in vault")
                continue

            try:
                nsg_ocid = self.config.networking.get("nsg_ocid")
                nsg_ids = [nsg_ocid] if nsg_ocid else []

                conn_kwargs = dict(
                    compartment_id=compartment,
                    display_name=conn_name,
                    technology_type="ORACLE_DATABASE",
                    connection_string=(
                        f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)"
                        f"(HOST={src['hostname']})(PORT={src['port']}))"
                        f"(CONNECT_DATA=(SERVICE_NAME={src['service_name']})))"
                    ),
                    username=src["username"],
                    password=src["password"],
                    vault_id=vault_ocid,
                    key_id=key_ocid,
                    subnet_id=subnet_ocid,
                    nsg_ids=nsg_ids,
                )

                # Add replication credentials for ONLINE migrations (GoldenGate CDC)
                if src.get("gg_username"):
                    conn_kwargs["replication_username"] = src["gg_username"]
                    conn_kwargs["replication_password"] = src["gg_password"]

                details = _create_oracle_connection_details(
                    oci.database_migration.models, **conn_kwargs)

                response = self.oci.dms.create_connection(details)
                conn_id = response.data.id
                created.append(conn_name)
                logger.info(f"  Created source connection: {conn_name} ({conn_id})")

                # Wait for ACTIVE
                self.wait_for_state(
                    self.oci.dms.get_connection, conn_id, "ACTIVE",
                    max_wait=600, interval=15
                )

            except Exception as e:
                errors.append(f"{conn_name}: {e}")
                logger.error(f"  Failed source connection {conn_name}: {e}")

        # Source container (CDB) connections — for multitenant
        source_containers = getattr(self.config, '_raw', {}).get("source_container_databases", {})
        for key, cdb in source_containers.items():
            conn_name = f"dms-src-{key}"
            if conn_name in existing_connections:
                logger.info(f"  CDB connection exists: {conn_name}")
                continue

            try:
                nsg_ocid = self.config.networking.get("nsg_ocid")
                nsg_ids = [nsg_ocid] if nsg_ocid else []

                details = _create_oracle_connection_details(
                    oci.database_migration.models,
                    compartment_id=compartment,
                    display_name=conn_name,
                    technology_type="ORACLE_DATABASE",
                    connection_string=(
                        f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)"
                        f"(HOST={cdb['hostname']})(PORT={cdb['port']}))"
                        f"(CONNECT_DATA=(SERVICE_NAME={cdb['service_name']})))"
                    ),
                    username=cdb["username"],
                    password=cdb["password"],
                    vault_id=vault_ocid,
                    key_id=key_ocid,
                    subnet_id=subnet_ocid,
                    nsg_ids=nsg_ids,
                )

                response = self.oci.dms.create_connection(details)
                conn_id = response.data.id
                created.append(conn_name)
                logger.info(f"  Created CDB connection: {conn_name} ({conn_id})")

                self.wait_for_state(
                    self.oci.dms.get_connection, conn_id, "ACTIVE",
                    max_wait=600, interval=15
                )

            except Exception as e:
                errors.append(f"{conn_name}: {e}")
                logger.error(f"  Failed CDB connection {conn_name}: {e}")

        # Target connections (ADB)
        for key, tgt in self.config.target_databases.items():
            conn_name = f"dms-tgt-{key}"
            if conn_name in existing_connections:
                logger.info(f"  Target connection exists: {conn_name}")
                continue

            try:
                nsg_ocid = self.config.networking.get("nsg_ocid")
                nsg_ids = [nsg_ocid] if nsg_ocid else []

                conn_kwargs = dict(
                    compartment_id=compartment,
                    display_name=conn_name,
                    technology_type="OCI_AUTONOMOUS_DATABASE",
                    database_id=tgt["adb_ocid"],
                    username=tgt["username"],
                    password=tgt["password"],
                    vault_id=vault_ocid,
                    key_id=key_ocid,
                    subnet_id=subnet_ocid,
                    nsg_ids=nsg_ids,
                )

                # Add replication credentials for ONLINE migrations (GoldenGate CDC)
                if tgt.get("gg_username"):
                    conn_kwargs["replication_username"] = tgt["gg_username"]
                    conn_kwargs["replication_password"] = tgt["gg_password"]

                details = _create_oracle_connection_details(
                    oci.database_migration.models, **conn_kwargs)

                response = self.oci.dms.create_connection(details)
                conn_id = response.data.id
                created.append(conn_name)
                logger.info(f"  Created target connection: {conn_name} ({conn_id})")

                self.wait_for_state(
                    self.oci.dms.get_connection, conn_id, "ACTIVE",
                    max_wait=600, interval=15
                )

            except Exception as e:
                errors.append(f"{conn_name}: {e}")
                logger.error(f"  Failed target connection {conn_name}: {e}")

        if errors:
            return OpResult(
                operation=self.name, resource_type="dms_connection",
                status=OpStatus.FAILED,
                error="; ".join(errors),
                details={"created": created, "errors": errors},
            )

        return OpResult(
            operation=self.name, resource_type="dms_connection",
            status=OpStatus.CREATED if created else OpStatus.SKIPPED,
            message=f"Created {len(created)} connections",
            details={"created": created},
        )
