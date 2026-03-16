# Requirements Tracking Table

## Display Format

Throughout the migration conversation, **maintain and display a requirements tracking table** that shows the user what you already have and what's still pending. Update and re-display this table every time new information is gathered.

```
| # | Category | Requirement | Status | Value |
|---|----------|-------------|--------|-------|
| 1 | OCI      | Tenancy OCID       | ✅ | ocid1.tenancy... |
| 2 | OCI      | Region             | ✅ | us-ashburn-1 |
| 3 | OCI      | Compartment        | ✅ | MyCompartment |
| 4 | Target   | ADB ADMIN password | ❌ | *pending* |
```

## Rules

- Use ✅ when the value is confirmed and available
- Use ❌ when the value is still needed from the user
- Use ❓ when the value needs clarification or confirmation
- Show the actual value (or resource name) next to ✅ items — never show full OCIDs inline, use display names
- Group rows by category: OCI, Source, Target, Migration
- Re-display the table after each round of information gathering so the user always sees progress
- When all items are ✅, announce that you're ready to generate `migration-config.json`

## Standard Requirements Checklist

The table MUST include a **Description** column so the user understands what each item is for.

**OCI**: Tenancy OCID, Region, Compartment, VCN, Subnet, NSG (existing OCID — must be pre-created), Vault, Key, Bucket, Namespace, CLI Profile

**Source**:
- Host:Port/Service — connection string for the source database
- CDB/PDB — is it multitenant? If yes, PDB name is required
- db_type — oracle_onprem | oracle_rds | oracle_exacs | oracle_exacc
- Data Pump user + password — user for initial load via Data Pump (needs DATAPUMP_EXP/IMP_FULL_DATABASE roles). This is NOT the CDC user.
- GGADMIN (CDB) + password — GoldenGate admin at CDB$ROOT level for CDC capture. In multitenant, this MUST be a common user (`C##GGADMIN`). Created at CDB level.
- GGADMIN (PDB) + password — GoldenGate admin at PDB level for schema access during replication. In non-CDB, there is only one GGADMIN.
- Assessment user + password — read-only user to run pre-migration checks (e.g. DBSNMP with SELECT_CATALOG_ROLE, or SYS AS SYSDBA). Not required if user prefers to run checks manually.

**Target**:
- ADB OCID
- ADMIN password — ADB admin for target connection
- GGADMIN password — for unlocking GGADMIN on target ADB (apply side)

**Migration**: Type (ONLINE/OFFLINE), Reverse replication (yes/no), Schemas to migrate

## Adjustments by Migration Type

- OFFLINE migrations don't need GGADMIN (no CDC)
- No reverse replication = no GoldenGate deployment section needed
- If schemas are unknown, offer to discover them from the source DB
- Non-CDB databases: only one GGADMIN, no CDB/PDB distinction
