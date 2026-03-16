# OCI Resource Discovery — Command Reference

## Discovery Sequence

1. **Verify OCI CLI authentication**: `oci iam region-subscription list --profile <profile>`
   - If it fails with config error: guide through `oci setup config` (API key) or `oci session authenticate` (browser SSO)
   - If session token expired: guide through `oci session authenticate --region <region> --profile <profile>`
2. **Get tenancy OCID** from `~/.oci/config` for the specified profile
3. **List compartments**: present as numbered list, user picks one (or auto-select if the ADB OCID reveals the compartment)
4. **From the selected compartment, discover in parallel**:
   - VCNs and subnets
   - Vaults and keys
   - Buckets
   - ADBs (if target OCID not already provided)
   - Object Storage namespace
5. **If ADB OCID is provided**, get its details to auto-discover: compartment, subnet, VCN (follow the subnet → VCN relationship)
6. **Present results as numbered lists** and let the user pick, or auto-select when there is only one option
7. **Populate `migration-config.json`** with all discovered OCIDs — the user never needs to copy-paste OCIDs

## CLI Commands

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
