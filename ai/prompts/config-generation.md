# Config Generation Prompt

Use this prompt to guide configuration generation. The AI should gather
the minimum required information and produce a valid migration-config.json.

## Required Information to Gather

1. **Source database(s)**:
   - Type: on-prem, AWS RDS, ExaCS, ExaCC?
   - Connection: host, port, service_name
   - Is it CDB/PDB? If yes, PDB name
   - Oracle version (11gR2, 12c, 19c, 21c, 23ai)

2. **Target ADB(s)**:
   - ADB OCID (mandatory)
   - Does it have private endpoint?

3. **Schemas to migrate**:
   - Which schemas? (e.g., HR, SALES, INVENTORY)
   - Any specific tables to exclude?
   - Which schemas need fallback (reverse replication)?

4. **OCI infrastructure**:
   - Compartment OCID
   - VCN/Subnet OCID
   - Vault OCID + Key OCID
   - Object Storage bucket name + namespace
   - ONS Topic OCID (for alerts, optional)

5. **Migration preferences**:
   - ONLINE or OFFLINE?
   - Auto-validate? Auto-start?
   - Data Pump parallelism preference

## Generation Rules

- Set `db_type` based on source type answer
- If CDB: set `is_cdb: true` and `pdb_name`
- If any schema needs fallback: set `enable_reverse_replication: true` on that migration + populate `goldengate` section
- Default `assessment_user` to "DBSNMP" with a note to grant SELECT_CATALOG_ROLE
- Default monitoring thresholds: lag_warning=60s, lag_critical=300s
- Default Data Pump parallelism: 4 (scale based on schema size if known)
- Always include `tablespace_remap: {"USERS": "DATA"}` as ADB uses DATA tablespace
- For RDS: note that archivelog/force_logging checks will be auto-skipped

## Post-Generation Instructions

After generating the config, instruct the user:
```bash
# 1. Save as migration-config.json
# 2. Validate
python migrate.py validate-config
# 3. Run assessment
python migrate.py assess
# 4. Fix any blockers (assessment generates remediation.sql)
# 5. Re-assess until 0 blockers
# 6. Deploy
python migrate.py deploy
```
