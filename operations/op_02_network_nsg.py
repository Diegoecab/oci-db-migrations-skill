"""
Operation 02: Create Network Security Group with migration rules.

Creates NSG in the target VCN with ingress/egress rules for:
  - Oracle DB ports (1521, 1522) from source CIDR
  - HTTPS (443) for GoldenGate REST API
"""

import logging
from typing import Optional

from operations.base import BaseOperation, OpResult, OpStatus

logger = logging.getLogger(__name__)


class NetworkNSGOperation(BaseOperation):

    NSG_DISPLAY_NAME = "dms-migration-nsg"

    @property
    def name(self) -> str:
        return "network-nsg"

    def check_exists(self, **kwargs) -> Optional[str]:
        """Check if NSG already exists — either from config (nsg_ocid) or by name in VCN."""
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

        # Otherwise, check if we previously created one by name
        try:
            compartment = self.config.oci["compartment_ocid"]
            vcn_ocid = self.config.networking["vcn_ocid"]

            nsgs = self.oci.virtual_network.list_network_security_groups(
                compartment_id=compartment,
                vcn_id=vcn_ocid,
            ).data

            for nsg in nsgs:
                if nsg.display_name == self.NSG_DISPLAY_NAME and nsg.lifecycle_state == "AVAILABLE":
                    return nsg.id

            return None
        except Exception as e:
            logger.debug(f"Cannot check NSG: {e}")
            return None

    def execute(self, **kwargs) -> OpResult:
        """Create NSG with migration rules. Skipped if nsg_ocid is set in config."""
        # If user provided an existing NSG, skip creation entirely
        nsg_ocid = self.config.networking.get("nsg_ocid")
        if nsg_ocid:
            return OpResult(
                operation=self.name, resource_type="nsg",
                resource_id=nsg_ocid, status=OpStatus.EXISTS,
                message=f"Using existing NSG from config: {nsg_ocid}",
            )
        """Create NSG with migration rules."""
        import oci

        compartment = self.config.oci["compartment_ocid"]
        vcn_ocid = self.config.networking["vcn_ocid"]
        nsg_rules = self.config.networking.get("nsg_rules", {})
        source_cidr = nsg_rules.get("source_cidr", "10.0.0.0/16")
        oracle_ports = nsg_rules.get("oracle_ports", [1521, 1522])
        https_port = nsg_rules.get("https_port", 443)

        # Create NSG
        nsg_details = oci.core.models.CreateNetworkSecurityGroupDetails(
            compartment_id=compartment,
            vcn_id=vcn_ocid,
            display_name=self.NSG_DISPLAY_NAME,
        )

        nsg = self.oci.virtual_network.create_network_security_group(nsg_details).data
        nsg_id = nsg.id
        logger.info(f"  Created NSG: {nsg_id}")

        # Wait for AVAILABLE
        if not self.wait_for_state(
            self.oci.virtual_network.get_network_security_group,
            nsg_id, "AVAILABLE", max_wait=120
        ):
            return OpResult(
                operation=self.name, resource_type="nsg",
                resource_id=nsg_id, status=OpStatus.FAILED,
                error="NSG did not reach AVAILABLE state",
            )

        # Add security rules
        rules = []

        # Ingress rules for Oracle ports
        for port in oracle_ports:
            rules.append(oci.core.models.AddSecurityRuleDetails(
                direction="INGRESS",
                protocol="6",  # TCP
                source=source_cidr,
                source_type="CIDR_BLOCK",
                tcp_options=oci.core.models.TcpOptions(
                    destination_port_range=oci.core.models.PortRange(
                        min=port, max=port
                    )
                ),
                description=f"Oracle DB port {port} from source",
            ))

        # Ingress HTTPS for GoldenGate
        rules.append(oci.core.models.AddSecurityRuleDetails(
            direction="INGRESS",
            protocol="6",
            source=source_cidr,
            source_type="CIDR_BLOCK",
            tcp_options=oci.core.models.TcpOptions(
                destination_port_range=oci.core.models.PortRange(
                    min=https_port, max=https_port
                )
            ),
            description="HTTPS for GoldenGate REST API",
        ))

        # Egress: allow all within VCN
        rules.append(oci.core.models.AddSecurityRuleDetails(
            direction="EGRESS",
            protocol="all",
            destination=source_cidr,
            destination_type="CIDR_BLOCK",
            description="Allow all egress to source CIDR",
        ))

        self.oci.virtual_network.add_network_security_group_security_rules(
            nsg_id,
            oci.core.models.AddNetworkSecurityGroupSecurityRulesDetails(
                security_rules=rules
            ),
        )
        logger.info(f"  Added {len(rules)} security rules")

        return OpResult(
            operation=self.name, resource_type="nsg",
            resource_id=nsg_id, status=OpStatus.CREATED,
            message=f"NSG created with {len(rules)} rules",
            details={"nsg_id": nsg_id, "rules_count": len(rules)},
        )
