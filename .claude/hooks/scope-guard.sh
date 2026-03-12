#!/usr/bin/env bash
# scope-guard.sh — PreToolUse hook for Bash commands
# Blocks OCI CLI commands that create/modify/delete resources outside DMS scope.
#
# Allowed write operations (DMS scope only):
#   oci database-migration connection create|update|delete
#   oci database-migration migration create|update|delete
#   oci database-migration job create
#   python3 migrate.py (any subcommand)
#
# Blocked write operations (outside scope):
#   oci iam policy|dynamic-group create|update|delete
#   oci network nsg|vcn|subnet|security-list create|update|delete
#   oci kms vault|key create|update|delete|schedule-deletion
#   oci vault secret create|update|delete|schedule-deletion
#   oci os bucket create|delete
#   oci db autonomous-database create|update|delete|stop|start
#   rm -rf / or other destructive system commands

set -euo pipefail

# Read the tool input from stdin (JSON with tool_name and tool_input)
INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || echo "")

# Only guard Bash tool calls
if [ "$TOOL_NAME" != "Bash" ]; then
    exit 0
fi

COMMAND=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")

# Allow python3 migrate.py commands (our tool)
if echo "$COMMAND" | grep -qE '^python3?\s+migrate\.py'; then
    exit 0
fi

# Allow python3 one-liners (SDK introspection, compile checks)
if echo "$COMMAND" | grep -qE '^python3?\s+-c\s'; then
    exit 0
fi

# Allow read-only OCI CLI commands (list, get, ns get)
if echo "$COMMAND" | grep -qE 'oci\s+.*\s+(list|get)\b'; then
    exit 0
fi

# Allow non-OCI commands (git, cat, echo, whoami, etc.)
if ! echo "$COMMAND" | grep -qE '\boci\s+'; then
    exit 0
fi

# --- Block dangerous OCI write commands outside DMS scope ---

# Block IAM modifications
if echo "$COMMAND" | grep -qEi 'oci\s+iam\s+(policy|dynamic-group)\s+(create|update|delete)'; then
    echo "BLOCKED: IAM policy/dynamic-group modifications are outside skill scope."
    echo "The skill only manages DMS connections, migrations, and GoldenGate deployments."
    echo "Inform the user what policies are needed and let their security team handle it."
    exit 2
fi

# Block network resource modifications
if echo "$COMMAND" | grep -qEi 'oci\s+network\s+(nsg|vcn|subnet|security-list|route-table|internet-gateway|nat-gateway|drg)\s+(create|update|delete)'; then
    echo "BLOCKED: Network resource modifications are outside skill scope."
    echo "VCNs, subnets, and NSGs must be pre-created by the user."
    exit 2
fi

# Block vault/key modifications
if echo "$COMMAND" | grep -qEi 'oci\s+kms\s+(vault|management\s+key)\s+(create|update|delete|schedule-deletion)'; then
    echo "BLOCKED: Vault/Key modifications are outside skill scope."
    echo "Vaults and keys must be pre-created by the user."
    exit 2
fi

# Block secret modifications
if echo "$COMMAND" | grep -qEi 'oci\s+vault\s+secret\s+(create|update|delete|schedule-deletion)'; then
    echo "BLOCKED: Vault secret modifications are outside skill scope."
    echo "Secrets must be pre-created by the user."
    exit 2
fi

# Block bucket modifications
if echo "$COMMAND" | grep -qEi 'oci\s+os\s+bucket\s+(create|delete)'; then
    echo "BLOCKED: Object Storage bucket modifications are outside skill scope."
    echo "Buckets must be pre-created by the user."
    exit 2
fi

# Block database modifications
if echo "$COMMAND" | grep -qEi 'oci\s+db\s+(autonomous-database|db-system|database)\s+(create|update|delete|stop|terminate)'; then
    echo "BLOCKED: Database modifications are outside skill scope."
    echo "Databases must be pre-created and managed by the user."
    exit 2
fi

# Allow DMS-specific write commands (our scope)
if echo "$COMMAND" | grep -qEi 'oci\s+database-migration\s+(connection|migration|job)\s+(create|update|delete)'; then
    exit 0
fi

# Any other OCI write command — warn but allow (user will be prompted by Claude Code)
exit 0
