#!/bin/bash
# =============================================================================
# OCI Database Migration AI Skill — Setup
# =============================================================================
# Auto-detects the best Python available, validates version,
# and installs dependencies accordingly.
#
# Usage:
#   ./setup.sh              # Auto-detect and install
#   ./setup.sh --check      # Check only, don't install
# =============================================================================

set -e

MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=8
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors (if terminal supports them)
if [ -t 1 ]; then
    GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
    BLUE='\033[0;34m'; NC='\033[0m'; BOLD='\033[1m'
else
    GREEN=''; RED=''; YELLOW=''; BLUE=''; NC=''; BOLD=''
fi

ok()   { echo -e "  ${GREEN}✅${NC} $1"; }
fail() { echo -e "  ${RED}❌${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠️ ${NC} $1"; }
info() { echo -e "  ${BLUE}ℹ️ ${NC} $1"; }

# =============================================================================
# Find best Python >= 3.8
# =============================================================================
find_python() {
    local best_python=""
    local best_version=0

    # Candidates in preference order (specific versions first, then generic)
    local candidates=(
        python3.13 python3.12 python3.11 python3.10 python3.9 python3.8
        python3 python
    )

    for cmd in "${candidates[@]}"; do
        if command -v "$cmd" &>/dev/null; then
            # Get version
            local version_str
            version_str=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+\.\d+' | head -1)
            if [ -z "$version_str" ]; then
                continue
            fi

            local major minor
            major=$(echo "$version_str" | cut -d. -f1)
            minor=$(echo "$version_str" | cut -d. -f2)

            # Check minimum version
            if [ "$major" -ge "$MIN_PYTHON_MAJOR" ] && [ "$minor" -ge "$MIN_PYTHON_MINOR" ]; then
                local version_num=$((major * 100 + minor))
                if [ "$version_num" -gt "$best_version" ]; then
                    best_version=$version_num
                    best_python="$cmd"
                fi
            fi
        fi
    done

    echo "$best_python"
}

# =============================================================================
# Check if pip module is available for a given Python
# =============================================================================
check_pip() {
    local python_cmd="$1"
    "$python_cmd" -m pip --version &>/dev/null
}

# =============================================================================
# Install dependencies
# =============================================================================
install_deps() {
    local python_cmd="$1"
    local pip_flags=""

    # Detect if we need --user (not root and not in venv)
    if [ "$(id -u)" -ne 0 ] && [ -z "$VIRTUAL_ENV" ]; then
        pip_flags="--user"
    fi

    # Detect if we need --break-system-packages (Python 3.11+ on some distros)
    if "$python_cmd" -m pip install --help 2>&1 | grep -q "break-system-packages"; then
        pip_flags="$pip_flags --break-system-packages"
    fi

    echo ""
    echo -e "${BOLD}Installing core dependencies...${NC}"
    "$python_cmd" -m pip install $pip_flags oci pyyaml 2>&1 | tail -5

    echo ""
    echo -e "${BOLD}Installing optional dependencies...${NC}"

    # oracledb (requires 3.8+, already validated)
    if "$python_cmd" -m pip install $pip_flags oracledb 2>&1 | tail -3; then
        ok "oracledb installed (thin + thick mode DB connector)"
    else
        warn "oracledb failed — sqlplus fallback will be used"
    fi

    # rich (terminal UI)
    if "$python_cmd" -m pip install $pip_flags rich 2>&1 | tail -3; then
        ok "rich installed (enhanced terminal output)"
    else
        warn "rich failed — plain text output will be used"
    fi
}

# =============================================================================
# Generate launcher script
# =============================================================================
generate_launcher() {
    local python_cmd="$1"
    local launcher="${SCRIPT_DIR}/migrate"

    cat > "$launcher" << EOF
#!/bin/bash
# Auto-generated launcher — uses detected Python
cd "${SCRIPT_DIR}" || exit 1
exec $python_cmd "${SCRIPT_DIR}/migrate.py" "\$@"
EOF
    chmod +x "$launcher"
    ok "Created launcher: ./migrate"
    info "Usage: ./migrate probe"
    info "       ./migrate assess"
    info "       ./migrate deploy"
}

# =============================================================================
# Main
# =============================================================================
echo ""
echo -e "${BOLD}OCI Database Migration AI Skill — Setup${NC}"
echo "========================================="
echo ""

# 1. Find Python
echo -e "${BOLD}Detecting Python...${NC}"

PYTHON_CMD=$(find_python)

if [ -z "$PYTHON_CMD" ]; then
    fail "No Python >= ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} found"
    echo ""
    echo "  Available Python versions on this system:"
    compgen -c python 2>/dev/null | grep -E '^python[0-9]' | sort -u | while read -r cmd; do
        ver=$($cmd --version 2>&1 || echo "unknown")
        echo "    $cmd → $ver"
    done
    echo ""
    echo "  Install Python 3.8+:"
    echo "    Oracle Linux 7: sudo yum install -y python38"
    echo "    Oracle Linux 8: sudo dnf install -y python38"
    echo "    Ubuntu/Debian:  sudo apt install -y python3.10"
    exit 1
fi

PYTHON_VERSION=$("$PYTHON_CMD" --version 2>&1)
ok "Found: $PYTHON_CMD ($PYTHON_VERSION)"

# 2. Check pip
echo ""
echo -e "${BOLD}Checking pip...${NC}"

if check_pip "$PYTHON_CMD"; then
    PIP_VERSION=$("$PYTHON_CMD" -m pip --version 2>&1 | head -1)
    ok "pip available: $PIP_VERSION"
else
    fail "pip not available for $PYTHON_CMD"
    echo ""
    echo "  Install pip:"
    echo "    $PYTHON_CMD -m ensurepip --upgrade"
    echo "    # or: curl https://bootstrap.pypa.io/get-pip.py | $PYTHON_CMD"
    exit 1
fi

# 3. Check-only mode
if [ "$1" = "--check" ]; then
    echo ""
    echo -e "${BOLD}Checking installed packages...${NC}"

    for pkg in oci oracledb pyyaml rich; do
        if "$PYTHON_CMD" -c "import $pkg" &>/dev/null; then
            ver=$("$PYTHON_CMD" -c "import $pkg; print(getattr($pkg, '__version__', 'ok'))" 2>/dev/null)
            ok "$pkg ($ver)"
        else
            fail "$pkg not installed"
        fi
    done

    echo ""
    echo -e "${BOLD}Checking tools...${NC}"

    for cmd in sqlplus sql oci jq; do
        if command -v "$cmd" &>/dev/null; then
            ok "$cmd found"
        else
            warn "$cmd not found"
        fi
    done

    echo ""
    exit 0
fi

# 4. Install
install_deps "$PYTHON_CMD"

# 5. Generate launcher
echo ""
echo -e "${BOLD}Generating launcher...${NC}"
generate_launcher "$PYTHON_CMD"

# 6. Verify
echo ""
echo -e "${BOLD}Verification...${NC}"
"$PYTHON_CMD" -c "
import sys
print(f'  Python: {sys.version}')
pkgs = {'oci': False, 'oracledb': False, 'yaml': False, 'rich': False}
for pkg in pkgs:
    try:
        __import__(pkg)
        pkgs[pkg] = True
    except ImportError:
        pass
for pkg, ok in pkgs.items():
    sym = '✅' if ok else '❌'
    print(f'  {sym} {pkg}')
"

echo ""
echo -e "${GREEN}${BOLD}Setup complete.${NC}"
echo ""
echo "  Next steps:"
echo "    ./migrate probe              # Verify environment + OCI config"
echo "    ./migrate setup-oci          # Configure OCI credentials (if needed)"
echo "    cp migration-config.json.example migration-config.json"
echo "    ./migrate validate-config    # Validate your configuration"
echo "    ./migrate assess             # Run pre-migration assessment"
echo ""
