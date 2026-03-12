"""
Pipeline orchestrator — runs operations in dependency order.

Usage:
    pipeline = Pipeline(config, kb, oci_factory)
    pipeline.run_all()              # Full deployment
    pipeline.run_step(3)            # Only DMS connections
    pipeline.run_from(3)            # From DMS connections onward
    pipeline.status()               # Show current state of all resources
"""

import logging
from typing import Dict, List, Optional, Type

from core.config import MigrationConfig
from core.kb_loader import KnowledgeBase
from core.oci_client import OCIClientFactory
from operations.base import BaseOperation, OpResult, OpStatus

logger = logging.getLogger(__name__)


# Step registry
STEPS: List[Dict] = [
    {
        "step": 1,
        "name": "vault-secrets",
        "description": "Create OCI Vault secrets for all credentials",
        "module": "operations.op_01_vault_secrets",
        "class": "VaultSecretsOperation",
    },
    {
        "step": 2,
        "name": "network-nsg",
        "description": "Create Network Security Group with migration rules",
        "module": "operations.op_02_network_nsg",
        "class": "NetworkNSGOperation",
    },
    {
        "step": 3,
        "name": "dms-connections",
        "description": "Create DMS connections (source + target)",
        "module": "operations.op_03_dms_connections",
        "class": "DMSConnectionsOperation",
    },
    {
        "step": 4,
        "name": "dms-migrations",
        "description": "Create, validate, and start DMS migrations",
        "module": "operations.op_04_dms_migration",
        "class": "DMSMigrationOperation",
    },
    {
        "step": 5,
        "name": "goldengate",
        "description": "Deploy GoldenGate and configure reverse replication",
        "module": "operations.op_05_goldengate",
        "class": "GoldenGateOperation",
    },
    # Steps 6-8 to be implemented:
    # {
    #     "step": 6, "name": "monitoring-alarms",
    #     "description": "Create OCI Monitoring alarms (DMS + GG)",
    # },
    # {
    #     "step": 7, "name": "events-notifications",
    #     "description": "Create OCI Events rules + ONS subscriptions",
    # },
    # {
    #     "step": 8, "name": "logging",
    #     "description": "Configure OCI Logging for DMS and GG",
    # },
]


class Pipeline:
    """Orchestrates migration operations in sequence."""

    def __init__(self, config: MigrationConfig, kb: KnowledgeBase,
                 oci_factory: OCIClientFactory):
        self.config = config
        self.kb = kb
        self.oci = oci_factory
        self.results: List[OpResult] = []

    def _load_operation(self, step_info: Dict) -> BaseOperation:
        """Dynamically load an operation class."""
        import importlib
        module = importlib.import_module(step_info["module"])
        op_class = getattr(module, step_info["class"])
        return op_class(self.config, self.kb, self.oci)

    def run_all(self) -> List[OpResult]:
        """Run all steps in order."""
        return self.run_from(1)

    def run_step(self, step_number: int) -> Optional[OpResult]:
        """Run a single step."""
        step_info = next((s for s in STEPS if s["step"] == step_number), None)
        if not step_info:
            logger.error(f"Unknown step: {step_number}")
            return None

        if "module" not in step_info:
            logger.warning(f"Step {step_number} ({step_info['name']}) not yet implemented")
            return OpResult(
                operation=step_info["name"],
                resource_type=step_info["name"],
                status=OpStatus.SKIPPED,
                message="Not yet implemented",
            )

        logger.info(f"\n{'='*60}")
        logger.info(f"Step {step_number}: {step_info['description']}")
        logger.info(f"{'='*60}")

        op = self._load_operation(step_info)
        result = op.run()

        self.results.append(result)
        self._log_result(result)
        return result

    def run_from(self, start_step: int) -> List[OpResult]:
        """Run all steps from start_step onward."""
        results = []
        for step_info in STEPS:
            if step_info["step"] < start_step:
                continue

            result = self.run_step(step_info["step"])
            if result:
                results.append(result)

            # Stop on failure
            if result and result.status == OpStatus.FAILED:
                logger.error(f"\nPipeline stopped at step {step_info['step']} due to failure.")
                if result.kb_diagnosis:
                    logger.info(f"KB diagnosis:\n{result.kb_diagnosis}")
                break

        self._print_summary(results)
        return results

    def list_steps(self):
        """Print available steps."""
        print("\nAvailable pipeline steps:")
        print(f"{'Step':>4}  {'Name':<25} {'Status':<12} Description")
        print(f"{'─'*4}  {'─'*25} {'─'*12} {'─'*40}")

        for step_info in STEPS:
            implemented = "✅" if "module" in step_info else "🚧"
            print(
                f"{step_info['step']:>4}  "
                f"{step_info['name']:<25} "
                f"{implemented:<12} "
                f"{step_info['description']}"
            )
        print()

    def _log_result(self, result: OpResult):
        """Log operation result."""
        symbols = {
            OpStatus.SUCCESS: "✅",
            OpStatus.CREATED: "✅",
            OpStatus.SKIPPED: "⏭️ ",
            OpStatus.FAILED: "❌",
            OpStatus.WAITING: "⏳",
        }
        symbol = symbols.get(result.status, "?")
        msg = result.message or result.error or result.status.value
        logger.info(f"  {symbol} [{result.operation}] {msg}")

        if result.status == OpStatus.FAILED and result.kb_diagnosis:
            for line in result.kb_diagnosis.splitlines():
                logger.info(f"     💡 {line}")

    def _print_summary(self, results: List[OpResult]):
        """Print pipeline execution summary."""
        print(f"\n{'='*60}")
        print("Pipeline Summary")
        print(f"{'='*60}")

        for r in results:
            symbols = {
                OpStatus.SUCCESS: "✅", OpStatus.CREATED: "✅",
                OpStatus.SKIPPED: "⏭️ ", OpStatus.FAILED: "❌",
            }
            sym = symbols.get(r.status, "?")
            print(f"  {sym} {r.operation}: {r.status.value} — {r.message or r.error or ''}")

        succeeded = sum(1 for r in results if r.status in (OpStatus.SUCCESS, OpStatus.CREATED, OpStatus.SKIPPED))
        failed = sum(1 for r in results if r.status == OpStatus.FAILED)
        print(f"\n  Total: {succeeded} OK, {failed} failed")
