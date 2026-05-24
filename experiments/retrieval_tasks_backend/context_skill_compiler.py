from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SelectedSubunitContext:
    source_skill_id: str
    subunit_id: str
    subunit_text: str
    subunit_score: float | None
    source_skill_path: Path


@dataclass(frozen=True)
class RescuedSubunitContext:
    source_skill_id: str
    subunit_id: str
    subunit_text: str
    subunit_score: float | None
    parent_final_score: float | None
    source_skill_path: Path


@dataclass(frozen=True)
class SelectedSkillContext:
    skill_id: str
    display_name: str
    description: str
    role_summary: str
    source_path: Path
    highlighted_subunits: tuple[SelectedSubunitContext, ...] = field(default_factory=tuple)


def _split_frontmatter(skill_md_text: str) -> tuple[dict[str, str], str]:
    if not skill_md_text.startswith("---\n"):
        return {}, skill_md_text.strip()

    _, _, remainder = skill_md_text.partition("\n")
    frontmatter, sep, body = remainder.partition("\n---\n")
    if not sep:
        return {}, skill_md_text.strip()

    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        fields[key.strip()] = value.strip()
    return fields, body.strip()


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, max_chars: int) -> str:
    cleaned = _collapse_whitespace(text)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _extract_role_summary(skill_body: str, max_chars: int = 240) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    for raw_line in skill_body.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if line.startswith("#"):
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))

    for paragraph in paragraphs:
        cleaned = _collapse_whitespace(paragraph)
        if cleaned:
            return _truncate(cleaned, max_chars)
    return "Use the source skill directly when you need its concrete procedures or reference details."


def load_selected_skill_context(
    skill_id: str,
    skill_md_path: Path,
    highlighted_subunits: list[SelectedSubunitContext] | None = None,
) -> SelectedSkillContext:
    skill_md_text = skill_md_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(skill_md_text)
    display_name = frontmatter.get("name", skill_id)
    description = frontmatter.get("description", "")
    role_summary = _extract_role_summary(body)
    return SelectedSkillContext(
        skill_id=skill_id,
        display_name=display_name,
        description=description,
        role_summary=role_summary,
        source_path=skill_md_path,
        highlighted_subunits=tuple(highlighted_subunits or []),
    )


def compile_context_skill_markdown(
    *,
    skill_name: str,
    task_id: str,
    task_instruction: str,
    selected_skills: list[SelectedSkillContext],
    rescued_subunits: list[RescuedSubunitContext] | None = None,
) -> str:
    task_intent = _collapse_whitespace(task_instruction)[:320]
    if len(_collapse_whitespace(task_instruction)) > 320:
        task_intent = task_intent.rstrip() + "..."

    primary_ids = ", ".join(skill.skill_id for skill in selected_skills) or "none"
    primary_map = "\n".join(
        (
            f"### {skill.display_name} (`{skill.skill_id}`)\n\n"
            f"- When to use: {skill.description or 'Use this source skill when its documented capability is directly relevant to the task.'}\n"
            f"- Role in this task context: {skill.role_summary}\n"
            f"- Provenance: `{skill.source_path}`"
        )
        for skill in selected_skills
    )
    provenance_index = "\n".join(
        f"- `{skill.skill_id}` -> `{skill.source_path}`" for skill in selected_skills
    ) or "- None"
    subunit_sections = []
    for skill in selected_skills:
        if not skill.highlighted_subunits:
            continue
        subunit_lines = []
        for subunit in skill.highlighted_subunits:
            score_text = (
                f" (contribution score: {subunit.subunit_score:.4f})"
                if isinstance(subunit.subunit_score, (int, float))
                else ""
            )
            subunit_lines.append(
                f"- `{subunit.subunit_id}`{score_text}: {subunit.subunit_text}\n"
                f"  Provenance: `{subunit.source_skill_id}` -> `{subunit.source_skill_path}`"
            )
        subunit_sections.append(
            f"### {skill.display_name} (`{skill.skill_id}`)\n\n" + "\n".join(subunit_lines)
        )
    highlighted_subunits_section = (
        "## Key Subunits From Selected Skills\n\n" + "\n\n".join(subunit_sections) + "\n\n"
        if subunit_sections
        else ""
    )
    rescued_subunit_lines = []
    for subunit in rescued_subunits or []:
        score_text = (
            f" (subunit contribution score: {subunit.subunit_score:.4f})"
            if isinstance(subunit.subunit_score, (int, float))
            else ""
        )
        parent_score_text = (
            f", parent skill score: {subunit.parent_final_score:.4f}"
            if isinstance(subunit.parent_final_score, (int, float))
            else ""
        )
        rescued_subunit_lines.append(
            f"- From non-selected skill `{subunit.source_skill_id}` / subunit `{subunit.subunit_id}`"
            f"{score_text}{parent_score_text}: {subunit.subunit_text}\n"
            f"  Provenance: `{subunit.source_skill_id}` -> `{subunit.source_skill_path}`"
        )
    rescued_subunits_section = (
        "## Rescued High-Value Subunits\n\n"
        "These are bounded supporting fragments from non-selected parent skills.\n"
        "Treat them as supplemental context only; they do not change the primary selected-skill set.\n\n"
        + "\n".join(rescued_subunit_lines)
        + "\n\n"
        if rescued_subunit_lines
        else ""
    )
    compilation_stage = (
        "stage3_selected_skills_plus_rescue"
        if rescued_subunit_lines
        else "stage2_selected_skills_subunits"
        if subunit_sections
        else "stage1_selected_skills"
    )

    return (
        "---\n"
        f"name: {skill_name}\n"
        f"description: Task-local context skill for {task_id}; advisory-only packaging of the selected retrieved skills.\n"
        "metadata:\n"
        "  skill_origin: generated\n"
        "  artifact_kind: context_skill\n"
        "  execution_semantics: advisory_only\n"
        f"  compilation_stage: {compilation_stage}\n"
        f"  task_id: {task_id}\n"
        f"  primary_skill_ids: [{primary_ids}]\n"
        "---\n\n"
        "# Task-Local Context Skill\n\n"
        "This is an advisory-only context skill compiled from the selected retrieved skills for this task.\n"
        "It packages grounded source-skill context into one task-local entrypoint without changing runtime control flow.\n"
        "If a factual detail conflicts with a source skill, defer to the original source skill.\n\n"
        "## Task Intent Recap\n\n"
        f"{task_intent or 'No task instruction available.'}\n\n"
        "## Primary Skill Map\n\n"
        f"{primary_map}\n\n"
        f"{highlighted_subunits_section}"
        f"{rescued_subunits_section}"
        "## Usage Notes\n\n"
        "- Use this document to orient across the selected skills.\n"
        "- Read the cited source skills for concrete procedures, commands, and edge-case details.\n"
        "- Treat rescued subunits as narrow supporting context from non-selected parents, not as an expanded selected-skill set.\n"
        "- Treat the guidance here as advisory context, not as a planner or controller.\n\n"
        "## Provenance Index\n\n"
        f"{provenance_index}\n"
    )


def compile_affiliation_coordinator_markdown(
    *,
    skill_name: str,
    task_id: str,
    task_instruction: str,
    selected_skills: list[SelectedSkillContext],
    affiliated_cue_notes: dict[str, dict[str, object]] | None = None,
) -> str:
    task_intent = _truncate(task_instruction, 320)
    primary_ids = ", ".join(skill.skill_id for skill in selected_skills) or "none"
    affiliated_cue_notes = affiliated_cue_notes or {}

    routing_sections = []
    for skill in selected_skills:
        note = affiliated_cue_notes.get(skill.skill_id, {})
        route_summary = _truncate(
            note.get("routing_hint") or skill.role_summary or skill.description or skill.skill_id,
            180,
        )
        cue_count = note.get("cue_count", 0)
        cue_path = note.get("cue_path")
        cue_focus = note.get("cue_focus")
        cue_line = "- Local affiliated cues: none attached for this task."
        if cue_count and cue_path:
            cue_line = (
                f"- Local affiliated cues: {cue_count} affiliated cue(s) available in `{cue_path}`."
            )
            if cue_focus:
                cue_line += f" Focus: {_truncate(str(cue_focus), 120)}"
        routing_sections.append(
            "\n".join(
                [
                    f"### {skill.display_name} (`{skill.skill_id}`)",
                    "",
                    f"- When to use: {skill.description or 'Use this source skill when its documented capability is directly relevant to the task.'}",
                    f"- Routing note: {route_summary}",
                    cue_line,
                    f"- Provenance: `{skill.source_path}`",
                ]
            )
        )

    provenance_index = "\n".join(
        f"- `{skill.skill_id}` -> `{skill.source_path}`" for skill in selected_skills
    ) or "- None"

    return (
        "---\n"
        f"name: {skill_name}\n"
        f"description: Task-local affiliation coordinator for {task_id}; compact routing over the selected retrieved skills.\n"
        "metadata:\n"
        "  skill_origin: generated\n"
        "  artifact_kind: affiliation_context_skill\n"
        "  execution_semantics: advisory_only\n"
        "  compilation_stage: affiliation_selected_skills_local_rescue\n"
        f"  task_id: {task_id}\n"
        f"  primary_skill_ids: [{primary_ids}]\n"
        "---\n\n"
        "# Task-Local Affiliation Coordinator\n\n"
        "This is a compact coordinator over the selected retrieved skills for this task.\n"
        "It preserves the selected skills as the main execution surface and points to any skill-local affiliated cues when helpful.\n"
        "If a factual detail conflicts with a selected skill, defer to the selected skill.\n\n"
        "## Task Intent Recap\n\n"
        f"{task_intent or 'No task instruction available.'}\n\n"
        "## Selected Skill Routing Index\n\n"
        + "\n\n".join(routing_sections)
        + "\n\n## Usage Notes\n\n"
        "- Use this coordinator to decide which selected skill to open first.\n"
        "- Treat any affiliated cue files as local supplemental context under the selected skills, not as a new global rescue pool.\n"
        "- Treat this document as routing guidance, not as a planner or controller.\n\n"
        "## Provenance Index\n\n"
        f"{provenance_index}\n"
    )
