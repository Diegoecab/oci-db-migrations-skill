"""
OCI configuration validator and guided setup.

Validates ~/.oci/config, checks API key, tests authentication.
Provides guided setup if config is missing or broken.

Used by:
  - migrate.py probe (quick check)
  - migrate.py setup-oci (guided setup)
  - assessment engine (pre-flight)
"""

import configparser
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.oci/config")
DEFAULT_KEY_PATH = os.path.expanduser("~/.oci/oci_api_key.pem")

REQUIRED_FIELDS = ["user", "fingerprint", "tenancy", "region", "key_file"]


@dataclass
class OCIConfigCheck:
    """Result of OCI config validation."""
    config_path: str = ""
    profile: str = "DEFAULT"
    file_exists: bool = False
    profile_exists: bool = False
    fields_present: Dict[str, bool] = field(default_factory=dict)
    missing_fields: List[str] = field(default_factory=list)
    key_file_path: str = ""
    key_file_exists: bool = False
    key_file_readable: bool = False
    auth_test_passed: bool = False
    auth_error: Optional[str] = None
    region: Optional[str] = None
    tenancy_ocid: Optional[str] = None
    user_ocid: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return (self.file_exists and self.profile_exists
                and not self.missing_fields and self.key_file_exists
                and self.key_file_readable)

    @property
    def is_authenticated(self) -> bool:
        return self.is_valid and self.auth_test_passed


def validate_oci_config(
    config_path: str = DEFAULT_CONFIG_PATH,
    profile: str = "DEFAULT",
    test_auth: bool = True,
) -> OCIConfigCheck:
    """
    Validate OCI CLI/SDK configuration.

    Checks:
      1. Config file exists
      2. Profile exists in config
      3. Required fields present (user, fingerprint, tenancy, region, key_file)
      4. API key file exists and is readable
      5. (Optional) Test authentication with a lightweight API call
    """
    result = OCIConfigCheck(config_path=config_path, profile=profile)

    # 1. File exists
    result.file_exists = os.path.isfile(config_path)
    if not result.file_exists:
        return result

    # 2. Parse and check profile
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path)
    except Exception as e:
        logger.error(f"Cannot parse {config_path}: {e}")
        return result

    result.profile_exists = profile in parser
    if not result.profile_exists:
        return result

    section = parser[profile]

    # 3. Required fields
    for f in REQUIRED_FIELDS:
        present = f in section and section[f].strip() != ""
        result.fields_present[f] = present
        if not present:
            result.missing_fields.append(f)

    # Extract values
    result.region = section.get("region", "").strip()
    result.tenancy_ocid = section.get("tenancy", "").strip()
    result.user_ocid = section.get("user", "").strip()

    # 4. Key file
    key_file = section.get("key_file", "").strip()
    # Expand ~ and env vars
    key_file = os.path.expanduser(os.path.expandvars(key_file))
    result.key_file_path = key_file
    result.key_file_exists = os.path.isfile(key_file)

    if result.key_file_exists:
        try:
            with open(key_file, "r") as f:
                content = f.read(50)
                result.key_file_readable = "BEGIN" in content  # PEM marker
        except PermissionError:
            result.key_file_readable = False

    # 5. Test authentication
    if test_auth and result.is_valid:
        result.auth_test_passed, result.auth_error = _test_authentication(
            config_path, profile
        )

    return result


def _test_authentication(config_path: str, profile: str) -> Tuple[bool, Optional[str]]:
    """Test OCI authentication with a lightweight API call."""
    try:
        import oci
        config = oci.config.from_file(file_location=config_path, profile_name=profile)
        oci.config.validate_config(config)

        # Lightweight call: get tenancy (always works if auth is valid)
        identity = oci.identity.IdentityClient(config)
        identity.get_tenancy(config["tenancy"])
        return True, None

    except ImportError:
        # No OCI SDK — try CLI
        import subprocess
        try:
            proc = subprocess.run(
                ["oci", "iam", "region", "list", "--output", "json",
                 "--config-file", config_path, "--profile", profile],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode == 0:
                return True, None
            else:
                error = proc.stderr.strip() or proc.stdout.strip()
                return False, error[:200]
        except FileNotFoundError:
            return False, "Neither OCI SDK nor OCI CLI available"
        except subprocess.TimeoutExpired:
            return False, "Authentication test timed out (15s)"

    except Exception as e:
        return False, str(e)[:200]


def print_validation_report(check: OCIConfigCheck):
    """Print human-readable validation report."""
    print(f"\nOCI Configuration Check")
    print(f"  Config file: {check.config_path}")
    print(f"  Profile:     {check.profile}")
    print()

    def sym(ok): return "✅" if ok else "❌"

    print(f"  {sym(check.file_exists)} Config file exists")

    if not check.file_exists:
        print(f"\n  Config file not found at {check.config_path}")
        print(f"  Run: python migrate.py setup-oci")
        print(f"  Or:  oci setup config")
        return

    print(f"  {sym(check.profile_exists)} Profile [{check.profile}] exists")

    if not check.profile_exists:
        print(f"\n  Profile '{check.profile}' not found in {check.config_path}")
        print(f"  Available profiles can be listed with: grep '\\[' {check.config_path}")
        return

    # Fields
    for f in REQUIRED_FIELDS:
        present = check.fields_present.get(f, False)
        print(f"  {sym(present)} {f}")

    # Key file
    if check.key_file_path:
        print(f"  {sym(check.key_file_exists)} Key file: {check.key_file_path}")
        if check.key_file_exists:
            print(f"  {sym(check.key_file_readable)} Key file readable (PEM format)")

    # Auth test
    if check.auth_test_passed:
        print(f"  ✅ Authentication successful")
        print(f"     Region:  {check.region}")
        print(f"     Tenancy: {check.tenancy_ocid}")
    elif check.auth_error:
        print(f"  ❌ Authentication failed: {check.auth_error}")

    # Overall
    print()
    if check.is_authenticated:
        print(f"  ✅ OCI configuration is valid and authenticated")
    elif check.is_valid:
        print(f"  ⚠️  Config looks correct but authentication failed")
        print(f"     Check: fingerprint matches key in OCI Console")
        print(f"     Check: clock is synchronized (sudo hwclock -s)")
    else:
        print(f"  ❌ OCI configuration needs fixes")
        if check.missing_fields:
            print(f"     Missing: {', '.join(check.missing_fields)}")
        print(f"     Run: python migrate.py setup-oci")


def guided_setup(config_path: str = DEFAULT_CONFIG_PATH, profile: str = "DEFAULT"):
    """Interactive guided OCI config setup."""
    print("\n" + "=" * 60)
    print("  OCI Configuration Setup")
    print("=" * 60)
    print()
    print("  This will create/update your OCI config file.")
    print("  You need the following from OCI Console:")
    print("    1. User OCID    (Identity > Users > your user)")
    print("    2. Tenancy OCID (Administration > Tenancy Details)")
    print("    3. Region       (top-right in Console, e.g. us-ashburn-1)")
    print("    4. API Key      (Identity > Users > API Keys)")
    print()

    # Check if file exists
    if os.path.isfile(config_path):
        print(f"  ⚠️  Config file exists: {config_path}")
        overwrite = input("  Overwrite? [y/N] ").strip().lower()
        if overwrite != "y":
            print("  Cancelled.")
            return

    # Gather inputs
    user_ocid = input("  User OCID:    ").strip()
    tenancy_ocid = input("  Tenancy OCID: ").strip()
    region = input("  Region:       ").strip()
    fingerprint = input("  API Key Fingerprint: ").strip()

    key_file = input(f"  API Key file path [{DEFAULT_KEY_PATH}]: ").strip()
    if not key_file:
        key_file = DEFAULT_KEY_PATH

    # Validate inputs
    errors = []
    if not user_ocid.startswith("ocid1.user."):
        errors.append("User OCID should start with 'ocid1.user.'")
    if not tenancy_ocid.startswith("ocid1.tenancy."):
        errors.append("Tenancy OCID should start with 'ocid1.tenancy.'")
    if ":" not in fingerprint:
        errors.append("Fingerprint should be colon-separated hex (aa:bb:cc:...)")

    if errors:
        print("\n  ⚠️  Validation warnings:")
        for e in errors:
            print(f"    - {e}")
        proceed = input("  Continue anyway? [y/N] ").strip().lower()
        if proceed != "y":
            print("  Cancelled.")
            return

    # Write config
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    config_content = f"""[{profile}]
user={user_ocid}
fingerprint={fingerprint}
tenancy={tenancy_ocid}
region={region}
key_file={key_file}
"""

    with open(config_path, "w") as f:
        f.write(config_content)
    os.chmod(config_path, 0o600)

    print(f"\n  ✅ Config written to: {config_path}")
    print(f"  Permissions set to 600 (owner read/write only)")

    # Check key file
    if not os.path.isfile(key_file):
        print(f"\n  ⚠️  API key file not found: {key_file}")
        print(f"  Generate one:")
        print(f"    openssl genrsa -out {key_file} 2048")
        print(f"    openssl rsa -pubout -in {key_file} -out {key_file.replace('.pem', '_public.pem')}")
        print(f"  Then upload the public key to OCI Console > Identity > Users > API Keys")
    else:
        print(f"\n  ✅ API key file found: {key_file}")

    # Test
    print("\n  Testing authentication...")
    check = validate_oci_config(config_path, profile, test_auth=True)
    if check.is_authenticated:
        print(f"  ✅ Authentication successful!")
    else:
        print(f"  ❌ Authentication failed: {check.auth_error}")
        print(f"  Verify your fingerprint and key file, then retry.")
