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


def setup_logging(verbose: bool = False):
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

    # -- diagnose --
    diag_parser = subparsers.add_parser("diagnose", help="Look up error in KB")
    diag_parser.add_argument("error_text", nargs="+", help="Error text to look up")

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
    setup_logging(args.verbose)

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
    elif args.command == "deploy":
        cmd_deploy(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "diagnose":
        cmd_diagnose(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
