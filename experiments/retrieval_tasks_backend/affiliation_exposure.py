from __future__ import annotations

import collections
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from experiments.retrieval_tasks_backend.context_skill_compiler import SelectedSkillContext


@dataclass(frozen=True)
class AffiliationConfig:
    active_l2_limit: int = 2
    min_affiliation_score: float = 0.12
    min_bridge_score: float = 0.16
    bridge_margin: float = 0.03
    max_visible_cues_per_skill: int = 2
    redundancy_threshold: float = 0.6
    enable_intent_community: bool = True


@dataclass(frozen=True)
class AffiliatedCue:
    attached_skill_id: str
    attached_skill_path: Path
    source_skill_id: str
    source_skill_path: Path
    subunit_id: str
    subunit_text: str
    subunit_score: float | None
    parent_final_score: float | None
    affiliation_score: float
    cue_type: str
    bridge_skill_id: str | None = None
    bridge_score: float | None = None
    active_l2_label: str | None = None


_EXECUTION_VERBS = {
    "analyze",
    "apply",
    "build",
    "check",
    "collect",
    "compute",
    "convert",
    "create",
    "download",
    "export",
    "extract",
    "find",
    "fit",
    "generate",
    "group",
    "identify",
    "install",
    "load",
    "log",
    "match",
    "normalize",
    "parse",
    "prioritize",
    "read",
    "remove",
    "run",
    "save",
    "search",
    "select",
    "sort",
    "test",
    "trace",
    "use",
    "validate",
    "write",
}
_CONSTRAINT_MARKERS = (
    "avoid",
    "default",
    "do not",
    "don't",
    "if ",
    "must",
    "only",
    "prefer",
    "required",
    "requires",
    "should",
)


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, max_chars: int) -> str:
    cleaned = _collapse_whitespace(text)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _normalize_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard_similarity(left: str, right: str) -> float:
    left_tokens = _normalize_tokens(left)
    right_tokens = _normalize_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _is_redundant(candidate_text: str, seen_texts: list[str], threshold: float) -> bool:
    for seen in seen_texts:
        if _jaccard_similarity(candidate_text, seen) >= threshold:
            return True
    return False


def _classify_cue_type(text: str) -> str:
    cleaned = _collapse_whitespace(text).lower()
    if not cleaned:
        return "Affiliated Objects / Parameters / Artifacts"
    if any(marker in cleaned for marker in _CONSTRAINT_MARKERS):
        return "Affiliated Cautions / Limits / Assumptions"
    first_token = next(iter(_normalize_tokens(cleaned)), "")
    if first_token.isdigit() or first_token in _EXECUTION_VERBS:
        return "Affiliated Execution Cues"
    return "Affiliated Objects / Parameters / Artifacts"


def _load_l2_index(repo_root: Path) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    skill_name_to_graph_id: dict[str, str] = {}
    graph_skill_to_l2_id: dict[str, str] = {}
    l2_labels: dict[str, str] = {}

    skill_nodes_path = repo_root / "skill_nodes.json"
    if skill_nodes_path.exists():
        try:
            for row in json.loads(skill_nodes_path.read_text(encoding="utf-8")):
                if isinstance(row, dict) and isinstance(row.get("name"), str) and isinstance(row.get("id"), str):
                    skill_name_to_graph_id[row["name"]] = row["id"]
        except Exception:
            pass

    l2_mapping_path = repo_root / "skill_l2_mapping.json"
    if l2_mapping_path.exists():
        try:
            data = json.loads(l2_mapping_path.read_text(encoding="utf-8"))
            for l2_id, payload in data.items():
                if not isinstance(payload, dict):
                    continue
                label = payload.get("label")
                if isinstance(label, str):
                    l2_labels[l2_id] = label
                for graph_skill_id in payload.get("skills", []):
                    if isinstance(graph_skill_id, str):
                        graph_skill_to_l2_id[graph_skill_id] = l2_id
        except Exception:
            pass

    return skill_name_to_graph_id, graph_skill_to_l2_id, l2_labels


def _select_active_l2(
    *,
    selected_skill_records_by_id: dict[str, dict[str, object]],
    skill_name_to_graph_id: dict[str, str],
    graph_skill_to_l2_id: dict[str, str],
    l2_labels: dict[str, str],
    config: AffiliationConfig,
) -> tuple[list[str], list[str]]:
    weights: dict[str, float] = collections.defaultdict(float)
    for skill_id, record in selected_skill_records_by_id.items():
        graph_skill_id = record.get("graph_skill_id")
        if not isinstance(graph_skill_id, str):
            graph_skill_id = skill_name_to_graph_id.get(skill_id)
        l2_id = graph_skill_to_l2_id.get(graph_skill_id)
        if not l2_id:
            continue
        score = record.get("final_score")
        weights[l2_id] += float(score) if isinstance(score, (int, float)) else 1.0

    ranked_l2_ids = [
        l2_id for l2_id, _ in sorted(weights.items(), key=lambda item: item[1], reverse=True)[: config.active_l2_limit]
    ]
    ranked_l2_labels = [l2_labels.get(l2_id, l2_id) for l2_id in ranked_l2_ids]
    return ranked_l2_ids, ranked_l2_labels


def _build_skill_profile(skill: SelectedSkillContext) -> str:
    highlighted = " ".join(subunit.subunit_text for subunit in skill.highlighted_subunits[:2])
    return " ".join(
        part
        for part in [
            skill.skill_id,
            skill.display_name,
            skill.description,
            skill.role_summary,
            highlighted,
        ]
        if part
    )


def _build_routing_hint(skill: SelectedSkillContext, attached_cues: list[AffiliatedCue]) -> str:
    if skill.highlighted_subunits:
        return _truncate(skill.highlighted_subunits[0].subunit_text, 140)
    if attached_cues:
        return _truncate(attached_cues[0].subunit_text, 140)
    return _truncate(skill.role_summary or skill.description or skill.skill_id, 140)


def build_affiliation_artifacts(
    *,
    task_instruction: str,
    selected_skills: list[SelectedSkillContext],
    selected_skill_records_by_id: dict[str, dict[str, object]],
    rescued_subunits: list[dict[str, object]],
    global_skill_pool: Path,
    repo_root: Path,
    config: AffiliationConfig | None = None,
) -> dict[str, object]:
    cfg = config or AffiliationConfig()
    skill_name_to_graph_id, graph_skill_to_l2_id, l2_labels = _load_l2_index(repo_root)
    if cfg.enable_intent_community:
        active_l2_ids, active_l2_labels = _select_active_l2(
            selected_skill_records_by_id=selected_skill_records_by_id,
            skill_name_to_graph_id=skill_name_to_graph_id,
            graph_skill_to_l2_id=graph_skill_to_l2_id,
            l2_labels=l2_labels,
            config=cfg,
        )
    else:
        active_l2_ids, active_l2_labels = [], []

    selected_score_values = [
        float(record["final_score"])
        for record in selected_skill_records_by_id.values()
        if isinstance(record.get("final_score"), (int, float))
    ]
    if selected_score_values:
        score_min = min(selected_score_values)
        score_span = max(selected_score_values) - score_min
    else:
        score_min = 0.0
        score_span = 0.0

    def normalized_selected_score(skill_id: str) -> float:
        record = selected_skill_records_by_id.get(skill_id, {})
        raw_score = record.get("final_score")
        if not isinstance(raw_score, (int, float)):
            return 0.0
        if score_span == 0:
            return 0.5
        return (float(raw_score) - score_min) / score_span

    selected_skill_l2: dict[str, str | None] = {}
    for skill in selected_skills:
        record = selected_skill_records_by_id.get(skill.skill_id, {})
        graph_skill_id = record.get("graph_skill_id")
        if not isinstance(graph_skill_id, str):
            graph_skill_id = skill_name_to_graph_id.get(skill.skill_id)
        selected_skill_l2[skill.skill_id] = graph_skill_to_l2_id.get(graph_skill_id)

    candidate_assignments: list[dict[str, object]] = []
    dropped_rescues: list[dict[str, object]] = []

    for rescue_row in rescued_subunits:
        rescue_text = str(rescue_row.get("subunit_text", "")).strip()
        source_skill_id = rescue_row.get("source_skill_id")
        if not isinstance(source_skill_id, str) or not rescue_text:
            continue

        rescue_graph_skill_id = rescue_row.get("source_graph_skill_id")
        rescue_l2_id = graph_skill_to_l2_id.get(rescue_graph_skill_id) if isinstance(rescue_graph_skill_id, str) else None
        rescue_l2_label = l2_labels.get(rescue_l2_id) if rescue_l2_id else None

        q_rel = _jaccard_similarity(rescue_text, task_instruction)
        scored_parents: list[tuple[float, SelectedSkillContext]] = []

        for skill in selected_skills:
            profile = _build_skill_profile(skill)
            role_overlap = _jaccard_similarity(rescue_text, profile)
            highlighted_overlap = max(
                (_jaccard_similarity(rescue_text, subunit.subunit_text) for subunit in skill.highlighted_subunits),
                default=0.0,
            )
            parent_fit = max(role_overlap, highlighted_overlap)
            selected_prior = normalized_selected_score(skill.skill_id)
            same_l2 = rescue_l2_id is not None and selected_skill_l2.get(skill.skill_id) == rescue_l2_id
            active_l2_match = rescue_l2_id is not None and rescue_l2_id in active_l2_ids
            graph_support = 1.0 if (cfg.enable_intent_community and same_l2) else 0.0
            community_consistency = (
                1.0 if same_l2 else 0.4 if active_l2_match else 0.0
            ) if cfg.enable_intent_community else 0.0
            score = (
                0.15 * q_rel
                + 0.45 * parent_fit
                + 0.10 * selected_prior
                + 0.15 * graph_support
                + 0.15 * community_consistency
            )
            scored_parents.append((score, skill))

        if not scored_parents:
            dropped_rescues.append(
                {
                    **rescue_row,
                    "drop_reason": "no_selected_parent_candidates",
                }
            )
            continue

        scored_parents.sort(key=lambda item: item[0], reverse=True)
        top_score, top_skill = scored_parents[0]
        second_score, second_skill = scored_parents[1] if len(scored_parents) > 1 else (0.0, None)
        exclusivity_bonus = max(0.0, top_score - second_score)
        affiliation_score = top_score + 0.10 * exclusivity_bonus
        if cfg.enable_intent_community and rescue_l2_id and active_l2_ids and rescue_l2_id not in active_l2_ids:
            affiliation_score -= 0.08

        if affiliation_score < cfg.min_affiliation_score:
            dropped_rescues.append(
                {
                    **rescue_row,
                    "drop_reason": "below_affiliation_threshold",
                    "best_parent_skill_id": top_skill.skill_id,
                    "affiliation_score": round(float(affiliation_score), 4),
                }
            )
            continue

        bridge_skill_id = None
        bridge_score = None
        if (
            second_skill is not None
            and second_score >= cfg.min_bridge_score
            and (top_score - second_score) <= cfg.bridge_margin
        ):
            bridge_skill_id = second_skill.skill_id
            bridge_score = round(float(second_score), 4)

        source_skill_path = global_skill_pool / source_skill_id / "SKILL.md"
        candidate_assignments.append(
            {
                "cue": AffiliatedCue(
                    attached_skill_id=top_skill.skill_id,
                    attached_skill_path=top_skill.source_path.parent,
                    source_skill_id=source_skill_id,
                    source_skill_path=source_skill_path,
                    subunit_id=str(rescue_row.get("subunit_id", "")),
                    subunit_text=_truncate(rescue_text, 220),
                    subunit_score=float(rescue_row["subunit_score"]) if isinstance(rescue_row.get("subunit_score"), (int, float)) else None,
                    parent_final_score=(
                        float(rescue_row["parent_final_score"])
                        if isinstance(rescue_row.get("parent_final_score"), (int, float))
                        else None
                    ),
                    affiliation_score=round(float(affiliation_score), 4),
                    cue_type=_classify_cue_type(rescue_text),
                    bridge_skill_id=bridge_skill_id,
                    bridge_score=bridge_score,
                    active_l2_label=rescue_l2_label,
                )
            }
        )

    candidate_assignments.sort(key=lambda item: item["cue"].affiliation_score, reverse=True)
    cues_by_skill_id: dict[str, list[AffiliatedCue]] = collections.defaultdict(list)
    kept_rows: list[dict[str, object]] = []

    for item in candidate_assignments:
        cue = item["cue"]
        existing_for_skill = cues_by_skill_id[cue.attached_skill_id]
        if len(existing_for_skill) >= cfg.max_visible_cues_per_skill:
            dropped_rescues.append(
                {
                    "source_skill_id": cue.source_skill_id,
                    "subunit_id": cue.subunit_id,
                    "subunit_text": cue.subunit_text,
                    "drop_reason": "per_skill_cue_budget",
                    "attached_skill_id": cue.attached_skill_id,
                }
            )
            continue
        if _is_redundant(cue.subunit_text, [row.subunit_text for row in existing_for_skill], cfg.redundancy_threshold):
            dropped_rescues.append(
                {
                    "source_skill_id": cue.source_skill_id,
                    "subunit_id": cue.subunit_id,
                    "subunit_text": cue.subunit_text,
                    "drop_reason": "redundant_with_attached_cues",
                    "attached_skill_id": cue.attached_skill_id,
                }
            )
            continue
        cues_by_skill_id[cue.attached_skill_id].append(cue)
        kept_rows.append(asdict(cue) | {"attached_skill_path": str(cue.attached_skill_path), "source_skill_path": str(cue.source_skill_path)})

    routing_notes: dict[str, dict[str, object]] = {}
    for skill in selected_skills:
        attached = cues_by_skill_id.get(skill.skill_id, [])
        routing_notes[skill.skill_id] = {
            "routing_hint": _build_routing_hint(skill, attached),
            "cue_count": len(attached),
            "cue_focus": attached[0].subunit_text if attached else None,
        }

    return {
        "active_l2_ids": active_l2_ids,
        "active_l2_labels": active_l2_labels,
        "cues_by_skill_id": cues_by_skill_id,
        "attached_rescued_subunits": kept_rows,
        "dropped_rescued_subunits": dropped_rescues,
        "routing_notes": routing_notes,
    }


def write_affiliated_cue_sidecars(
    *,
    task_id: str,
    dest_skills_dir: Path,
    selected_skills: list[SelectedSkillContext],
    cues_by_skill_id: dict[str, list[AffiliatedCue]],
) -> dict[str, str]:
    sidecar_paths: dict[str, str] = {}
    for skill in selected_skills:
        cues = cues_by_skill_id.get(skill.skill_id, [])
        if not cues:
            continue
        cue_dir = dest_skills_dir / skill.skill_id / ".affiliation"
        cue_dir.mkdir(parents=True, exist_ok=True)
        cue_path = cue_dir / "AFFILIATED_CUES.md"
        grouped: dict[str, list[AffiliatedCue]] = collections.defaultdict(list)
        for cue in cues:
            grouped[cue.cue_type].append(cue)

        sections: list[str] = []
        for section_title in [
            "Affiliated Execution Cues",
            "Affiliated Objects / Parameters / Artifacts",
            "Affiliated Cautions / Limits / Assumptions",
        ]:
            section_cues = grouped.get(section_title)
            if not section_cues:
                continue
            cue_lines = []
            for cue in section_cues:
                cue_lines.append(
                    "\n".join(
                        [
                            f"- Cue: {cue.subunit_text}",
                            f"  Derived from non-selected skill: `{cue.source_skill_id}`",
                            (
                                f"  Also relevant to selected skill: `{cue.bridge_skill_id}`"
                                if cue.bridge_skill_id
                                else ""
                            ),
                            f"  Provenance: `{cue.source_skill_path}`",
                        ]
                    ).replace("\n\n", "\n")
                )
            sections.append(f"## {section_title}\n\n" + "\n".join(cue_lines))

        cue_path.write_text(
            (
                "---\n"
                f"name: affiliated-cues-{skill.skill_id}\n"
                f"description: Task-local affiliated cues attached under {skill.skill_id} for {task_id}.\n"
                "metadata:\n"
                "  skill_origin: generated\n"
                "  artifact_kind: affiliated_cues\n"
                "  execution_semantics: advisory_only\n"
                f"  task_id: {task_id}\n"
                f"  parent_skill_id: {skill.skill_id}\n"
                f"  cue_count: {len(cues)}\n"
                "---\n\n"
                f"# Affiliated Cues For `{skill.skill_id}`\n\n"
                "These are task-local affiliated rescue cues attached under this selected skill.\n"
                "They provide local supplemental context and do not expand the selected skill set or change runtime control flow.\n"
                "If any detail conflicts with the selected skill, defer to the selected skill.\n\n"
                + "\n\n".join(sections)
                + "\n"
            ),
            encoding="utf-8",
        )
        sidecar_paths[skill.skill_id] = str(cue_path)
    return sidecar_paths
