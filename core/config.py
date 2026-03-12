"""
Configuration loader and validator.

Loads migration-config.json, validates required fields, resolves
cross-references (source_db_key → actual source config), and
extracts migration scope (schemas/tables from include_allow_objects).
"""

import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def resolve_password(db_config, field="password", label=None):
    """
    Resolve a password from environment variable, then config, then prompt.

    Lookup order:
      1. Environment variable: DMS_<FIELD>_<KEY> (e.g., DMS_PASSWORD_BASEDB_PDB1)
         The env var name is stored in config as <field>_env_var.
      2. Config value (for backward compatibility — will be removed).
      3. Interactive prompt (if running in a terminal).

    Returns the password string, or raises ValueError if not found.
    """
    if label is None:
        label = db_config.get("display_name", "database")

    # 1. Check explicit env var name from config
    env_var_name = db_config.get(f"{field}_env_var")
    if env_var_name and os.environ.get(env_var_name):
        return os.environ[env_var_name]

    # 2. Auto-generate env var name from convention: DMS_<FIELD>_<KEY>
    # e.g., DMS_PASSWORD_BASEDB_PDB1, DMS_GG_PASSWORD_BASEDB_PDB1
    for key_hint in ("_key", "display_name", "username"):
        hint = db_config.get(key_hint, "")
        if hint:
            auto_env = f"DMS_{field.upper()}_{hint.upper().replace(' ', '_').replace('-', '_')}"
            if os.environ.get(auto_env):
                return os.environ[auto_env]

    # 3. Config value (backward compat — passwords in JSON)
    if db_config.get(field):
        return db_config[field]

    # 4. Interactive prompt (only if terminal)
    if hasattr(sys, 'stdin') and sys.stdin.isatty():
        import getpass
        return getpass.getpass(f"  Password for {label} ({field}): ")

    raise ValueError(f"Password not found for {label} ({field}). "
                     f"Set env var DMS_{field.upper()}_<KEY> or pass via config.")


# =============================================================================
# Parsed scope helpers
# =============================================================================
@dataclass
class MigrationScope:
    """Parsed object scope for a single migration."""
    migration_key: str
    schemas: Set[str] = field(default_factory=set)
    specific_tables: List[Tuple[str, str]] = field(default_factory=list)  # (schema, table)
    is_full_schema: Dict[str, bool] = field(default_factory=dict)

    @classmethod
    def from_object_list(cls, migration_key: str, objects: List[str]) -> "MigrationScope":
        """
        Parse include_allow_objects into structured scope.

        Formats:
            "HR.*"        → full schema HR
            "SALES.ORDERS" → specific table SALES.ORDERS
        """
        scope = cls(migration_key=migration_key)
        for obj in objects:
            parts = obj.split(".", 1)
            if len(parts) != 2:
                logger.warning(f"Invalid object format '{obj}' — expected 'SCHEMA.OBJECT'")
                continue

            schema, obj_name = parts[0].upper(), parts[1]
            scope.schemas.add(schema)

            if obj_name == "*":
                scope.is_full_schema[schema] = True
            else:
                scope.specific_tables.append((schema, obj_name.upper()))
                scope.is_full_schema.setdefault(schema, False)

        return scope


# =============================================================================
# Config class
# =============================================================================
class MigrationConfig:
    """Loaded and validated migration configuration."""

    REQUIRED_SECTIONS = ["oci", "source_databases", "target_databases", "migrations"]
    REQUIRED_OCI = ["tenancy_ocid", "compartment_ocid", "region"]
    REQUIRED_SOURCE_FIELDS = ["host", "port", "service_name", "username"]
    REQUIRED_TARGET_FIELDS = ["adb_ocid", "username"]
    REQUIRED_MIGRATION_FIELDS = ["migration_type", "source_db_key", "target_db_key"]

    def __init__(self, config_path: str):
        self.config_path = os.path.abspath(config_path)
        self._raw: Dict[str, Any] = {}
        self._errors: List[str] = []
        self._warnings: List[str] = []
        self._scopes: Dict[str, MigrationScope] = {}

    def load(self) -> bool:
        """Load and validate config. Returns True if valid (no blockers)."""
        if not os.path.exists(self.config_path):
            self._errors.append(f"Config file not found: {self.config_path}")
            return False

        try:
            with open(self.config_path, "r") as f:
                self._raw = json.load(f)
        except json.JSONDecodeError as e:
            self._errors.append(f"Invalid JSON: {e}")
            return False

        self._validate()
        return len(self._errors) == 0

    # ---- Accessors ----
    @property
    def oci(self) -> Dict[str, Any]:
        return self._raw.get("oci", {})

    @property
    def networking(self) -> Dict[str, Any]:
        return self._raw.get("networking", {})

    @property
    def vault(self) -> Dict[str, Any]:
        return self._raw.get("vault", {})

    @property
    def object_storage(self) -> Dict[str, Any]:
        return self._raw.get("object_storage", {})

    @property
    def source_databases(self) -> Dict[str, Dict]:
        return self._raw.get("source_databases", {})

    @property
    def target_databases(self) -> Dict[str, Dict]:
        return self._raw.get("target_databases", {})

    @property
    def migrations(self) -> Dict[str, Dict]:
        return self._raw.get("migrations", {})

    @property
    def goldengate(self) -> Dict[str, Any]:
        return self._raw.get("goldengate", {})

    @property
    def monitoring(self) -> Dict[str, Any]:
        return self._raw.get("monitoring", {})

    @property
    def assessment_config(self) -> Dict[str, Any]:
        return self._raw.get("assessment", {})

    @property
    def errors(self) -> List[str]:
        return self._errors

    @property
    def warnings(self) -> List[str]:
        return self._warnings

    def source_db(self, key: str) -> Dict[str, Any]:
        return self.source_databases.get(key, {})

    def target_db(self, key: str) -> Dict[str, Any]:
        return self.target_databases.get(key, {})

    def migration_scope(self, migration_key: str) -> MigrationScope:
        """Get parsed scope for a migration."""
        if migration_key not in self._scopes:
            mig = self.migrations.get(migration_key, {})
            objects = mig.get("include_allow_objects", [])
            self._scopes[migration_key] = MigrationScope.from_object_list(migration_key, objects)
        return self._scopes[migration_key]

    def all_schemas_for_source(self, source_key: str) -> Set[str]:
        """Get union of all schemas migrating from a specific source."""
        schemas = set()
        for mig_key, mig in self.migrations.items():
            if mig.get("source_db_key") == source_key:
                schemas.update(self.migration_scope(mig_key).schemas)
        return schemas

    def has_reverse_replication(self) -> bool:
        """Any migration has enable_reverse_replication?"""
        return any(m.get("enable_reverse_replication", False) for m in self.migrations.values())

    def resolve_source_for_migration(self, migration_key: str) -> Dict[str, Any]:
        """Get source DB config resolved from migration's source_db_key."""
        mig = self.migrations.get(migration_key, {})
        return self.source_db(mig.get("source_db_key", ""))

    def resolve_target_for_migration(self, migration_key: str) -> Dict[str, Any]:
        """Get target DB config resolved from migration's target_db_key."""
        mig = self.migrations.get(migration_key, {})
        return self.target_db(mig.get("target_db_key", ""))

    # ---- Validation ----
    def _validate(self):
        """Run all validations."""
        for section in self.REQUIRED_SECTIONS:
            if section not in self._raw:
                self._errors.append(f"Missing required section: '{section}'")

        if self._errors:
            return  # Can't continue without required sections

        self._validate_oci()
        self._validate_sources()
        self._validate_targets()
        self._validate_migrations()
        self._validate_goldengate()

    def _validate_oci(self):
        for f in self.REQUIRED_OCI:
            if not self.oci.get(f):
                self._errors.append(f"oci.{f} is required")

    def _validate_sources(self):
        if not self.source_databases:
            self._errors.append("At least one source_databases entry required")
            return
        for key, src in self.source_databases.items():
            for f in self.REQUIRED_SOURCE_FIELDS:
                if not src.get(f):
                    self._errors.append(f"source_databases.{key}.{f} is required")
            # hostname vs host
            if src.get("host") and not src.get("hostname"):
                self._warnings.append(
                    f"source_databases.{key}: 'hostname' not set. "
                    "DMS requires FQDN, not IP. Add a hostname field."
                )
            # Data Pump directory config
            if not src.get("datapump_dir_name"):
                self._warnings.append(
                    f"source_databases.{key}: 'datapump_dir_name' not set. "
                    "Defaulting to 'DATA_PUMP_DIR'. Set the actual Oracle directory name."
                )
            if not src.get("datapump_dir_path"):
                self._warnings.append(
                    f"source_databases.{key}: 'datapump_dir_path' not set. "
                    "Required for remediation — set the OS path on the DB server."
                )
            if not src.get("ssl_wallet_dir"):
                self._warnings.append(
                    f"source_databases.{key}: 'ssl_wallet_dir' not set. "
                    "Required for Data Pump via Object Storage (HTTPS). "
                    "Set the path where the SSL wallet (cwallet.sso) is/will be."
                )

    def _validate_targets(self):
        if not self.target_databases:
            self._errors.append("At least one target_databases entry required")
            return
        for key, tgt in self.target_databases.items():
            for f in self.REQUIRED_TARGET_FIELDS:
                if not tgt.get(f):
                    self._errors.append(f"target_databases.{key}.{f} is required")

    def _validate_migrations(self):
        if not self.migrations:
            self._errors.append("At least one migration entry required")
            return

        for key, mig in self.migrations.items():
            for f in self.REQUIRED_MIGRATION_FIELDS:
                if not mig.get(f):
                    self._errors.append(f"migrations.{key}.{f} is required")

            # Cross-reference validation
            src_key = mig.get("source_db_key", "")
            if src_key and src_key not in self.source_databases:
                self._errors.append(
                    f"migrations.{key}.source_db_key '{src_key}' "
                    "not found in source_databases"
                )

            tgt_key = mig.get("target_db_key", "")
            if tgt_key and tgt_key not in self.target_databases:
                self._errors.append(
                    f"migrations.{key}.target_db_key '{tgt_key}' "
                    "not found in target_databases"
                )

            # Object list validation
            includes = mig.get("include_allow_objects", [])
            excludes = mig.get("exclude_objects", [])
            if includes and excludes:
                self._errors.append(
                    f"migrations.{key}: include_allow_objects and "
                    "exclude_objects are mutually exclusive"
                )
            if not includes and not excludes:
                self._warnings.append(
                    f"migrations.{key}: no include_allow_objects defined — "
                    "DMS will migrate all schemas (may not be intended)"
                )

            for obj in includes:
                if "." not in obj:
                    self._errors.append(
                        f"migrations.{key}: invalid object '{obj}' — "
                        "use 'SCHEMA.*' or 'SCHEMA.TABLE' format"
                    )

            # Migration scope
            scope = mig.get("migration_scope", "SCHEMA")
            if scope not in ("SCHEMA", "FULL"):
                self._errors.append(
                    f"migrations.{key}.migration_scope must be SCHEMA or FULL"
                )
            if scope == "FULL" and (includes or excludes):
                self._warnings.append(
                    f"migrations.{key}: migration_scope=FULL ignores "
                    "include_allow_objects and exclude_objects"
                )

            # Migration type
            mig_type = mig.get("migration_type", "")
            if mig_type not in ("ONLINE", "OFFLINE"):
                self._errors.append(
                    f"migrations.{key}.migration_type must be ONLINE or OFFLINE"
                )

            # Reverse replication requires ONLINE
            if mig.get("enable_reverse_replication") and mig_type != "ONLINE":
                self._errors.append(
                    f"migrations.{key}: enable_reverse_replication "
                    "requires migration_type = ONLINE"
                )

    def _validate_goldengate(self):
        if not self.has_reverse_replication():
            return  # GG section not needed
        if not self.goldengate:
            self._errors.append(
                "goldengate section required when any migration "
                "has enable_reverse_replication = true"
            )
            return
        if not self.goldengate.get("admin_password"):
            self._errors.append("goldengate.admin_password is required")
