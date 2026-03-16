# OCI Resource Discovery — Command Reference

## Discovery Philosophy

Discovery focuses on **DMS migrations and connections first** — these are the primary objects of interest. Infrastructure resources (VCN, subnet, vault, bucket, ADB) are discovered **on demand**, only when needed to configure a new migration or inspect an existing one.

## Discovery Sequence

### Phase 1 — Authentication & Compartment Selection

1. **Verify OCI CLI authentication**: `oci iam region-subscription list --profile <profile>`
   - If it fails with config error: guide through `oci setup config` (API key) or `oci session authenticate` (browser SSO)
   - If session token expired: guide through `oci session authenticate --region <region> --profile <profile>`
2. **Get tenancy OCID** from `~/.oci/config` for the specified profile
3. **List compartments**: present as numbered list, user picks one

### Phase 2 — DMS Migrations & Connections (primary discovery)

4. **List DMS migrations** in the selected compartment — show name, state, type, source/target
5. **List DMS connections** in the selected compartment — show name, type, state
6. **Present findings**: what migrations exist, their current state, which connections they use
7. If no migrations exist, inform the user and proceed to project setup

### Phase 3 — Infrastructure (on demand, only when needed)

Only discover these resources when configuring a new migration or inspecting an existing one:

8. **VCNs and subnets** — when selecting networking for a new migration
9. **Vaults and keys** — when configuring encryption for a new migration
10. **Buckets and Object Storage namespace** — when configuring transfer medium
11. **ADBs** — when selecting a target database
12. **NSGs** — when configuring network security for DMS connections

**If ADB OCID is provided**, get its details to auto-discover: compartment, subnet, VCN (follow the subnet → VCN relationship).

**Present results as numbered lists** and let the user pick, or auto-select when there is only one option.

## CLI Commands

### DMS (Phase 2)

```bash
# List DMS migrations in a compartment
oci database-migration migration list --compartment-id <compartment_ocid> --query "data.items[].{name:\"display-name\", id:id, state:\"lifecycle-state\", type:type, \"source-db\":\"source-database-connection-id\", \"target-db\":\"target-database-connection-id\"}" --output table

# Get migration details
oci database-migration migration get --migration-id <migration_ocid> --output json

# List DMS connections in a compartment
oci database-migration connection list --compartment-id <compartment_ocid> --query "data.items[].{name:\"display-name\", id:id, state:\"lifecycle-state\", \"db-type\":\"database-type\", \"tech-type\":\"technology-type\"}" --output table

# Get connection details
oci database-migration connection get --connection-id <connection_ocid> --output json
```

### Infrastructure (Phase 3 — on demand)

```bash
# Compartments (use tenancy OCID for root-level listing)
oci iam compartment list --compartment-id <tenancy_ocid> --compartment-id-in-subtree true --query "data[?\"lifecycle-state\"=='ACTIVE'].{name:name, id:id}" --output table

# VCNs in a compartment
oci network vcn list --compartment-id <compartment_ocid> --query "data[].{name:\"display-name\", id:id, cidr:\"cidr-blocks\"}" --output table

# Subnets in a compartment (optionally filter by VCN)
oci network subnet list --compartment-id <compartment_ocid> --vcn-id <vcn_ocid> --query "data[].{name:\"display-name\", id:id, cidr:\"cidr-block\", access:\"prohibit-public-ip-ingress\"}" --output table

# Get subnet details (to find its VCN)
oci network subnet get --subnet-id <subnet_ocid> --query "data.{name:\"display-name\", \"vcn-id\":\"vcn-id\", cidr:\"cidr-block\", \"compartment-id\":\"compartment-id\"}" --output table

# NSGs in a VCN (user must select an existing one — the tool does not create NSGs)
oci network nsg list --compartment-id <compartment_ocid> --vcn-id <vcn_ocid> --query "data[?\"lifecycle-state\"=='AVAILABLE'].{name:\"display-name\", id:id}" --output table

# Vaults in a compartment
oci kms management vault list --compartment-id <compartment_ocid> --query "data[?\"lifecycle-state\"=='ACTIVE'].{name:\"display-name\", id:id, endpoint:\"management-endpoint\"}" --output table

# Keys in a vault (use --endpoint, NOT --management-endpoint)
oci kms management key list --compartment-id <compartment_ocid> --endpoint <vault_management_endpoint> --query "data[?\"lifecycle-state\"=='ENABLED'].{name:\"display-name\", id:id, algorithm:algorithm}" --output table

# Buckets
oci os bucket list --compartment-id <compartment_ocid> --query "data[].{name:name}" --output table

# Object Storage namespace
oci os ns get --query "data" --raw-output

# ADB details
oci db autonomous-database get --autonomous-database-id <adb_ocid> --query "data.{name:\"display-name\", state:\"lifecycle-state\", \"db-name\":\"db-name\", \"private-endpoint-ip\":\"private-endpoint-ip\", \"subnet-id\":\"subnet-id\", \"compartment-id\":\"compartment-id\"}" --output table

# List ADBs in a compartment
oci db autonomous-database list --compartment-id <compartment_ocid> --query "data[?\"lifecycle-state\"=='AVAILABLE'].{name:\"display-name\", id:id, \"db-name\":\"db-name\"}" --output table
```

## Important Notes

- Always add `--profile <profile>` when the user specifies a non-DEFAULT profile
- Suppress warnings with env vars: `export OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING=True && export SUPPRESS_LABEL_WARNING=True`
- For key listing, use `--endpoint` (not `--management-endpoint`) with the vault's management endpoint URL
- When resources span multiple compartments (e.g., ADB in one, networking in another), follow the OCID references to discover related resources automatically
- If a compartment has no vaults/buckets, search in parent or sibling compartments (security, networking)
