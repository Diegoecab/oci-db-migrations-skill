# Configuration Schema (migration-config.json)

## Key Sections

- **oci**: Tenancy, compartment, region, OCI config profile
- **networking**: VCN, subnet, NSG config.
  - `nsg_ocid`: OCID of the pre-existing NSG to use. The NSG must already exist with the required security rules (Oracle ports 1521/1522, HTTPS 443). Discovery should list available NSGs and let the user pick.
- **vault**: Vault OCID + encryption key OCID
- **object_storage**: Bucket for Data Pump staging
- **source_databases**: Map of source DBs with connection details + assessment user
  - `db_type`: oracle_onprem | oracle_rds | oracle_exacs | oracle_exacc
  - `host`: IP address (used for connectivity)
  - `hostname`: FQDN (required by DMS connections — always ask for both IP and hostname)
  - `assessment_user/password`: Read-only user for pre-migration checks (e.g., DBSNMP)
- **target_databases**: Map of target ADBs with OCID + credentials
- **migrations**: Map of migration definitions
  - `migration_type`: ONLINE (with GG CDC) or OFFLINE (Data Pump only)
  - `migration_scope`: SCHEMA (specific schemas) or FULL (entire database)
  - `include_allow_objects`: ["SCHEMA.*"] or ["SCHEMA.TABLE"] — only when `migration_scope` is SCHEMA. Omit or leave empty for FULL.
  - `enable_reverse_replication`: true → provisions GG fallback
  - `auto_validate` / `auto_start`: post-creation automation
- **goldengate**: GG deployment config (only needed if any migration has reverse replication)
- **monitoring**: Alarms thresholds (lag, CPU)
- **assessment**: Connector preference, remediation mode

## Resource Naming Convention

DMS connections and other resources use auto-generated names based on the config keys:
- Source connection: `dms-src-{source_db_key}` (e.g., `dms-src-basedb_pdb1`)
- Target connection: `dms-tgt-{target_db_key}` (e.g., `dms-tgt-adb_target`)
- Vault secrets: `dms-src-{key}-password`, `dms-src-{key}-gg-password`, etc.
- NSG: `dms-migration-nsg` (must be pre-created)
- Migration: `dms-mig-{migration_key}` (e.g., `dms-mig-testuser_migration`)

**Before generating the config**, present the proposed resource names to the user and ask if they want to customize them. The names derive from the config keys (`source_db_key`, `target_db_key`, `migration_key`), so changing the key changes all related resource names.

## Source Database Variants

The tool handles variant-specific behavior automatically:

| Variant | Key Differences |
|---------|----------------|
| **oracle_onprem** | Full control. Standard remediation SQL. |
| **oracle_rds** | Archivelog/force_logging always enabled. Use `rdsadmin` procedures for supplemental logging and GG auth. No OS access. DATA_PUMP_DIR pre-configured. |
| **oracle_exacs** | Use `dbaascli` for some operations. Similar to on-prem otherwise. |
| **oracle_exacc** | Customer-managed Exadata. Full control like on-prem. |
