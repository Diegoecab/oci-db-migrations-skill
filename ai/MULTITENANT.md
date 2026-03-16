# CDB+PDB (Multitenant) Considerations

When the source is multitenant (CDB+PDB), the skill MUST ask for and track:

## Connections

1. **CDB-level connection** — host:port/CDB_service_name. Needed for:
   - DMS source_container_database connection (Oracle < 21c)
   - Creating common user `C##GGADMIN` at CDB level
   - GoldenGate Extract (capture) connects at CDB level

2. **PDB-level connection** — host:port/PDB_service_name. Needed for:
   - DMS source_database connection (the actual migration source)
   - Data Pump export runs against the PDB
   - GGADMIN at PDB level for schema access

## User Setup Differences

- `C##GGADMIN` — common user created at CDB level: `CREATE USER C##GGADMIN IDENTIFIED BY ... CONTAINER=ALL;`
- `GGADMIN` — local user at PDB level for schema access
- Data Pump user — created at PDB level
- GoldenGate authorization (`DBMS_GOLDENGATE_AUTH.GRANT_ADMIN_PRIVILEGE`) must run at CDB level for `C##GGADMIN`

## Config Impact

In the config, multitenant requires both `source_databases` (PDB connection) and `source_container_databases` (CDB connection).

**CDB/PDB with Oracle < 21c**: DMS needs BOTH connections — CDB-level via `source_container_databases` (for GG Extract/capture) and PDB-level via `source_databases` (for Data Pump). GoldenGate GGADMIN must be a common user (`C##GGADMIN`) at CDB level with `CONTAINER=ALL`.
