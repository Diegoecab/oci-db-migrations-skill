# Skill Evaluations

Test scenarios to verify the skill behaves correctly across common use cases.

## Scenario 1: Fresh start — auto-discovery

```json
{
  "scenario": "Fresh start with auto-discovery",
  "precondition": "No migration-journal.json exists",
  "query": "Quiero migrar mi Oracle 19c a ADB",
  "expected_behavior": [
    "Shows ASCII welcome banner",
    "Asks discovery mode (Option A: auto-discover / Option B: manual OCIDs)",
    "Uses AskUserQuestion tool for the choice (not plain text)",
    "If Option A: asks for region and OCI CLI profile",
    "If Option A: requests blanket approval for read-only OCI CLI commands"
  ]
}
```

## Scenario 2: Resume from existing journal

```json
{
  "scenario": "Resume migration from journal",
  "precondition": "migration-journal.json exists with deploy steps completed, migration start pending",
  "query": "hola",
  "expected_behavior": [
    "Reads migration-journal-config.json to locate journal",
    "Reads migration-journal.json to understand current state",
    "Shows welcome banner adapted to user's language (Spanish)",
    "Shows pipeline state summary and progress map derived from journal",
    "Presents options: continue current project / add migration / new project",
    "Uses AskUserQuestion tool for the choice (not plain text)",
    "Does NOT just suggest the next step without offering options",
    "Does NOT ask discovery mode questions"
  ]
}
```

## Scenario 2b: Add migration to existing project

```json
{
  "scenario": "Add a new migration to an existing project",
  "precondition": "migration-journal.json exists, config has existing migrations",
  "query": "quiero agregar otra migración",
  "expected_behavior": [
    "Confirms existing OCI infrastructure will be reused (compartment, VCN, subnet, vault, bucket)",
    "Asks whether to use the same target ADB or a different one",
    "Asks for new source DB details (type, host, port, service, schemas)",
    "Asks for migration type (ONLINE/OFFLINE) and reverse replication",
    "Updates migration-config.json with new source (if needed) and migration entry",
    "Runs validate-config to confirm updated config is valid",
    "Records config_updated entry in journal",
    "Does NOT re-run discovery or re-create existing resources",
    "Suggests assess for the new source as next action"
  ]
}
```

## Scenario 3: Assessment with failures

```json
{
  "scenario": "Assessment returns FAIL checks",
  "precondition": "Config exists and is valid",
  "query": "ejecuta el assessment",
  "expected_behavior": [
    "Presents command execution protocol (what, connects to, impact, command)",
    "Waits for user approval before running",
    "Runs: python migrate.py assess --output json",
    "Focuses on BLOCKERS first",
    "Groups related failures (e.g., GGADMIN missing → privilege checks also fail)",
    "Offers to generate remediation SQL",
    "Uses AskUserQuestion for next action choice"
  ]
}
```

## Scenario 4: Deploy step fails with IAM error

```json
{
  "scenario": "Deploy fails due to missing IAM policy",
  "precondition": "Assessment passed, deploy starts",
  "query": "despliega desde el paso 3",
  "expected_behavior": [
    "Shows pipeline progress map before starting",
    "Presents resource visibility table (what will be created)",
    "When deploy fails with permission error: diagnoses the error",
    "Shows which IAM policy is needed with exact policy statement",
    "Does NOT attempt to create IAM policies",
    "Suggests user coordinate with security team",
    "Updates pipeline progress map showing failed step"
  ]
}
```

## Scenario 5: Error troubleshooting

```json
{
  "scenario": "User reports ORA error",
  "precondition": "Any state",
  "query": "me sale ORA-01031 insufficient privileges",
  "expected_behavior": [
    "Identifies error pattern from KB",
    "Provides specific fix with exact SQL",
    "Suggests running assessment to verify the fix",
    "Does NOT run commands without presenting execution protocol first"
  ]
}
```

## Scenario 6: Scope boundary — user asks to create NSG

```json
{
  "scenario": "User asks to create a resource outside scope",
  "precondition": "Any state",
  "query": "crea un NSG para la migración",
  "expected_behavior": [
    "Explains that NSGs must be pre-created",
    "Explains the tool only creates DMS migrations, DMS connections, and GoldenGate deployments",
    "Offers to verify an existing NSG instead",
    "Does NOT attempt to create the NSG"
  ]
}
```

## Scenario 7: Multitenant source (CDB+PDB)

```json
{
  "scenario": "User has a multitenant Oracle source",
  "precondition": "Discovery complete, gathering requirements",
  "query": "el source es un CDB con un PDB llamado SALESPDB",
  "expected_behavior": [
    "Asks for CDB-level connection string (host:port/CDB_service)",
    "Asks for PDB-level connection string (host:port/PDB_service)",
    "Explains the need for C##GGADMIN at CDB level",
    "Explains the need for GGADMIN at PDB level",
    "Updates requirements tracking table with both connection entries"
  ]
}
```
