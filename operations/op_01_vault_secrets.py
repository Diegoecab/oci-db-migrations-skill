"""
Operation 01: Create OCI Vault secrets for all database credentials.

Creates Base64-encoded secrets for:
  - Source DB credentials (per source)
  - Source GoldenGate credentials (per source)
  - Target DB credentials (per target)
  - Target GoldenGate credentials (per target)
  - GoldenGate admin password

Idempotent: checks if secret with same name exists before creating.
"""

import base64
import logging
from typing import Optional

from operations.base import BaseOperation, OpResult, OpStatus

logger = logging.getLogger(__name__)


class VaultSecretsOperation(BaseOperation):

    @property
    def name(self) -> str:
        return "vault-secrets"

    def check_exists(self, **kwargs) -> Optional[str]:
        """Check if all expected secrets exist."""
        try:
            vault_ocid = self.config.vault["vault_ocid"]
            compartment = self.config.oci["compartment_ocid"]

            import oci
            vaults_client = self.oci.kms_vault()
            vault = vaults_client.get_vault(vault_ocid).data

            secrets_client = oci.vault.VaultsClient(self.oci.config)
            existing = secrets_client.list_secrets(
                compartment_id=compartment,
                vault_id=vault_ocid,
                lifecycle_state="ACTIVE",
            ).data

            existing_names = {s.secret_name for s in existing}
            expected = self._expected_secret_names()

            if expected.issubset(existing_names):
                return vault_ocid  # All exist
            else:
                missing = expected - existing_names
                logger.info(f"Missing secrets: {missing}")
                return None

        except Exception as e:
            logger.debug(f"Cannot check secrets: {e}")
            return None

    def execute(self, **kwargs) -> OpResult:
        """Create missing secrets."""
        import oci

        vault_ocid = self.config.vault["vault_ocid"]
        key_ocid = self.config.vault["key_ocid"]
        compartment = self.config.oci["compartment_ocid"]

        vaults_client = oci.vault.VaultsClient(self.oci.config)
        secrets_client = vaults_client

        # Get existing to avoid duplicates
        existing = secrets_client.list_secrets(
            compartment_id=compartment,
            vault_id=vault_ocid,
            lifecycle_state="ACTIVE",
        ).data
        existing_names = {s.secret_name for s in existing}

        created = []
        errors = []

        for secret_name, secret_value in self._secret_map().items():
            if secret_name in existing_names:
                logger.info(f"  Secret exists: {secret_name}")
                continue

            try:
                b64_value = base64.b64encode(secret_value.encode()).decode()

                details = oci.vault.models.CreateSecretDetails(
                    compartment_id=compartment,
                    vault_id=vault_ocid,
                    key_id=key_ocid,
                    secret_name=secret_name,
                    secret_content=oci.vault.models.Base64SecretContentDetails(
                        content_type="BASE64",
                        content=b64_value,
                    ),
                    description=f"DMS migration credential: {secret_name}",
                )

                response = secrets_client.create_secret(details)
                created.append(secret_name)
                logger.info(f"  Created secret: {secret_name}")

            except Exception as e:
                errors.append(f"{secret_name}: {e}")
                logger.error(f"  Failed: {secret_name}: {e}")

        if errors:
            return OpResult(
                operation=self.name, resource_type="vault_secret",
                status=OpStatus.FAILED,
                error="; ".join(errors),
                details={"created": created, "errors": errors},
            )

        return OpResult(
            operation=self.name, resource_type="vault_secret",
            resource_id=vault_ocid,
            status=OpStatus.CREATED if created else OpStatus.SKIPPED,
            message=f"Created {len(created)} secrets",
            details={"created": created},
        )

    def _expected_secret_names(self) -> set:
        """Build set of expected secret names."""
        names = set()
        for key in self.config.source_databases:
            names.add(f"dms-src-{key}-password")
            names.add(f"dms-src-{key}-gg-password")
        for key in self.config.target_databases:
            names.add(f"dms-tgt-{key}-password")
            names.add(f"dms-tgt-{key}-gg-password")
        if self.config.has_reverse_replication():
            names.add("dms-gg-admin-password")
        return names

    def _secret_map(self) -> dict:
        """Build name→value map for all secrets."""
        secrets = {}
        for key, src in self.config.source_databases.items():
            secrets[f"dms-src-{key}-password"] = src.get("password", "")
            secrets[f"dms-src-{key}-gg-password"] = src.get("gg_password", "")
        for key, tgt in self.config.target_databases.items():
            secrets[f"dms-tgt-{key}-password"] = tgt.get("password", "")
            secrets[f"dms-tgt-{key}-gg-password"] = tgt.get("gg_password", "")
        if self.config.has_reverse_replication():
            secrets["dms-gg-admin-password"] = self.config.goldengate.get("admin_password", "")
        return secrets
