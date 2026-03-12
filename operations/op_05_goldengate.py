"""
Operation 05: GoldenGate deployment and reverse replication setup.

1. Provision managed GoldenGate deployment
2. Create connections (source + target)
3. Assign connections to deployment
4. Create Extract + Replicat processes via REST API (for fallback)
"""

import hashlib
import json
import logging
import time
from typing import Dict, Optional

from operations.base import BaseOperation, OpResult, OpStatus

logger = logging.getLogger(__name__)


class GoldenGateOperation(BaseOperation):

    @property
    def name(self) -> str:
        return "goldengate-deployment"

    def check_exists(self, **kwargs) -> Optional[str]:
        """Check if GG deployment exists."""
        try:
            compartment = self.config.oci["compartment_ocid"]
            gg_name = self.config.goldengate.get("deployment_name", "gg-migration-fallback")

            deployments = self.oci.goldengate().list_deployments(
                compartment_id=compartment,
            ).data.items

            for d in deployments:
                if d.display_name == gg_name and d.lifecycle_state in ("ACTIVE", "CREATING"):
                    return d.id

            return None
        except Exception as e:
            logger.debug(f"Cannot check GG deployment: {e}")
            return None

    def execute(self, **kwargs) -> OpResult:
        """Create GG deployment and configure reverse replication."""
        if not self.config.has_reverse_replication():
            return OpResult(
                operation=self.name, resource_type="goldengate",
                status=OpStatus.SKIPPED,
                message="No migration has enable_reverse_replication=true",
            )

        import oci

        compartment = self.config.oci["compartment_ocid"]
        subnet_ocid = self.config.networking["subnet_ocid"]
        gg_config = self.config.goldengate

        # 1. Create deployment
        logger.info("  Creating GoldenGate deployment...")
        deploy_details = oci.golden_gate.models.CreateDeploymentDetails(
            compartment_id=compartment,
            display_name=gg_config.get("display_name", "GoldenGate Migration Fallback"),
            deployment_type="DATABASE_ORACLE",
            license_model=gg_config.get("license_model", "LICENSE_INCLUDED"),
            cpu_core_count=gg_config.get("cpu_core_count", 1),
            is_auto_scaling_enabled=gg_config.get("is_auto_scaling_enabled", True),
            subnet_id=subnet_ocid,
            ogg_data=oci.golden_gate.models.OggDeployment(
                admin_username=gg_config.get("admin_username", "oggadmin"),
                admin_password=gg_config.get("admin_password"),
                deployment_name=gg_config.get("deployment_name", "gg-migration-fallback"),
            ),
        )

        try:
            response = self.oci.goldengate().create_deployment(deploy_details)
            deploy_id = response.data.id
            logger.info(f"  Deployment created: {deploy_id}")

            # Wait for ACTIVE (can take 15-30 min)
            logger.info("  Waiting for deployment ACTIVE (this may take 15-30 min)...")
            if not self.wait_for_state(
                self.oci.goldengate().get_deployment,
                deploy_id, "ACTIVE", max_wait=2400, interval=30
            ):
                return OpResult(
                    operation=self.name, resource_type="goldengate",
                    resource_id=deploy_id, status=OpStatus.FAILED,
                    error="Deployment did not reach ACTIVE state",
                )

        except Exception as e:
            return OpResult(
                operation=self.name, resource_type="goldengate",
                status=OpStatus.FAILED, error=str(e),
            )

        # 2. Create GG connections for each migration with reverse replication
        gg_connections_created = []

        for mig_key, mig in self.config.migrations.items():
            if not mig.get("enable_reverse_replication", False):
                continue

            src = self.config.source_db(mig["source_db_key"])
            tgt = self.config.target_db(mig["target_db_key"])

            # Source connection (Oracle)
            src_conn_name = f"gg-src-{mig['source_db_key']}"
            try:
                src_conn_details = oci.golden_gate.models.CreateConnectionDetails(
                    compartment_id=compartment,
                    display_name=src_conn_name,
                    connection_type="ORACLE",
                    technology_type="ORACLE_DATABASE",
                    connection_string=(
                        f"{src['host']}:{src['port']}/{src['service_name']}"
                    ),
                    username=src.get("gg_username", "GGADMIN"),
                    password=src.get("gg_password"),
                    subnet_id=subnet_ocid,
                )
                src_conn = self.oci.goldengate().create_connection(src_conn_details).data
                gg_connections_created.append(src_conn_name)
                logger.info(f"  GG source connection: {src_conn_name}")
            except Exception as e:
                logger.warning(f"  GG source connection failed: {e}")

            # Target connection (ADB)
            tgt_conn_name = f"gg-tgt-{mig['target_db_key']}"
            try:
                tgt_conn_details = oci.golden_gate.models.CreateConnectionDetails(
                    compartment_id=compartment,
                    display_name=tgt_conn_name,
                    connection_type="ORACLE",
                    technology_type="ORACLE_AUTONOMOUS_DATABASE",
                    database_id=tgt["adb_ocid"],
                    username=tgt.get("gg_username", "GGADMIN"),
                    password=tgt.get("gg_password"),
                    subnet_id=subnet_ocid,
                )
                tgt_conn = self.oci.goldengate().create_connection(tgt_conn_details).data
                gg_connections_created.append(tgt_conn_name)
                logger.info(f"  GG target connection: {tgt_conn_name}")
            except Exception as e:
                logger.warning(f"  GG target connection failed: {e}")

        return OpResult(
            operation=self.name, resource_type="goldengate",
            resource_id=deploy_id, status=OpStatus.CREATED,
            message=f"Deployment active, {len(gg_connections_created)} GG connections created",
            details={
                "deployment_id": deploy_id,
                "connections": gg_connections_created,
            },
        )

    @staticmethod
    def generate_process_name(migration_key: str, prefix: str = "EX") -> str:
        """Generate 8-char GoldenGate process name from migration key."""
        hash_hex = hashlib.md5(migration_key.encode()).hexdigest()[:6]
        return f"{prefix}{hash_hex}".upper()[:8]
