from __future__ import annotations

import json
import os
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import httpx
from openai import OpenAI


REPO_ROOT = Path(__file__).resolve().parents[2]
GLOBAL_SKILL_POOL = REPO_ROOT / "global_skill_pool"
DEFAULT_TOP_M = 8
DEFAULT_MODEL = "gpt-5.2-mini"
DEFAULT_TIMEOUT_SECONDS = 20.0
CODEX_CONFIG_PATH = REPO_ROOT / ".codex" / "config.toml"
CODEX_THIRDPARTY_CONFIG_PATH = REPO_ROOT / ".codex" / "config.thirdparty.toml"
LLM_TOPK_SELECTOR_VERSION = "llm_topk_v1"


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


@dataclass
class SkillCandidate:
    skill_id: str
    skill_name: str
    description: str
    rank: int


@dataclass
class SkillCatalogEntry:
    skill_id: str
    skill_name: str
    description: str


def load_skill_candidates(skill_ids: list[str], top_m: int) -> list[SkillCandidate]:
    candidates: list[SkillCandidate] = []
    for rank, skill_id in enumerate(skill_ids[:top_m], start=1):
        skill_md = GLOBAL_SKILL_POOL / skill_id / "SKILL.md"
        skill_name = skill_id
        description = ""
        if skill_md.exists():
            name, desc = extract_frontmatter_fields(skill_md.read_text(encoding="utf-8"))
            skill_name = name or skill_id
            description = desc or ""
        candidates.append(
            SkillCandidate(
                skill_id=skill_id,
                skill_name=skill_name,
                description=description,
                rank=rank,
            )
        )
    return candidates


def _build_prompt(task_instruction: str, candidates: list[SkillCandidate], max_keep: int) -> str:
    skill_lines = []
    for candidate in candidates:
        desc = candidate.description.replace("\n", " ").strip()
        skill_lines.append(
            f'- skill_id="{candidate.skill_id}" rank={candidate.rank} '
            f'name="{candidate.skill_name}" description="{desc}"'
        )

    joined_skills = "\n".join(skill_lines) or "- none"
    return (
        "Task instruction:\n"
        f"{task_instruction.strip()}\n\n"
        "Candidate skills:\n"
        f"{joined_skills}\n\n"
        "Return strict JSON only. The entire response must be valid JSON.\n"
        "Return JSON with keys: keep_k, task_mode, use_no_skill, selected, dropped.\n"
        f"keep_k must be an integer between 0 and {max_keep}.\n"
        'task_mode must be one of ["single","composite","uncertain"].\n'
        "selected must be an array ordered by recommended read/use order.\n"
        'Each selected item must have: skill_id, role, utility, reason.\n'
        'role must be one of ["primary","complementary","backup","redundant","infeasible"].\n'
        "utility must be an integer 1-5.\n"
        "reason must be one short sentence.\n"
        "Only use skill_id values from the candidates above.\n"
        "This is utility reranking/filtering, not new retrieval."
    )


def _extract_content(response) -> str:
    if getattr(response, "output_text", None):
        return response.output_text

    text_parts: list[str] = []
    for output in getattr(response, "output", []) or []:
        for content in getattr(output, "content", []) or []:
            if getattr(content, "type", None) in {"output_text", "text"} and getattr(content, "text", None):
                text_parts.append(content.text)
    return "".join(text_parts)


def load_runtime_config() -> tuple[str | None, str | None, str | None]:
    candidate_paths: list[Path] = []
    env_config_path = os.environ.get("CODEX_CONFIG_PATH")
    if env_config_path:
        candidate_paths.append(Path(env_config_path).expanduser())
    candidate_paths.extend([CODEX_THIRDPARTY_CONFIG_PATH, CODEX_CONFIG_PATH])

    for config_path in candidate_paths:
        if not config_path.exists():
            continue
        cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
        provider_name = cfg.get("model_provider")
        providers = cfg.get("model_providers", {})
        provider_cfg = providers.get(provider_name, {}) if isinstance(providers, dict) else {}
        env_key = provider_cfg.get("env_key", "OPENAI_API_KEY")
        api_key = os.environ.get(env_key) or os.environ.get("OPENAI_API_KEY")
        return (
            cfg.get("model"),
            provider_cfg.get("base_url"),
            api_key,
        )

    return (
        None,
        os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or os.environ.get("OPENAI_URL_BASE"),
        os.environ.get("OPENAI_API_KEY"),
    )


def _build_topk_prompt(task_instruction: str, catalog: list[SkillCatalogEntry], top_k: int) -> str:
    catalog_lines = []
    for entry in catalog:
        desc = entry.description.replace("\n", " ").strip()
        catalog_lines.append(
            f'- skill_id="{entry.skill_id}" name="{entry.skill_name}" description="{desc}"'
        )

    joined_catalog = "\n".join(catalog_lines) or "- none"
    return (
        "Task instruction:\n"
        f"{task_instruction.strip()}\n\n"
        "Skill catalog:\n"
        f"{joined_catalog}\n\n"
        "Return strict JSON only. The entire response must be valid JSON.\n"
        'Return exactly one object with a single key: "selected".\n'
        f'"selected" must be an array with exactly {top_k} items when the catalog has at least {top_k} skills.\n'
        "Each item must have keys: skill_id, rank, reason.\n"
        f"rank must be an integer from 1 to {top_k}, preserving the ranked order.\n"
        "reason must be one short sentence.\n"
        "Only use skill_id values from the catalog above.\n"
        "Do not invent extra keys or commentary."
    )


def _normalize_topk_selection(
    payload: dict,
    catalog_ids: list[str],
    top_k: int,
) -> tuple[list[str], list[dict[str, object]]]:
    selected = payload.get("selected")
    if not isinstance(selected, list):
        raise ValueError("selected must be a list")

    seen: set[str] = set()
    normalized: list[dict[str, object]] = []
    for expected_rank, item in enumerate(selected, start=1):
        if not isinstance(item, dict):
            raise ValueError("selected items must be objects")
        skill_id = item.get("skill_id")
        if not isinstance(skill_id, str) or skill_id not in catalog_ids:
            raise ValueError(f"invalid skill_id: {skill_id!r}")
        if skill_id in seen:
            raise ValueError(f"duplicate skill_id: {skill_id}")
        rank = item.get("rank")
        if not isinstance(rank, int) or rank != expected_rank:
            raise ValueError("rank values must be consecutive and preserve order")
        seen.add(skill_id)
        normalized.append(
            {
                "skill_id": skill_id,
                "rank": rank,
                "reason": item.get("reason"),
            }
        )

    if len(normalized) != top_k:
        raise ValueError(f"expected exactly {top_k} selections, got {len(normalized)}")

    return [item["skill_id"] for item in normalized], normalized


class LlmTopKSelector:
    def __init__(
        self,
        model: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        provider_model, base_url, api_key = load_runtime_config()
        self.model = model or os.environ.get("LLM_TOPK_MODEL") or provider_model or DEFAULT_MODEL
        self.timeout_seconds = timeout_seconds
        client_kwargs = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url
        client_kwargs["http_client"] = httpx.Client(trust_env=False, timeout=timeout_seconds)
        self.client = OpenAI(**client_kwargs)

    def _request_json(self, prompt: str) -> str:
        retry_budget = int(os.environ.get("LLM_TOPK_API_RETRIES", "1"))
        last_exc: Exception | None = None

        for attempt in range(retry_budget + 1):
            try:
                response = self.client.responses.create(
                    model=self.model,
                    input=prompt,
                    timeout=self.timeout_seconds,
                    temperature=0,
                    text={"format": {"type": "json_object"}},
                )
                raw_text = _extract_content(response).strip()
                if not raw_text:
                    raise ValueError("empty selector response")
                return raw_text
            except Exception as exc:
                last_exc = exc
                exc_name = type(exc).__name__
                retryable = exc_name in {"APITimeoutError", "APIConnectionError", "RateLimitError"}
                if not retryable or attempt >= retry_budget:
                    raise
                time.sleep(min(2.0, 0.5 * (attempt + 1)))

        raise RuntimeError(f"selector request failed: {last_exc}")

    def _repair_response(self, prompt: str, raw_text: str, error_message: str) -> str:
        repair_prompt = (
            "The previous response was invalid.\n\n"
            f"Original prompt:\n{prompt}\n\n"
            f"Invalid response:\n{raw_text}\n\n"
            f"Validation error: {error_message}\n\n"
            "Return corrected strict JSON only."
        )
        return self._request_json(repair_prompt)

    def select(self, task_instruction: str, catalog: list[SkillCatalogEntry], top_k: int) -> dict[str, object]:
        effective_top_k = min(top_k, len(catalog))
        prompt = _build_topk_prompt(task_instruction, catalog, top_k=effective_top_k)
        catalog_ids = [entry.skill_id for entry in catalog]
        raw_text = self._request_json(prompt)

        repaired = False
        try:
            payload = json.loads(raw_text)
            selected_ids, normalized = _normalize_topk_selection(payload, catalog_ids, effective_top_k)
        except Exception as exc:
            repaired = True
            repaired_text = self._repair_response(prompt, raw_text, f"{type(exc).__name__}: {exc}")
            payload = json.loads(repaired_text)
            selected_ids, normalized = _normalize_topk_selection(payload, catalog_ids, effective_top_k)
            raw_text = repaired_text

        return {
            "selected_skill_ids": selected_ids,
            "selected": normalized,
            "raw_response": raw_text,
            "repair_used": repaired,
            "selector_version": LLM_TOPK_SELECTOR_VERSION,
        }


def _normalize_result(
    payload: dict,
    original_skill_ids: list[str],
    max_keep: int,
) -> tuple[list[str], dict]:
    selected = payload.get("selected")
    if not isinstance(selected, list):
        raise ValueError("selected must be a list")

    seen: set[str] = set()
    reranked: list[str] = []
    skill_notes: dict[str, dict[str, object]] = {}
    for item in selected:
        if not isinstance(item, dict):
            continue
        skill_id = item.get("skill_id")
        if not isinstance(skill_id, str) or skill_id not in original_skill_ids or skill_id in seen:
            continue
        seen.add(skill_id)
        reranked.append(skill_id)
        skill_notes[skill_id] = {
            "role": item.get("role"),
            "utility": item.get("utility"),
            "reason": item.get("reason"),
        }

    keep_k = payload.get("keep_k")
    if not isinstance(keep_k, int):
        raise ValueError("keep_k must be an integer")
    keep_k = max(0, min(keep_k, max_keep))

    use_no_skill = bool(payload.get("use_no_skill", False))
    if use_no_skill:
        reranked = []
        keep_k = 0

    final_skills = reranked[:keep_k]
    if not use_no_skill and not final_skills and original_skill_ids:
        raise ValueError("selected produced no valid skills")

    return final_skills, {
        "task_mode": payload.get("task_mode"),
        "keep_k": keep_k,
        "use_no_skill": use_no_skill,
        "selected": [item for item in selected if isinstance(item, dict)],
        "dropped": payload.get("dropped") if isinstance(payload.get("dropped"), list) else [],
        "skill_notes": skill_notes,
    }


class LlmSkillReranker:
    def __init__(
        self,
        model: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        top_m: int = DEFAULT_TOP_M,
        max_keep: int | None = None,
    ) -> None:
        provider_model, base_url, api_key = load_runtime_config()
        self.model = model or os.environ.get("POST_RETRIEVAL_RERANK_MODEL") or provider_model or DEFAULT_MODEL
        self.timeout_seconds = timeout_seconds
        self.top_m = top_m
        self.max_keep = max_keep
        client_kwargs = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url
        client_kwargs["http_client"] = httpx.Client(trust_env=False, timeout=timeout_seconds)
        self.client = OpenAI(**client_kwargs)

    def rerank(self, task_instruction: str, ranked_skills: list[str]) -> dict:
        candidate_limit = min(len(ranked_skills), self.top_m)
        candidates = load_skill_candidates(ranked_skills, candidate_limit)
        max_keep = min(len(candidates), self.max_keep or len(candidates))
        prompt = _build_prompt(task_instruction, candidates, max_keep=max_keep)
        response = self.client.responses.create(
            model=self.model,
            input=prompt,
            timeout=self.timeout_seconds,
            text={"format": {"type": "json_object"}},
        )
        raw_text = _extract_content(response).strip()
        if not raw_text:
            raise ValueError("empty rerank response")
        payload = json.loads(raw_text)
        reranked_skills, normalized = _normalize_result(payload, [c.skill_id for c in candidates], max_keep=max_keep)
        return {
            "reranked_skills": reranked_skills,
            "raw_response": raw_text,
            **normalized,
        }
