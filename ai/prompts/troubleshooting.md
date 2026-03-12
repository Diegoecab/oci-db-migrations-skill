# Troubleshooting Prompt

Use this prompt when a user reports an error during any phase of migration.

## Diagnosis Framework

1. **Identify the phase**: Assessment? Provisioning? Validation? Replication? Cutover?
2. **Extract the error pattern**: ORA-NNNNN, GGS-*, HTTP status, DMS message
3. **Match against KB** (kb/errors.yaml patterns):
   - 409-Conflict → resource exists
   - FQDN cannot be IP → hostname needed
   - objectName must not be null → format issue
   - CPAT failed → compatibility check
   - ORA-65040/65050 → CDB/PDB issue
   - ORA-39001/39002/39070 → Data Pump directory
   - ORA-01031 → privileges
   - ORA-12154/12541/12543 → connectivity
   - ABENDED → GG process crash
   - 401-NotAuthenticated → API key issue
4. **Provide specific fix** with exact commands (SQL, CLI, or config change)
5. **Suggest verification** command

## Response Structure

```
## Error Identified
[Pattern]: [Description from KB]

## Root Cause
[Explain why this happens in this specific context]

## Fix
[Exact SQL, CLI command, or config change]

## Verify
[Command to confirm the fix worked]

## Prevention
[How to avoid this in the future]
```

## Common Multi-Error Scenarios

### "DMS validation failed with multiple errors"
- Run: `python migrate.py assess --source <key>`
- Assessment handles dependency ordering (e.g., GGADMIN must exist before checking privileges)
- Fix in order: user creation → grants → GG auth → supplemental logging

### "GoldenGate process keeps abending"
1. Get process report: `curl -u oggadmin https://<gg_url>/services/v2/extracts/<name>/info/report`
2. Common causes:
   - Unsupported DDL → add DDL exclusion rules
   - Trail file corrupt → purge trail, restart from checkpoint
   - Network timeout → check NSG rules
3. Restart: `curl -X PATCH .../services/v2/extracts/<name> -d '{"status":"running"}'`

### "Migration stuck in WAITING state"
- This is NORMAL — DMS pauses at cutover point
- Replication is caught up, waiting for user confirmation
- Run pre-cutover validation, then resume:
  `oci database-migration migration resume --migration-id <OCID>`

## Context-Aware Diagnosis

When the user mentions:
- **AWS RDS**: Check if they used `rdsadmin` procedures instead of standard SQL
- **PDB source**: Check if `source_container_databases` is configured
- **Large schemas (50GB+)**: Check Data Pump parallelism, Object Storage bucket size
- **Cross-region**: Check VPN/FastConnect connectivity, peering routes
