#!/usr/bin/env python3
"""
Generate SKILL.md from KB + code structure.

Compiles the AI skill file from:
  - kb/prerequisites.yaml → structured check list
  - kb/errors.yaml → error patterns
  - Code structure → available commands, config schema
  - ai/prompts/*.md → interaction patterns

This ensures SKILL.md never gets out of sync with the actual KB and code.

Usage:
    python ai/generate_skill.py
    python ai/generate_skill.py --output ai/SKILL.md
"""

import os
import sys
import json
from datetime import datetime, timezone

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.kb_loader import KnowledgeBase


def generate_skill(output_path: str = None):
    """Generate SKILL.md from KB and code."""

    kb = KnowledgeBase()
    kb.load()

    # Load config example for schema reference
    example_config_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "migration-config.json.example"
    )
    config_schema = ""
    if os.path.exists(example_config_path):
        with open(example_config_path) as f:
            config_data = json.load(f)
        config_schema = json.dumps(
            {k: type(v).__name__ if not isinstance(v, dict) else "{...}"
             for k, v in config_data.items()},
            indent=2
        )

    # Load prompt templates
    prompts_dir = os.path.join(os.path.dirname(__file__), "prompts")
    prompt_sections = []
    if os.path.isdir(prompts_dir):
        for fname in sorted(os.listdir(prompts_dir)):
            if fname.endswith(".md"):
                with open(os.path.join(prompts_dir, fname)) as f:
                    prompt_sections.append(f.read())

    # Build SKILL.md
    lines = [
        "# OCI Database Migration AI Skill",
        "",
        f"*Auto-generated on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
        f"*Source: kb/prerequisites.yaml, kb/errors.yaml, code structure*",
        "",
    ]

    # Identity section
    lines.extend([
        "## Identity",
        "",
        "You are an Oracle Database Migration specialist embedded in the `oci-db-migrations-cli` toolset.",
        "You help users plan, configure, execute, troubleshoot, and validate migrations from Oracle databases",
        "(on-premises, AWS RDS, ExaCS) to OCI Autonomous Database using OCI DMS and OCI GoldenGate.",
        "",
    ])

    # KB export
    lines.extend([
        "## Knowledge Base",
        "",
        kb.export_for_prompt(),
        "",
    ])

    # Config schema
    if config_schema:
        lines.extend([
            "## Configuration Schema (migration-config.json)",
            "",
            "Top-level sections:",
            f"```json",
            config_schema,
            "```",
            "",
        ])

    # Prompt templates
    for prompt in prompt_sections:
        lines.extend([
            "---",
            "",
            prompt,
            "",
        ])

    # Language instruction
    lines.extend([
        "---",
        "",
        "## Language",
        "",
        "Respond in the same language the user uses. Most interactions will be in Spanish.",
        "",
    ])

    skill_text = "\n".join(lines)

    if output_path is None:
        output_path = os.path.join(os.path.dirname(__file__), "SKILL.md")

    with open(output_path, "w") as f:
        f.write(skill_text)

    print(f"Generated: {output_path}")
    print(f"  Prerequisites: {sum(len(v) for v in kb._prerequisites.values())} checks")
    print(f"  Error patterns: {len(kb._errors)}")
    print(f"  Prompt templates: {len(prompt_sections)}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate SKILL.md from KB")
    parser.add_argument("--output", "-o", default=None, help="Output path")
    args = parser.parse_args()
    generate_skill(args.output)
