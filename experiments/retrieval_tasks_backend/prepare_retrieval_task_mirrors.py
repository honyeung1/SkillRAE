#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

from retrieval import MODEL_NAME, _local_snapshot_candidates
from experiments.retrieval_tasks_backend.affiliation_exposure import (
    AffiliationConfig,
    build_affiliation_artifacts,
    write_affiliated_cue_sidecars,
)
from experiments.retrieval_tasks_backend.coordinator_ablation import (
    AFFILIATE_REFINE_VERSION,
    AFFILIATE_REFINE_V2_VERSION,
    AFFILIATE_REFINE_COMPACT_VERSION,
    DEFAULT_FRONT_PACKET_TOKEN_BUDGET,
    STATE_CONSISTENCY_VERSION,
    affiliate_refine_compact_enabled_for_variant,
    affiliate_refine_enabled_for_variant,
    affiliate_refine_v2_enabled_for_variant,
    build_front_packet,
    compute_subunit_degree_map,
    execution_guard_enabled_for_variant,
    EXECUTION_GUARD_VERSION,
    extract_task_output_contract,
    get_variant_spec,
    state_consistency_enabled_for_variant,
    strip_task_contract_guard,
)
from experiments.retrieval_tasks_backend.context_skill_compiler import (
    compile_context_skill_markdown,
    compile_affiliation_coordinator_markdown,
    load_selected_skill_context,
    RescuedSubunitContext,
    SelectedSubunitContext,
)
from experiments.retrieval_tasks_backend.rescue_selector import select_rescued_subunits


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "tasks"
GLOBAL_SKILL_POOL = REPO_ROOT / "global_skill_pool"
INIT_SKILL_SCRIPT = REPO_ROOT / "tools" / "skill_utils" / "init_skill.py"


def resolve_runs_dir() -> Path:
    configured_runs_root = os.environ.get("SKILLSBENCH_RUNS_ROOT")
    if configured_runs_root:
        return Path(configured_runs_root).expanduser()

    user_name = os.environ.get("USER") or os.environ.get("USERNAME")
    if user_name:
        user_runs_dir = Path("/mnt/data") / user_name / "runs"
        if user_runs_dir.exists():
            return user_runs_dir

    shared_data_root = Path("/mnt/data")
    if shared_data_root.exists():
        for candidate in sorted(shared_data_root.glob("*/runs")):
            if candidate.is_dir():
                return candidate

    return Path.home() / "skillsbench-artifacts" / "runs"


RUNS_DIR = resolve_runs_dir()
CACHE_DIR = RUNS_DIR / "retrieval_cache"
TASK_COPY_IGNORE_NAMES = (".claude", ".claude.json")
CACHE_SCHEMA_VERSION = 3
RETRIEVER_FINGERPRINT = "SkillRetriever_v2_repo_root_failfast_k5"
DEFAULT_K = 5
DEFAULT_RETRIEVAL_MODE = "topk"
DEFAULT_SYNTH_POSITION_MODE = "synth-last"
DEFAULT_POST_RERANK_TOP_M = 8
DEFAULT_POST_RERANK_TIMEOUT_SECONDS = 20.0
DEFAULT_LLM_TOPK_TIMEOUT_SECONDS = 20.0
DEFAULT_COORDINATOR_VARIANT = "A0"
DEFAULT_FRONT_PACKET_BUDGET = DEFAULT_FRONT_PACKET_TOKEN_BUDGET
SUBUNIT_DEGREE_MAP = compute_subunit_degree_map(REPO_ROOT)
ABLATION_RETRIEVAL_MODE = "topk_context_selected_affiliated_rescue"
RETRIEVAL_MODE_BIPARTITE_ONLY = "topk_context_selected_affiliated_rescue_bipartite_only"
RETRIEVAL_MODE_NO_SUBUNIT = "topk_context_selected_affiliated_rescue_no_subunit"
RETRIEVAL_MODE_TOPK_FRONT_PACKET_SELECTED_ONLY = "topk_front_packet_selected_only"
RETRIEVAL_MODE_VANILLA_TOPK_FRONT_PACKET_SELECTED_ONLY = "vanilla_topk_front_packet_selected_only"
RETRIEVAL_MODE_LLM_TOPK_FRONT_PACKET_SELECTED_ONLY = "llm_topk_front_packet_selected_only"
VANILLA_TOPK_RETRIEVAL_MODES = {
    "vanilla_topk",
    "vanilla_topk_plus_synth",
    RETRIEVAL_MODE_VANILLA_TOPK_FRONT_PACKET_SELECTED_ONLY,
}
LLM_TOPK_RETRIEVAL_MODES = {
    "llm_topk",
    RETRIEVAL_MODE_LLM_TOPK_FRONT_PACKET_SELECTED_ONLY,
}
FRONT_PACKET_SELECTED_ONLY_RETRIEVAL_MODES = {
    RETRIEVAL_MODE_TOPK_FRONT_PACKET_SELECTED_ONLY,
    RETRIEVAL_MODE_VANILLA_TOPK_FRONT_PACKET_SELECTED_ONLY,
    RETRIEVAL_MODE_LLM_TOPK_FRONT_PACKET_SELECTED_ONLY,
}
AFFILIATED_RESCUE_RETRIEVAL_MODES = {
    ABLATION_RETRIEVAL_MODE,
    RETRIEVAL_MODE_BIPARTITE_ONLY,
    RETRIEVAL_MODE_NO_SUBUNIT,
}
ABLATION_RETRIEVAL_MODES = set(AFFILIATED_RESCUE_RETRIEVAL_MODES)
COORDINATOR_RETRIEVAL_MODES = ABLATION_RETRIEVAL_MODES | FRONT_PACKET_SELECTED_ONLY_RETRIEVAL_MODES
A2_TOPK_GUARD_VARIANTS = {"A2_topk_guard"}
TASK_CONTRACT_GUARD_ENV = "SKILLSBENCH_TASK_CONTRACT_GUARD_ENABLE"
TASK_CONTRACT_GUARD_DISABLE_TARGETS = {
    ("A6", RETRIEVAL_MODE_BIPARTITE_ONLY),
    ("A6", RETRIEVAL_MODE_NO_SUBUNIT),
    ("A2_topk_guard", "topk"),
}
RESCUE_RETRIEVAL_MODES = {
    "topk_context_selected_plus_rescue",
    ABLATION_RETRIEVAL_MODE,
    RETRIEVAL_MODE_BIPARTITE_ONLY,
}
AFFILIATE_REFINE_V2_HIGH_RISK_BUCKETS = {
    "context_not_consumed",
    "context_consumed_but_timeout",
    "agent_command_timeout_or_no_result",
    "output_contract_or_format_issue",
}


def env_flag_enabled(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def task_contract_guard_disabled_for_run(
    coordinator_variant: str, retrieval_mode: str
) -> bool:
    return (
        not env_flag_enabled(TASK_CONTRACT_GUARD_ENV, default=True)
        and (coordinator_variant, retrieval_mode) in TASK_CONTRACT_GUARD_DISABLE_TARGETS
    )
AFFILIATE_REFINE_V2_A3_ROWS_DIR = Path(
    os.environ.get(
        "AFFILIATE_REFINE_V2_A3_ROWS_DIR",
        "<repo-root>/deployment/runner/artifacts/retrieval_tasks_backend/runs/full87_A3_prewarm_api_only_proxy_p3_stagger45_20260426_034308/rows",
    )
)


def log(message: str) -> None:
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


def load_tasks(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def load_historical_a3_task_outcome(task_id: str) -> dict[str, object] | None:
    rows_dir = AFFILIATE_REFINE_V2_A3_ROWS_DIR
    if not rows_dir.exists():
        return None
    matches = sorted(rows_dir.glob(f"*-{task_id}.json"))
    if not matches:
        return None
    try:
        payload = json.loads(matches[-1].read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def get_instruction_hash(instruction_text: str) -> str:
    return hashlib.sha256(instruction_text.encode("utf-8")).hexdigest()


def get_cache_path(instr_hash: str, retrieval_mode: str) -> Path:
    if retrieval_mode not in {"topk", "topk_plus_synth"}:
        return CACHE_DIR / f"{instr_hash}.{retrieval_mode}.json"
    return CACHE_DIR / f"{instr_hash}.json"


def load_cached_retrieval(
    instr_hash: str,
    k: int,
    retrieval_mode: str,
) -> tuple[list[str] | None, dict[str, object] | None]:
    cache_path = get_cache_path(instr_hash, retrieval_mode)
    if not cache_path.exists():
        return None, None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    if data.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
        return None, None
    if data.get("retriever_fingerprint") != RETRIEVER_FINGERPRINT:
        return None, None
    if data.get("k") != k:
        return None, None
    cached_mode = data.get("retrieval_mode")
    if cached_mode is None and retrieval_mode in {"topk", "topk_plus_synth"}:
        cached_mode = "topk"
    if cached_mode != retrieval_mode:
        return None, None
    skills = data.get("skills")
    metadata = data.get("retrieval_metadata")
    if (
        retrieval_mode in LLM_TOPK_RETRIEVAL_MODES
        and isinstance(metadata, dict)
        and metadata.get("fallback_used") is True
    ):
        # Do not reuse llm_topk fallback cache entries.
        # They usually come from transient API failures or stale credentials and
        # should be refreshed instead of being treated as a healthy llm result.
        return None, None
    return (
        skills if isinstance(skills, list) else None,
        metadata if isinstance(metadata, dict) else None,
    )


def save_cached_retrieval(
    instr_hash: str,
    skills: list[str],
    k: int,
    retrieval_mode: str,
    retrieval_metadata: dict[str, object] | None = None,
) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = get_cache_path(instr_hash, retrieval_mode)
    payload = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "retriever_fingerprint": RETRIEVER_FINGERPRINT,
        "k": k,
        "query_hash": instr_hash,
        "retrieval_mode": retrieval_mode,
        "skills": skills,
        "retrieval_metadata": retrieval_metadata or {},
        "updated_at": dt.datetime.now().isoformat(),
    }
    cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ranked_unique(skills: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for skill in skills:
        if skill not in seen:
            seen.add(skill)
            out.append(skill)
    return out


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _ablation_score(record: dict[str, object], retrieval_mode: str) -> float:
    l0 = _as_float(record.get("l0_score"))
    l1 = _as_float(record.get("l1_score"))
    prior = _as_float(record.get("prior_score"))
    if retrieval_mode == RETRIEVAL_MODE_BIPARTITE_ONLY:
        return l0
    if retrieval_mode == RETRIEVAL_MODE_NO_SUBUNIT:
        score = 0.6 * l1 + 0.15 * prior
        if bool(record.get("l2_boosted")):
            score *= 1.10
        return score
    return _as_float(record.get("final_score"))


def apply_retrieval_mode_ablation(
    *,
    retrieval_mode: str,
    retrieval_metadata: dict[str, object] | None,
    k: int,
) -> dict[str, object]:
    if retrieval_mode not in {RETRIEVAL_MODE_BIPARTITE_ONLY, RETRIEVAL_MODE_NO_SUBUNIT}:
        return retrieval_metadata or {}
    metadata = dict(retrieval_metadata or {})
    selected = metadata.get("selected_skill_records")
    rescue = metadata.get("rescue_candidate_skill_records")
    if not isinstance(selected, list) and not isinstance(rescue, list):
        return metadata

    merged_by_skill: dict[str, dict[str, object]] = {}
    for row in (selected or []):
        if isinstance(row, dict) and isinstance(row.get("skill_id"), str):
            merged_by_skill[row["skill_id"]] = dict(row)
    for row in (rescue or []):
        if isinstance(row, dict) and isinstance(row.get("skill_id"), str):
            merged_by_skill.setdefault(row["skill_id"], dict(row))
    if not merged_by_skill:
        return metadata

    rescored = sorted(
        merged_by_skill.values(),
        key=lambda record: (
            _ablation_score(record, retrieval_mode),
            _as_float(record.get("final_score")),
        ),
        reverse=True,
    )
    rescue_pool_size = min(
        len(rescored),
        max(10, k * 4),
    )
    selected_rows: list[dict[str, object]] = []
    rescue_rows: list[dict[str, object]] = []

    for idx, row in enumerate(rescored):
        updated = dict(row)
        updated["final_score"] = round(_ablation_score(updated, retrieval_mode), 4)
        if retrieval_mode == RETRIEVAL_MODE_NO_SUBUNIT:
            updated["l0_score"] = 0.0
            updated["top_subunits"] = []
        if idx < k:
            selected_rows.append(updated)
            continue
        if retrieval_mode != RETRIEVAL_MODE_NO_SUBUNIT and idx < rescue_pool_size:
            rescue_rows.append(updated)

    metadata["selected_skill_records"] = selected_rows
    metadata["selected_skill_ids"] = [row["skill_id"] for row in selected_rows if isinstance(row.get("skill_id"), str)]
    metadata["rescue_candidate_skill_records"] = rescue_rows
    metadata["rescue_candidate_pool_size"] = 0 if retrieval_mode == RETRIEVAL_MODE_NO_SUBUNIT else len(rescue_rows)
    metadata["retrieval_mode"] = retrieval_mode
    metadata["ablation_profile"] = (
        "l0_bipartite_only_no_intent_community"
        if retrieval_mode == RETRIEVAL_MODE_BIPARTITE_ONLY
        else "l2_l1_only_no_subunit"
    )
    metadata["ablation_layer_switches"] = {
        "intent_community_enabled": retrieval_mode != RETRIEVAL_MODE_BIPARTITE_ONLY,
        "subunit_layer_enabled": retrieval_mode != RETRIEVAL_MODE_NO_SUBUNIT,
    }
    if retrieval_mode == RETRIEVAL_MODE_NO_SUBUNIT:
        metadata["top_n_subunits_considered"] = 0
        metadata["top_selected_subunits_per_skill"] = 0
    return metadata


def build_synthesized_skill_name(task_id: str, instr_hash: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")
    slug = slug[:24] or "task"
    return f"synth-coordinator-{slug}-{instr_hash[:8]}"


def build_context_skill_name(task_id: str, instr_hash: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")
    slug = slug[:24] or "task"
    return f"context-skill-{slug}-{instr_hash[:8]}"


def build_affiliation_skill_name(task_id: str, instr_hash: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")
    slug = slug[:24] or "task"
    return f"affiliation-coordinator-{slug}-{instr_hash[:8]}"


def extract_frontmatter_fields(skill_md_text: str) -> tuple[str | None, str | None]:
    if not skill_md_text.startswith("---\n"):
        return None, None
    _, _, remainder = skill_md_text.partition("\n")
    frontmatter, sep, _ = remainder.partition("\n---\n")
    if not sep:
        return None, None

    name = None
    description = None
    for line in frontmatter.splitlines():
        if line.startswith("name:"):
            name = line.partition(":")[2].strip()
        elif line.startswith("description:"):
            description = line.partition(":")[2].strip()
    return name, description


def apply_post_retrieval_rerank(
    task_instruction: str,
    ranked_skills: list[str],
    enabled: bool,
    top_m: int,
    model: str | None,
    timeout_seconds: float,
    max_keep: int | None,
) -> dict:
    result = {
        "post_rerank_enabled": enabled,
        "post_rerank_applied": False,
        "post_rerank_failed": False,
        "post_rerank_failure_reason": None,
        "original_skill_order": list(ranked_skills),
        "reranked_skill_order": list(ranked_skills),
        "keep_k": len(ranked_skills),
        "use_no_skill": False,
        "task_mode": None,
        "post_rerank_selected": [],
        "post_rerank_dropped": [],
        "post_rerank_skill_notes": {},
        "post_rerank_raw_response": None,
    }
    if not enabled:
        return result

    try:
        from experiments.retrieval_tasks_backend.llm_skill_reranker import LlmSkillReranker

        reranker = LlmSkillReranker(
            model=model,
            timeout_seconds=timeout_seconds,
            top_m=top_m,
            max_keep=max_keep,
        )
        rerank_output = reranker.rerank(task_instruction, ranked_skills)
        fallback_tail = [skill for skill in ranked_skills if skill not in rerank_output["reranked_skills"]]
        reranked_skill_order = list(rerank_output["reranked_skills"]) + fallback_tail
        result.update(
            {
                "post_rerank_applied": True,
                "reranked_skill_order": reranked_skill_order,
                "keep_k": rerank_output["keep_k"],
                "use_no_skill": rerank_output["use_no_skill"],
                "task_mode": rerank_output["task_mode"],
                "post_rerank_selected": rerank_output["selected"],
                "post_rerank_dropped": rerank_output["dropped"],
                "post_rerank_skill_notes": rerank_output["skill_notes"],
                "post_rerank_raw_response": rerank_output["raw_response"],
            }
        )
        if rerank_output["use_no_skill"]:
            result["reranked_skill_order"] = []
        else:
            result["reranked_skill_order"] = reranked_skill_order[: rerank_output["keep_k"]]
    except Exception as exc:
        result["post_rerank_failed"] = True
        result["post_rerank_failure_reason"] = f"{type(exc).__name__}: {exc}"
    return result


class VanillaTopKRetriever:
    def __init__(self, skill_pool_dir: Path = GLOBAL_SKILL_POOL):
        self.skill_pool_dir = Path(skill_pool_dir)
        self.model = self._load_model()
        self.skill_names: list[str] = []
        self.skill_keys: list[str] = []
        self.skill_embeddings = np.empty((0, 0), dtype=np.float32)
        self._build_skill_index()

    def _load_model(self) -> SentenceTransformer:
        model_path_env = os.environ.get("SKILLSBENCH_EMBED_MODEL_PATH")
        load_errors: list[str] = []

        if model_path_env:
            model_path = Path(model_path_env).expanduser()
            return SentenceTransformer(str(model_path), device="cpu", local_files_only=True)

        for local_path in _local_snapshot_candidates(MODEL_NAME):
            try:
                model = SentenceTransformer(str(local_path), device="cpu", local_files_only=True)
                log(f"Loaded local embedding snapshot for vanilla_topk: {local_path}")
                return model
            except Exception as exc:
                load_errors.append(f"{local_path}: {exc}")

        try:
            model = SentenceTransformer(MODEL_NAME, device="cpu", local_files_only=True)
            log("Loaded local HF cache by model id for vanilla_topk.")
            return model
        except Exception as exc:
            load_errors.append(f"{MODEL_NAME} (local cache): {exc}")

        try:
            return SentenceTransformer(MODEL_NAME, device="cpu")
        except Exception as exc:
            load_errors.append(f"{MODEL_NAME} (remote): {exc}")
            joined_errors = "; ".join(load_errors)
            raise RuntimeError(f"Failed to load embedding model for vanilla_topk: {joined_errors}") from exc

    def _build_skill_index(self) -> None:
        skill_names: list[str] = []
        skill_keys: list[str] = []
        skill_texts: list[str] = []

        for skill_dir in sorted(self.skill_pool_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md_path = skill_dir / "SKILL.md"
            if not skill_md_path.exists():
                continue
            skill_md_text = skill_md_path.read_text(encoding="utf-8")
            frontmatter_name, description = extract_frontmatter_fields(skill_md_text)
            skill_name = frontmatter_name or skill_dir.name
            skill_description = description or ""
            skill_text = f"{skill_name}. {skill_description}".strip()
            skill_keys.append(skill_dir.name)
            skill_names.append(skill_name)
            skill_texts.append(skill_text)

        self.skill_keys = skill_keys
        self.skill_names = skill_names
        if not skill_texts:
            self.skill_embeddings = np.empty((0, 0), dtype=np.float32)
            return

        log(f"Encoding {len(skill_texts)} skills for vanilla_topk.")
        self.skill_embeddings = self.model.encode(
            skill_texts,
            batch_size=32,
            show_progress_bar=True,
            normalize_embeddings=True,
        )

    def retrieve(self, task_description: str, k: int) -> list[dict[str, float | str]]:
        if not self.skill_names:
            return []

        task_embedding = self.model.encode([task_description], normalize_embeddings=True)[0]
        similarities = self.skill_embeddings @ task_embedding
        top_indices = np.argsort(similarities)[-k:][::-1]

        return [
            {
                "skill_name": self.skill_keys[idx],
                "score": round(float(similarities[idx]), 4),
                "origin": "vanilla",
            }
            for idx in top_indices
        ]


class LlmTopKRetriever:
    def __init__(
        self,
        skill_pool_dir: Path = GLOBAL_SKILL_POOL,
        selector=None,
        fallback_retriever: VanillaTopKRetriever | None = None,
        timeout_seconds: float = DEFAULT_LLM_TOPK_TIMEOUT_SECONDS,
        candidate_pool_size: int | None = None,
    ):
        from experiments.retrieval_tasks_backend.llm_skill_reranker import LlmTopKSelector, SkillCatalogEntry

        self.skill_pool_dir = Path(skill_pool_dir)
        self.selector = selector or LlmTopKSelector(timeout_seconds=timeout_seconds)
        self.fallback_retriever = fallback_retriever or VanillaTopKRetriever(skill_pool_dir=self.skill_pool_dir)
        env_candidate_pool = os.environ.get("LLM_TOPK_CANDIDATE_POOL")
        if candidate_pool_size is None:
            if env_candidate_pool and env_candidate_pool.isdigit():
                candidate_pool_size = int(env_candidate_pool)
            else:
                candidate_pool_size = 32
        self.candidate_pool_size = max(1, candidate_pool_size)
        self._catalog_entry_cls = SkillCatalogEntry
        self.catalog = self._build_skill_catalog()
        self.catalog_by_id = {entry.skill_id: entry for entry in self.catalog}

    def _build_skill_catalog(self) -> list:
        catalog: list = []
        for skill_dir in sorted(self.skill_pool_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md_path = skill_dir / "SKILL.md"
            if not skill_md_path.exists():
                continue
            skill_md_text = skill_md_path.read_text(encoding="utf-8")
            frontmatter_name, description = extract_frontmatter_fields(skill_md_text)
            catalog.append(
                self._catalog_entry_cls(
                    skill_id=skill_dir.name,
                    skill_name=frontmatter_name or skill_dir.name,
                    description=description or "",
                )
            )
        return catalog

    def retrieve_with_metadata(self, task_description: str, k: int) -> tuple[list[dict[str, object]], dict[str, object]]:
        from experiments.retrieval_tasks_backend.llm_skill_reranker import LLM_TOPK_SELECTOR_VERSION

        effective_top_k = min(k, len(self.catalog))
        candidate_fetch_k = min(len(self.catalog), max(effective_top_k, self.candidate_pool_size))
        candidate_rows = self.fallback_retriever.retrieve(task_description, k=candidate_fetch_k)
        candidate_skill_ids = [row["skill_name"] for row in candidate_rows if isinstance(row.get("skill_name"), str)]
        candidate_catalog = [self.catalog_by_id[skill_id] for skill_id in candidate_skill_ids if skill_id in self.catalog_by_id]
        if len(candidate_catalog) < effective_top_k:
            candidate_catalog = self.catalog

        base_metadata: dict[str, object] = {
            "retrieval_mode": "llm_topk",
            "retrieval_model": getattr(self.selector, "model", None),
            "selector_version": LLM_TOPK_SELECTOR_VERSION,
            "candidate_pool_size": len(self.catalog),
            "candidate_pool_size_used": len(candidate_catalog),
            "top_k": k,
            "selected_skill_ids": [],
            "fallback_used": False,
            "fallback_mode": None,
        }
        try:
            selection = self.selector.select(task_description, candidate_catalog, effective_top_k)
            selected_skill_ids = selection["selected_skill_ids"]
            metadata = {
                **base_metadata,
                "selected_skill_ids": selected_skill_ids,
                "selector_version": selection.get("selector_version", LLM_TOPK_SELECTOR_VERSION),
                "selector_repair_used": bool(selection.get("repair_used", False)),
            }
            return (
                [
                    {
                        "skill_name": skill_id,
                        "score": None,
                        "origin": "llm_topk",
                    }
                    for skill_id in selected_skill_ids
                ],
                metadata,
            )
        except Exception as exc:
            fallback_rows = self.fallback_retriever.retrieve(task_description, k=k)
            fallback_skill_ids = [row["skill_name"] for row in fallback_rows]
            return (
                [
                    {
                        **row,
                        "origin": "llm_topk_fallback",
                    }
                    for row in fallback_rows
                ],
                {
                    **base_metadata,
                    "selected_skill_ids": fallback_skill_ids,
                    "fallback_used": True,
                    "fallback_mode": "vanilla_topk",
                    "fallback_reason": f"{type(exc).__name__}: {exc}",
                    "selector_version": LLM_TOPK_SELECTOR_VERSION,
                    "selector_repair_used": True,
                },
            )

    def retrieve(self, task_description: str, k: int) -> list[dict[str, object]]:
        rows, _ = self.retrieve_with_metadata(task_description, k)
        return rows


def build_placeholder_skill_md(
    skill_name: str,
    task_id: str,
    retrieved_skills_ranked: list[str],
) -> str:
    retrieved_list = "\n".join(f"- `{skill}`" for skill in retrieved_skills_ranked) or "- None"
    return (
        f"---\n"
        f"name: {skill_name}\n"
        f"description: Task-local synthesized coordinator placeholder for {task_id}; use it to orient across the retrieved skills without overriding source-skill facts.\n"
        f"metadata:\n"
        f"  skill_origin: generated\n"
        f"  synthesis_stage: placeholder\n"
        f"  task_id: {task_id}\n"
        f"---\n\n"
        f"# Synthesized Coordinator Placeholder\n\n"
        f"This is a task-local synthesized coordinator placeholder for `{task_id}`.\n\n"
        f"Use it as an integration entrypoint across the retrieved skills when helpful.\n"
        f"If any fine-grained factual detail conflicts with an original retrieved skill or its traceable source context, defer to the original source skill.\n\n"
        f"## Current Scope\n\n"
        f"- Placeholder only.\n"
        f"- No real synthesis from subunits or source skills has been implemented yet.\n"
        f"- Treat this as organizational guidance, not as a higher-priority fact source.\n\n"
        f"## Retrieved Skills In Scope\n\n"
        f"{retrieved_list}\n"
    )


def create_placeholder_synthesized_skill(
    skill_name: str,
    task_id: str,
    dest_skills_dir: Path,
    retrieved_skills_ranked: list[str],
) -> Path:
    subprocess.run(
        [sys.executable, str(INIT_SKILL_SCRIPT), skill_name, "--path", str(dest_skills_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    skill_dir = dest_skills_dir / skill_name
    skill_md_path = skill_dir / "SKILL.md"
    skill_md_path.write_text(
        build_placeholder_skill_md(
            skill_name=skill_name,
            task_id=task_id,
            retrieved_skills_ranked=retrieved_skills_ranked,
        )
        + "\n",
        encoding="utf-8",
    )
    return skill_dir


def build_selected_skill_contexts(
    *,
    retrieved_skills_ranked: list[str],
    retrieval_metadata: dict[str, object] | None,
) -> tuple[list, dict[str, dict[str, object]], list[dict[str, object]]]:
    selected_skill_records = {
        record["skill_id"]: record
        for record in (retrieval_metadata or {}).get("selected_skill_records", [])
        if isinstance(record, dict) and isinstance(record.get("skill_id"), str)
    }
    selected_skill_contexts = []
    for skill_id in ranked_unique(retrieved_skills_ranked):
        skill_md_path = GLOBAL_SKILL_POOL / skill_id / "SKILL.md"
        if not skill_md_path.exists():
            continue
        skill_record = selected_skill_records.get(skill_id, {})
        highlighted_subunits = []
        for subunit in skill_record.get("top_subunits", []):
            if not isinstance(subunit, dict):
                continue
            highlighted_subunits.append(
                SelectedSubunitContext(
                    source_skill_id=skill_id,
                    subunit_id=str(subunit.get("subunit_id", "")),
                    subunit_text=str(subunit.get("subunit_text", "")),
                    subunit_score=subunit.get("subunit_score"),
                    source_skill_path=skill_md_path,
                )
            )
        selected_skill_contexts.append(
            load_selected_skill_context(
                skill_id=skill_id,
                skill_md_path=skill_md_path,
                highlighted_subunits=highlighted_subunits,
            )
        )
    selected_highlighted_subunits = [
        {
            "source_skill_id": skill.skill_id,
            "subunit_id": subunit.subunit_id,
            "subunit_text": subunit.subunit_text,
            "subunit_score": subunit.subunit_score,
        }
        for skill in selected_skill_contexts
        for subunit in skill.highlighted_subunits
    ]
    return selected_skill_contexts, selected_skill_records, selected_highlighted_subunits


def build_rescued_subunit_rows(
    *,
    retrieval_mode: str,
    selected_skill_contexts: list,
    selected_highlighted_subunits: list[dict[str, object]],
    retrieval_metadata: dict[str, object] | None,
) -> list[dict[str, object]]:
    if retrieval_mode not in RESCUE_RETRIEVAL_MODES:
        return []
    return select_rescued_subunits(
        selected_skill_ids=[skill.skill_id for skill in selected_skill_contexts],
        selected_subunit_texts=[subunit["subunit_text"] for subunit in selected_highlighted_subunits],
        rescue_candidate_skill_records=(retrieval_metadata or {}).get("rescue_candidate_skill_records", []),
    )


def create_context_skill(
    skill_name: str,
    task_id: str,
    task_instruction: str,
    dest_skills_dir: Path,
    retrieved_skills_ranked: list[str],
    retrieval_metadata: dict[str, object] | None = None,
) -> tuple[Path, dict[str, object]]:
    selected_skill_contexts, _, selected_highlighted_subunits = build_selected_skill_contexts(
        retrieved_skills_ranked=retrieved_skills_ranked,
        retrieval_metadata=retrieval_metadata,
    )
    rescued_subunit_contexts: list[RescuedSubunitContext] = []
    rescued_subunit_rows = build_rescued_subunit_rows(
        retrieval_mode=(retrieval_metadata or {}).get("retrieval_mode", ""),
        selected_skill_contexts=selected_skill_contexts,
        selected_highlighted_subunits=selected_highlighted_subunits,
        retrieval_metadata=retrieval_metadata,
    )
    if rescued_subunit_rows:
        for rescue_row in rescued_subunit_rows:
            parent_skill_id = rescue_row["source_skill_id"]
            parent_skill_md_path = GLOBAL_SKILL_POOL / parent_skill_id / "SKILL.md"
            if not parent_skill_md_path.exists():
                continue
            rescued_subunit_contexts.append(
                RescuedSubunitContext(
                    source_skill_id=parent_skill_id,
                    subunit_id=str(rescue_row.get("subunit_id", "")),
                    subunit_text=str(rescue_row.get("subunit_text", "")),
                    subunit_score=rescue_row.get("subunit_score"),
                    parent_final_score=rescue_row.get("parent_final_score"),
                    source_skill_path=parent_skill_md_path,
                )
            )

    subprocess.run(
        [sys.executable, str(INIT_SKILL_SCRIPT), skill_name, "--path", str(dest_skills_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    skill_dir = dest_skills_dir / skill_name
    skill_md_path = skill_dir / "SKILL.md"
    skill_md_path.write_text(
        compile_context_skill_markdown(
            skill_name=skill_name,
            task_id=task_id,
            task_instruction=task_instruction,
            selected_skills=selected_skill_contexts,
            rescued_subunits=rescued_subunit_contexts,
        )
        + "\n",
        encoding="utf-8",
    )
    return skill_dir, {
        "selected_highlighted_subunits": selected_highlighted_subunits,
        "rescued_subunits": rescued_subunit_rows,
        "rescued_parent_ids": sorted({row["source_skill_id"] for row in rescued_subunit_rows}),
    }


def build_affiliation_context_bundle(
    *,
    task_id: str,
    task_instruction: str,
    task_dir: Path,
    dest_skills_dir: Path,
    retrieved_skills_ranked: list[str],
    retrieval_metadata: dict[str, object] | None = None,
    emit_affiliated_sidecars: bool = True,
    affiliation_config: AffiliationConfig | None = None,
) -> dict[str, object]:
    selected_skill_contexts, selected_skill_records, selected_highlighted_subunits = build_selected_skill_contexts(
        retrieved_skills_ranked=retrieved_skills_ranked,
        retrieval_metadata=retrieval_metadata,
    )
    rescued_subunit_rows = build_rescued_subunit_rows(
        retrieval_mode=(retrieval_metadata or {}).get("retrieval_mode", ""),
        selected_skill_contexts=selected_skill_contexts,
        selected_highlighted_subunits=selected_highlighted_subunits,
        retrieval_metadata=retrieval_metadata,
    )
    affiliation_artifacts = build_affiliation_artifacts(
        task_instruction=task_instruction,
        selected_skills=selected_skill_contexts,
        selected_skill_records_by_id=selected_skill_records,
        rescued_subunits=rescued_subunit_rows,
        global_skill_pool=GLOBAL_SKILL_POOL,
        repo_root=REPO_ROOT,
        config=affiliation_config,
    )
    affiliation_sidecar_paths = (
        write_affiliated_cue_sidecars(
            task_id=task_id,
            dest_skills_dir=dest_skills_dir,
            selected_skills=selected_skill_contexts,
            cues_by_skill_id=affiliation_artifacts["cues_by_skill_id"],
        )
        if emit_affiliated_sidecars
        else {}
    )
    routing_notes = affiliation_artifacts["routing_notes"]
    for selected_skill_id, cue_path in affiliation_sidecar_paths.items():
        routing_notes.setdefault(selected_skill_id, {})
        routing_notes[selected_skill_id]["cue_path"] = cue_path
        routing_notes[selected_skill_id]["cue_count"] = len(
            affiliation_artifacts["cues_by_skill_id"].get(selected_skill_id, [])
        )

    affiliation_manifest_path = task_dir / "affiliation_manifest.json"
    affiliation_manifest_payload = {
        "task_id": task_id,
        "retrieval_mode": (retrieval_metadata or {}).get("retrieval_mode"),
        "selected_skill_ids": [skill.skill_id for skill in selected_skill_contexts],
        "active_l2_ids": affiliation_artifacts["active_l2_ids"],
        "active_l2_labels": affiliation_artifacts["active_l2_labels"],
        "affiliated_cue_files": affiliation_sidecar_paths,
        "attached_rescued_subunits": affiliation_artifacts["attached_rescued_subunits"],
        "dropped_rescued_subunits": affiliation_artifacts["dropped_rescued_subunits"],
    }
    affiliation_manifest_path.write_text(
        json.dumps(affiliation_manifest_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    affiliated_cue_counts = {
        skill_id: len(cues)
        for skill_id, cues in affiliation_artifacts["cues_by_skill_id"].items()
        if cues
    }
    return {
        "selected_skill_contexts": selected_skill_contexts,
        "selected_skill_records": selected_skill_records,
        "routing_notes": routing_notes,
        "affiliation_artifacts": affiliation_artifacts,
        "affiliation_sidecar_paths": affiliation_sidecar_paths,
        "affiliation_manifest_path": affiliation_manifest_path,
        "affiliated_cue_counts": affiliated_cue_counts,
        "selected_highlighted_subunits": selected_highlighted_subunits,
        "rescued_subunits": rescued_subunit_rows,
        "rescued_parent_ids": sorted({row["source_skill_id"] for row in rescued_subunit_rows}),
        "affiliated_rescued_subunits": affiliation_artifacts["attached_rescued_subunits"],
        "dropped_rescued_subunits": affiliation_artifacts["dropped_rescued_subunits"],
        "affiliated_skill_ids": sorted(affiliated_cue_counts),
        "affiliated_cue_counts": affiliated_cue_counts,
        "affiliated_cue_files": affiliation_sidecar_paths,
        "affiliation_manifest_path": str(affiliation_manifest_path),
        "active_l2_labels": affiliation_artifacts["active_l2_labels"],
    }


def create_affiliation_context_skill(
    *,
    skill_name: str,
    task_id: str,
    task_instruction: str,
    dest_skills_dir: Path,
    bundle: dict[str, object],
) -> tuple[Path, dict[str, object]]:
    subprocess.run(
        [sys.executable, str(INIT_SKILL_SCRIPT), skill_name, "--path", str(dest_skills_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    skill_dir = dest_skills_dir / skill_name
    (skill_dir / "SKILL.md").write_text(
        compile_affiliation_coordinator_markdown(
            skill_name=skill_name,
            task_id=task_id,
            task_instruction=task_instruction,
            selected_skills=bundle["selected_skill_contexts"],
            affiliated_cue_notes=bundle["routing_notes"],
        )
        + "\n",
        encoding="utf-8",
    )
    return skill_dir, {
        key: value
        for key, value in bundle.items()
        if key not in {"selected_skill_contexts", "selected_skill_records", "routing_notes", "affiliation_artifacts"}
    }


def materialize_task_mirror(
    task_id: str,
    ranked_skills: list[str],
    mirror_root: Path,
    retrieval_mode: str,
    synth_position_mode: str,
    instr_hash: str,
    retrieval_metadata: dict[str, object] | None = None,
    coordinator_variant: str = DEFAULT_COORDINATOR_VARIANT,
    front_packet_budget: int = DEFAULT_FRONT_PACKET_BUDGET,
) -> dict:
    src_task_dir = TASKS_DIR / task_id
    dest_task_dir = mirror_root / task_id
    if dest_task_dir.exists():
        shutil.rmtree(dest_task_dir)

    shutil.copytree(
        src_task_dir,
        dest_task_dir,
        ignore=shutil.ignore_patterns(*TASK_COPY_IGNORE_NAMES),
    )

    dest_skills_dir = dest_task_dir / "environment" / "skills"
    if dest_skills_dir.exists():
        shutil.rmtree(dest_skills_dir)
    dest_skills_dir.mkdir(parents=True, exist_ok=True)
    instruction_path = src_task_dir / "instruction.md"
    task_instruction = instruction_path.read_text(encoding="utf-8") if instruction_path.exists() else ""

    copied_skills: list[str] = []
    missing_skills: list[str] = []
    skill_paths: dict[str, str] = {}
    skill_origins: dict[str, dict[str, str | None]] = {}
    retrieved_skill_names = ranked_unique(ranked_skills)
    synthesized_skill_name: str | None = None
    synthesized_skill_dir: Path | None = None
    generated_skill_kind: str | None = None
    generated_skill_manifest: dict[str, object] = {}
    coordinator_kind = "none"
    coordinator_task_local_path: str | None = None
    front_packet_task_local_path: str | None = None
    retrieved_origin = "vanilla" if retrieval_mode in {"vanilla_topk", "vanilla_topk_plus_synth"} else "retrieved"
    variant_spec = get_variant_spec(coordinator_variant)
    task_contract_guard_disabled = task_contract_guard_disabled_for_run(
        coordinator_variant, retrieval_mode
    )
    if task_contract_guard_disabled:
        variant_spec = strip_task_contract_guard(variant_spec)
    task_contract_guard_enabled = not task_contract_guard_disabled
    execution_guard_enabled = execution_guard_enabled_for_variant(coordinator_variant)
    execution_guard_version = EXECUTION_GUARD_VERSION if execution_guard_enabled else None
    state_consistency_enabled = state_consistency_enabled_for_variant(coordinator_variant)
    state_consistency_version = STATE_CONSISTENCY_VERSION if state_consistency_enabled else None
    affiliate_refine_enabled = affiliate_refine_enabled_for_variant(coordinator_variant)
    affiliate_refine_version = AFFILIATE_REFINE_VERSION if affiliate_refine_enabled else None
    affiliate_refine_v2_enabled = False
    affiliate_refine_v2_card_count = 0
    affiliate_refine_v2_gated_reason = "not_v2_variant"
    a3_refine_compact_enabled = affiliate_refine_compact_enabled_for_variant(coordinator_variant)
    a3_refine_compact_card_count = 0
    a3_refine_compact_gated_reason = "disabled" if a3_refine_compact_enabled else "not_compact_variant"
    if affiliate_refine_v2_enabled_for_variant(coordinator_variant):
        affiliate_refine_enabled = True
        affiliate_refine_version = AFFILIATE_REFINE_V2_VERSION
        historical = load_historical_a3_task_outcome(task_id)
        if historical is None:
            affiliate_refine_v2_gated_reason = "history_missing"
        else:
            hist_reward = historical.get("reward")
            hist_bucket = str(historical.get("failure_bucket") or "")
            if hist_reward == 1.0:
                affiliate_refine_v2_gated_reason = "stable_reward_1"
            elif hist_bucket in AFFILIATE_REFINE_V2_HIGH_RISK_BUCKETS:
                affiliate_refine_v2_enabled = True
                affiliate_refine_v2_gated_reason = f"high_risk:{hist_bucket}"
            else:
                affiliate_refine_v2_gated_reason = f"not_high_risk:{hist_bucket or 'unknown'}"
    output_contract = extract_task_output_contract(dest_task_dir)
    (dest_task_dir / "OUTPUT_CONTRACT.json").write_text(
        json.dumps(output_contract.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    def copy_retrieved_skills() -> None:
        for skill_name in retrieved_skill_names:
            src_skill_dir = GLOBAL_SKILL_POOL / skill_name
            if not src_skill_dir.exists():
                missing_skills.append(skill_name)
                continue
            shutil.copytree(src_skill_dir, dest_skills_dir / skill_name)
            copied_skills.append(skill_name)
            skill_paths[skill_name] = str(src_skill_dir)
            skill_origins[skill_name] = {
                "origin": retrieved_origin,
                "source_path": str(src_skill_dir),
                "task_local_path": str(dest_skills_dir / skill_name),
            }

    if retrieval_mode in AFFILIATED_RESCUE_RETRIEVAL_MODES:
        copy_retrieved_skills()
        affiliation_config = (
            AffiliationConfig(enable_intent_community=False)
            if retrieval_mode == RETRIEVAL_MODE_BIPARTITE_ONLY
            else None
        )
        affiliation_bundle = build_affiliation_context_bundle(
            task_id=task_id,
            task_instruction=task_instruction,
            task_dir=dest_task_dir,
            dest_skills_dir=dest_skills_dir,
            retrieved_skills_ranked=ranked_skills,
            retrieval_metadata=retrieval_metadata,
            emit_affiliated_sidecars=variant_spec.emit_affiliated_sidecars,
            affiliation_config=affiliation_config,
        )
        if variant_spec.create_coordinator_skill:
            synthesized_skill_name = build_affiliation_skill_name(task_id=task_id, instr_hash=instr_hash)
            generated_skill_kind = "affiliation_context_skill"
            synthesized_skill_dir, generated_skill_manifest = create_affiliation_context_skill(
                skill_name=synthesized_skill_name,
                task_id=task_id,
                task_instruction=task_instruction,
                dest_skills_dir=dest_skills_dir,
                bundle=affiliation_bundle,
            )
            skill_origins[synthesized_skill_name] = {
                "origin": "generated",
                "source_path": None,
                "task_local_path": str(synthesized_skill_dir),
            }
            coordinator_kind = "skill_file"
            coordinator_task_local_path = str(synthesized_skill_dir / "SKILL.md")
        else:
            generated_skill_manifest = {
                key: value
                for key, value in affiliation_bundle.items()
                if key not in {"selected_skill_contexts", "selected_skill_records", "routing_notes", "affiliation_artifacts"}
            }

        if variant_spec.create_front_packet:
            front_packet_payload = build_front_packet(
                variant_spec=variant_spec,
                task_id=task_id,
                selected_skill_contexts=affiliation_bundle["selected_skill_contexts"],
                routing_notes=affiliation_bundle["routing_notes"],
                contract=output_contract,
                affiliated_cue_rows=(
                    generated_skill_manifest.get("affiliated_rescued_subunits")
                    or generated_skill_manifest.get("rescued_subunits", [])
                ),
                budget_tokens=front_packet_budget,
                subunit_degree_map=SUBUNIT_DEGREE_MAP,
                affiliate_refine_v2_enabled=affiliate_refine_v2_enabled,
                affiliate_refine_v2_gated_reason=affiliate_refine_v2_gated_reason,
            )
            affiliate_refine_v2_card_count = int(front_packet_payload.get("affiliate_refine_v2_card_count") or 0)
            if a3_refine_compact_enabled:
                affiliate_refine_enabled = True
                affiliate_refine_version = AFFILIATE_REFINE_COMPACT_VERSION
                a3_refine_compact_card_count = int(front_packet_payload.get("A3_refine_compact_card_count") or 0)
                a3_refine_compact_gated_reason = str(front_packet_payload.get("A3_refine_compact_gated_reason") or "disabled")
            coordinator_packet_path = dest_task_dir / "COORDINATOR_PACKET.json"
            coordinator_packet_payload = {
                "task_id": task_id,
                "variant_id": coordinator_variant,
                "payload_kind": "coordinator_packet",
                "created_at": dt.datetime.now().isoformat(),
                "front_packet_total_tokens": front_packet_payload["total_tokens"],
                "front_packet_section_tokens": front_packet_payload["section_tokens"],
                "front_packet_section_texts": front_packet_payload["section_texts"],
                "output_contract_targets": list(output_contract.output_paths),
                "output_contract_module_targets": list(output_contract.module_paths),
                "output_contract_formats": list(output_contract.formats),
                "contract_probe_targets": [
                    *list(output_contract.output_paths),
                    *list(output_contract.module_paths),
                ],
                "sources": list(output_contract.sources),
                "verifier_cautions": list(output_contract.verifier_cautions),
                "task_contract_guard_enabled": task_contract_guard_enabled,
                "task_contract_guard_disabled_by_env": task_contract_guard_disabled,
                "execution_guard_enabled": execution_guard_enabled,
                "execution_guard_version": execution_guard_version,
                "state_consistency_enabled": state_consistency_enabled,
                "state_consistency_version": state_consistency_version,
                "affiliate_refine_enabled": affiliate_refine_enabled,
                "affiliate_refine_version": affiliate_refine_version,
                "affiliate_refine_skill_count": front_packet_payload.get("affiliate_refine_skill_count", 0),
                "affiliate_refine_subunit_count": front_packet_payload.get("affiliate_refine_subunit_count", 0),
                "A3_refine_compact_enabled": a3_refine_compact_enabled,
                "A3_refine_compact_card_count": a3_refine_compact_card_count,
                "A3_refine_compact_gated_reason": a3_refine_compact_gated_reason,
            }
            if affiliate_refine_v2_enabled_for_variant(coordinator_variant):
                coordinator_packet_payload.update(
                    {
                        "affiliate_refine_v2_enabled": affiliate_refine_v2_enabled,
                        "affiliate_refine_v2_card_count": affiliate_refine_v2_card_count,
                        "affiliate_refine_v2_gated_reason": affiliate_refine_v2_gated_reason,
                    }
                )
            coordinator_packet_path.write_text(
                json.dumps(coordinator_packet_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            read_first_path = dest_task_dir / "READ_FIRST.md"
            read_first_path.write_text(front_packet_payload["text"], encoding="utf-8")
            front_packet_task_local_path = str(read_first_path)
            coordinator_kind = "front_packet"
            coordinator_task_local_path = front_packet_task_local_path
            generated_skill_manifest.update(
                {
                    "front_packet_task_local_path": front_packet_task_local_path,
                    "front_packet_total_tokens": front_packet_payload["total_tokens"],
                    "front_packet_section_tokens": front_packet_payload["section_tokens"],
                    "front_packet_tokenizer": front_packet_payload["tokenizer_name"],
                    "task_contract_guard_enabled": task_contract_guard_enabled,
                    "task_contract_guard_disabled_by_env": task_contract_guard_disabled,
                    "affiliate_refine_skill_count": front_packet_payload.get("affiliate_refine_skill_count", 0),
                    "affiliate_refine_subunit_count": front_packet_payload.get("affiliate_refine_subunit_count", 0),
                    "A3_refine_compact_enabled": a3_refine_compact_enabled,
                    "A3_refine_compact_card_count": a3_refine_compact_card_count,
                    "A3_refine_compact_gated_reason": a3_refine_compact_gated_reason,
                }
            )
            if affiliate_refine_v2_enabled_for_variant(coordinator_variant):
                generated_skill_manifest.update(
                    {
                        "affiliate_refine_v2_enabled": affiliate_refine_v2_enabled,
                        "affiliate_refine_v2_card_count": affiliate_refine_v2_card_count,
                        "affiliate_refine_v2_gated_reason": affiliate_refine_v2_gated_reason,
                    }
                )
    elif retrieval_mode in FRONT_PACKET_SELECTED_ONLY_RETRIEVAL_MODES:
        copy_retrieved_skills()
        selected_skill_contexts, selected_skill_records, selected_highlighted_subunits = build_selected_skill_contexts(
            retrieved_skills_ranked=ranked_skills,
            retrieval_metadata=retrieval_metadata,
        )
        routing_notes = {
            skill.skill_id: {
                "rank": idx,
                "source": retrieval_mode,
                "routing_hint": f"Top-{idx} retrieved skill; open only if directly useful for this task.",
            }
            for idx, skill in enumerate(selected_skill_contexts[:5], start=1)
        }
        generated_skill_manifest = {
            "retrieval_mode": retrieval_mode,
            "front_packet_selected_only": True,
            "selected_skill_ids": [skill.skill_id for skill in selected_skill_contexts],
            "selected_skill_records": list(selected_skill_records.values()),
            "selected_highlighted_subunits": selected_highlighted_subunits,
            "affiliate_rescue_enabled": False,
            "affiliated_rescued_subunits": [],
            "rescued_subunits": [],
        }
        if variant_spec.create_front_packet:
            front_packet_payload = build_front_packet(
                variant_spec=variant_spec,
                task_id=task_id,
                selected_skill_contexts=selected_skill_contexts,
                routing_notes=routing_notes,
                contract=output_contract,
                affiliated_cue_rows=[],
                budget_tokens=front_packet_budget,
                subunit_degree_map=SUBUNIT_DEGREE_MAP,
                affiliate_refine_v2_enabled=False,
                affiliate_refine_v2_gated_reason="not_v2_variant",
            )
            coordinator_packet_path = dest_task_dir / "COORDINATOR_PACKET.json"
            coordinator_packet_payload = {
                "task_id": task_id,
                "variant_id": coordinator_variant,
                "payload_kind": "coordinator_packet",
                "created_at": dt.datetime.now().isoformat(),
                "front_packet_total_tokens": front_packet_payload["total_tokens"],
                "front_packet_section_tokens": front_packet_payload["section_tokens"],
                "front_packet_section_texts": front_packet_payload["section_texts"],
                "output_contract_targets": list(output_contract.output_paths),
                "output_contract_module_targets": list(output_contract.module_paths),
                "output_contract_formats": list(output_contract.formats),
                "contract_probe_targets": [
                    *list(output_contract.output_paths),
                    *list(output_contract.module_paths),
                ],
                "sources": list(output_contract.sources),
                "verifier_cautions": list(output_contract.verifier_cautions),
                "task_contract_guard_enabled": task_contract_guard_enabled,
                "task_contract_guard_disabled_by_env": task_contract_guard_disabled,
                "execution_guard_enabled": execution_guard_enabled,
                "execution_guard_version": execution_guard_version,
                "state_consistency_enabled": state_consistency_enabled,
                "state_consistency_version": state_consistency_version,
                "affiliate_refine_enabled": False,
                "affiliate_refine_version": None,
                "affiliate_refine_skill_count": 0,
                "affiliate_refine_subunit_count": 0,
                "A3_refine_compact_enabled": False,
                "A3_refine_compact_card_count": 0,
                "A3_refine_compact_gated_reason": "not_compact_variant",
                "front_packet_selected_only": True,
            }
            coordinator_packet_path.write_text(
                json.dumps(coordinator_packet_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            read_first_path = dest_task_dir / "READ_FIRST.md"
            read_first_path.write_text(front_packet_payload["text"], encoding="utf-8")
            front_packet_task_local_path = str(read_first_path)
            coordinator_kind = "front_packet"
            coordinator_task_local_path = front_packet_task_local_path
            generated_skill_manifest.update(
                {
                    "front_packet_task_local_path": front_packet_task_local_path,
                    "front_packet_total_tokens": front_packet_payload["total_tokens"],
                    "front_packet_section_tokens": front_packet_payload["section_tokens"],
                    "front_packet_tokenizer": front_packet_payload["tokenizer_name"],
                    "affiliate_refine_skill_count": 0,
                    "affiliate_refine_subunit_count": 0,
                    "A3_refine_compact_enabled": False,
                    "A3_refine_compact_card_count": 0,
                    "A3_refine_compact_gated_reason": "not_compact_variant",
                }
            )
    elif retrieval_mode in {"topk_context_selected", "topk_context_selected_plus_rescue"}:
        synthesized_skill_name = build_context_skill_name(task_id=task_id, instr_hash=instr_hash)
        generated_skill_kind = "context_skill"
        if synth_position_mode == "synth-first":
            synthesized_skill_dir, generated_skill_manifest = create_context_skill(
                skill_name=synthesized_skill_name,
                task_id=task_id,
                task_instruction=task_instruction,
                dest_skills_dir=dest_skills_dir,
                retrieved_skills_ranked=ranked_skills,
                retrieval_metadata=retrieval_metadata,
            )
            copy_retrieved_skills()
        else:
            copy_retrieved_skills()
            synthesized_skill_dir, generated_skill_manifest = create_context_skill(
                skill_name=synthesized_skill_name,
                task_id=task_id,
                task_instruction=task_instruction,
                dest_skills_dir=dest_skills_dir,
                retrieved_skills_ranked=ranked_skills,
                retrieval_metadata=retrieval_metadata,
            )
        skill_origins[synthesized_skill_name] = {
            "origin": "generated",
            "source_path": None,
            "task_local_path": str(synthesized_skill_dir),
        }
    elif retrieval_mode in {"topk_plus_synth", "topk_synth_only", "vanilla_topk_plus_synth"}:
        synthesized_skill_name = build_synthesized_skill_name(task_id=task_id, instr_hash=instr_hash)
        generated_skill_kind = "placeholder_synth"
        if retrieval_mode == "topk_synth_only":
            synthesized_skill_dir = create_placeholder_synthesized_skill(
                skill_name=synthesized_skill_name,
                task_id=task_id,
                dest_skills_dir=dest_skills_dir,
                retrieved_skills_ranked=ranked_skills,
            )
        elif synth_position_mode == "synth-first":
            synthesized_skill_dir = create_placeholder_synthesized_skill(
                skill_name=synthesized_skill_name,
                task_id=task_id,
                dest_skills_dir=dest_skills_dir,
                retrieved_skills_ranked=ranked_skills,
            )
            copy_retrieved_skills()
        else:
            copy_retrieved_skills()
            synthesized_skill_dir = create_placeholder_synthesized_skill(
                skill_name=synthesized_skill_name,
                task_id=task_id,
                dest_skills_dir=dest_skills_dir,
                retrieved_skills_ranked=ranked_skills,
            )
        skill_origins[synthesized_skill_name] = {
            "origin": "generated",
            "source_path": None,
            "task_local_path": str(synthesized_skill_dir),
        }
    else:
        copy_retrieved_skills()
        if retrieval_mode == "topk" and coordinator_variant in A2_TOPK_GUARD_VARIANTS:
            selected_skill_contexts, selected_skill_records, selected_highlighted_subunits = build_selected_skill_contexts(
                retrieved_skills_ranked=ranked_skills,
                retrieval_metadata=retrieval_metadata,
            )
            routing_notes = {
                skill.skill_id: {
                    "rank": idx,
                    "source": retrieval_mode,
                    "routing_hint": f"Top-{idx} retrieved skill; open only if directly useful for this task.",
                }
                for idx, skill in enumerate(selected_skill_contexts[:5], start=1)
            }
            front_packet_payload = build_front_packet(
                variant_spec=variant_spec,
                task_id=task_id,
                selected_skill_contexts=selected_skill_contexts,
                routing_notes=routing_notes,
                contract=output_contract,
                affiliated_cue_rows=[],
                budget_tokens=front_packet_budget,
                subunit_degree_map=SUBUNIT_DEGREE_MAP,
                affiliate_refine_v2_enabled=False,
                affiliate_refine_v2_gated_reason="not_v2_variant",
            )
            coordinator_packet_path = dest_task_dir / "COORDINATOR_PACKET.json"
            coordinator_packet_payload = {
                "task_id": task_id,
                "variant_id": coordinator_variant,
                "payload_kind": "coordinator_packet",
                "created_at": dt.datetime.now().isoformat(),
                "front_packet_total_tokens": front_packet_payload["total_tokens"],
                "front_packet_section_tokens": front_packet_payload["section_tokens"],
                "front_packet_section_texts": front_packet_payload["section_texts"],
                "output_contract_targets": list(output_contract.output_paths),
                "output_contract_module_targets": list(output_contract.module_paths),
                "output_contract_formats": list(output_contract.formats),
                "contract_probe_targets": [
                    *list(output_contract.output_paths),
                    *list(output_contract.module_paths),
                ],
                "sources": list(output_contract.sources),
                "verifier_cautions": list(output_contract.verifier_cautions),
                "task_contract_guard_enabled": task_contract_guard_enabled,
                "task_contract_guard_disabled_by_env": task_contract_guard_disabled,
                "execution_guard_enabled": execution_guard_enabled,
                "execution_guard_version": execution_guard_version,
                "state_consistency_enabled": state_consistency_enabled,
                "state_consistency_version": state_consistency_version,
                "affiliate_refine_enabled": False,
                "affiliate_refine_version": None,
                "affiliate_refine_skill_count": 0,
                "affiliate_refine_subunit_count": 0,
                "A3_refine_compact_enabled": False,
                "A3_refine_compact_card_count": 0,
                "A3_refine_compact_gated_reason": "not_compact_variant",
                "topk_guard_enabled": task_contract_guard_enabled,
                "selected_skill_ids": [skill.skill_id for skill in selected_skill_contexts],
                "selected_skill_records": list(selected_skill_records.values()),
                "selected_highlighted_subunits": selected_highlighted_subunits,
            }
            coordinator_packet_path.write_text(
                json.dumps(coordinator_packet_payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            read_first_path = dest_task_dir / "READ_FIRST.md"
            read_first_path.write_text(front_packet_payload["text"], encoding="utf-8")
            front_packet_task_local_path = str(read_first_path)
            coordinator_kind = "front_packet"
            coordinator_task_local_path = front_packet_task_local_path
            generated_skill_manifest.update(
                {
                    "retrieval_mode": retrieval_mode,
                    "topk_guard_enabled": task_contract_guard_enabled,
                    "front_packet_task_local_path": front_packet_task_local_path,
                    "front_packet_total_tokens": front_packet_payload["total_tokens"],
                    "front_packet_section_tokens": front_packet_payload["section_tokens"],
                    "front_packet_tokenizer": front_packet_payload["tokenizer_name"],
                    "task_contract_guard_enabled": task_contract_guard_enabled,
                    "task_contract_guard_disabled_by_env": task_contract_guard_disabled,
                    "selected_skill_ids": [skill.skill_id for skill in selected_skill_contexts],
                    "selected_skill_records": list(selected_skill_records.values()),
                    "selected_highlighted_subunits": selected_highlighted_subunits,
                    "affiliate_rescue_enabled": False,
                    "affiliated_rescued_subunits": [],
                    "rescued_subunits": [],
                    "affiliate_refine_skill_count": 0,
                    "affiliate_refine_subunit_count": 0,
                    "A3_refine_compact_enabled": False,
                    "A3_refine_compact_card_count": 0,
                    "A3_refine_compact_gated_reason": "not_compact_variant",
                }
            )

    effective_synth_position_mode = (
        variant_spec.synth_position_mode
        if retrieval_mode in AFFILIATED_RESCUE_RETRIEVAL_MODES
        else synth_position_mode
    )
    injected_skill_order = (
        [synthesized_skill_name, *copied_skills]
        if synthesized_skill_name and effective_synth_position_mode == "synth-first"
        else [*copied_skills, synthesized_skill_name]
        if synthesized_skill_name
        else [*copied_skills]
    )

    retrieved_skill_paths = [
        str(dest_skills_dir / skill_name / "SKILL.md")
        for skill_name in copied_skills
        if (dest_skills_dir / skill_name / "SKILL.md").exists()
    ]
    variant_manifest_payload = {
        "variant_id": coordinator_variant,
        "coordinator_kind": coordinator_kind,
        "coordinator_task_local_path": coordinator_task_local_path,
        "front_packet_task_local_path": front_packet_task_local_path,
        "coordinator_packet_path": (
            str(dest_task_dir / "COORDINATOR_PACKET.json")
            if front_packet_task_local_path is not None
            else None
        ),
        "front_packet_total_tokens": generated_skill_manifest.get("front_packet_total_tokens"),
        "front_packet_section_tokens": generated_skill_manifest.get("front_packet_section_tokens", {}),
        "front_packet_tokenizer": generated_skill_manifest.get("front_packet_tokenizer"),
        "output_contract_targets": list(output_contract.output_paths),
        "output_contract_module_targets": list(output_contract.module_paths),
        "output_contract_formats": list(output_contract.formats),
        "contract_probe_targets": [*list(output_contract.output_paths), *list(output_contract.module_paths)],
        "verifier_sensitive_cautions": list(output_contract.verifier_cautions),
        "retrieved_skill_paths": retrieved_skill_paths,
        "injected_skill_list": injected_skill_order,
        "retrieved_skill_ids": copied_skills,
        "synthesized_skill_name": synthesized_skill_name,
        "coordinator_prompt_delivered": bool(front_packet_task_local_path),
        "task_contract_guard_enabled": task_contract_guard_enabled,
        "task_contract_guard_disabled_by_env": task_contract_guard_disabled,
        "task_contract_guard_env": os.environ.get(TASK_CONTRACT_GUARD_ENV, "1"),
        "execution_guard_enabled": execution_guard_enabled,
        "execution_guard_version": execution_guard_version,
        "state_consistency_enabled": state_consistency_enabled,
        "state_consistency_version": state_consistency_version,
        "affiliate_refine_enabled": affiliate_refine_enabled,
        "affiliate_refine_version": affiliate_refine_version,
        "affiliate_refine_skill_count": generated_skill_manifest.get("affiliate_refine_skill_count", 0),
        "affiliate_refine_subunit_count": generated_skill_manifest.get("affiliate_refine_subunit_count", 0),
        "A3_refine_compact_enabled": generated_skill_manifest.get("A3_refine_compact_enabled", a3_refine_compact_enabled),
        "A3_refine_compact_card_count": generated_skill_manifest.get("A3_refine_compact_card_count", a3_refine_compact_card_count),
        "A3_refine_compact_gated_reason": generated_skill_manifest.get("A3_refine_compact_gated_reason", a3_refine_compact_gated_reason),
    }
    if affiliate_refine_v2_enabled_for_variant(coordinator_variant):
        variant_manifest_payload.update(
            {
                "affiliate_refine_v2_enabled": affiliate_refine_v2_enabled,
                "affiliate_refine_v2_card_count": generated_skill_manifest.get(
                    "affiliate_refine_v2_card_count", affiliate_refine_v2_card_count
                ),
                "affiliate_refine_v2_gated_reason": generated_skill_manifest.get(
                    "affiliate_refine_v2_gated_reason", affiliate_refine_v2_gated_reason
                ),
            }
        )
    (dest_task_dir / "coordinator_variant_manifest.json").write_text(
        json.dumps(variant_manifest_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return {
        "task_id": task_id,
        "mirror_path": str(dest_task_dir),
        "copied_skills": copied_skills,
        "missing_skills": missing_skills,
        "skill_paths": skill_paths,
        "retrieval_mode": retrieval_mode,
        "variant_id": coordinator_variant,
        "synthesized_skill_name": synthesized_skill_name,
        "generated_skill_kind": generated_skill_kind,
        "generated_skill_path": str(synthesized_skill_dir) if synthesized_skill_dir else None,
        "synthesized_skill_position_mode": effective_synth_position_mode if synthesized_skill_name else None,
        "injected_skill_order": injected_skill_order,
        "skill_origins": skill_origins,
        "coordinator_kind": coordinator_kind,
        "coordinator_task_local_path": coordinator_task_local_path,
        "front_packet_task_local_path": front_packet_task_local_path,
        "output_contract_targets": list(output_contract.output_paths),
        "output_contract_module_targets": list(output_contract.module_paths),
        "output_contract_formats": list(output_contract.formats),
        "verifier_sensitive_cautions": list(output_contract.verifier_cautions),
        "retrieved_skill_paths": retrieved_skill_paths,
        **generated_skill_manifest,
        **variant_manifest_payload,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build tasks-compatible task mirrors that only replace environment/skills."
    )
    parser.add_argument("--tasks-file", type=Path, required=True)
    parser.add_argument("--mirror-root", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--retrieval-mode",
        choices=[
            "topk",
            "topk_plus_synth",
            "topk_synth_only",
            "topk_context_selected",
            "topk_context_selected_plus_rescue",
            "topk_context_selected_affiliated_rescue",
            RETRIEVAL_MODE_BIPARTITE_ONLY,
            RETRIEVAL_MODE_NO_SUBUNIT,
            RETRIEVAL_MODE_TOPK_FRONT_PACKET_SELECTED_ONLY,
            RETRIEVAL_MODE_VANILLA_TOPK_FRONT_PACKET_SELECTED_ONLY,
            RETRIEVAL_MODE_LLM_TOPK_FRONT_PACKET_SELECTED_ONLY,
            "vanilla_topk",
            "vanilla_topk_plus_synth",
            "llm_topk",
        ],
        default=DEFAULT_RETRIEVAL_MODE,
    )
    parser.add_argument(
        "--synthesized-skill-position-mode",
        choices=["synth-first", "synth-last"],
        default=DEFAULT_SYNTH_POSITION_MODE,
    )
    parser.add_argument(
        "--coordinator-variant",
        choices=sorted(
            {
                "A0",
                "A0_no_coord",
                "A1",
                "A2",
                "A2_topk_guard",
                "A3",
                "A3_contract_probe",
                "A3_exec_fix",
                "A3b",
                "A4",
                "A5",
                "A6",
                "A6_guard",
                "A7",
                "A7_guard",
                "A3_guard",
                "A3_affiliate_refine",
                "A3_affiliate_refine_v2",
                "A3_refine_compact",
                "A3_refine_compact_state_consistent",
                "A7_affiliate_refine",
            }
        ),
        default=DEFAULT_COORDINATOR_VARIANT,
    )
    parser.add_argument("--front-packet-budget", type=int, default=DEFAULT_FRONT_PACKET_BUDGET)
    parser.add_argument("--post-retrieval-rerank-enabled", action="store_true")
    parser.add_argument("--post-retrieval-rerank-top-m", type=int, default=DEFAULT_POST_RERANK_TOP_M)
    parser.add_argument("--post-retrieval-rerank-model", default=None)
    parser.add_argument("--post-retrieval-rerank-timeout", type=float, default=DEFAULT_POST_RERANK_TIMEOUT_SECONDS)
    parser.add_argument("--post-retrieval-rerank-max-keep", type=int, default=None)
    args = parser.parse_args()

    if (
        args.coordinator_variant != DEFAULT_COORDINATOR_VARIANT
        and args.retrieval_mode not in COORDINATOR_RETRIEVAL_MODES
        and not (args.coordinator_variant in A2_TOPK_GUARD_VARIANTS and args.retrieval_mode == "topk")
    ):
        raise SystemExit(
            "--coordinator-variant A1-A7 and A3_contract_probe requires "
            f"--retrieval-mode one of {sorted(COORDINATOR_RETRIEVAL_MODES)}"
        )

    tasks = load_tasks(args.tasks_file)
    if not tasks:
        raise SystemExit("No tasks found in tasks file.")

    args.mirror_root.mkdir(parents=True, exist_ok=True)
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)

    if args.retrieval_mode in VANILLA_TOPK_RETRIEVAL_MODES:
        retriever = VanillaTopKRetriever()
    elif args.retrieval_mode in LLM_TOPK_RETRIEVAL_MODES:
        retriever = LlmTopKRetriever()
    else:
        try:
            from retrieval import SkillRetriever
        except Exception as exc:
            raise SystemExit(f"Failed to import SkillRetriever: {type(exc).__name__}: {exc}") from exc
        retriever = SkillRetriever()
    manifest_rows: list[dict] = []

    for task_id in tasks:
        task_dir = TASKS_DIR / task_id
        instruction_path = task_dir / "instruction.md"
        if not instruction_path.exists():
            log(f"SKIP {task_id}: missing instruction.md")
            continue

        instruction_text = instruction_path.read_text(encoding="utf-8")
        instr_hash = get_instruction_hash(instruction_text)
        cache_hit = False
        retrieval_metadata: dict[str, object] = {}
        skills, cached_metadata = (None, None) if args.no_cache else load_cached_retrieval(instr_hash, args.k, args.retrieval_mode)
        if skills is not None:
            cache_hit = True
            retrieval_metadata = cached_metadata or {}
            retrieval_metadata = apply_retrieval_mode_ablation(
                retrieval_mode=args.retrieval_mode,
                retrieval_metadata=retrieval_metadata,
                k=args.k,
            )
            selected_records = retrieval_metadata.get("selected_skill_records", [])
            if isinstance(selected_records, list) and selected_records:
                skills = [
                    row.get("skill_id")
                    for row in selected_records
                    if isinstance(row, dict) and isinstance(row.get("skill_id"), str)
                ]
            log(f"CACHE HIT {task_id}")
        else:
            log(f"RETRIEVE {task_id}")
            if args.retrieval_mode == "llm_topk":
                retrieved_rows, retrieval_metadata = retriever.retrieve_with_metadata(instruction_text, k=args.k)
            else:
                if hasattr(retriever, "retrieve_with_metadata"):
                    retrieved_rows, retrieval_metadata = retriever.retrieve_with_metadata(instruction_text, k=args.k)
                else:
                    retrieved_rows = retriever.retrieve(instruction_text, k=args.k)
                    retrieval_metadata = {}
            if retrieval_metadata:
                retrieval_metadata["retrieval_mode"] = args.retrieval_mode
            retrieval_metadata = apply_retrieval_mode_ablation(
                retrieval_mode=args.retrieval_mode,
                retrieval_metadata=retrieval_metadata,
                k=args.k,
            )
            selected_records = retrieval_metadata.get("selected_skill_records", [])
            if isinstance(selected_records, list) and selected_records:
                skills = [
                    row.get("skill_id")
                    for row in selected_records
                    if isinstance(row, dict) and isinstance(row.get("skill_id"), str)
                ]
            else:
                skills = [row["skill_name"] for row in retrieved_rows]
            if args.retrieval_mode != "topk_synth_only" and not skills:
                raise SystemExit(
                    f"Retriever returned zero skills for {task_id} in mode {args.retrieval_mode}; "
                    "aborting to avoid empty skill injection."
                )
            if not args.no_cache:
                save_cached_retrieval(
                    instr_hash,
                    skills,
                    args.k,
                    args.retrieval_mode,
                    retrieval_metadata=retrieval_metadata,
                )

        if args.retrieval_mode != "topk_synth_only" and not skills:
            retrieval_source = "cache" if cache_hit else "retriever"
            raise SystemExit(
                f"{retrieval_source} returned zero skills for {task_id} in mode {args.retrieval_mode}; "
                "aborting to avoid empty skill injection."
            )

        rerank_meta = apply_post_retrieval_rerank(
            task_instruction=instruction_text,
            ranked_skills=skills,
            enabled=args.post_retrieval_rerank_enabled,
            top_m=args.post_retrieval_rerank_top_m,
            model=args.post_retrieval_rerank_model,
            timeout_seconds=args.post_retrieval_rerank_timeout,
            max_keep=args.post_retrieval_rerank_max_keep,
        )
        skills_for_injection = rerank_meta["reranked_skill_order"]

        mirror_meta = materialize_task_mirror(
            task_id=task_id,
            ranked_skills=skills_for_injection,
            mirror_root=args.mirror_root,
            retrieval_mode=args.retrieval_mode,
            synth_position_mode=args.synthesized_skill_position_mode,
            instr_hash=instr_hash,
            retrieval_metadata=retrieval_metadata,
            coordinator_variant=args.coordinator_variant,
            front_packet_budget=args.front_packet_budget,
        )
        manifest_rows.append(
            {
                "task_id": task_id,
                "query_hash": instr_hash,
                "cache_hit": cache_hit,
                "retrieved_skills_ranked": skills,
                **({"retrieval_metadata": retrieval_metadata} if retrieval_metadata else {}),
                "post_rerank_retrieved_skills_ranked": skills_for_injection,
                **rerank_meta,
                **mirror_meta,
            }
        )
        log(
            f"MIRROR {task_id}: copied={len(mirror_meta['copied_skills'])} "
            f"missing={len(mirror_meta['missing_skills'])} synth={mirror_meta['synthesized_skill_name']} "
            f"path={mirror_meta['mirror_path']}"
        )
        if rerank_meta["post_rerank_enabled"]:
            log(
                f"POST_RERANK {task_id}: applied={rerank_meta['post_rerank_applied']} "
                f"failed={rerank_meta['post_rerank_failed']} keep_k={rerank_meta['keep_k']} "
                f"use_no_skill={rerank_meta['use_no_skill']}"
            )

    payload = {
        "created_at": dt.datetime.now().isoformat(),
        "repo_root": str(REPO_ROOT),
        "tasks_file": str(args.tasks_file),
        "mirror_root": str(args.mirror_root),
        "k": args.k,
        "retrieval_mode": args.retrieval_mode,
        "synthesized_skill_position_mode": args.synthesized_skill_position_mode,
        "coordinator_variant": args.coordinator_variant,
        "front_packet_budget": args.front_packet_budget,
        "post_retrieval_rerank_enabled": args.post_retrieval_rerank_enabled,
        "post_retrieval_rerank_top_m": args.post_retrieval_rerank_top_m,
        "post_retrieval_rerank_model": args.post_retrieval_rerank_model,
        "post_retrieval_rerank_timeout": args.post_retrieval_rerank_timeout,
        "post_retrieval_rerank_max_keep": args.post_retrieval_rerank_max_keep,
        "tasks": [row["task_id"] for row in manifest_rows],
        "rows": manifest_rows,
    }
    args.manifest_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"WROTE manifest to {args.manifest_out}")


if __name__ == "__main__":
    main()
