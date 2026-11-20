import re
from dataclasses import dataclass
from importlib.resources import files

import yaml


SKILL_NAMES = (
    "goal-planning",
    "daily-progress-recording",
    "progress-analysis",
    "weekly-report-generation",
    "monthly-report-generation",
    "daily-mail-writing",
    "goal-review",
    "week-planning",
)

_NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class SkillLoadError(ValueError):
    """A bundled Skill is missing or has invalid content."""


@dataclass(frozen=True)
class RuntimeSkill:
    name: str
    description: str
    instructions: str


def load_skill(name: str) -> RuntimeSkill:
    """Load one application-owned Skill by its approved name."""
    if name not in SKILL_NAMES:
        raise SkillLoadError(
            f"Unknown bundled Skill: {name}"
        )

    resource = files("goal_tracker").joinpath(
        "skills",
        "bundled",
        name,
        "SKILL.md",
    )

    try:
        content = resource.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise SkillLoadError(
            f"Could not read bundled Skill: {name}"
        ) from error

    lines = content.splitlines()

    if not lines or lines[0].strip() != "---":
        raise SkillLoadError(
            f"Skill {name} must begin with YAML frontmatter."
        )

    closing_line = next(
        (
            index
            for index in range(1, len(lines))
            if lines[index].strip() == "---"
        ),
        None,
    )

    if closing_line is None:
        raise SkillLoadError(
            f"Skill {name} has no closing frontmatter delimiter."
        )

    frontmatter = "\n".join(lines[1:closing_line])
    instructions = "\n".join(
        lines[closing_line + 1:]
    ).strip()

    try:
        metadata = yaml.safe_load(frontmatter)
    except yaml.YAMLError as error:
        raise SkillLoadError(
            f"Skill {name} contains invalid YAML metadata."
        ) from error

    if not isinstance(metadata, dict):
        raise SkillLoadError(
            f"Skill {name} metadata must be a mapping."
        )

    declared_name = metadata.get("name")
    description = metadata.get("description")

    if (
        not isinstance(declared_name, str)
        or not 1 <= len(declared_name) <= 64
        or _NAME_PATTERN.fullmatch(declared_name) is None
        or declared_name != name
    ):
        raise SkillLoadError(
            f"Skill {name} has an invalid or mismatched name."
        )

    if (
        not isinstance(description, str)
        or not 1 <= len(description.strip()) <= 1024
    ):
        raise SkillLoadError(
            f"Skill {name} needs a description of 1–1024 characters."
        )

    if not instructions:
        raise SkillLoadError(
            f"Skill {name} has no instructions."
        )

    return RuntimeSkill(
        name=declared_name,
        description=description.strip(),
        instructions=instructions,
    )


def discover_skills() -> list[dict[str, str]]:
    """Return the bundled Skill catalog without exposing instruction bodies."""
    catalog = []

    for name in SKILL_NAMES:
        skill = load_skill(name)
        catalog.append(
            {
                "name": skill.name,
                "description": skill.description,
            }
        )

    return catalog