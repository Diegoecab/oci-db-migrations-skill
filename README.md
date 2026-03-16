# OCI Database Migration AI Skill

**An AI skill that prepares, provisions, and orchestrates Oracle database migrations to Autonomous Database.**

Describe your migration scenario to an AI assistant. It assesses your databases, generates remediation scripts, executes fixes, and — optionally — provisions DMS infrastructure and guides you through cutover. Use it for the full lifecycle or just to get your source and target databases ready for a migration you manage through the OCI Console. The AI skill uses an embedded Knowledge Base of 40+ prerequisite checks and 20+ error patterns.

---

## How It Works

You interact with an **AI assistant** loaded with the project's `SKILL.md`. The skill transforms any capable LLM into an OCI DMS migration specialist. It reads your configuration, executes assessment and provisioning tools, interprets results, and tells you exactly what to do next.

**You say:** *"I need to migrate HR and SALES schemas from Oracle 19c on AWS RDS to ADB-S in Ashburn."*

**The AI assistant** offers three workflows:

### Full pipeline (the AI deploys DMS for you)

| Step | What it does | Command |
|------|-------------|---------|
| 1 | Creates a migration project and generates `migration-config.json` | *(auto-generated)* |
| 2 | Assesses source DB — finds 3 blockers | `migrate.py assess --source rds_prod` |
| 3 | Generates remediation SQL, asks before executing | `migrate.py assess --generate-sql` |
| 4 | Re-assesses: 0 blockers, ready | `migrate.py assess --source rds_prod` |
| 5 | Deploys: vault, NSG, DMS connections, migrations, GoldenGate | `migrate.py deploy` |
| 6 | Validates and starts migration jobs | `migrate.py start-migration` |
| 7 | Monitors progress, advises on cutover timing | `migrate.py status --json` |

### Prepare databases only (you deploy DMS via Console/Terraform)

| Step | What it does | Command |
|------|-------------|---------|
| 1 | Creates a project and collects source/target details | *(auto-generated)* |
| 2 | Assesses source & target DBs | `migrate.py assess --output json` |
| 3 | Generates SQL scripts for all failed checks | `migrate.py assess --generate-sql` |
| 4 | Optionally executes fixes directly on the databases | `migrate.py assess --remediate --source rds_prod` |
| 5 | Re-assesses until all checks pass | `migrate.py assess --source rds_prod` |

You take the prepared databases and create the DMS migration yourself through the OCI Console, Terraform, or any other method.

### Scripts only (no DB connectivity needed)

If the tool can't connect to the databases (no VPN, no credentials), it generates verification and remediation SQL scripts that you run manually and paste the results back.

---

The AI skill never connects to databases directly. A Python tool layer handles all connectivity, authentication, and OCI API calls. The skill handles interpretation, decision logic, sequencing, and guidance.

---

## Quick Start

### Option A: Via AI Assistant (Recommended)

**Agentic mode** — for AI coding tools with terminal access (Claude Code, Cursor, Windsurf, Copilot agent, etc.):

```bash
# 1. Clone and install
git clone https://github.com/Diegoecab/oci-db-migrations-skill.git
cd oci-db-migrations-skill
chmod +x setup.sh && ./setup.sh    # Auto-detects Python, installs deps, creates ./migrate launcher

# 2. Open your AI coding tool in this directory and say:
#    "I need to migrate schemas X, Y, Z from Oracle source to ADB.
#     Here are my details: [source host, ADB OCID, compartment, etc.]"
#
#    The assistant will ask what you want to do:
#    a) Full pipeline — assess, remediate, deploy DMS, monitor, cutover
#    b) Prepare databases only — assess + generate/execute scripts (you deploy DMS via Console)
#    c) Scripts only — generate SQL scripts for manual execution (no DB connectivity needed)
```

**Advisory mode** — for chat interfaces without code execution:

1. Create a project or custom assistant in your preferred platform
2. Upload `ai/SKILL.md` as the system prompt or project knowledge
3. Start chatting: describe your migration scenario
4. The assistant generates configs and commands — you run them in your terminal and paste results back

**API integration**:

Include `ai/SKILL.md` as the system prompt in your API calls to any compatible LLM.

### Option B: Direct CLI (Without AI)

All operations are also available as standalone commands:

```bash
# 1. Install and verify environment
chmod +x setup.sh && ./setup.sh
./migrate probe

# 2. Set up OCI credentials (if not already configured)
./migrate setup-oci

# 3. Create and validate your configuration
cp migration-config.json.example migration-config.json
# Edit with your OCIDs, usernames, and schema definitions
# Set passwords via env vars (see Password Resolution section)
./migrate validate-config

# 4. Assess pre-migration readiness
./migrate assess
./migrate assess --generate-sql     # Fix script for any blockers
./migrate assess --remediate --source aws_oracle_prod  # Execute fixes

# 5. Deploy OCI resources
./migrate deploy

# 6. Monitor migration state
./migrate status

# 7. Troubleshoot errors
./migrate diagnose "ORA-01031"
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                            OCI Tenancy                               │
│                                                                      │
│  ┌────────────┐     ┌──────────────┐     ┌────────────────────┐     │
│  │  DMS        │     │  GoldenGate  │     │  Autonomous DB     │     │
│  │  Service    │─────│  Deployment  │─────│  (Target)          │     │
│  │  (Free)     │     │  (Fallback)  │     │  Private Endpoint  │     │
│  └──────┬──────┘     └──────┬───────┘     └────────────────────┘     │
│         │    NSG (1521-1522, 443)                                    │
│  ┌──────┴───────────────────┴────────────────────────────────────┐   │
│  │  OCI Vault │ Object Storage │ Monitoring │ Events │ ONS       │   │
│  └───────────────────────────────────────────────────────────────┘   │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ VPN / FastConnect / Peering
                  ┌────────┴─────────┐
                  │  Source Oracle DB │
                  │  (On-Prem / RDS)  │
                  └──────────────────┘
```

### Layers

```
┌─────────────────────────────────────────┐
│  AI Skill (SKILL.md)                    │  Interprets, decides, guides
│  LLM-agnostic system prompt             │
├─────────────────────────────────────────┤
│  CLI Interface (migrate.py)             │  Commands with JSON output
├─────────────────────────────────────────┤
│  Operations (operations/)               │  Idempotent OCI SDK calls
├─────────────────────────────────────────┤
│  Assessment (assessment/)               │  DB checks + gap analysis
├─────────────────────────────────────────┤
│  Core (core/)                           │  DB connector, OCI client, KB
├─────────────────────────────────────────┤
│  Knowledge Base (kb/)                   │  Prerequisites + error catalog
└─────────────────────────────────────────┘
```

---

## AI Skill

### What It Does

The `ai/SKILL.md` file is a system prompt that transforms any capable LLM into an OCI DMS migration specialist with these capabilities:

**Project Setup** — Create a new migration project from scratch. Describe your environment (source type, schemas, target ADB, criticality), and the assistant generates a complete, validated `migration-config.json`. Auto-discovers OCI resources via CLI or accepts manual OCIDs.

**Assessment + Scripts** — Assess source and target databases against 40+ prerequisite checks. Generate SQL scripts you can run yourself, or let the assistant execute fixes directly. Works even without DB connectivity — generates offline verification scripts for manual execution.

**Deploy + Operate** — *(Optional)* Provision DMS connections, migrations, and GoldenGate deployments. Not everyone needs this — you can prepare your databases with this tool and deploy DMS through the OCI Console or Terraform instead.

**Cutover Advisory** — The assistant evaluates replication lag trend, GoldenGate fallback state, source load, and timing to give a go/no-go recommendation with an execution sequence and rollback plan.

### Compatibility

The SKILL.md is a standard system prompt — it works with any LLM that supports structured instructions. The skill relies on the model's ability to interpret JSON output, follow multi-step procedures, and reason about Oracle database concepts.

**Tested with:**

| Model | Best For |
|-------|----------|
| Claude Opus / Sonnet | Full lifecycle orchestration, complex troubleshooting |
| Claude Haiku | Quick lookups, simple diagnostics |
| GPT-4o / GPT-4 | Config generation, assessment interpretation |
| Gemini Pro | Advisory mode, status interpretation |

The skill is most effective with models that have strong tool use / agentic capabilities and solid Oracle knowledge in their training data.

### Usage Modes

| Mode | Tools | How |
|------|-------|-----|
| **Agentic** | Claude Code, Cursor, Windsurf, Copilot | AI executes `migrate.py` commands, reads JSON output, iterates |
| **Advisory** | Any chat UI (ChatGPT, Claude.ai, Gemini, etc.) | Upload SKILL.md as context. User runs commands, pastes results |
| **API** | Any LLM API | Include SKILL.md as system prompt in API calls |

### Keeping the Skill in Sync

The SKILL.md is generated from the Knowledge Base. When you update `kb/prerequisites.yaml` or `kb/errors.yaml`, regenerate:

```bash
python ai/generate_skill.py
```

---

## Migration Journal — Team Collaboration

The migration journal is a shared audit log that tracks every action, decision, and pipeline state across sessions and team members. It enables anyone on the team (or any new AI session) to pick up a migration exactly where it was left off.

### How It Works

```
migration-journal-config.json    ← Tracked in git. Points to the journal location.
         │
         ▼
migration-journal.json           ← NOT in git. Contains the full history.
```

- **`migration-journal-config.json`** is committed to the repo and contains only the path to the journal file
- **`migration-journal.json`** is the actual journal — it is gitignored because it contains operational history and may include sensitive context

### Storage Options

The journal path in `migration-journal-config.json` can point to:

| Location | Example | Best For |
|----------|---------|----------|
| Local (default) | `./migration-journal.json` | Single-user or dev/test |
| NFS mount | `/mnt/shared/migrations/journal.json` | Team collaboration |
| Shared filesystem | `/efs/dba-team/project-x/journal.json` | AWS environments |

When the AI skill starts a session, it reads the pointer file, locates the journal, and resumes from the last known state. If the journal doesn't exist at the configured path, it asks where to create it.

### What Gets Tracked

| Section | Contents |
|---------|----------|
| `project` | Migration name, description, region, creation date |
| `team` | OS users and hostnames that have participated (auto-detected) |
| `pipeline_state` | Current status of each migration step (completed/pending/failed/skipped) |
| `entries` | Chronological log of every action: who, what, when, result |
| `decisions` | Key choices made during the migration and their reasoning |

Each entry includes an ISO 8601 timestamp and the OS user@hostname, auto-detected from the environment.

### Example Entry

```json
{
  "id": 5,
  "timestamp": "2026-03-12T14:30:00Z",
  "user": "jsmith@dba-workstation-01",
  "action": "assess",
  "phase": "Pre-flight",
  "description": "Source DB assessment completed. 2 blockers found: supplemental logging missing on 5 tables, GGADMIN lacks 3 privileges.",
  "result": "warning"
}
```

---

## What It Does Step by Step

### 1. Environment Check

Verifies your setup: database connectors (oracledb thin/thick, sqlplus), Python packages (oci, pyyaml, rich), and OCI CLI/SDK configuration (`~/.oci/config` — validates profile, API key, and tests authentication).

If OCI config is missing, provides guided setup.

### 2. Pre-Migration Assessment

Connects to source and target databases with a read-only user (e.g., DBSNMP with SELECT_CATALOG_ROLE) and checks 40+ prerequisites:

**Source database**: archive log mode, force logging, supplemental logging (min + ALL COLUMNS per table in scope), GoldenGate replication parameter, streams pool size, GGADMIN user and 23+ privileges, GoldenGate authorization, DMS user with Data Pump roles, Data Pump directory and grants, schema existence, size estimation, unsupported datatypes.

**Target ADB**: AVAILABLE state, private endpoint, GGADMIN unlocked, Object Storage credential, wallet downloadable.

**OCI infrastructure**: Object Storage bucket, Vault and encryption key, IAM policies for DMS and GoldenGate, dynamic groups, TCP connectivity to source.

Each check has variant-specific behavior. For example, on AWS RDS the archivelog check is auto-skipped (always enabled), and remediation uses `rdsadmin` procedures instead of standard SQL.

Assessment output (terminal or JSON):

```
╔══════════════════════════════════════════════════════╗
║  Source: AWS Oracle Production                       ║
╠══════════════════════════════════════════════════════╣
║  ✅ Archive Log Mode .............. ARCHIVELOG        ║
║  ✅ Force Logging ................. YES               ║
║  ❌ Supplemental Log (ALL COLUMNS). 5 tables missing ║
║  ✅ GGADMIN exists ................ OPEN              ║
║  ⚠️  GGADMIN privileges ........... 3 of 25 missing  ║
║  ❌ Data Pump Directory ........... NOT FOUND         ║
║                                                      ║
║  RESULT: 2 blockers, 1 warning                       ║
╚══════════════════════════════════════════════════════╝
```

### 3. Remediation

Generates a ready-to-execute SQL script from all failed checks, with comments explaining each fix and Oracle documentation references. Optionally executes remediation interactively (prompts before each statement, requires privileged user).

### 4. Infrastructure Deployment

Idempotent pipeline (every step checks if resources exist before creating):

| Step | Operation | Action |
|------|-----------|--------|
| 1 | Vault Secrets | Verify pre-created secrets exist and are ACTIVE |
| 2 | Network NSG | Verify existing NSG is AVAILABLE with rules |
| 3 | DMS Connections | Create source + target database connections |
| 4 | DMS Migrations | Create migration definitions with auto-validate |
| 5 | GoldenGate | Create deployment + reverse replication (if enabled) |

After deployment, use `./migrate validate-migration` to run the DMS premigration advisor and `./migrate start-migration` to start the migration jobs. These are separate commands to give you explicit control over when jobs begin.

### 5. State Monitoring

Provides a full snapshot of all resources: vault, NSG, DMS connections, migrations, jobs, GoldenGate deployment. Computes recommended next actions based on current state. JSON output lets the AI skill make context-aware decisions.

### 6. Error Diagnosis

Matches error text against 20+ known patterns (DMS, GoldenGate, ORA-, OCI API) and returns root cause + specific fix. When an operation fails during the pipeline, diagnosis happens automatically using the KB.

---

## Configuration

### `migration-config.json`

All migration parameters in a single JSON file. See [`migration-config.json.example`](migration-config.json.example) for the complete template.

Key sections:

```jsonc
{
  "oci": { /* tenancy, compartment, region, config profile */ },
  "networking": { /* VCN, subnet, NSG OCID (pre-existing) */ },
  "vault": { /* vault OCID, encryption key OCID */ },
  "object_storage": { /* bucket for Data Pump staging */ },

  "source_databases": {
    "aws_oracle_prod": {
      "db_type": "oracle_rds",        // oracle_onprem | oracle_rds | oracle_exacs
      "host": "10.0.1.100",
      "hostname": "oracle-prod.internal",  // FQDN required by DMS
      "port": 1521,
      "service_name": "ORCL",
      "username": "dms_admin",
      "gg_username": "GGADMIN",
      "assessment_user": "DBSNMP",    // Read-only user for pre-migration checks
      "datapump_dir_name": "DATA_PUMP_DIR",
      "datapump_dir_path": "/u01/app/oracle/admin/ORCL/dpdump"
      // ...
    }
  },

  "target_databases": {
    "adb_prod": {
      "adb_ocid": "ocid1.autonomousdatabase...",
      "username": "ADMIN",
      "gg_username": "GGADMIN"
      // ...
    }
  },

  "migrations": {
    "hr_migration": {
      "migration_type": "ONLINE",           // ONLINE (with GG CDC) or OFFLINE
      "migration_scope": "SCHEMA",          // SCHEMA or FULL
      "source_db_key": "aws_oracle_prod",
      "target_db_key": "adb_prod",
      "include_allow_objects": ["HR.*"],     // SCHEMA.* or SCHEMA.TABLE
      "enable_reverse_replication": true,    // GoldenGate fallback
      "auto_validate": true,
      // ...
    }
  }
}
```

When using the AI skill, you don't need to write this manually. Describe your scenario and the assistant generates it.

### Password Resolution

Passwords are **never stored in JSON config files**. They are resolved at runtime in this order:

| Priority | Source | Example |
|----------|--------|---------|
| 1 | Explicit env var (from config `password_env_var`) | `MY_CUSTOM_VAR` |
| 2 | Auto-generated env var convention | `DMS_PASSWORD_BASEDB_PDB1` |
| 3 | Interactive prompt (`getpass` — masked input) | *(nothing shown on screen)* |

Env var naming convention: `DMS_<FIELD>_<KEY>` where FIELD is `PASSWORD`, `GG_PASSWORD`, or `ASSESSMENT_PASSWORD`, and KEY is derived from the database key in config.

```bash
# Example: set passwords via environment
export DMS_PASSWORD_BASEDB_PDB1="secret"
export DMS_GG_PASSWORD_BASEDB_PDB1="gg_secret"
export DMS_ASSESSMENT_PASSWORD_BASEDB_PDB1="assess_secret"
export DMS_PASSWORD_ADB_TARGET="admin_secret"
```

### Source Database Variants

| Variant | Adjustments |
|---------|------------|
| `oracle_onprem` | Full control. Standard SQL remediation |
| `oracle_rds` | Archivelog/force_logging auto-skipped. Uses `rdsadmin` procedures |
| `oracle_exacs` | Uses `dbaascli` for some operations |
| `oracle_exacc` | Customer-managed Exadata. Same as on-prem |

---

## Database Connectivity

The tool auto-detects the best available database connector:

| Priority | Connector | Requirements |
|----------|-----------|-------------|
| 1 | `oracledb` thin mode | `pip install oracledb` — Python 3.8+, pure Python, no Oracle Client |
| 2 | `oracledb` thick mode | Above + Oracle Instant Client installed |
| 3 | `sqlplus` subprocess | `sqlplus` or `sql` (SQLcl) in PATH — works on any Python version |

On Python 3.6/3.7 environments, `oracledb` is not available. The tool automatically falls back to `sqlplus`, which is fully functional for all assessment and remediation operations.

Override with `"db_connector_preference": "sqlplus"` in the assessment config.

---

## Migration Fallback (Reverse Replication)

For migrations with `enable_reverse_replication: true`, the tool provisions a standalone OCI GoldenGate deployment with Extract/Replicat processes for Target→Source rollback.

DMS handles forward migration (Source → Target) using its internal GoldenGate. The standalone GG deployment is independent and provides a proven rollback path: if issues arise after cutover, data flows back from Target to Source.

Processes are created in stopped state by default (recommended). The AI skill guides you on when and how to activate them relative to cutover timing.

---

## Knowledge Base

The `kb/` directory is the single source of truth for migration intelligence. Both the Python code and the AI skill consume it:

**`kb/prerequisites.yaml`** — 40+ checks with SQL queries, expected values, severity, variant-specific behavior, remediation templates, dependency chains, and Oracle doc references.

**`kb/errors.yaml`** — 20+ error patterns with regex matching, root cause descriptions, fix commands, and severity classification.

When an operation fails, the pipeline automatically looks up the error in the KB and shows the diagnosis alongside the failure message. The AI skill uses the same KB to provide contextual troubleshooting.

---

## CLI Reference

After running `setup.sh`, use `./migrate` (or `python3.x migrate.py` directly):

| Command | Description |
|---------|-------------|
| `./migrate probe` | Check connectors, packages, OCI config |
| `./migrate setup-oci` | Guided OCI CLI/SDK configuration |
| `./migrate validate-config` | Validate migration-config.json |
| `./migrate assess` | Full pre-migration assessment |
| `./migrate assess --source X` | Assess specific source |
| `./migrate assess --generate-sql` | Generate remediation script |
| `./migrate assess --remediate --source X` | Execute fixes interactively |
| `./migrate deploy` | Run full provisioning pipeline |
| `./migrate deploy --step N` | Run specific step |
| `./migrate deploy --from-step N` | Run from step N onward |
| `./migrate deploy --list-steps` | Show available steps |
| `./migrate validate-migration` | Run DMS premigration advisor on all migrations |
| `./migrate validate-migration --migration X` | Validate specific migration |
| `./migrate validate-migration --wait` | Wait for validation to complete and show results |
| `./migrate start-migration` | Start migration jobs (all configured migrations) |
| `./migrate start-migration --migration X` | Start specific migration |
| `./migrate start-migration --wait` | Start and wait for completion |
| `./migrate status` | Show resource state |
| `./migrate status --json` | JSON output (for AI skill) |
| `./migrate status --migration X` | Specific migration |
| `./migrate cleanup connection "name"` | Delete a DMS connection by display name |
| `./migrate cleanup migration "name"` | Delete a DMS migration by display name |
| `./migrate diagnose "ORA-XXXXX"` | KB error lookup |
| `./migrate generate-wallet-script` | Generate SSL wallet setup script for source DB |
| `./migrate generate-wallet-script --source X` | Generate for specific source |

---

## OCI CLI Setup

The tool uses the OCI CLI/SDK to discover resources and provision infrastructure. If you don't have OCI CLI configured, you can set it up using an API key from the OCI Console.

### Option 1: Generate API Key from OCI Console (no CLI needed initially)

1. Log into [OCI Console](https://cloud.oracle.com)
2. Click your profile icon (top right) → **My Profile**
3. Scroll to **API Keys** → **Add API Key**
4. Choose **Generate API Key Pair** → **Download Private Key** → **Add**
5. The console shows a **Configuration File Preview** with all needed values:
   ```
   [DEFAULT]
   user=ocid1.user.oc1..aaaa...
   fingerprint=xx:xx:xx:...
   tenancy=ocid1.tenancy.oc1..aaaa...
   region=us-ashburn-1
   key_file=<path to your downloaded private key>
   ```
6. Save this as `~/.oci/config` and place the downloaded `.pem` file at the path specified in `key_file`
7. Fix permissions: `chmod 600 ~/.oci/config ~/.oci/*.pem`

### Option 2: Interactive Setup via CLI

```bash
oci setup config
```

### Option 3: Browser Session Authentication (temporary, no API key)

```bash
oci session authenticate --region us-ashburn-1
```
Opens your browser for SSO login. Session expires after 1 hour.

### Verify Authentication

```bash
oci iam region-subscription list
```

---

## OCI Prerequisites

### Required OCI Resources (Pre-existing)

- VCN with private subnet and connectivity to source DB
- OCI Vault with Master Encryption Key
- Object Storage bucket for Data Pump staging
- Autonomous Database (target) with private endpoint
- ONS Notification Topic (optional, for alerts)

### Required IAM Policies

These policies must be created by your IAM/security team **before** running the migration. The tool will **never** attempt to create, modify, or delete IAM policies — it only informs you what's needed.

The assessment checks for these policies on a best-effort basis, but the OCI CLI user may not have permissions to list them. If the check reports INFO or FAIL, the tool proceeds with deployment anyway. If a policy is actually missing, the resource creation will fail with a clear permission error indicating which policy is needed.

```
Allow group <group> to manage odms-family in compartment <compartment>
Allow group <group> to manage goldengate-family in compartment <compartment>
Allow group <group> to use virtual-network-family in compartment <compartment>
Allow group <group> to manage network-security-groups in compartment <compartment>
Allow group <group> to manage secret-family in compartment <compartment>
Allow group <group> to manage object-family in compartment <compartment>
Allow group <group> to read autonomous-database-family in compartment <compartment>
```

For GoldenGate reverse replication, also create a dynamic group:

```
Dynamic Group: ALL {resource.type = 'goldengatedeployment', resource.compartment.id = '<ocid>'}
Policy: Allow dynamic-group GGDeployments to read secret-bundles in compartment <compartment>
```

### Source and Target Database Preparation (`dms-db-prep-v2.sh` Replacement)

Oracle provides an interactive bash script (`dms-db-prep-v2.sh`) that generates SQL to prepare source and target databases for DMS migrations. **This tool replaces that script entirely** — you do not need to download or run it.

The built-in assessment (`./migrate assess`) covers all checks from the Oracle script and more:

| Oracle `dms-db-prep-v2.sh` | This tool (`migrate.py assess`) |
|---|---|
| Archive log mode | `ARCHIVELOG_MODE` — auto-skipped on RDS |
| Force logging | `FORCE_LOGGING` — uses `rdsadmin` on RDS |
| Supplemental logging (min) | `SUPPLEMENTAL_LOG_MIN` + `SUPPLEMENTAL_LOG_PK` + per-table `SUPPLEMENTAL_LOG_ALL_COLUMNS` |
| Streams pool size | `STREAMS_POOL_SIZE` (min 256MB) |
| `enable_goldengate_replication` | `ENABLE_GG_REPLICATION` |
| Create GGADMIN user + 10 grants | `GGADMIN_EXISTS` + `GGADMIN_UNLOCKED` + `GGADMIN_PRIVILEGES` (23+ grants) |
| `DBMS_GOLDENGATE_AUTH.GRANT_ADMIN_PRIVILEGE` | `GGADMIN_GG_AUTH` |
| CDB/PDB user handling (`c##ggadmin`) | Multitenant support in DMS connections |
| Global names check | Included in assessment |
| Job queue processes | Included in assessment |
| Data Pump directory (not in Oracle script) | `DATAPUMP_DIR` + `DATAPUMP_DIR_GRANTS` |
| — | 20+ additional checks (schema validation, datatypes, target ADB state, OCI infra) |

> **Note:** The checks in this tool were aligned with the `dms-db-prep-v2.sh` script as of February 2026. Oracle may release updated versions through My Oracle Support (MOS). It is recommended to periodically check the corresponding MOS Doc ID for newer revisions of the script and verify that no new preparation steps have been added.

**Key differences from the Oracle script:**

- **Non-interactive**: connects to the actual database, runs checks, and reports results — no manual Q&A
- **Variant-aware**: adjusts automatically for on-prem, RDS (`rdsadmin` procedures), and ExaCS (`dbaascli`)
- **Generates executable SQL**: `./migrate assess --generate-sql` produces a remediation script equivalent to the Oracle script's `DMS_Configuration.sql`
- **Can execute fixes directly**: `./migrate assess --remediate --source <key>` applies fixes interactively with confirmation
- **Covers both source and target**: a single command assesses all configured databases

---

## File Structure

```
oci-db-migrations-skill/
├── migrate.py                         # CLI entry point
├── migration-config.json.example      # Configuration template
├── migration-journal-config.json      # Pointer to journal location (tracked in git)
├── migration-journal.json             # Team audit log (NOT in git, gitignored)
├── requirements.txt                   # Python dependencies
│
├── kb/                                # Knowledge Base
│   ├── prerequisites.yaml             # 40+ checks: source, target, OCI
│   └── errors.yaml                    # Error patterns + diagnosis + fixes
│
├── core/                              # Engine layer
│   ├── config.py                      # Config loader + validator
│   ├── db_connector.py                # Auto-detect DB connector
│   ├── kb_loader.py                   # KB query interface
│   ├── oci_client.py                  # OCI SDK factory + CLI fallback
│   └── oci_config_validator.py        # ~/.oci/config validation + setup
│
├── assessment/                        # Pre-migration assessment
│   ├── engine.py                      # Discovery + gap analysis
│   ├── remediation.py                 # SQL generator + executor
│   └── report.py                      # Terminal / JSON renderer
│
├── operations/                        # Migration pipeline
│   ├── base.py                        # Idempotent operation pattern
│   ├── pipeline.py                    # Step orchestrator
│   ├── status.py                      # Resource state snapshot
│   ├── op_01_vault_secrets.py         # Verify pre-created vault secrets (read-only)
│   ├── op_02_network_nsg.py           # Verify existing NSG (read-only)
│   ├── op_03_dms_connections.py       # DMS connections
│   ├── op_04_dms_migration.py         # Migration create + validate + start
│   └── op_05_goldengate.py            # GG deployment + reverse replication
│
├── ai/                                # AI skill (LLM-agnostic)
│   ├── SKILL.md                       # System prompt for any capable LLM
│   ├── generate_skill.py              # Compiles KB → SKILL.md
│   └── prompts/                       # Interaction templates
│       ├── config-generation.md
│       ├── troubleshooting.md
│       └── cutover-readiness.md
│
├── .claude/                           # Claude Code integration
│   ├── settings.json                  # Deny rules + hooks (tracked in git)
│   ├── settings.local.json            # Per-user permissions (gitignored)
│   └── hooks/
│       └── scope-guard.sh             # PreToolUse hook: blocks OCI ops outside DMS scope
│
├── templates/                         # GG parameter file templates
└── docs/                              # Additional documentation
```

---

## Scope & Safety

The skill enforces strict boundaries on what it can create, modify, or delete. Two layers of protection ensure the AI assistant stays within its scope:

### What the skill CAN manage (DMS scope)

- DMS connections (source, target, CDB)
- DMS migrations (create, validate, start)
- GoldenGate deployments (for reverse replication)

### What the skill CANNOT touch

All other OCI resources must exist before the skill runs. The skill will never attempt to create, modify, or delete:

- IAM policies or dynamic groups
- VCNs, subnets, NSGs, route tables, gateways
- Vaults or encryption keys
- Vault secrets
- Object Storage buckets
- Autonomous Databases or DB Systems

### How it's enforced

| Layer | Mechanism | Location |
|-------|-----------|----------|
| **Deny rules** | Hard blocks on dangerous command patterns | `.claude/settings.json` |
| **PreToolUse hook** | Validates every Bash command before execution | `.claude/hooks/scope-guard.sh` |
| **SKILL.md instructions** | Behavioral guardrails in the AI system prompt | `ai/SKILL.md` |

If the skill detects a command outside its scope, it blocks it with an explanation of why and what the user should do instead (e.g., "Inform your security team about the required IAM policies").

---

## Official Oracle References

- [OCI Database Migration Service](https://docs.oracle.com/en-us/iaas/database-migration/doc/overview-database-migration.html)
- [Preparing an Oracle Source Database](https://docs.oracle.com/en-us/iaas/database-migration/doc/preparing-oracle-source-database.html)
- [Selecting Objects for Migration](https://docs.oracle.com/en-us/iaas/database-migration/doc/selecting-objects-oracle-migration.html)
- [DMS Known Issues](https://docs.oracle.com/en/cloud/paas/database-migration/known-issues/index.html)
- [OCI GoldenGate](https://docs.oracle.com/en-us/iaas/goldengate/doc/overview-goldengate.html)
- [OCI GoldenGate IAM Policies](https://docs.oracle.com/en-us/iaas/goldengate/doc/policies.html)

## Disclaimer

This is **not** an official Oracle product. This project is an independent, community-driven tool and is not affiliated with, endorsed by, or supported by Oracle Corporation. Oracle, OCI, Oracle Database, Oracle GoldenGate, and all related names and logos are trademarks or registered trademarks of Oracle Corporation and/or its affiliates. All rights reserved.

## License

MIT
