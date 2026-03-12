"""
Knowledge Base loader.

Loads YAML KB files (prerequisites, errors) and provides query methods
used by both the assessment engine and the troubleshooter.
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Try yaml, fall back to json
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class KnowledgeBase:
    """Loads and queries the KB directory."""

    def __init__(self, kb_dir: Optional[str] = None):
        if kb_dir is None:
            kb_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "kb")
        self.kb_dir = kb_dir
        self._prerequisites: Dict[str, List[Dict]] = {}
        self._errors: List[Dict] = []
        self._loaded = False

    def load(self) -> bool:
        """Load all KB files."""
        if not os.path.isdir(self.kb_dir):
            logger.error(f"KB directory not found: {self.kb_dir}")
            return False

        # Load prerequisites
        prereq_path = os.path.join(self.kb_dir, "prerequisites.yaml")
        if os.path.exists(prereq_path):
            data = self._load_yaml(prereq_path)
            if data:
                self._prerequisites = {
                    "source_database": data.get("source_database", []),
                    "target_adb": data.get("target_adb", []),
                    "oci_infrastructure": data.get("oci_infrastructure", []),
                }

        # Load errors
        errors_path = os.path.join(self.kb_dir, "errors.yaml")
        if os.path.exists(errors_path):
            data = self._load_yaml(errors_path)
            if data:
                for section in ["dms_errors", "oracle_errors", "goldengate_errors", "oci_errors"]:
                    self._errors.extend(data.get(section, []))

        self._loaded = True
        logger.info(
            f"KB loaded: {sum(len(v) for v in self._prerequisites.values())} prerequisites, "
            f"{len(self._errors)} error patterns"
        )
        return True

    def _load_yaml(self, path: str) -> Optional[Dict]:
        """Load YAML (or JSON fallback)."""
        try:
            with open(path, "r") as f:
                if HAS_YAML:
                    return yaml.safe_load(f)
                else:
                    import json
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load {path}: {e}")
            return None

    # ---- Prerequisites ----
    def get_source_checks(self, migration_type: str = "ONLINE",
                          db_type: str = "oracle_onprem") -> List[Dict]:
        """Get applicable source DB checks, filtered by migration type and variant."""
        checks = []
        for check in self._prerequisites.get("source_database", []):
            # Filter by migration type
            applies_to = check.get("applies_to")
            if applies_to and applies_to != migration_type:
                continue

            # Apply variant overrides
            check_copy = dict(check)
            variants = check.get("variants", {})
            if db_type in variants:
                variant = variants[db_type]
                if variant.get("severity") == "info" and check["severity"] == "blocker":
                    # Variant downgrades severity → skip or mark info
                    check_copy["severity"] = "info"
                    check_copy["notes"] = variant.get("notes", check_copy.get("notes", ""))
                if "remediation" in variant:
                    check_copy["remediation"] = variant["remediation"]
                if "notes" in variant:
                    check_copy["notes"] = variant["notes"]

            checks.append(check_copy)
        return checks

    def get_target_checks(self) -> List[Dict]:
        """Get target ADB checks."""
        return list(self._prerequisites.get("target_adb", []))

    def get_oci_checks(self, has_reverse_replication: bool = False) -> List[Dict]:
        """Get OCI infrastructure checks."""
        checks = []
        for check in self._prerequisites.get("oci_infrastructure", []):
            condition = check.get("condition", "")
            if "enable_reverse_replication" in condition and not has_reverse_replication:
                continue
            checks.append(check)
        return checks

    def get_check_by_id(self, check_id: str) -> Optional[Dict]:
        """Find a specific check by ID across all sections."""
        for section_checks in self._prerequisites.values():
            for check in section_checks:
                if check.get("id") == check_id:
                    return check
        return None

    # ---- Error lookup ----
    def lookup_error(self, error_text: str) -> Optional[Dict]:
        """Find best matching error entry for given error text."""
        for entry in self._errors:
            pattern = entry.get("pattern", "")
            try:
                if re.search(pattern, error_text, re.IGNORECASE):
                    return entry
            except re.error:
                if pattern.lower() in error_text.lower():
                    return entry
        return None

    def lookup_errors(self, error_text: str) -> List[Dict]:
        """Find all matching error entries."""
        matches = []
        for entry in self._errors:
            pattern = entry.get("pattern", "")
            try:
                if re.search(pattern, error_text, re.IGNORECASE):
                    matches.append(entry)
            except re.error:
                if pattern.lower() in error_text.lower():
                    matches.append(entry)
        return matches

    # ---- For AI / SKILL.md generation ----
    def export_for_prompt(self) -> str:
        """Export KB as structured text for AI prompt context."""
        lines = ["# Migration Prerequisites Knowledge Base\n"]

        for section, checks in self._prerequisites.items():
            lines.append(f"\n## {section.replace('_', ' ').title()}\n")
            for c in checks:
                sev = c.get("severity", "info").upper()
                lines.append(f"- [{sev}] {c['id']}: {c.get('description', '')}")
                if c.get("remediation"):
                    rem = c["remediation"].strip().split("\n")[0]  # First line
                    lines.append(f"  Fix: {rem}")

        lines.append("\n\n# Known Error Patterns\n")
        for e in self._errors:
            lines.append(f"- Pattern: {e.get('pattern', '')} → {e.get('description', '')}")
            lines.append(f"  Fix: {e.get('fix', '').strip().split(chr(10))[0]}")

        return "\n".join(lines)
