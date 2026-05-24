from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RescueConfig:
    min_parent_score: float = 0.35
    min_subunit_score: float = 0.12
    max_global_rescues: int = 3
    max_per_parent: int = 1
    redundancy_threshold: float = 0.6


def _normalize_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _is_redundant(candidate_text: str, seen_texts: list[str], threshold: float) -> bool:
    candidate_tokens = _normalize_tokens(candidate_text)
    if not candidate_tokens:
        return False
    for text in seen_texts:
        seen_tokens = _normalize_tokens(text)
        if not seen_tokens:
            continue
        overlap = len(candidate_tokens & seen_tokens) / len(candidate_tokens | seen_tokens)
        if overlap >= threshold:
            return True
    return False


def select_rescued_subunits(
    *,
    selected_skill_ids: list[str],
    selected_subunit_texts: list[str],
    rescue_candidate_skill_records: list[dict[str, object]],
    config: RescueConfig | None = None,
) -> list[dict[str, object]]:
    cfg = config or RescueConfig()
    rescued: list[dict[str, object]] = []
    seen_texts = list(selected_subunit_texts)
    rescued_per_parent: dict[str, int] = {}
    selected_skill_id_set = set(selected_skill_ids)

    for record in rescue_candidate_skill_records:
        if not isinstance(record, dict):
            continue
        parent_skill_id = record.get("skill_id")
        if not isinstance(parent_skill_id, str) or parent_skill_id in selected_skill_id_set:
            continue

        parent_score = record.get("final_score")
        if not isinstance(parent_score, (int, float)) or parent_score < cfg.min_parent_score:
            continue

        allowed_for_parent = cfg.max_per_parent - rescued_per_parent.get(parent_skill_id, 0)
        if allowed_for_parent <= 0:
            continue

        for subunit in record.get("top_subunits", []):
            if len(rescued) >= cfg.max_global_rescues or allowed_for_parent <= 0:
                break
            if not isinstance(subunit, dict):
                continue

            subunit_score = subunit.get("subunit_score")
            subunit_text = subunit.get("subunit_text")
            if not isinstance(subunit_score, (int, float)) or subunit_score < cfg.min_subunit_score:
                continue
            if not isinstance(subunit_text, str) or not subunit_text.strip():
                continue
            if _is_redundant(subunit_text, seen_texts, cfg.redundancy_threshold):
                continue

            rescued.append(
                {
                    "source_skill_id": parent_skill_id,
                    "source_graph_skill_id": record.get("graph_skill_id"),
                    "parent_final_score": round(float(parent_score), 4),
                    "subunit_id": subunit.get("subunit_id"),
                    "subunit_text": subunit_text,
                    "subunit_score": round(float(subunit_score), 4),
                    "subunit_similarity": subunit.get("subunit_similarity"),
                }
            )
            seen_texts.append(subunit_text)
            rescued_per_parent[parent_skill_id] = rescued_per_parent.get(parent_skill_id, 0) + 1
            allowed_for_parent -= 1

    return rescued
