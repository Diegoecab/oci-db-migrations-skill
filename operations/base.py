"""
Operations base module.

Provides the BaseOperation class with idempotent patterns:
  1. Check if resource exists → skip if already OK
  2. Create → wait for target state
  3. If error → consult KB → show diagnosis

All operations inherit from this.
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core.config import MigrationConfig
from core.kb_loader import KnowledgeBase
from core.oci_client import OCIClientFactory

logger = logging.getLogger(__name__)


class OpStatus(Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"      # Already exists in desired state
    CREATED = "created"      # Newly created
    FAILED = "failed"
    WAITING = "waiting"


@dataclass
class OpResult:
    """Result of a single operation."""
    operation: str
    resource_type: str
    resource_id: Optional[str] = None
    status: OpStatus = OpStatus.SUCCESS
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    kb_diagnosis: Optional[str] = None  # From KB error lookup


class BaseOperation(ABC):
    """
    Base class for all migration operations.

    Implements the idempotent pattern:
      check_exists() → True  → log skip, return SKIPPED
      check_exists() → False → execute() → wait_ready() → return CREATED
      Any error → kb.lookup_error() → return FAILED with diagnosis
    """

    def __init__(self, config: MigrationConfig, kb: KnowledgeBase,
                 oci: OCIClientFactory):
        self.config = config
        self.kb = kb
        self.oci = oci

    @property
    @abstractmethod
    def name(self) -> str:
        """Operation display name."""
        pass

    @abstractmethod
    def check_exists(self, **kwargs) -> Optional[str]:
        """
        Check if resource already exists.
        Returns resource OCID if exists, None otherwise.
        """
        pass

    @abstractmethod
    def execute(self, **kwargs) -> OpResult:
        """Create the resource."""
        pass

    def run(self, **kwargs) -> OpResult:
        """
        Run with idempotent pattern.
        Override this only if you need a completely custom flow.
        """
        logger.info(f"[{self.name}] Starting...")

        try:
            existing_id = self.check_exists(**kwargs)
            if existing_id:
                logger.info(f"[{self.name}] Already exists: {existing_id}")
                return OpResult(
                    operation=self.name,
                    resource_type=self._resource_type(),
                    resource_id=existing_id,
                    status=OpStatus.SKIPPED,
                    message="Resource already exists",
                )

            result = self.execute(**kwargs)

            if result.status == OpStatus.FAILED and result.error:
                diagnosis = self.kb.lookup_error(result.error)
                if diagnosis:
                    result.kb_diagnosis = (
                        f"KB Match: {diagnosis.get('description', '')}\n"
                        f"Fix: {diagnosis.get('fix', '')}"
                    )
                    logger.error(f"[{self.name}] {result.kb_diagnosis}")

            return result

        except Exception as e:
            error_str = str(e)
            diagnosis = self.kb.lookup_error(error_str)
            kb_msg = None
            if diagnosis:
                kb_msg = f"KB: {diagnosis.get('description', '')} → {diagnosis.get('fix', '')}"
                logger.error(f"[{self.name}] {kb_msg}")

            return OpResult(
                operation=self.name,
                resource_type=self._resource_type(),
                status=OpStatus.FAILED,
                error=error_str,
                kb_diagnosis=kb_msg,
            )

    def wait_for_state(self, get_fn, resource_id: str, target_state: str,
                       max_wait: int = 1800, interval: int = 15) -> bool:
        """Poll until resource reaches target state."""
        elapsed = 0
        while elapsed < max_wait:
            try:
                response = get_fn(resource_id)
                current = response.data.lifecycle_state
                logger.debug(f"[{self.name}] State: {current} (target: {target_state})")

                if current == target_state:
                    return True
                if current in ("FAILED", "TERMINATED", "DELETED"):
                    logger.error(f"[{self.name}] Terminal state: {current}")
                    return False
            except Exception as e:
                logger.warning(f"[{self.name}] Poll error: {e}")

            time.sleep(interval)
            elapsed += interval

        logger.error(f"[{self.name}] Timeout after {max_wait}s waiting for {target_state}")
        return False

    def _resource_type(self) -> str:
        return self.__class__.__name__.replace("Operation", "").lower()
