"""
OCI SDK client factory.

Creates properly configured OCI service clients.
Falls back to CLI subprocess for operations with known SDK bugs.
"""

import json
import logging
import os
import subprocess
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import oci
    HAS_SDK = True
except ImportError:
    HAS_SDK = False


class OCIClientFactory:
    """Creates and caches OCI service clients."""

    def __init__(self, config_profile: str = "DEFAULT", region: Optional[str] = None):
        self.config_profile = config_profile
        self.region_override = region
        self._config = None
        self._clients: Dict[str, Any] = {}

    @property
    def config(self):
        """Lazy-load OCI config."""
        if self._config is None:
            if not HAS_SDK:
                raise ImportError("oci package not installed. Run: pip install oci")
            self._config = oci.config.from_file(profile_name=self.config_profile)
            if self.region_override:
                self._config["region"] = self.region_override
            oci.config.validate_config(self._config)
        return self._config

    def _get_client(self, client_class, cache_key: str, **kwargs):
        """Get or create a cached client."""
        if cache_key not in self._clients:
            self._clients[cache_key] = client_class(self.config, **kwargs)
        return self._clients[cache_key]

    # ---- Service clients ----
    @property
    def database(self):
        return self._get_client(oci.database.DatabaseClient, "database")

    @property
    def dms(self):
        return self._get_client(
            oci.database_migration.DatabaseMigrationClient, "dms"
        )

    @property
    def identity(self):
        return self._get_client(oci.identity.IdentityClient, "identity")

    @property
    def object_storage(self):
        return self._get_client(
            oci.object_storage.ObjectStorageClient, "object_storage"
        )

    @property
    def virtual_network(self):
        return self._get_client(
            oci.core.VirtualNetworkClient, "virtual_network"
        )

    @property
    def monitoring(self):
        return self._get_client(oci.monitoring.MonitoringClient, "monitoring")

    @property
    def events(self):
        return self._get_client(oci.events.EventsClient, "events")

    @property
    def ons(self):
        return self._get_client(oci.ons.NotificationControlPlaneClient, "ons")

    @property
    def logging_mgmt(self):
        return self._get_client(
            oci.logging.LoggingManagementClient, "logging_mgmt"
        )

    def kms_vault(self, vault_ocid: Optional[str] = None):
        return self._get_client(oci.key_management.KmsVaultClient, "kms_vault")

    def kms_management(self, vault_management_endpoint: str):
        cache_key = f"kms_mgmt_{vault_management_endpoint}"
        if cache_key not in self._clients:
            self._clients[cache_key] = oci.key_management.KmsManagementClient(
                self.config,
                service_endpoint=vault_management_endpoint,
            )
        return self._clients[cache_key]

    def goldengate(self):
        return self._get_client(
            oci.golden_gate.GoldenGateClient, "goldengate"
        )

    # ---- SDK waiters ----
    def wait_until(self, client, get_fn_response, field: str, target_state: str,
                   max_wait: int = 1800, interval: int = 15):
        """Wait until a resource field reaches target state."""
        return oci.wait_until(
            client, get_fn_response, field, target_state,
            max_wait_seconds=max_wait,
            max_interval_seconds=interval,
        )

    # ---- CLI fallback ----
    @staticmethod
    def cli_execute(command: str, timeout: int = 60) -> Dict[str, Any]:
        """
        Execute OCI CLI command as subprocess.
        Returns parsed JSON or error dict.
        """
        try:
            proc = subprocess.run(
                command.split(),
                capture_output=True, text=True, timeout=timeout
            )
            if proc.returncode == 0:
                try:
                    return {"success": True, "data": json.loads(proc.stdout)}
                except json.JSONDecodeError:
                    return {"success": True, "data": proc.stdout.strip()}
            else:
                return {"success": False, "error": proc.stderr.strip() or proc.stdout.strip()}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"CLI command timed out ({timeout}s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ---- Connectivity test ----
    @staticmethod
    def test_tcp_connect(host: str, port: int, timeout: int = 10) -> bool:
        """Test raw TCP connectivity."""
        import socket
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()
            return True
        except (socket.timeout, socket.error, OSError):
            return False
