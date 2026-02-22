"""Tests for the skill .md loader and definition system."""

import pytest
from app.domain.skill_loader import (
    parse_skill_md,
    SkillDefinition,
    load_all_skill_definitions,
    SKILLS_DIR,
)


class TestParseSkillMd:
    """Test YAML frontmatter + markdown parsing."""

    def test_parse_valid_skill(self):
        content = """---
name: test_skill
description: A test skill for unit testing
required_role: admin
version: "2.0"
tags: [testing, unit]
parameters:
  - name: input_file
    type: string
    description: Path to input file
    required: true
  - name: mode
    type: string
    description: Operating mode
    enum: [fast, slow]
    default: fast
---

# Test Skill

## When to Use
Use this when testing things.

## Examples
- "Run the test" -> triggers this skill
- "Execute test mode" -> triggers this skill
"""
        defn = parse_skill_md(content, "test.md")

        assert defn.name == "test_skill"
        assert defn.description == "A test skill for unit testing"
        assert defn.required_role == "admin"
        assert defn.version == "2.0"
        assert defn.tags == ["testing", "unit"]
        assert len(defn.parameters) == 2
        assert defn.parameters[0]["name"] == "input_file"
        assert defn.parameters[0]["required"] is True
        assert defn.parameters[1]["enum"] == ["fast", "slow"]
        assert defn.when_to_use == "Use this when testing things."
        assert len(defn.examples) == 2
        assert "Run the test" in defn.examples

    def test_parse_missing_frontmatter(self):
        content = "# No frontmatter here"
        with pytest.raises(ValueError, match="missing YAML frontmatter"):
            parse_skill_md(content)

    def test_parameters_schema_generation(self):
        content = """---
name: schema_test
description: Test schema generation
parameters:
  - name: file_path
    type: string
    description: Path to file
    required: true
  - name: count
    type: integer
    description: How many
    default: 10
  - name: mode
    type: string
    description: Mode
    enum: [a, b, c]
---

# Schema Test
"""
        defn = parse_skill_md(content)
        schema = defn.parameters_schema

        assert schema["type"] == "object"
        assert "file_path" in schema["properties"]
        assert schema["properties"]["file_path"]["type"] == "string"
        assert "file_path" in schema["required"]
        assert schema["properties"]["count"]["default"] == 10
        assert schema["properties"]["mode"]["enum"] == ["a", "b", "c"]

    def test_full_description_includes_examples(self):
        content = """---
name: desc_test
description: Base description.
parameters: []
---

## When to Use
Use for testing only.

## Examples
- "Do the thing" -> test
"""
        defn = parse_skill_md(content)
        full = defn.full_description

        assert "Base description." in full
        assert "Use for testing only." in full
        assert "Do the thing" in full

    def test_parse_null_role(self):
        content = """---
name: no_role
description: No role required
required_role: null
parameters: []
---

# No Role Skill
"""
        defn = parse_skill_md(content)
        assert defn.required_role is None


class TestLoadAllDefinitions:
    """Test loading all .md files from the skills directory."""

    def test_load_all_from_skills_dir(self):
        """All 15 skill .md files should be loadable."""
        defs = load_all_skill_definitions()
        assert len(defs) >= 15, f"Expected at least 15 definitions, got {len(defs)}"

        expected = [
            "ingest_actuals", "generate_baseline", "score_confidence",
            "manage_versions", "query_forecast", "apply_override",
            "compare_forecasts", "collect_driver_input", "review_forecast",
            "export_audit", "run_ensemble", "generate_commentary",
            "branch_forecast", "auto_accuracy_report", "detect_anomalies",
        ]
        for name in expected:
            assert name in defs, f"Missing skill definition: {name}"

    def test_all_definitions_have_required_fields(self):
        """Every skill definition should have name, description, and parameters."""
        defs = load_all_skill_definitions()
        for name, defn in defs.items():
            assert defn.name, f"{name} has no name"
            assert defn.description, f"{name} has no description"
            assert isinstance(defn.parameters, list), f"{name} parameters is not a list"
            assert isinstance(defn.tags, list), f"{name} tags is not a list"

    def test_skills_dir_exists(self):
        """The skills directory should exist."""
        assert SKILLS_DIR.exists(), f"Skills directory not found: {SKILLS_DIR}"
