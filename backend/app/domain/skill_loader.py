"""
Skill Definition Loader -- reads editable .md skill files with YAML frontmatter.

Each skill is defined by a SKILL.md file with this structure:

    ---
    name: skill_name
    description: >
      Natural language description for the LLM.
    required_role: generate
    parameters:
      - name: param_name
        type: string
        description: What this param does
        required: true
        enum: [option1, option2]
        default: option1
    tags: [forecasting, generation]
    version: 1.0
    ---

    # Skill Name

    ## When to Use
    Instructions for the LLM on when to invoke this skill...

    ## Behavior
    Detailed behavior notes...

    ## Examples
    - "Generate a 12-month forecast" → triggers this skill
    - "Refresh the forecast for Q2" → triggers this skill

Users can edit the .md files to customize skill behavior, descriptions,
and parameter definitions without touching Python code.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"


@dataclass
class SkillDefinition:
    """Parsed skill definition from a .md file."""
    name: str
    description: str
    required_role: str | None = None
    parameters: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    version: str = "1.0"
    instructions: str = ""          # Full markdown body (for LLM context)
    when_to_use: str = ""           # Extracted "When to Use" section
    examples: list[str] = field(default_factory=list)
    file_path: str = ""

    @property
    def parameters_schema(self) -> dict:
        """Convert parameters list to JSON Schema format."""
        properties = {}
        required = []
        for p in self.parameters:
            prop: dict[str, Any] = {
                "type": p.get("type", "string"),
                "description": p.get("description", ""),
            }
            if "enum" in p:
                prop["enum"] = p["enum"]
            if "default" in p:
                prop["default"] = p["default"]
            if "items" in p:
                prop["items"] = p["items"]

            properties[p["name"]] = prop

            if p.get("required", False):
                required.append(p["name"])

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema

    @property
    def full_description(self) -> str:
        """Combine description + when_to_use for the LLM."""
        parts = [self.description]
        if self.when_to_use:
            parts.append(self.when_to_use)
        if self.examples:
            examples_text = " ".join(f'Example: "{e}"' for e in self.examples[:3])
            parts.append(examples_text)
        return " ".join(parts)


def parse_skill_md(content: str, file_path: str = "") -> SkillDefinition:
    """Parse a SKILL.md file into a SkillDefinition."""
    # Split YAML frontmatter from markdown body
    frontmatter_match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)

    if not frontmatter_match:
        raise ValueError(f"Invalid skill file: missing YAML frontmatter in {file_path}")

    yaml_text = frontmatter_match.group(1)
    markdown_body = frontmatter_match.group(2).strip()

    # Parse YAML
    try:
        meta = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML frontmatter in {file_path}: {e}")

    # Extract sections from markdown body
    when_to_use = _extract_section(markdown_body, "When to Use")
    examples_text = _extract_section(markdown_body, "Examples")
    examples = _parse_examples(examples_text)

    return SkillDefinition(
        name=meta.get("name", ""),
        description=meta.get("description", ""),
        required_role=meta.get("required_role"),
        parameters=meta.get("parameters", []),
        tags=meta.get("tags", []),
        version=str(meta.get("version", "1.0")),
        instructions=markdown_body,
        when_to_use=when_to_use,
        examples=examples,
        file_path=file_path,
    )


def _extract_section(markdown: str, heading: str) -> str:
    """Extract content under a specific ## heading."""
    pattern = rf'##\s+{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)'
    match = re.search(pattern, markdown, re.DOTALL)
    return match.group(1).strip() if match else ""


def _parse_examples(examples_text: str) -> list[str]:
    """Parse example lines from the Examples section."""
    if not examples_text:
        return []
    examples = []
    for line in examples_text.split("\n"):
        line = line.strip().lstrip("- ").strip()
        if line:
            # Extract quoted text if present
            quoted = re.findall(r'"([^"]+)"', line)
            if quoted:
                examples.append(quoted[0])
            elif line:
                examples.append(line)
    return examples


def load_skill_definition(skill_name: str) -> SkillDefinition | None:
    """Load a single skill definition from the skills directory."""
    file_path = SKILLS_DIR / f"{skill_name}.md"
    if not file_path.exists():
        return None

    try:
        content = file_path.read_text(encoding="utf-8")
        return parse_skill_md(content, str(file_path))
    except Exception as e:
        logger.error(f"Failed to load skill definition '{skill_name}': {e}")
        return None


def load_all_skill_definitions() -> dict[str, SkillDefinition]:
    """Load all skill definitions from the skills directory."""
    definitions = {}

    if not SKILLS_DIR.exists():
        logger.warning(f"Skills directory not found: {SKILLS_DIR}")
        return definitions

    for file_path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            content = file_path.read_text(encoding="utf-8")
            defn = parse_skill_md(content, str(file_path))
            if defn.name:
                definitions[defn.name] = defn
                logger.debug(f"Loaded skill definition: {defn.name}")
        except Exception as e:
            logger.error(f"Failed to load {file_path.name}: {e}")

    return definitions


def save_skill_definition(skill_name: str, content: str) -> bool:
    """Save/update a skill definition .md file."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    file_path = SKILLS_DIR / f"{skill_name}.md"

    try:
        # Validate that it parses correctly before saving
        parse_skill_md(content, str(file_path))
        file_path.write_text(content, encoding="utf-8")
        logger.info(f"Saved skill definition: {skill_name}")
        return True
    except Exception as e:
        logger.error(f"Failed to save skill definition '{skill_name}': {e}")
        return False


def list_skill_files() -> list[dict[str, Any]]:
    """List all skill .md files with basic metadata."""
    results = []
    if not SKILLS_DIR.exists():
        return results

    for file_path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            content = file_path.read_text(encoding="utf-8")
            defn = parse_skill_md(content, str(file_path))
            results.append({
                "name": defn.name,
                "file": file_path.name,
                "description": defn.description[:120],
                "tags": defn.tags,
                "version": defn.version,
                "required_role": defn.required_role,
                "param_count": len(defn.parameters),
            })
        except Exception:
            results.append({
                "name": file_path.stem,
                "file": file_path.name,
                "description": "Error: failed to parse",
                "tags": [],
                "version": "?",
                "required_role": None,
                "param_count": 0,
            })
    return results
