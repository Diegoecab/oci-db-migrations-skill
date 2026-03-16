#!/usr/bin/env python3
"""
OCI Database Migration AI Skill — CLI entry point.

Usage:
    python migrate.py --assess                          # Full assessment (source + target + OCI)
    python migrate.py --assess --source aws_oracle_prod # Assess specific source
    python migrate.py --assess --target adb_prod        # Assess specific target
    python migrate.py --assess --generate-sql           # Generate remediation SQL
    python migrate.py --assess --remediate              # Interactive remediation execution
    python migrate.py --probe                           # Check available connectors/tools
    python migrate.py --validate-config                 # Validate config only
    python migrate.py --diagnose "ORA-01031"            # KB error lookup
"""

import argparse
import json
import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import MigrationConfig
from core.kb_loader import KnowledgeBase
from core.db_connector import DBConnector


def setup_logging(verbose: bool = False, quiet: bool = False):
    if quiet:
        # JSON output mode: suppress all logs and warnings for clean stdout
        import warnings
        warnings.filterwarnings("ignore")
        logging.basicConfig(level=logging.CRITICAL + 1)
        return
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_probe(args):
    """Check available connector backends and tools."""
    print("\nDatabase Connectors:")
    available = DBConnector.probe_available()
    for name, ok in available.items():
        symbol = "✅" if ok else "❌"
        print(f"  {symbol} {name}")

    print("\nPython Packages:")
    # Check OCI SDK
    try:
        import oci
        print(f"  ✅ oci SDK ({oci.__version__})")
    except ImportError:
        print("  ❌ oci SDK (pip install oci)")

    # Check yaml
    try:
        import yaml
        print(f"  ✅ PyYAML ({yaml.__version__})")
    except ImportError:
        print("  ❌ PyYAML (pip install pyyaml)")

    # Check rich
    try:
        import rich
        print(f"  ✅ rich ({rich.__version__})")
    except ImportError:
        print("  ⚠️  rich (optional: pip install rich)")

    # OCI Configuration
    from core.oci_config_validator import validate_oci_config, print_validation_report
    profile = "DEFAULT"

    # If config file is loaded, use its profile
    if os.path.isfile(args.config):
        try:
            config = MigrationConfig(args.config)
            if config.load():
                profile = config.oci.get("config_profile", "DEFAULT")
        except Exception:
            pass

    check = validate_oci_config(profile=profile, test_auth=True)
    print_validation_report(check)
    print()


def cmd_setup_oci(args):
    """Guided OCI configuration setup."""
    from core.oci_config_validator import guided_setup
    profile = args.profile or "DEFAULT"
    guided_setup(profile=profile)


def cmd_validate_config(args):
    """Validate configuration file."""
    config = MigrationConfig(args.config)
    if config.load():
        print("✅ Configuration is valid.")
        if config.warnings:
            print("\nWarnings:")
            for w in config.warnings:
                print(f"  ⚠️  {w}")

        # Summary
        print(f"\n  Sources:    {len(config.source_databases)}")
        print(f"  Targets:    {len(config.target_databases)}")
        print(f"  Migrations: {len(config.migrations)}")
        for mig_key in config.migrations:
            scope = config.migration_scope(mig_key)
            print(f"    {mig_key}: schemas={list(scope.schemas)}")
    else:
        print("❌ Configuration errors:")
        for e in config.errors:
            print(f"  • {e}")
        sys.exit(1)


def cmd_assess(args):
    """Run migration assessment."""
    # Load config
    config = MigrationConfig(args.config)
    if not config.load():
        print("❌ Configuration errors:")
        for e in config.errors:
            print(f"  • {e}")
        sys.exit(1)

    # Load KB
    kb = KnowledgeBase()
    if not kb.load():
        print("❌ Failed to load Knowledge Base")
        sys.exit(1)

    # OCI factory (optional — degrades gracefully)
    oci_factory = None
    try:
        from core.oci_client import OCIClientFactory
        oci_factory = OCIClientFactory(
            config_profile=config.oci.get("config_profile", "DEFAULT"),
            region=config.oci.get("region"),
        )
    except Exception as e:
        logging.warning(f"OCI SDK not available: {e}. OCI checks will be skipped.")

    # Run assessment
    from assessment.engine import AssessmentEngine
    engine = AssessmentEngine(config, kb, oci_factory)

    if args.source:
        reports = [engine.assess_source(args.source)]
    elif args.target:
        reports = [engine.assess_target(args.target)]
    elif args.oci_only:
        reports = [engine.assess_oci()]
    else:
        reports = engine.run_full_assessment()

    # Render
    output_format = args.output or config.assessment_config.get("output_format", "terminal")

    if output_format == "json":
        from assessment.report import render_json
        print(render_json(reports))
    else:
        from assessment.report import render_terminal
        render_terminal(reports)

    # Generate SQL if requested
    if args.generate_sql:
        from assessment.remediation import RemediationGenerator
        gen = RemediationGenerator(reports)
        output_path = args.sql_output or "remediation.sql"
        sql = gen.generate_sql(output_path=output_path)
        print(f"\n📝 Remediation script: {output_path}")

    # Auto-remediate if requested
    if args.remediate:
        from assessment.remediation import RemediationGenerator
        gen = RemediationGenerator(reports)

        # Connect with privileged user
        rem_user = config.assessment_config.get("remediation_user", "SYS")
        rem_as = config.assessment_config.get("remediation_connect_as", "SYSDBA")

        if args.source:
            src = config.source_db(args.source)
            if not src:
                print(f"❌ Source '{args.source}' not found")
                sys.exit(1)

            print(f"\n⚠️  Will execute remediation as {rem_user} (AS {rem_as})")
            print(f"   Target: {src.get('host')}:{src.get('port')}/{src.get('service_name')}")
            confirm = input("   Proceed? [y/N] ").strip().lower()
            if confirm != "y":
                print("Cancelled.")
                return

            password = input(f"   Password for {rem_user}: ").strip()
            preference = config.assessment_config.get("db_connector_preference", "auto")

            try:
                connector = DBConnector.create(
                    host=src["host"], port=src["port"],
                    service_name=src["service_name"],
                    user=rem_user, password=password,
                    preference=preference,
                    connect_as=rem_as,
                )
                results = gen.execute_remediation(connector, scope="source")
                connector.close()

                succeeded = sum(1 for r in results if r.get("success"))
                failed = sum(1 for r in results if r.get("success") is False)
                skipped = sum(1 for r in results if r.get("success") is None)
                print(f"\nRemediation: {succeeded} succeeded, {failed} failed, {skipped} skipped")

            except Exception as e:
                print(f"❌ Connection failed: {e}")
                sys.exit(1)
        else:
            print("❌ --remediate requires --source <key> to specify which DB to fix")
            sys.exit(1)


def cmd_deploy(args):
    """Run migration pipeline (create OCI resources)."""
    config = MigrationConfig(args.config)
    if not config.load():
        print("❌ Configuration errors:")
        for e in config.errors:
            print(f"  • {e}")
        sys.exit(1)

    kb = KnowledgeBase()
    kb.load()

    try:
        from core.oci_client import OCIClientFactory
        oci_factory = OCIClientFactory(
            config_profile=config.oci.get("config_profile", "DEFAULT"),
            region=config.oci.get("region"),
        )
    except Exception as e:
        print(f"❌ OCI SDK required for deploy: {e}")
        sys.exit(1)

    from operations.pipeline import Pipeline

    pipeline = Pipeline(config, kb, oci_factory)

    if args.list_steps:
        pipeline.list_steps()
        return

    if args.step:
        pipeline.run_step(args.step)
    elif args.from_step:
        pipeline.run_from(args.from_step)
    else:
        pipeline.run_all()


def cmd_status(args):
    """Show current state of all migration resources."""
    config = MigrationConfig(args.config)
    if not config.load():
        print(json.dumps({"config_valid": False, "errors": config.errors}, indent=2))
        sys.exit(1)

    try:
        from core.oci_client import OCIClientFactory
        oci_factory = OCIClientFactory(
            config_profile=config.oci.get("config_profile", "DEFAULT"),
            region=config.oci.get("region"),
        )
    except Exception as e:
        print(json.dumps({"error": f"OCI SDK required: {e}"}, indent=2))
        sys.exit(1)

    from operations.status import StatusCollector
    collector = StatusCollector(config, oci_factory)
    status = collector.collect(migration_key=args.migration)

    if args.json:
        print(status.to_json())
    else:
        # Human-readable summary
        print(f"\n{'='*60}")
        print(f"  Migration Status — {status.timestamp}")
        print(f"{'='*60}")

        # Infrastructure
        for res in [status.vault, status.nsg, status.bucket]:
            if res:
                sym = "✅" if res.state in ("ACTIVE", "AVAILABLE") else "❌"
                state_str = res.state or res.error or "UNKNOWN"
                print(f"  {sym} {res.resource_type}: {res.name} [{state_str}]")

        # Migrations
        for m in status.migrations:
            print(f"\n  --- {m.display_name} ({m.migration_type}) ---")

            for label, res in [
                ("Source conn", m.dms_source_connection),
                ("Target conn", m.dms_target_connection),
                ("Migration", m.dms_migration),
                ("Job", m.dms_job),
                ("GG Deployment", m.gg_deployment),
            ]:
                if res:
                    sym = "✅" if res.state in ("ACTIVE", "AVAILABLE", "SUCCEEDED") else \
                          "⏳" if res.state in ("CREATING", "IN_PROGRESS", "WAITING") else \
                          "❌"
                    print(f"    {sym} {label}: {res.state or res.error or 'NOT_FOUND'}")

            if m.recommended_actions:
                print(f"    → Next: {m.recommended_actions[0]}")

        # Global next action
        if status.next_action:
            print(f"\n  🎯 {status.next_action}")
            if status.next_action_command:
                print(f"     $ {status.next_action_command}")
        print()


def cmd_validate_migration(args):
    """Run DMS premigration advisor (evaluate) on migrations."""
    config = MigrationConfig(args.config)
    if not config.load():
        print("❌ Configuration errors:")
        for e in config.errors:
            print(f"  • {e}")
        sys.exit(1)

    try:
        from core.oci_client import OCIClientFactory
        oci_factory = OCIClientFactory(
            config_profile=config.oci.get("config_profile", "DEFAULT"),
            region=config.oci.get("region"),
        )
    except Exception as e:
        print(f"❌ OCI SDK required: {e}")
        sys.exit(1)

    import oci

    dms_client = oci_factory.dms

    # Get migration OCIDs from DMS by matching display names
    compartment = config.oci["compartment_ocid"]
    all_migrations = dms_client.list_migrations(compartment_id=compartment).data

    # Filter to migrations from config
    target_keys = [args.migration] if args.migration else list(config.migrations.keys())
    results = []

    for mig_key in target_keys:
        mig_config = config.migrations.get(mig_key)
        if not mig_config:
            print(f"❌ Migration key '{mig_key}' not found in config")
            continue

        display_name = mig_config.get("display_name", mig_key)

        # Find matching DMS migration
        dms_mig = None
        for m in all_migrations.items:
            if m.display_name == display_name and m.lifecycle_state == "ACTIVE":
                dms_mig = m
                break

        if not dms_mig:
            print(f"❌ {display_name}: DMS migration not found or not ACTIVE")
            continue

        print(f"\n⏳ {display_name}: Starting DMS premigration validation...")
        print(f"   Migration OCID: {dms_mig.id}")

        try:
            response = dms_client.evaluate_migration(migration_id=dms_mig.id)
            work_request_id = response.headers.get("opc-work-request-id", "unknown")
            print(f"   ✅ Validation job submitted (work request: {work_request_id})")
            result_entry = {"key": mig_key, "name": display_name, "status": "submitted", "work_request": work_request_id}

            if args.wait:
                import time
                print(f"   Waiting for validation to complete...")
                final_status = None
                for i in range(120):  # up to 20 min
                    try:
                        wr = dms_client.get_work_request(work_request_id).data
                        pct = wr.percent_complete or 0
                        print(f"\r   Progress: {pct:.0f}% ({wr.status})", end="", flush=True)
                        if wr.status in ("SUCCEEDED", "FAILED", "CANCELED"):
                            print()
                            final_status = wr.status
                            break
                    except Exception:
                        pass
                    time.sleep(10)

                result_entry["final_status"] = final_status or "TIMEOUT"

                if final_status == "FAILED":
                    print(f"   ❌ Validation FAILED for {display_name}")
                    print(f"")
                    print(f"   DMS evaluation fails silently when source DB prerequisites are not met.")
                    print(f"   Common causes:")
                    print(f"   - Missing privileges on migration user (DATAPUMP_EXP, GGADMIN)")
                    print(f"   - Supplemental logging not enabled")
                    print(f"   - GoldenGate replication parameter not set")
                    print(f"   - Network connectivity issues (DMS cannot reach source DB)")
                    print(f"")
                    src_key = mig_config.get("source_db_key", "")
                    print(f"   Run: python3 migrate.py assess --source {src_key}")
                elif final_status == "SUCCEEDED":
                    print(f"   ✅ Validation PASSED for {display_name}")
                    print(f"   Ready to start: python3 migrate.py start-migration --migration {mig_key}")
            else:
                print(f"   Use --wait to poll for results, or check:")
                print(f"   python3 migrate.py status --migration {mig_key} --json")

            results.append(result_entry)
        except oci.exceptions.ServiceError as e:
            print(f"   ❌ Failed: {e.message}")
            results.append({"key": mig_key, "name": display_name, "status": "failed", "error": e.message})

    if args.output == "json":
        print(json.dumps(results, indent=2))

    if not results:
        print("No migrations found to validate.")


def cmd_start_migration(args):
    """Start (run) DMS migration jobs."""
    config = MigrationConfig(args.config)
    if not config.load():
        print("❌ Configuration errors:")
        for e in config.errors:
            print(f"  • {e}")
        sys.exit(1)

    try:
        from core.oci_client import OCIClientFactory
        oci_factory = OCIClientFactory(
            config_profile=config.oci.get("config_profile", "DEFAULT"),
            region=config.oci.get("region"),
        )
    except Exception as e:
        print(f"❌ OCI SDK required: {e}")
        sys.exit(1)

    import oci

    dms = oci_factory.dms
    compartment = config.oci["compartment_ocid"]
    all_migs = dms.list_migrations(compartment_id=compartment).data.items

    target_keys = [args.migration] if args.migration else list(config.migrations.keys())
    results = []

    for mig_key in target_keys:
        mig_config = config.migrations.get(mig_key)
        if not mig_config:
            print(f"❌ Migration key '{mig_key}' not found in config")
            continue

        display_name = mig_config.get("display_name", mig_key)

        dms_mig = None
        for m in all_migs:
            if m.display_name == display_name and m.lifecycle_state == "ACTIVE":
                dms_mig = m
                break

        if not dms_mig:
            print(f"❌ {display_name}: not found or not ACTIVE")
            continue

        # Check for a failed evaluation job blocking start_migration
        blocked_by_failed_eval = False
        try:
            mig_detail = dms.get_migration(dms_mig.id).data
            exec_job_id = getattr(mig_detail, 'executing_job_id', None)
            if exec_job_id:
                exec_job = dms.get_job(exec_job_id).data
                if exec_job.lifecycle_state == "FAILED" and getattr(exec_job, 'type', '') == "EVALUATION":
                    blocked_by_failed_eval = True
                    print(f"\n⚠️  {display_name}: blocked by a FAILED evaluation job")
                    print(f"   Job OCID: {exec_job_id}")
                    print(f"   Job type: EVALUATION | State: FAILED")
                    print(f"")
                    print(f"   This typically means source database prerequisites are not met:")
                    print(f"   - Migration user privileges (DATAPUMP_EXP, GGADMIN)")
                    print(f"   - Supplemental logging, archivelog mode, GoldenGate replication")
                    print(f"   - Network connectivity from DMS to source DB")
                    print(f"")
                    print(f"   Recommended actions:")
                    src_key = mig_config.get('source_db_key', '')
                    print(f"   1. Run assessment:  python3 migrate.py assess --source {src_key}")
                    print(f"   2. Fix prerequisites on the source database")
                    print(f"   3. Re-validate:     python3 migrate.py validate-migration --migration {mig_key} --wait")
                    print(f"   4. Then retry:      python3 migrate.py start-migration --migration {mig_key}")
                    results.append({
                        "key": mig_key, "name": display_name,
                        "status": "blocked",
                        "reason": "failed_evaluation_job",
                        "failed_job_id": exec_job_id,
                    })
        except Exception as e:
            logging.debug(f"Could not check executing job: {e}")

        if blocked_by_failed_eval:
            continue

        print(f"\n🚀 {display_name}: Starting migration job...")
        print(f"   Migration OCID: {dms_mig.id}")
        print(f"   Type: {dms_mig.type}")

        try:
            response = dms.start_migration(migration_id=dms_mig.id)
            work_request_id = response.headers.get("opc-work-request-id", "unknown")
            print(f"   ✅ Migration job started (work request: {work_request_id})")
            results.append({
                "key": mig_key, "name": display_name,
                "status": "started", "work_request": work_request_id,
            })

            if args.wait:
                print(f"   Waiting for job to complete (this may take a long time)...")
                import time
                for i in range(360):  # up to 1 hour
                    try:
                        wr = dms.get_work_request(work_request_id).data
                        pct = wr.percent_complete or 0
                        print(f"\r   Progress: {pct:.0f}% ({wr.status})", end="", flush=True)
                        if wr.status in ("SUCCEEDED", "FAILED", "CANCELED"):
                            print()
                            results[-1]["final_status"] = wr.status
                            break
                    except Exception:
                        pass
                    time.sleep(10)

        except oci.exceptions.ServiceError as e:
            print(f"   ❌ Failed: {e.message}")
            results.append({
                "key": mig_key, "name": display_name,
                "status": "failed", "error": e.message,
            })

    if args.output == "json":
        print(json.dumps(results, indent=2))

    if not results:
        print("No migrations found to start.")
    else:
        started = sum(1 for r in results if r["status"] == "started")
        print(f"\nStarted {started}/{len(target_keys)} migration(s)")
        print("Monitor progress: python3 migrate.py status --json")


def cmd_generate_wallet_script(args):
    """Generate shell script to create SSL wallet on source DB server."""
    config = MigrationConfig(args.config)
    if not config.load():
        print("❌ Configuration errors:")
        for e in config.errors:
            print(f"  • {e}")
        sys.exit(1)

    source_key = args.source or list(config.source_databases.keys())[0]
    src = config.source_db(source_key)
    if not src:
        print(f"❌ Source '{source_key}' not found")
        sys.exit(1)

    region = config.oci.get("region", "us-ashburn-1")
    dp_dir_name = src.get("datapump_dir_name", "DATA_PUMP_DIR")
    dp_dir_path = src.get("datapump_dir_path", "")
    if not dp_dir_path:
        print(f"❌ Source '{source_key}' has no datapump_dir_path configured.")
        sys.exit(1)
    # SSL wallet files go inside the Data Pump directory — no separate directory needed
    wallet_dir = dp_dir_path
    username = src.get("username", "DMS_USER")
    gg_username = src.get("gg_username", "GGADMIN")

    output_path = args.output or os.path.join("scripts", f"setup-ssl-wallet_{source_key}.sh")

    script = f"""#!/bin/bash
# =============================================================================
# SSL Wallet Setup for OCI DMS Data Pump via Object Storage
# Generated by: oci-db-migrations-skill
# Source DB: {source_key} ({src.get('host')}:{src.get('port')}/{src.get('service_name')})
# Region: {region}
# =============================================================================
#
# Run this script ON THE SOURCE DATABASE SERVER as the oracle user.
#
# DMS requires an SSL wallet on the database host for HTTPS Data Pump
# uploads to OCI Object Storage. This script follows Oracle's official
# procedure from the DMS documentation:
#   https://docs.oracle.com/en-us/iaas/database-migration/doc/creating-migrations.html
#
# Method 1 (preferred): Download Oracle's pre-created walletSSL.zip
# Method 2 (fallback):  Create wallet manually with orapki + CA certificate
#
# Prerequisites:
#   - curl or wget for downloading walletSSL.zip
#   - unzip utility
#   - Write access to {wallet_dir}
#   - (Fallback only) orapki in PATH + openssl
# =============================================================================

set -euo pipefail

WALLET_DIR="{wallet_dir}"
REGION="{region}"
WALLET_ZIP="/tmp/walletSSL.zip"

# Oracle's official pre-created SSL wallet download URL
# Ref: https://docs.oracle.com/en-us/iaas/database-migration/doc/creating-migrations.html
WALLET_URL="https://objectstorage.us-phoenix-1.oraclecloud.com/p/YYkalHlLbbrfOAMIor-Mzl1qcFxaAZOvrYABKzRQYPErFQdzJrVjma1cUg4SIXEu/n/axsdric7bk0y/b/SSL-Wallet-For-No-SSH-Migrations-Setup/o/walletSSL.zip"

echo "=== Step 1: Create wallet directory ==="
mkdir -p "$WALLET_DIR"
echo "  Directory: $WALLET_DIR"

echo ""
echo "=== Step 2: Download Oracle's pre-created walletSSL.zip ==="
DOWNLOAD_OK=false
if command -v curl &>/dev/null; then
    if curl -fsSL -o "$WALLET_ZIP" "$WALLET_URL" 2>/dev/null; then
        DOWNLOAD_OK=true
    fi
elif command -v wget &>/dev/null; then
    if wget -q -O "$WALLET_ZIP" "$WALLET_URL" 2>/dev/null; then
        DOWNLOAD_OK=true
    fi
fi

if [ "$DOWNLOAD_OK" = true ] && [ -f "$WALLET_ZIP" ] && [ -s "$WALLET_ZIP" ]; then
    echo "  Downloaded: $WALLET_ZIP"

    echo ""
    echo "=== Step 3: Unzip wallet to $WALLET_DIR ==="
    unzip -o "$WALLET_ZIP" -d "$WALLET_DIR"
    echo "  Wallet files extracted to: $WALLET_DIR"

    echo ""
    echo "=== Step 4: Verify wallet contents ==="
    ls -la "$WALLET_DIR"/
    if [ -f "$WALLET_DIR/cwallet.sso" ]; then
        echo "  ✅ cwallet.sso found"
    else
        echo "  ⚠️  cwallet.sso not found — checking for alternative wallet files..."
        ls -la "$WALLET_DIR"/*.sso "$WALLET_DIR"/*.p12 2>/dev/null || true
    fi

    rm -f "$WALLET_ZIP"
else
    echo "  ⚠️  Could not download walletSSL.zip — falling back to manual wallet creation"
    echo ""
    echo "=== Fallback Step 2b: Download OCI Object Storage CA certificate ==="
    CERT_FILE="/tmp/oci-os-cert.pem"
    openssl s_client -connect objectstorage.${{REGION}}.oraclecloud.com:443 \\
        -showcerts </dev/null 2>/dev/null | \\
        awk '/BEGIN CERTIFICATE/,/END CERTIFICATE/{{print}}' | \\
        awk 'BEGIN{{n=0}} /BEGIN CERTIFICATE/{{n++}} n>0{{print > "/tmp/oci-os-cert-"n".pem"}}'

    LAST_CERT=$(ls -1 /tmp/oci-os-cert-*.pem 2>/dev/null | tail -1)
    if [ -z "$LAST_CERT" ]; then
        echo "  ERROR: Could not download certificate. Check network connectivity."
        exit 1
    fi
    cp "$LAST_CERT" "$CERT_FILE"
    echo "  Certificate saved: $CERT_FILE"

    echo ""
    echo "=== Fallback Step 3b: Create Oracle auto-login wallet ==="
    if [ -f "$WALLET_DIR/cwallet.sso" ]; then
        echo "  Wallet already exists at $WALLET_DIR/cwallet.sso"
    else
        orapki wallet create -wallet "$WALLET_DIR" -auto_login_only
        echo "  Wallet created: $WALLET_DIR/cwallet.sso"
    fi

    echo ""
    echo "=== Fallback Step 4b: Add OCI certificate to wallet ==="
    orapki wallet add -wallet "$WALLET_DIR" \\
        -trusted_cert -cert "$CERT_FILE" -auto_login_only
    echo "  Certificate added to wallet"

    echo ""
    echo "=== Fallback Step 5b: Verify wallet ==="
    orapki wallet display -wallet "$WALLET_DIR" | grep -i "subject\\|trusted"

    rm -f /tmp/oci-os-cert-*.pem "$CERT_FILE"
fi

echo ""
echo "=== Step 5: Set permissions ==="
chmod 640 "$WALLET_DIR"/cwallet.sso 2>/dev/null || true
chmod 640 "$WALLET_DIR"/ewallet.p12 2>/dev/null || true
ls -la "$WALLET_DIR"/

echo ""
echo "=== Done! ==="
echo ""
echo "Now run these SQL commands as SYS/SYSDBA to create/verify the directory object:"
echo ""
echo "  CREATE OR REPLACE DIRECTORY {dp_dir_name} AS '{dp_dir_path}';"
echo "  GRANT READ, WRITE ON DIRECTORY {dp_dir_name} TO {username};"
echo "  GRANT READ, WRITE ON DIRECTORY {dp_dir_name} TO {gg_username};"
echo ""
echo "The SSL wallet files are inside the Data Pump directory ({dp_dir_name})."
echo "Use '{dp_dir_path}' as the SSL Wallet Path in your DMS migration."
echo ""
"""

    with open(output_path, "w") as f:
        f.write(script)

    os.chmod(output_path, 0o755)

    print(f"✅ Wallet setup script generated: {output_path}")
    print(f"")
    print(f"   Source: {source_key} ({src.get('host')})")
    print(f"   Region: {region}")
    print(f"   Wallet dir: {wallet_dir}")
    print(f"   DataPump dir: {dp_dir_name} -> {dp_dir_path}")
    print(f"")
    script_filename = os.path.basename(output_path)
    print(f"   Copy this script to the source DB server and run as oracle user:")
    print(f"   scp {output_path} oracle@{src.get('host')}:/tmp/")
    print(f"   ssh oracle@{src.get('host')} 'bash /tmp/{script_filename}'")


def cmd_cleanup(args):
    """Delete DMS connections or migrations by display name."""
    config = MigrationConfig(args.config)
    if not config.load():
        print("❌ Configuration errors:")
        for e in config.errors:
            print(f"  • {e}")
        sys.exit(1)

    try:
        from core.oci_client import OCIClientFactory
        oci_factory = OCIClientFactory(
            config_profile=config.oci.get("config_profile", "DEFAULT"),
            region=config.oci.get("region"),
        )
    except Exception as e:
        print(f"❌ OCI SDK required: {e}")
        sys.exit(1)

    import oci
    import time

    dms = oci_factory.dms
    compartment = config.oci["compartment_ocid"]
    resource_type = args.type  # "connection" or "migration"
    names = args.name  # list of display names

    if resource_type == "connection":
        items = dms.list_connections(compartment_id=compartment).data.items
    else:
        items = dms.list_migrations(compartment_id=compartment).data.items

    active = {i.display_name: i.id for i in items
              if i.lifecycle_state in ("ACTIVE", "CREATING")}

    deleted = []
    for name in names:
        if name not in active:
            print(f"⚠️  {resource_type} '{name}' not found (or not ACTIVE)")
            continue

        rid = active[name]
        print(f"🗑  Deleting {resource_type}: {name} ({rid})...")
        try:
            if resource_type == "connection":
                dms.delete_connection(rid)
            else:
                dms.delete_migration(rid)

            # Wait for deletion
            for _ in range(30):
                try:
                    if resource_type == "connection":
                        r = dms.get_connection(rid).data
                    else:
                        r = dms.get_migration(rid).data
                    if r.lifecycle_state in ("DELETED", "FAILED"):
                        break
                except oci.exceptions.ServiceError as se:
                    if se.status == 404:
                        break
                    raise
                time.sleep(10)

            print(f"   ✅ Deleted: {name}")
            deleted.append(name)
        except Exception as e:
            print(f"   ❌ Failed: {e}")

    print(f"\nDeleted {len(deleted)}/{len(names)} {resource_type}(s)")


def cmd_diagnose(args):
    """Look up error in KB."""
    kb = KnowledgeBase()
    kb.load()

    error_text = " ".join(args.error_text)
    matches = kb.lookup_errors(error_text)

    if not matches:
        print(f"No KB entry found for: {error_text}")
        print("Try: python migrate.py --diagnose ORA-01031")
        return

    for m in matches:
        print(f"\n{'=' * 60}")
        print(f"  Pattern:     {m.get('pattern', '')}")
        print(f"  Description: {m.get('description', '')}")
        print(f"  Severity:    {m.get('severity', '')}")
        print(f"  Fix:")
        for line in m.get("fix", "").strip().splitlines():
            print(f"    {line}")
        if m.get("doc_url"):
            print(f"  Ref: {m['doc_url']}")


def main():
    parser = argparse.ArgumentParser(
        description="OCI Database Migration AI Skill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", "-c", default="migration-config.json",
                        help="Path to migration-config.json")
    parser.add_argument("--verbose", "-v", action="store_true")

    subparsers = parser.add_subparsers(dest="command")

    # -- probe --
    subparsers.add_parser("probe", help="Check available tools, connectors, and OCI config")

    # -- setup-oci --
    setup_parser = subparsers.add_parser("setup-oci", help="Guided OCI CLI/SDK config setup")
    setup_parser.add_argument("--profile", default="DEFAULT", help="OCI config profile name")

    # -- validate-config --
    subparsers.add_parser("validate-config", help="Validate configuration file")

    # -- assess --
    assess_parser = subparsers.add_parser("assess", help="Run migration assessment")
    assess_parser.add_argument("--source", help="Assess specific source DB key")
    assess_parser.add_argument("--target", help="Assess specific target DB key")
    assess_parser.add_argument("--oci-only", action="store_true", help="Assess OCI infra only")
    assess_parser.add_argument("--output", choices=["terminal", "json"], help="Output format")
    assess_parser.add_argument("--generate-sql", action="store_true",
                               help="Generate remediation SQL script")
    assess_parser.add_argument("--sql-output", default="remediation.sql",
                               help="Output path for SQL script")
    assess_parser.add_argument("--remediate", action="store_true",
                               help="Execute remediation interactively")

    # -- validate-migration --
    val_mig_parser = subparsers.add_parser("validate-migration",
                                            help="Run DMS premigration advisor (evaluate)")
    val_mig_parser.add_argument("--migration", help="Specific migration key (default: all)")
    val_mig_parser.add_argument("--wait", action="store_true", help="Wait for validation to complete and show results")
    val_mig_parser.add_argument("--output", choices=["terminal", "json"], help="Output format")

    # -- diagnose --
    diag_parser = subparsers.add_parser("diagnose", help="Look up error in KB")
    diag_parser.add_argument("error_text", nargs="+", help="Error text to look up")

    # -- generate-wallet-script --
    wallet_parser = subparsers.add_parser("generate-wallet-script",
                                           help="Generate SSL wallet setup script for source DB server")
    wallet_parser.add_argument("--source", help="Source DB key (default: first source)")
    wallet_parser.add_argument("--output", default=None,
                               help="Output script path (default: scripts/setup-ssl-wallet_<source>.sh)")

    # -- start-migration --
    start_parser = subparsers.add_parser("start-migration", help="Start (run) DMS migration jobs")
    start_parser.add_argument("--migration", help="Specific migration key (default: all)")
    start_parser.add_argument("--wait", action="store_true", help="Wait for job completion")
    start_parser.add_argument("--output", choices=["terminal", "json"], help="Output format")

    # -- cleanup --
    cleanup_parser = subparsers.add_parser("cleanup", help="Delete DMS connections or migrations by name")
    cleanup_parser.add_argument("type", choices=["connection", "migration"],
                                help="Resource type to delete")
    cleanup_parser.add_argument("name", nargs="+", help="Display name(s) of resource(s) to delete")

    # -- deploy --
    deploy_parser = subparsers.add_parser("deploy", help="Run migration pipeline")
    deploy_parser.add_argument("--step", type=int, help="Run specific step number")
    deploy_parser.add_argument("--from-step", type=int, help="Run from step N onward")
    deploy_parser.add_argument("--list-steps", action="store_true", help="List available steps")

    # -- status --
    status_parser = subparsers.add_parser("status", help="Show migration resource state")
    status_parser.add_argument("--migration", help="Show specific migration key")
    status_parser.add_argument("--json", action="store_true", help="Output raw JSON (for AI skill)")

    args = parser.parse_args()
    # Detect JSON output mode: suppress logs/warnings for clean stdout
    wants_json = getattr(args, 'json', False) or getattr(args, 'output', None) == 'json'
    setup_logging(args.verbose, quiet=wants_json)

    # Also support --flags directly (backward compat)
    if not args.command:
        # Check for legacy-style flags
        if "--probe" in sys.argv:
            cmd_probe(args)
        elif "--validate-config" in sys.argv:
            cmd_validate_config(args)
        elif "--assess" in sys.argv:
            args.source = None
            args.target = None
            args.oci_only = False
            args.output = None
            args.generate_sql = "--generate-sql" in sys.argv
            args.sql_output = "remediation.sql"
            args.remediate = "--remediate" in sys.argv
            cmd_assess(args)
        elif "--diagnose" in sys.argv:
            idx = sys.argv.index("--diagnose")
            args.error_text = sys.argv[idx + 1:]
            cmd_diagnose(args)
        else:
            parser.print_help()
        return

    if args.command == "probe":
        cmd_probe(args)
    elif args.command == "setup-oci":
        cmd_setup_oci(args)
    elif args.command == "validate-config":
        cmd_validate_config(args)
    elif args.command == "assess":
        cmd_assess(args)
    elif args.command == "generate-wallet-script":
        cmd_generate_wallet_script(args)
    elif args.command == "start-migration":
        cmd_start_migration(args)
    elif args.command == "cleanup":
        cmd_cleanup(args)
    elif args.command == "deploy":
        cmd_deploy(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "validate-migration":
        cmd_validate_migration(args)
    elif args.command == "diagnose":
        cmd_diagnose(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
