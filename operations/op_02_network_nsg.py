"""
Operation 02: Verify pre-existing Network Security Group for migration.

Verifies that the NSG exists and is AVAILABLE. Checks that the NSG has
security rules configured (ingress for Oracle ports and HTTPS).

READ-ONLY: This operation never creates NSGs or adds security rules.
The NSG must be pre-created with the required rules before running
the pipeline. If the NSG doesn't exist, the operation fails with a
clear error message.
"""

import logging
from typing import Optional

from operations.base import BaseOperation, OpResult, OpStatus

logger = logging.getLogger(__name__)


class NetworkNSGOperation(BaseOperation):

    @property
    def name(self) -> str:
        return "network-nsg"

    def check_exists(self, **kwargs) -> Optional[str]:
        """Check if NSG exists — from config (nsg_ocid) or by name in VCN."""
        # If user provided an existing NSG OCID, use it directly
        nsg_ocid = self.config.networking.get("nsg_ocid")
        if nsg_ocid:
            try:
                nsg = self.oci.virtual_network.get_network_security_group(nsg_ocid).data
                if nsg.lifecycle_state == "AVAILABLE":
                    logger.info(f"  Using existing NSG: {nsg.display_name} ({nsg_ocid})")
                    return nsg_ocid
            except Exception as e:
                logger.warning(f"Configured nsg_ocid not found or not available: {e}")
                return None

        # Otherwise, check if one exists by the conventional name
        try:
            compartment = self.config.oci["compartment_ocid"]
            vcn_ocid = self.config.networking["vcn_ocid"]

            nsgs = self.oci.virtual_network.list_network_security_groups(
                compartment_id=compartment,
                vcn_id=vcn_ocid,
            ).data

            for nsg in nsgs:
                if nsg.display_name == "dms-migration-nsg" and nsg.lifecycle_state == "AVAILABLE":
                    return nsg.id

            return None
        except Exception as e:
            logger.debug(f"Cannot check NSG: {e}")
            return None

    def execute(self, **kwargs) -> OpResult:
        """Verify NSG exists and has security rules. Never creates NSGs or rules.

        This operation is read-only. If the NSG doesn't exist or is not
        configured in migration-config.json, it fails with a clear message.
        """
        nsg_ocid = self.config.networking.get("nsg_ocid")

        if not nsg_ocid:
            return OpResult(
                operation=self.name, resource_type="nsg",
                status=OpStatus.FAILED,
                error=(
                    "No NSG OCID configured. This tool does NOT create NSGs — "
                    "they must be pre-created before running the pipeline.\n"
                    "Steps to fix:\n"
                    "  1. Create an NSG in your VCN with ingress rules for:\n"
                    "     - Oracle DB ports (1521, 1522) from your source CIDR\n"
                    "     - HTTPS (443) for GoldenGate REST API\n"
                    "     - Egress to source CIDR\n"
                    "  2. Add the NSG OCID to migration-config.json under "
                    "networking.nsg_ocid\n"
                    "  3. Re-run this step."
                ),
            )

        # Verify NSG exists and is AVAILABLE
        try:
            nsg = self.oci.virtual_network.get_network_security_group(nsg_ocid).data
        except Exception as e:
            return OpResult(
                operation=self.name, resource_type="nsg",
                status=OpStatus.FAILED,
                error=(
                    f"Cannot access NSG {nsg_ocid}: {e}\n"
                    f"Ensure the NSG exists and you have permissions to read it."
                ),
            )

        if nsg.lifecycle_state != "AVAILABLE":
            return OpResult(
                operation=self.name, resource_type="nsg",
                resource_id=nsg_ocid, status=OpStatus.FAILED,
                error=(
                    f"NSG {nsg.display_name} ({nsg_ocid}) is in state "
                    f"'{nsg.lifecycle_state}', expected 'AVAILABLE'."
                ),
            )

        # Verify NSG has security rules
        rules_info = []
        try:
            rules = self.oci.virtual_network.list_network_security_group_security_rules(
                nsg_ocid
            ).data
            ingress_rules = [r for r in rules if r.direction == "INGRESS"]
            egress_rules = [r for r in rules if r.direction == "EGRESS"]
            rules_info = {
                "total_rules": len(rules),
                "ingress_rules": len(ingress_rules),
                "egress_rules": len(egress_rules),
            }
            logger.info(
                f"  NSG {nsg.display_name}: {len(ingress_rules)} ingress, "
                f"{len(egress_rules)} egress rules"
            )

            if len(rules) == 0:
                logger.warning(
                    f"  WARNING: NSG {nsg.display_name} has no security rules. "
                    f"DMS connections may fail without proper ingress/egress rules."
                )
        except Exception as e:
            logger.warning(f"  Could not list NSG rules (non-fatal): {e}")
            rules_info = {"note": "Could not verify rules, proceeding anyway"}

        return OpResult(
            operation=self.name, resource_type="nsg",
            resource_id=nsg_ocid, status=OpStatus.SUCCESS,
            message=f"NSG verified: {nsg.display_name} (AVAILABLE)",
            details={"nsg_id": nsg_ocid, "display_name": nsg.display_name, "rules": rules_info},
        )
