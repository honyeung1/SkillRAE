#!/usr/bin/env python3

from __future__ import annotations

import ast
import csv
import importlib.util
import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "tasks"

DEFAULT_FRONT_PACKET_TOKEN_BUDGET = 384
DEFAULT_TOKENIZER_NAME = "o200k_base"
DEFAULT_CONFIRMATORY_PARALLEL = 5
DEFAULT_PILOT_PARALLEL = 10

COMMAND_RE = re.compile(r'"command":"(.*?)","aggregated_output":"(.*?)","exit_code":')
SKILL_PATH_RE = re.compile(r"/(?:root|logs/agent)/[^\"'\s]*skills(?:/\.system)?/([^/\s]+)/([^\s\"']+)")
ABSOLUTE_TARGET_RE = re.compile(r'["\'](/(?:app/workspace|root/workspace|root|workspace|output)(?:/[^"\'\\s]+)?)["\']')
LEADING_COMMENT_RE = re.compile(r"^\s*#\s*")
WHITESPACE_RE = re.compile(r"\s+")
NON_OUTPUT_ABSOLUTE_PATHS = {
    "/root",
    "/app/workspace",
    "/root/output",
    "/root/workspace",
    "/workspace",
}
KNOWN_OUTPUT_EXTENSIONS = {
    ".csv",
    ".docx",
    ".html",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".pdf",
    ".png",
    ".pptx",
    ".py",
    ".rst",
    ".txt",
    ".xls",
    ".xlsx",
    ".xml",
    ".yml",
    ".yaml",
    ".zip",
}
KNOWN_OUTPUT_PATH_PREFIXES = (
    "/app/workspace/",
    "/root/workspace/",
    "/workspace/",
    "/output/",
    "/root/",
)
SUPPORTED_ABSOLUTE_PATH_PREFIXES = (
    "/app/workspace/",
    "/app/workspace",
    "/root/workspace/",
    "/root/workspace",
    "/workspace/",
    "/workspace",
    "/output/",
    "/output",
    "/root/",
    "/root",
)
OUTPUT_CONTRACT_FAILURE_RE = re.compile(
    r"missing output file|could not import|no module named|jsondecodeerror|schema|not a valid zip|invalid zip|"
    r"format|extension|missing .*json|missing .*csv|missing .*pptx|missing .*xlsx",
    re.IGNORECASE,
)
TIMEOUT_RE = re.compile(r"timeout|wait_for|agenttimeouterror|environmentstarttimeouterror", re.IGNORECASE)
WARNING_MARKERS = (
    "preserve",
    "unchanged",
    "do not",
    "don't",
    "must not",
    "must remain",
    "within",
    "tolerance",
    "formula",
    "cache",
    "rounding",
    "inverse",
    "schema",
    "nan",
    "exact",
)
IO_HINT_MARKERS = (
    ".json",
    ".csv",
    ".xlsx",
    ".pptx",
    ".txt",
    ".md",
    "answer",
    "report",
    "result",
    "output",
    "module",
    "import",
    "write",
    "save",
)
GENERIC_HINT_MARKERS = (
    "carefully",
    "best practice",
    "double check",
    "make sure",
    "consider using",
    "generally",
)
CROSS_DOMAIN_MARKERS = (
    "reflow",
    "solder",
    "pcb",
    "firmware",
    "welding",
)
AFFINITY_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "only",
    "task",
    "file",
    "path",
}

SECTION_OUTPUT_CONTRACT = "Output Contract"
SECTION_OUTPUT_CONTRACT_PROBE = "Output Contract Probe"
SECTION_ROUTING_PACKET = "Routing Packet"
SECTION_MAIN_TASK_INSTRUCTION = "Main task instruction"
SECTION_SELECTED_SKILLS = "Selected skills"
SECTION_L0_EVIDENCE = "L0 Evidence"
SECTION_VERIFIER_CAUTIONS = "Verifier-Sensitive Cautions"
SECTION_CHECKLIST = "Execution Checklist"
SECTION_RETRIEVED_SUMMARY = "Retrieved Summary"
SECTION_EXECUTION_GUARD = "Execution Guard"
SECTION_OPTIONAL_EXECUTION_HINTS = "Optional Execution Hints"

EXECUTION_GUARD_SUFFIX = "_guard"
EXECUTION_GUARD_BASE_VARIANTS = {"A3", "A6", "A7"}
EXECUTION_GUARD_VERSION = "v1"
AFFILIATE_REFINE_SUFFIX = "_affiliate_refine"
AFFILIATE_REFINE_BASE_VARIANTS = {"A3", "A7"}
AFFILIATE_REFINE_VERSION = "v1"
AFFILIATE_REFINE_V2_SUFFIX = "_affiliate_refine_v2"
AFFILIATE_REFINE_V2_BASE_VARIANTS = {"A3"}
AFFILIATE_REFINE_V2_VERSION = "v2"
AFFILIATE_REFINE_COMPACT_SUFFIX = "_refine_compact"
AFFILIATE_REFINE_COMPACT_BASE_VARIANTS = {"A3"}
AFFILIATE_REFINE_COMPACT_VERSION = "v1"
STATE_CONSISTENCY_SUFFIX = "_state_consistent"
STATE_CONSISTENCY_BASE_VARIANTS = {"A3_refine_compact"}
STATE_CONSISTENCY_VERSION = "v1"

SECTION_UPPER_BOUNDS = {
    SECTION_MAIN_TASK_INSTRUCTION: 64,
    SECTION_SELECTED_SKILLS: 224,
    SECTION_OUTPUT_CONTRACT: 128,
    SECTION_OUTPUT_CONTRACT_PROBE: 128,
    SECTION_ROUTING_PACKET: 224,
    SECTION_L0_EVIDENCE: 160,
    SECTION_VERIFIER_CAUTIONS: 96,
    SECTION_CHECKLIST: 48,
    SECTION_RETRIEVED_SUMMARY: 224,
    SECTION_EXECUTION_GUARD: 96,
    SECTION_OPTIONAL_EXECUTION_HINTS: 128,
}


@dataclass(frozen=True)
class VariantSpec:
    variant_id: str
    synth_position_mode: str
    create_coordinator_skill: bool
    create_front_packet: bool
    emit_affiliated_sidecars: bool
    include_output_contract: bool
    include_routing_packet: bool
    include_retrieved_summary: bool
    include_execution_checklist: bool
    include_l0_mode: str
    include_verifier_cautions: bool
    use_affiliate_ordering: bool
    priority_sections: tuple[str, ...]


VARIANT_SPECS = {
    "A0": VariantSpec(
        variant_id="A0",
        synth_position_mode="synth-last",
        create_coordinator_skill=True,
        create_front_packet=False,
        emit_affiliated_sidecars=True,
        include_output_contract=False,
        include_routing_packet=False,
        include_retrieved_summary=False,
        include_execution_checklist=False,
        include_l0_mode="none",
        include_verifier_cautions=False,
        use_affiliate_ordering=False,
        priority_sections=(),
    ),
    "A0_no_coord": VariantSpec(
        variant_id="A0_no_coord",
        synth_position_mode="synth-last",
        create_coordinator_skill=False,
        create_front_packet=False,
        emit_affiliated_sidecars=True,
        include_output_contract=False,
        include_routing_packet=False,
        include_retrieved_summary=False,
        include_execution_checklist=False,
        include_l0_mode="none",
        include_verifier_cautions=False,
        use_affiliate_ordering=False,
        priority_sections=(),
    ),
    "A1": VariantSpec(
        variant_id="A1",
        synth_position_mode="synth-first",
        create_coordinator_skill=True,
        create_front_packet=False,
        emit_affiliated_sidecars=True,
        include_output_contract=False,
        include_routing_packet=False,
        include_retrieved_summary=False,
        include_execution_checklist=False,
        include_l0_mode="none",
        include_verifier_cautions=False,
        use_affiliate_ordering=False,
        priority_sections=(),
    ),
    "A2": VariantSpec(
        variant_id="A2",
        synth_position_mode="synth-last",
        create_coordinator_skill=False,
        create_front_packet=True,
        emit_affiliated_sidecars=True,
        include_output_contract=False,
        include_routing_packet=True,
        include_retrieved_summary=False,
        include_execution_checklist=True,
        include_l0_mode="none",
        include_verifier_cautions=False,
        use_affiliate_ordering=False,
        priority_sections=(SECTION_ROUTING_PACKET, SECTION_CHECKLIST),
    ),
    "A2_topk_guard": VariantSpec(
        variant_id="A2_topk_guard",
        synth_position_mode="synth-last",
        create_coordinator_skill=False,
        create_front_packet=True,
        emit_affiliated_sidecars=False,
        include_output_contract=True,
        include_routing_packet=True,
        include_retrieved_summary=False,
        include_execution_checklist=True,
        include_l0_mode="none",
        include_verifier_cautions=False,
        use_affiliate_ordering=False,
        priority_sections=(SECTION_OUTPUT_CONTRACT, SECTION_ROUTING_PACKET, SECTION_CHECKLIST),
    ),
    "A3": VariantSpec(
        variant_id="A3",
        synth_position_mode="synth-last",
        create_coordinator_skill=False,
        create_front_packet=True,
        emit_affiliated_sidecars=True,
        include_output_contract=True,
        include_routing_packet=True,
        include_retrieved_summary=False,
        include_execution_checklist=True,
        include_l0_mode="none",
        include_verifier_cautions=False,
        use_affiliate_ordering=False,
        priority_sections=(SECTION_OUTPUT_CONTRACT, SECTION_ROUTING_PACKET, SECTION_CHECKLIST),
    ),
    "A3_contract_probe": VariantSpec(
        variant_id="A3_contract_probe",
        synth_position_mode="synth-last",
        create_coordinator_skill=False,
        create_front_packet=True,
        emit_affiliated_sidecars=True,
        include_output_contract=True,
        include_routing_packet=False,
        include_retrieved_summary=False,
        include_execution_checklist=False,
        include_l0_mode="none",
        include_verifier_cautions=False,
        use_affiliate_ordering=False,
        priority_sections=(SECTION_OUTPUT_CONTRACT_PROBE,),
    ),
    "A3_exec_fix": VariantSpec(
        variant_id="A3_exec_fix",
        synth_position_mode="synth-last",
        create_coordinator_skill=False,
        create_front_packet=True,
        emit_affiliated_sidecars=True,
        include_output_contract=True,
        include_routing_packet=True,
        include_retrieved_summary=False,
        include_execution_checklist=True,
        include_l0_mode="none",
        include_verifier_cautions=False,
        use_affiliate_ordering=False,
        priority_sections=(SECTION_OUTPUT_CONTRACT, SECTION_CHECKLIST, SECTION_ROUTING_PACKET),
    ),
    "A3b": VariantSpec(
        variant_id="A3b",
        synth_position_mode="synth-last",
        create_coordinator_skill=False,
        create_front_packet=True,
        emit_affiliated_sidecars=True,
        include_output_contract=True,
        include_routing_packet=True,
        include_retrieved_summary=False,
        include_execution_checklist=True,
        include_l0_mode="none",
        include_verifier_cautions=True,
        use_affiliate_ordering=False,
        priority_sections=(
            SECTION_OUTPUT_CONTRACT,
            SECTION_ROUTING_PACKET,
            SECTION_VERIFIER_CAUTIONS,
            SECTION_CHECKLIST,
        ),
    ),
    "A4": VariantSpec(
        variant_id="A4",
        synth_position_mode="synth-last",
        create_coordinator_skill=False,
        create_front_packet=True,
        emit_affiliated_sidecars=True,
        include_output_contract=True,
        include_routing_packet=True,
        include_retrieved_summary=False,
        include_execution_checklist=True,
        include_l0_mode="tiny",
        include_verifier_cautions=True,
        use_affiliate_ordering=False,
        priority_sections=(
            SECTION_OUTPUT_CONTRACT,
            SECTION_ROUTING_PACKET,
            SECTION_L0_EVIDENCE,
            SECTION_VERIFIER_CAUTIONS,
            SECTION_CHECKLIST,
        ),
    ),
    "A5": VariantSpec(
        variant_id="A5",
        synth_position_mode="synth-last",
        create_coordinator_skill=False,
        create_front_packet=True,
        emit_affiliated_sidecars=True,
        include_output_contract=True,
        include_routing_packet=True,
        include_retrieved_summary=False,
        include_execution_checklist=True,
        include_l0_mode="full",
        include_verifier_cautions=True,
        use_affiliate_ordering=False,
        priority_sections=(
            SECTION_OUTPUT_CONTRACT,
            SECTION_ROUTING_PACKET,
            SECTION_L0_EVIDENCE,
            SECTION_VERIFIER_CAUTIONS,
            SECTION_CHECKLIST,
        ),
    ),
    "A6": VariantSpec(
        variant_id="A6",
        synth_position_mode="synth-last",
        create_coordinator_skill=False,
        create_front_packet=True,
        emit_affiliated_sidecars=False,
        include_output_contract=True,
        include_routing_packet=False,
        include_retrieved_summary=True,
        include_execution_checklist=True,
        include_l0_mode="none",
        include_verifier_cautions=False,
        use_affiliate_ordering=False,
        priority_sections=(SECTION_OUTPUT_CONTRACT, SECTION_RETRIEVED_SUMMARY, SECTION_CHECKLIST),
    ),
    "A7": VariantSpec(
        variant_id="A7",
        synth_position_mode="synth-last",
        create_coordinator_skill=False,
        create_front_packet=True,
        emit_affiliated_sidecars=True,
        include_output_contract=True,
        include_routing_packet=True,
        include_retrieved_summary=False,
        include_execution_checklist=True,
        include_l0_mode="tiny",
        include_verifier_cautions=True,
        use_affiliate_ordering=True,
        priority_sections=(
            SECTION_OUTPUT_CONTRACT,
            SECTION_ROUTING_PACKET,
            SECTION_L0_EVIDENCE,
            SECTION_VERIFIER_CAUTIONS,
            SECTION_CHECKLIST,
        ),
    ),
}


@dataclass(frozen=True)
class TaskOutputContract:
    output_paths: tuple[str, ...]
    module_paths: tuple[str, ...]
    formats: tuple[str, ...]
    sources: tuple[str, ...]
    verifier_cautions: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def normalize_text(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip()


def stable_task_sort_key(task_id: str, seed: int) -> str:
    digest = hashlib.sha256(f"{seed}:{task_id}".encode("utf-8")).hexdigest()
    return digest


def get_variant_spec(variant_id: str) -> VariantSpec:
    base_variant_id = variant_id
    execution_guard_enabled = False
    affiliate_refine_enabled = False
    affiliate_refine_v2_enabled = False
    affiliate_refine_compact_enabled = False
    state_consistency_enabled = False

    if base_variant_id in VARIANT_SPECS:
        return VARIANT_SPECS[base_variant_id]

    if base_variant_id.endswith(STATE_CONSISTENCY_SUFFIX):
        base_variant_id = base_variant_id[: -len(STATE_CONSISTENCY_SUFFIX)]
        if base_variant_id not in STATE_CONSISTENCY_BASE_VARIANTS:
            raise KeyError(f"Unknown coordinator variant: {variant_id}")
        state_consistency_enabled = True

    if base_variant_id.endswith(AFFILIATE_REFINE_COMPACT_SUFFIX):
        base_variant_id = base_variant_id[: -len(AFFILIATE_REFINE_COMPACT_SUFFIX)]
        if base_variant_id not in AFFILIATE_REFINE_COMPACT_BASE_VARIANTS:
            raise KeyError(f"Unknown coordinator variant: {variant_id}")
        affiliate_refine_enabled = True
        affiliate_refine_compact_enabled = True
    elif base_variant_id.endswith(AFFILIATE_REFINE_V2_SUFFIX):
        base_variant_id = base_variant_id[: -len(AFFILIATE_REFINE_V2_SUFFIX)]
        if base_variant_id not in AFFILIATE_REFINE_V2_BASE_VARIANTS:
            raise KeyError(f"Unknown coordinator variant: {variant_id}")
        affiliate_refine_enabled = True
        affiliate_refine_v2_enabled = True
    elif base_variant_id.endswith(AFFILIATE_REFINE_SUFFIX):
        base_variant_id = base_variant_id[: -len(AFFILIATE_REFINE_SUFFIX)]
        if base_variant_id not in AFFILIATE_REFINE_BASE_VARIANTS:
            raise KeyError(f"Unknown coordinator variant: {variant_id}")
        affiliate_refine_enabled = True

    if base_variant_id.endswith(EXECUTION_GUARD_SUFFIX):
        base_variant_id = base_variant_id[: -len(EXECUTION_GUARD_SUFFIX)]
        if base_variant_id not in EXECUTION_GUARD_BASE_VARIANTS:
            raise KeyError(f"Unknown coordinator variant: {variant_id}")
        execution_guard_enabled = True

    try:
        spec = VARIANT_SPECS[base_variant_id]
    except KeyError as exc:
        raise KeyError(f"Unknown coordinator variant: {variant_id}") from exc
    if (
        not execution_guard_enabled
        and not affiliate_refine_enabled
        and not affiliate_refine_compact_enabled
        and not state_consistency_enabled
    ):
        return spec

    priority_sections = spec.priority_sections
    include_l0_mode = spec.include_l0_mode
    if affiliate_refine_enabled:
        if SECTION_L0_EVIDENCE not in priority_sections:
            priority_sections = (*priority_sections, SECTION_L0_EVIDENCE)
        if include_l0_mode == "none":
            include_l0_mode = "tiny"
    if affiliate_refine_compact_enabled:
        include_l0_mode = "rescue+affiliate"
        priority_sections = tuple(
            section for section in priority_sections if section not in {SECTION_L0_EVIDENCE, SECTION_OPTIONAL_EXECUTION_HINTS}
        )
        priority_sections = (
            SECTION_MAIN_TASK_INSTRUCTION,
            SECTION_SELECTED_SKILLS,
            SECTION_OUTPUT_CONTRACT,
            *priority_sections,
            SECTION_CHECKLIST,
            SECTION_OPTIONAL_EXECUTION_HINTS,
        )
        seen_sections: set[str] = set()
        priority_sections = tuple(
            section for section in priority_sections if not (section in seen_sections or seen_sections.add(section))
        )
    if affiliate_refine_v2_enabled:
        priority_sections = tuple(section for section in priority_sections if section != SECTION_L0_EVIDENCE)
        if SECTION_OPTIONAL_EXECUTION_HINTS not in priority_sections:
            priority_sections = (*priority_sections, SECTION_OPTIONAL_EXECUTION_HINTS)
    if SECTION_EXECUTION_GUARD not in priority_sections:
        if execution_guard_enabled:
            priority_sections = (*priority_sections, SECTION_EXECUTION_GUARD)
    return replace(
        spec,
        variant_id=variant_id,
        include_l0_mode=include_l0_mode,
        priority_sections=priority_sections,
    )


def execution_guard_enabled_for_variant(variant_id: str) -> bool:
    return variant_id.endswith(EXECUTION_GUARD_SUFFIX) and (
        variant_id[: -len(EXECUTION_GUARD_SUFFIX)] in EXECUTION_GUARD_BASE_VARIANTS
    )


def strip_task_contract_guard(variant_spec: VariantSpec) -> VariantSpec:
    """Remove task/contract guard sections while keeping retrieval/packet content."""
    stripped_sections = {SECTION_OUTPUT_CONTRACT, SECTION_CHECKLIST}
    return replace(
        variant_spec,
        include_output_contract=False,
        include_execution_checklist=False,
        priority_sections=tuple(
            section
            for section in variant_spec.priority_sections
            if section not in stripped_sections
        ),
    )


def affiliate_refine_enabled_for_variant(variant_id: str) -> bool:
    return variant_id.endswith(AFFILIATE_REFINE_SUFFIX) and (
        variant_id[: -len(AFFILIATE_REFINE_SUFFIX)] in AFFILIATE_REFINE_BASE_VARIANTS
    )


def affiliate_refine_v2_enabled_for_variant(variant_id: str) -> bool:
    return variant_id.endswith(AFFILIATE_REFINE_V2_SUFFIX) and (
        variant_id[: -len(AFFILIATE_REFINE_V2_SUFFIX)] in AFFILIATE_REFINE_V2_BASE_VARIANTS
    )


def affiliate_refine_compact_enabled_for_variant(variant_id: str) -> bool:
    return variant_id.endswith(AFFILIATE_REFINE_COMPACT_SUFFIX) and (
        variant_id[: -len(AFFILIATE_REFINE_COMPACT_SUFFIX)] in AFFILIATE_REFINE_COMPACT_BASE_VARIANTS
    )


def state_consistency_enabled_for_variant(variant_id: str) -> bool:
    return variant_id.endswith(STATE_CONSISTENCY_SUFFIX) and (
        variant_id[: -len(STATE_CONSISTENCY_SUFFIX)] in STATE_CONSISTENCY_BASE_VARIANTS
    )


def _load_tokenizer():
    try:
        import tiktoken
    except Exception:
        return None, "missing"

    try:
        return tiktoken.encoding_for_model("gpt-5.2"), "gpt-5.2"
    except Exception:
        return tiktoken.get_encoding(DEFAULT_TOKENIZER_NAME), DEFAULT_TOKENIZER_NAME


def count_tokens(text: str) -> tuple[int, str]:
    tokenizer, tokenizer_name = _load_tokenizer()
    if tokenizer is None:
        return len(text.split()), "whitespace"
    return len(tokenizer.encode(text)), tokenizer_name


def truncate_to_tokens(text: str, max_tokens: int) -> tuple[str, int, str]:
    tokenizer, tokenizer_name = _load_tokenizer()
    if tokenizer is None:
        words = text.split()
        clipped = " ".join(words[:max_tokens]) if max_tokens > 0 else ""
        if clipped and len(words) > max_tokens:
            clipped = clipped.rstrip() + " ..."
        return clipped.strip(), len(clipped.split()) if clipped else 0, tokenizer_name

    tokens = tokenizer.encode(text)
    if len(tokens) <= max_tokens:
        return text.strip(), len(tokens), tokenizer_name
    if max_tokens <= 0:
        return "", 0, tokenizer_name
    clipped = tokenizer.decode(tokens[:max_tokens]).strip()
    if clipped and not clipped.endswith("..."):
        clipped = clipped.rstrip() + " ..."
    return clipped, len(tokenizer.encode(clipped)), tokenizer_name


def _iter_task_test_files(task_dir: Path) -> Iterable[Path]:
    tests_dir = task_dir / "tests"
    if not tests_dir.exists():
        return []
    return sorted(p for p in tests_dir.rglob("*") if p.is_file() and p.suffix in {".py", ".sh", ".txt"})


def _normalize_output_root(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    for prefix in KNOWN_OUTPUT_PATH_PREFIXES:
        normalized = normalized.replace(prefix, "")
    return normalized.lstrip("/")


def _looks_like_output_candidate(value: str, var_name: str | None = None) -> bool:
    text = value.replace("\\", "/")
    stripped = text.rstrip("/").strip()
    if not stripped:
        return False
    if stripped.lower().startswith(("http://", "https://")):
        return False
    if stripped.startswith("/"):
        return True
    if var_name:
        upper = var_name.upper()
        if any(token in upper for token in ("OUTPUT", "RESULT", "ANSWER", "REPORT", "TRACE", "FILE", "PATH", "DIR")):
            return True
    if Path(stripped).suffix:
        return Path(stripped).suffix.lower() in KNOWN_OUTPUT_EXTENSIONS
    return "output" in text.lower() or stripped == "out.txt"


def _expand_relative_output_path(relative_path: str) -> set[str]:
    rel = relative_path.lstrip("./").lstrip(".\\").replace("\\", "/").strip("/")
    if not rel:
        return set()
    expanded = {relative_path.rstrip("/"), f"/app/workspace/{rel}", f"/root/workspace/{rel}"}
    if rel != relative_path.strip("/"):
        expanded.add(relative_path.strip("/"))
    return {p.rstrip("/") for p in expanded if p}


def _normalize_output_path_candidate(raw: str, var_name: str | None = None) -> set[str]:
    text = raw.replace("\\", "/").strip()
    if not text or text.lower().startswith(("http://", "https://")):
        return set()
    if text.startswith("/") and not any(text.startswith(prefix) for prefix in SUPPORTED_ABSOLUTE_PATH_PREFIXES):
        return set()
    had_sep = text.endswith("/")
    normalized = text.rstrip("/")
    candidates: set[str] = set()
    if normalized.startswith("/"):
        if normalized in NON_OUTPUT_ABSOLUTE_PATHS:
            return set()
        candidates.add((normalized + "/") if had_sep else normalized)
        root_relative = _normalize_output_root(normalized)
        if root_relative:
            candidates.add((root_relative + "/") if had_sep else root_relative)
        return candidates

    if not _looks_like_output_candidate(normalized, var_name):
        return set()
    expanded = _expand_relative_output_path(normalized)
    if had_sep:
        return {p.rstrip("/") for p in expanded} | {f"{normalized.rstrip('/')}/"}
    return expanded


def _cartesian_product_strings(values: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    if not values:
        return []
    outputs: list[tuple[str, ...]] = [tuple()]
    for group in values:
        outputs = [(*prefix, item) for prefix in outputs for item in group]
    return outputs


def _is_os_path_join(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "os"
        and node.func.value.attr == "path"
        and node.func.attr == "join"
    )


def _resolve_path_value(node: ast.AST, env: dict[str, set[str]]) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return set(env.get(node.id, set()))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve_path_value(node.left, env)
        right = _resolve_path_value(node.right, env)
        if not left or not right:
            return set()
        return {f"{a}{b}" for a in left for b in right}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _resolve_path_value(node.left, env)
        right = _resolve_path_value(node.right, env)
        if not left or not right:
            return set()
        return {os.path.join(a, b).replace("\\", "/") for a in left for b in right}
    if isinstance(node, ast.Call):
        if _is_os_path_join(node):
            arg_values = [_resolve_path_value(arg, env) for arg in node.args]
            if any(not group for group in arg_values):
                return set()
            return {
                os.path.join(*parts).replace("\\", "/")
                for parts in _cartesian_product_strings([tuple(group) for group in arg_values])
            }
        if isinstance(node.func, ast.Name) and node.func.id in {"str", "Path"} and len(node.args) == 1:
            return _resolve_path_value(node.args[0], env)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "Path" and len(node.args) == 1:
            return _resolve_path_value(node.args[0], env)
    return set()


def _extract_output_paths_from_python_ast(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except Exception:
        return set()

    env: dict[str, set[str]] = {}
    extracted: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.target:
                targets = [node.target]

            value_paths = _resolve_path_value(node.value, env) if node.value else set()
            if not value_paths:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    env[target.id] = set(value_paths)
                    for raw in value_paths:
                        extracted.update(_normalize_output_path_candidate(raw, target.id))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call_name = ""
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            if call_name not in {"exists", "isfile", "isdir", "dirname", "join", "joinpath", "open"}:
                continue
            for arg in node.args:
                for raw in _resolve_path_value(arg, env):
                    extracted.update(_normalize_output_path_candidate(raw))

    return extracted


def _extract_output_paths(text: str) -> list[str]:
    output_paths: set[str] = set()
    for match in ABSOLUTE_TARGET_RE.finditer(text):
        path = match.group(1).rstrip("/")
        if path in NON_OUTPUT_ABSOLUTE_PATHS:
            continue
        output_paths.add(path)
    return sorted(output_paths)


def _is_std_or_external_module(module_name: str) -> bool:
    if not module_name or module_name in {"typing", "typing_extensions"}:
        return True
    try:
        spec = importlib.util.find_spec(module_name)
    except Exception:
        return False
    if spec is None or spec.origin is None:
        return False
    origin = spec.origin.replace("\\", "/").lower()
    if origin in {"built-in", "frozen"}:
        return True
    return any(token in origin for token in ("/site-packages/", "/dist-packages/", "/lib/python", "/python3.", "/python2.", "<frozen>"))


def _has_workspace_insertion(text: str) -> bool:
    return (
        '"/root/workspace"' in text
        or "'/root/workspace'" in text
        or '"/app/workspace"' in text
        or "'/app/workspace'" in text
        or '"/workspace/"' in text
        or "'/workspace/'" in text
    )


def _preferred_workspace_root(text: str) -> str:
    if '"/app/workspace"' in text or "'/app/workspace'" in text:
        return "/app/workspace"
    if '"/workspace"' in text or "'/workspace'" in text:
        return "/workspace"
    return "/root/workspace"


def _extract_missing_workspace_modules(task_dir: Path, text: str) -> list[str]:
    if not _has_workspace_insertion(text):
        return []
    try:
        tree = ast.parse(text)
    except Exception:
        return []

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".", 1)[0])

    excluded = {
        "os",
        "sys",
        "json",
        "pytest",
        "time",
        "math",
        "random",
        "typing",
        "collections",
        "pathlib",
        "re",
        "tempfile",
        "zipfile",
        "csv",
        "numpy",
        "pandas",
        "openpyxl",
    }
    targets = []
    for module in sorted(modules):
        if module in excluded:
            continue
        if _is_std_or_external_module(module):
            continue
        if (task_dir / f"{module}.py").exists():
            continue
        if (task_dir / module).exists():
            continue
        targets.append(module)
    return targets


def _extract_verifier_cautions(text: str) -> list[str]:
    cautions: list[str] = []
    for raw_line in text.splitlines():
        line = normalize_text(LEADING_COMMENT_RE.sub("", raw_line))
        if not line:
            continue
        lowered = line.lower()
        if ABSOLUTE_TARGET_RE.search(line):
            continue
        if any(marker in lowered for marker in WARNING_MARKERS):
            cautions.append(line)
    deduped: list[str] = []
    seen: set[str] = set()
    for line in cautions:
        if line not in seen:
            seen.add(line)
            deduped.append(line)
    return deduped[:12]


def extract_task_output_contract(task_dir: Path) -> TaskOutputContract:
    output_paths: set[str] = set()
    module_paths: set[str] = set()
    caution_lines: list[str] = []
    sources: set[str] = set()

    instruction_path = task_dir / "instruction.md"
    if instruction_path.exists():
        instruction_text = instruction_path.read_text(encoding="utf-8", errors="replace")
        output_paths.update(_extract_output_paths(instruction_text))
        instruction_modules = _extract_missing_workspace_modules(task_dir, instruction_text)
        if instruction_modules:
            module_root = _preferred_workspace_root(instruction_text)
            module_paths.update(f"{module_root}/{module_name}.py" for module_name in instruction_modules)
        if output_paths:
            sources.add(str(instruction_path.relative_to(task_dir)))
        if instruction_modules:
            sources.add(str(instruction_path.relative_to(task_dir)))

    for file_path in _iter_task_test_files(task_dir):
        text = file_path.read_text(encoding="utf-8", errors="replace")
        found_paths = _extract_output_paths(text)
        ast_paths = _extract_output_paths_from_python_ast(text)
        found_paths.extend(sorted(ast_paths))
        if found_paths:
            output_paths.update(found_paths)
            sources.add(str(file_path.relative_to(task_dir)))
        module_names = _extract_missing_workspace_modules(task_dir, text)
        if module_names:
            module_root = _preferred_workspace_root(text)
            module_paths.update(f"{module_root}/{module_name}.py" for module_name in module_names)
            sources.add(str(file_path.relative_to(task_dir)))
        caution_lines.extend(_extract_verifier_cautions(text))

    formats = sorted(
        {
            Path(path).suffix.lower().lstrip(".")
            for path in [*output_paths, *module_paths]
            if Path(path).suffix
        }
    )
    deduped_cautions: list[str] = []
    seen_cautions: set[str] = set()
    for line in caution_lines:
        if line not in seen_cautions:
            seen_cautions.add(line)
            deduped_cautions.append(line)

    return TaskOutputContract(
        output_paths=tuple(sorted(output_paths)),
        module_paths=tuple(sorted(module_paths)),
        formats=tuple(formats),
        sources=tuple(sorted(sources)),
        verifier_cautions=tuple(deduped_cautions[:12]),
    )


def _describe_output_contract(contract: TaskOutputContract) -> str:
    lines = ["## Output Contract", ""]
    targets = [*contract.output_paths, *contract.module_paths]
    if not targets:
        lines.append("- Produce the expected output artifacts exactly where the verifier looks for them.")
    else:
        lines.append("- Target artifacts:")
        for target in targets:
            lines.append(f"  - `{target}`")
    if contract.formats:
        lines.append(f"- Required formats: {', '.join(f'`{fmt}`' for fmt in contract.formats)}")
    return "\n".join(lines).strip()


def _describe_output_contract_probe(contract: TaskOutputContract) -> str:
    lines = ["## Output Contract Probe", ""]
    targets = [*contract.output_paths, *contract.module_paths]
    if not targets:
        lines.append("- No explicit output targets were extracted; treat as format-only probe.")
        return "\n".join(lines).strip()
    lines.append("- Contract targets:")
    for target in targets:
        lines.append(f"  - `{target}`")
    if contract.formats:
        lines.append(f"- Required formats: {', '.join(f'`{fmt}`' for fmt in contract.formats)}")
    lines.append("- Probe result should be `ok`, `partial`, or `missing` based on output file snapshots.")
    return "\n".join(lines).strip()


def _describe_routing(
    selected_skill_contexts: list[object],
    routing_notes: dict[str, dict[str, object]],
) -> str:
    lines = ["## Routing Packet", ""]
    if not selected_skill_contexts:
        lines.append("- No retrieved skills were materialized.")
        return "\n".join(lines).strip()
    lines.append("- Start with the highest-signal retrieved skill for the task, then open a second skill only if needed.")
    for idx, skill in enumerate(selected_skill_contexts[:5], start=1):
        note = routing_notes.get(skill.skill_id, {})
        route_summary = normalize_text(str(note.get("routing_hint") or skill.role_summary or skill.description or skill.skill_id))
        cue_path = note.get("cue_path")
        suffix = f" cue file: `{cue_path}`" if cue_path else ""
        lines.append(f"{idx}. `{skill.skill_id}`: {route_summary}{suffix}")
    return "\n".join(lines).strip()


def _describe_main_task_instruction(task_id: str, contract: TaskOutputContract) -> str:
    lines = ["## Main task instruction", ""]
    lines.append(f"- Solve task `{task_id}` using the selected skills and produce verifier-compatible outputs.")
    targets = [*contract.output_paths, *contract.module_paths]
    if targets:
        lines.append(f"- Prioritize these targets first: {', '.join(f'`{t}`' for t in targets[:3])}")
    return "\n".join(lines).strip()


def _describe_selected_skills(
    selected_skill_contexts: list[object],
    routing_notes: dict[str, dict[str, object]],
) -> str:
    lines = ["## Selected skills", ""]
    if not selected_skill_contexts:
        lines.append("- No retrieved skills were materialized.")
        return "\n".join(lines).strip()
    lines.append("- Start with the highest-signal selected skill, then open a second one only if needed.")
    for idx, skill in enumerate(selected_skill_contexts[:5], start=1):
        note = routing_notes.get(skill.skill_id, {})
        route_summary = normalize_text(str(note.get("routing_hint") or skill.role_summary or skill.description or skill.skill_id))
        short_summary = _clip_text_words(route_summary, 8)
        lines.append(f"{idx}. `{skill.skill_id}`: {short_summary}")
    return "\n".join(lines).strip()


def _describe_retrieved_summary(selected_skill_contexts: list[object]) -> str:
    lines = ["## Retrieved Summary", ""]
    if not selected_skill_contexts:
        lines.append("- No retrieved skills were materialized.")
        return "\n".join(lines).strip()
    lines.append("- Retrieved skills are available under `/root/.agents/skills` and `/root/.codex/skills`.")
    for idx, skill in enumerate(selected_skill_contexts[:5], start=1):
        summary = normalize_text(skill.description or skill.role_summary or skill.skill_id)
        lines.append(f"{idx}. `{skill.skill_id}`: {summary}")
    return "\n".join(lines).strip()


def _score_l0_card(
    cue_row: dict[str, object],
    subunit_degree_map: dict[str, int],
    contract: TaskOutputContract,
) -> tuple[float, float, float]:
    base_score = float(cue_row.get("affiliation_score") or 0.0)
    degree = subunit_degree_map.get(str(cue_row.get("subunit_id") or ""), 1)
    hub_penalty = math.log(max(degree, 1), 2) / 10.0
    cue_text = normalize_text(str(cue_row.get("subunit_text") or "")).lower()
    io_bonus = 0.0
    for marker in [*contract.formats, *IO_HINT_MARKERS]:
        if marker and marker.lower() in cue_text:
            io_bonus += 0.04
    verifier_bonus = 0.0
    for caution in contract.verifier_cautions:
        if not caution:
            continue
        overlap = set(cue_text.split()) & set(normalize_text(caution).lower().split())
        if overlap:
            verifier_bonus += min(0.08, 0.01 * len(overlap))
    display_score = base_score - hub_penalty + io_bonus + verifier_bonus
    return display_score, io_bonus, verifier_bonus


def compute_subunit_degree_map(repo_root: Path = REPO_ROOT) -> dict[str, int]:
    edges_path = repo_root / "edges.json"
    if not edges_path.exists():
        return {}
    try:
        edges = json.loads(edges_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    counts: Counter[str] = Counter()
    for row in edges:
        if isinstance(row, dict) and isinstance(row.get("subunit_id"), str):
            counts[row["subunit_id"]] += 1
    return dict(counts)


def select_l0_cards(
    cue_rows: list[dict[str, object]],
    contract: TaskOutputContract,
    *,
    mode: str,
    use_affiliate_ordering: bool,
    subunit_degree_map: dict[str, int] | None = None,
) -> list[dict[str, object]]:
    if mode == "none":
        return []
    degree_map = subunit_degree_map or {}
    scored_rows: list[tuple[tuple[float, float, float], dict[str, object]]] = []
    for row in cue_rows:
        score = _score_l0_card(row, degree_map, contract)
        scored_rows.append((score, row))
    if use_affiliate_ordering:
        scored_rows.sort(key=lambda item: item[0], reverse=True)
    else:
        scored_rows.sort(key=lambda item: (float(item[1].get("affiliation_score") or 0.0), str(item[1].get("subunit_id") or "")), reverse=True)
    rows = [row for _score, row in scored_rows]
    if mode == "tiny":
        return rows[:5]
    return rows


def _describe_l0_cards(card_rows: list[dict[str, object]], *, mode: str) -> str:
    lines = ["## L0 Evidence", ""]
    if not card_rows:
        lines.append("- No affiliate-selected L0 evidence was attached for this task.")
        return "\n".join(lines).strip()
    for row in card_rows:
        attached_skill_id = row.get("attached_skill_id")
        source_skill_id = row.get("source_skill_id")
        subunit_id = row.get("subunit_id")
        subunit_text = normalize_text(str(row.get("subunit_text") or ""))
        if mode == "tiny":
            lines.append(
                f"- `{attached_skill_id}` <= `{source_skill_id}` / `{subunit_id}`: {subunit_text}"
            )
        else:
            lines.append(
                f"- Attached to `{attached_skill_id}` from `{source_skill_id}` / `{subunit_id}` "
                f"(affiliation={row.get('affiliation_score')}): {subunit_text}"
            )
    return "\n".join(lines).strip()


def _clip_text_words(text: str, max_words: int) -> str:
    words = normalize_text(text).split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip() + " ..."


def _describe_affiliate_refined_l0_cards(
    *,
    card_rows: list[dict[str, object]],
    selected_skill_contexts: list[object],
    routing_notes: dict[str, dict[str, object]],
    contract: TaskOutputContract,
) -> tuple[str, int, int]:
    lines = ["## L0 Evidence", ""]
    if not card_rows:
        lines.append("- No affiliate-selected L0 evidence was attached for this task.")
        return "\n".join(lines).strip(), 0, 0

    selected_skill_ids = [skill.skill_id for skill in selected_skill_contexts[:5]]
    grouped: dict[str, list[dict[str, object]]] = {skill_id: [] for skill_id in selected_skill_ids}
    overflow: list[dict[str, object]] = []

    for row in card_rows:
        attached_skill_id = str(row.get("attached_skill_id") or "")
        source_skill_id = str(row.get("source_skill_id") or "")
        skill_id = attached_skill_id or source_skill_id
        if skill_id in grouped and len(grouped[skill_id]) < 5:
            grouped[skill_id].append(row)
        elif not attached_skill_id and source_skill_id in grouped and len(grouped[source_skill_id]) < 5:
            grouped[source_skill_id].append(row)
        elif len(overflow) < 5:
            overflow.append(row)

    lines.append("- Affiliate cues are organized under selected skills as executable action cards.")
    skill_count = 0
    subunit_count = 0

    for skill_id in selected_skill_ids:
        skill_rows = grouped.get(skill_id, [])
        if not skill_rows:
            continue
        skill_count += 1
        lines.append(f"### `{skill_id}`")
        cue_path = (routing_notes.get(skill_id, {}) or {}).get("cue_path")
        for idx, row in enumerate(skill_rows, start=1):
            subunit_count += 1
            subunit_id = str(row.get("subunit_id") or "subunit")
            subunit_text = _clip_text_words(str(row.get("subunit_text") or ""), 28)
            if not subunit_text:
                subunit_text = f"Use `{skill_id}` subunit `{subunit_id}` as implementation anchor."
            when_useful = (
                f"When `{skill_id}` is needed for a concrete implementation step tied to `{subunit_id}`."
            )
            if cue_path:
                hint = f"Open `{cue_path}` and map `{subunit_id}` to concrete command/code edits."
            else:
                hint = f"Open `/root/.agents/skills/{skill_id}/SKILL.md` and execute `{subunit_id}` directly."
            warning = normalize_text(contract.verifier_cautions[0]) if contract.verifier_cautions else "Keep output path/format exactly aligned with Output Contract."
            lines.append(f"{idx}. Action card `{subunit_id}`")
            lines.append(f"   - what to do: {subunit_text}")
            lines.append(f"   - when useful: {when_useful}")
            lines.append(f"   - concrete hint: {hint}")
            lines.append(f"   - warning: {warning}")

    if skill_count == 0 and overflow:
        lines.append("### `affiliate-fallback`")
        for idx, row in enumerate(overflow[:5], start=1):
            subunit_count += 1
            subunit_id = str(row.get("subunit_id") or "subunit")
            subunit_text = _clip_text_words(str(row.get("subunit_text") or ""), 28)
            lines.append(f"{idx}. Action card `{subunit_id}`")
            lines.append(f"   - what to do: {subunit_text}")
            lines.append("   - when useful: When no selected-skill card is directly matched.")
            lines.append("   - concrete hint: Trace the source skill and apply only verifier-relevant steps.")
            lines.append("   - warning: Keep output path/format exactly aligned with Output Contract.")

    return "\n".join(lines).strip(), skill_count, subunit_count


def _extract_affiliate_v2_hint(
    *,
    subunit_text: str,
    cue_path: str | None,
    contract: TaskOutputContract,
) -> str | None:
    lower = subunit_text.lower()
    has_concrete = any(
        marker in lower
        for marker in (
            "python",
            "bash",
            "curl",
            "command",
            "api",
            "verifier",
            "output",
            ".py",
            ".json",
            ".csv",
            "file",
            "path",
        )
    )
    if cue_path:
        return f"open `{cue_path}` and execute only verifier-relevant steps"
    if has_concrete:
        return _clip_text_words(subunit_text, 16)
    if contract.verifier_cautions:
        caution = _clip_text_words(normalize_text(contract.verifier_cautions[0]), 14)
        if caution:
            return f"verifier hint: {caution}"
    return None




def _extract_affiliate_compact_hint(
    *,
    subunit_text: str,
    cue_path: str | None,
    contract: TaskOutputContract,
) -> str | None:
    lower_text = subunit_text.lower()
    if cue_path:
        return f"file: {cue_path}"
    cmd_match = re.search(r"(?:python|bash|sh|make|pytest|node|npm)\s+[^\n]+", subunit_text, re.IGNORECASE)
    if cmd_match:
        return f"command: {_clip_text_words(cmd_match.group(0), 10)}"
    path_match = re.search(r"(?:[\w.-]+/)+[\w.-]+", subunit_text)
    if path_match:
        return f"file: {path_match.group(0)}"
    if any(token in lower_text for token in ["api", "endpoint", "curl", "http", "request", "json"]):
        return f"api: {_clip_text_words(subunit_text, 12)}"
    caution_text = normalize_text(" ".join(contract.verifier_cautions or []))
    if caution_text:
        hint = _clip_text_words(caution_text, 12)
        if hint:
            return f"verifier: {hint}"
    return None


def _compact_tokens(text: str) -> set[str]:
    return {
        tok
        for tok in re.findall(r"[a-z0-9_./-]+", normalize_text(text).lower())
        if len(tok) >= 3 and tok not in AFFINITY_STOPWORDS
    }


def _is_generic_affiliate_action(action: str) -> bool:
    action_l = normalize_text(action).lower()
    if not action_l:
        return True
    if any(marker in action_l for marker in GENERIC_HINT_MARKERS):
        return True
    return len(_compact_tokens(action_l)) < 3


def _compact_affinity_tokens(
    *,
    task_id: str,
    skill_id: str,
    skill_desc: str,
    contract: TaskOutputContract,
) -> set[str]:
    corpus = [
        task_id.replace("-", " "),
        skill_id.replace("-", " ").replace("_", " "),
        skill_desc,
        " ".join(contract.output_paths),
        " ".join(contract.module_paths),
        " ".join(contract.formats),
        " ".join(contract.verifier_cautions[:2]),
    ]
    tokens: set[str] = set()
    for chunk in corpus:
        tokens.update(_compact_tokens(chunk))
    return tokens


def _compact_card_reason(
    *,
    task_id: str,
    skill_id: str,
    skill_desc: str,
    action: str,
    hint: str | None,
    attached_skill_id: str,
    source_skill_id: str,
    selected_skill_ids: set[str],
    contract: TaskOutputContract,
) -> str:
    if skill_id not in selected_skill_ids:
        return "skipped_low_task_affinity"
    if not hint:
        return "skipped_no_concrete_hint"
    if _is_generic_affiliate_action(action):
        return "skipped_generic_cross_domain"

    combined = f"{action} {hint}"
    combined_tokens = _compact_tokens(combined)
    affinity_tokens = _compact_affinity_tokens(
        task_id=task_id,
        skill_id=skill_id,
        skill_desc=skill_desc,
        contract=contract,
    )
    overlap = combined_tokens & affinity_tokens
    if not overlap:
        if any(marker in combined.lower() for marker in CROSS_DOMAIN_MARKERS):
            return "skipped_generic_cross_domain"
        return "skipped_low_task_affinity"

    if not attached_skill_id and source_skill_id and source_skill_id not in selected_skill_ids:
        return "skipped_low_task_affinity"
    return "enabled"


def _describe_affiliate_refined_l0_cards_compact(
    *,
    task_id: str,
    card_rows: list[dict[str, object]],
    selected_skill_contexts: list[object],
    routing_notes: dict[str, dict[str, object]],
    contract: TaskOutputContract,
) -> tuple[str, int, int, str]:
    lines = ["## Optional compact affiliate cues", "", "- Optional. Use only if selected-skill path stalls."]
    if not card_rows:
        return "\n".join(lines).strip(), 0, 0, "skipped_no_concrete_hint"

    selected_skill_ids = [skill.skill_id for skill in selected_skill_contexts[:5]]
    selected_skill_id_set = set(selected_skill_ids)
    selected_skill_desc: dict[str, str] = {
        skill.skill_id: normalize_text(str(getattr(skill, "role_summary", "") or getattr(skill, "description", "") or ""))
        for skill in selected_skill_contexts[:5]
    }
    grouped: dict[str, list[dict[str, object]]] = {skill_id: [] for skill_id in selected_skill_ids}
    for row in card_rows:
        attached_skill_id = str(row.get("attached_skill_id") or "")
        source_skill_id = str(row.get("source_skill_id") or "")
        skill_id = attached_skill_id or source_skill_id
        if skill_id in grouped and not grouped[skill_id]:
            grouped[skill_id].append(row)

    card_count = 0
    skill_count = 0
    reasons_seen: list[str] = []
    card_lines: list[str] = []
    for skill_id in selected_skill_ids:
        if card_count >= 2:
            break
        skill_rows = grouped.get(skill_id, [])
        if not skill_rows:
            continue
        row = skill_rows[0]
        attached_skill_id = str(row.get("attached_skill_id") or "")
        source_skill_id = str(row.get("source_skill_id") or "")
        action = _clip_text_words(str(row.get("subunit_text") or ""), 16)
        if not action:
            reasons_seen.append("skipped_incomplete_card")
            continue
        cue_path = (routing_notes.get(skill_id, {}) or {}).get("cue_path")
        hint = _extract_affiliate_compact_hint(
            subunit_text=str(row.get("subunit_text") or ""),
            cue_path=str(cue_path) if cue_path else None,
            contract=contract,
        )
        reason = _compact_card_reason(
            task_id=task_id,
            skill_id=skill_id,
            skill_desc=selected_skill_desc.get(skill_id, ""),
            action=action,
            hint=hint,
            attached_skill_id=attached_skill_id,
            source_skill_id=source_skill_id,
            selected_skill_ids=selected_skill_id_set,
            contract=contract,
        )
        if reason != "enabled":
            reasons_seen.append(reason)
            continue
        card_lines.extend(
            [
                f"### `{skill_id}`",
                f"- Action: {action}",
                f"- Hint: {hint}",
            ]
        )
        card_count += 1
        skill_count += 1

    if card_count == 0:
        reason = "skipped_low_task_affinity"
        for candidate in ("skipped_generic_cross_domain", "skipped_no_concrete_hint", "skipped_incomplete_card"):
            if candidate in reasons_seen:
                reason = candidate
                break
        return "\n".join(lines).strip(), 0, 0, reason
    lines.append("")
    lines.extend(card_lines)
    return "\n".join(lines).strip(), skill_count, card_count, "enabled"

def _describe_affiliate_refined_l0_cards_v2(
    *,
    card_rows: list[dict[str, object]],
    selected_skill_contexts: list[object],
    routing_notes: dict[str, dict[str, object]],
    contract: TaskOutputContract,
) -> tuple[str, int, int]:
    lines = ["## Optional Execution Hints", ""]
    lines.append("- Optional. Use only when blocked after Output Contract + selected skills.")
    if not card_rows:
        return "\n".join(lines).strip(), 0, 0

    selected_skill_ids = [skill.skill_id for skill in selected_skill_contexts[:5]]
    grouped: dict[str, list[dict[str, object]]] = {skill_id: [] for skill_id in selected_skill_ids}
    for row in card_rows:
        attached_skill_id = str(row.get("attached_skill_id") or "")
        source_skill_id = str(row.get("source_skill_id") or "")
        skill_id = attached_skill_id or source_skill_id
        if skill_id in grouped and len(grouped[skill_id]) < 1:
            grouped[skill_id].append(row)

    skill_count = 0
    card_count = 0
    for skill_id in selected_skill_ids:
        if card_count >= 2:
            break
        skill_rows = grouped.get(skill_id, [])
        if not skill_rows:
            continue
        row = skill_rows[0]
        subunit_text = _clip_text_words(str(row.get("subunit_text") or ""), 14)
        if not subunit_text:
            continue
        cue_path = (routing_notes.get(skill_id, {}) or {}).get("cue_path")
        hint = _extract_affiliate_v2_hint(
            subunit_text=subunit_text,
            cue_path=str(cue_path) if cue_path else None,
            contract=contract,
        )
        if not hint:
            continue
        skill_count += 1
        card_count += 1
        lines.append(f"- `{skill_id}`: {subunit_text}")
        lines.append(f"  verifier/action hint: {hint}")

    return "\n".join(lines).strip(), skill_count, card_count


def _describe_verifier_cautions(contract: TaskOutputContract) -> str:
    lines = ["## Verifier-Sensitive Cautions", ""]
    if not contract.verifier_cautions:
        lines.append("- No verifier-sensitive cautions were extracted.")
        return "\n".join(lines).strip()
    for caution in contract.verifier_cautions[:6]:
        lines.append(f"- {normalize_text(caution)}")
    return "\n".join(lines).strip()


def _describe_checklist(variant_id: str, selected_skill_contexts: list[object], contract: TaskOutputContract) -> str:
    lines = ["## Execution Checklist", ""]
    if variant_id in {"A3", "A4", "A5", "A7"}:
        lines.append("1. Before editing, confirm the exact output targets and formats from the Output Contract section.")
        if selected_skill_contexts:
            lines.append(f"2. Open `{selected_skill_contexts[0].skill_id}` before implementing.")
            if len(selected_skill_contexts) > 1:
                lines.append(f"3. Open `{selected_skill_contexts[1].skill_id}` only if it is directly needed.")
        else:
            lines.append("2. Inspect the most relevant retrieved skill before implementing.")
        lines.append("4. Create the target artifact exactly where the verifier expects it, then self-check.")
    elif variant_id == "A3_exec_fix":
        lines.append("1. Fast path first: read the Output Contract and extract only the exact target output path(s) and format(s).")
        lines.append("2. Implement the smallest possible command path to create each target artifact in one shot.")
        lines.append("3. Avoid `python`; prefer `python3` for every script invocation.")
        lines.append("4. After the first edit attempt, check whether any target output path exists; if none, stop immediately and report the blocker.")
        lines.append("5. If output appears but is invalid, stop and report the format/path mismatch before retrying.")
        lines.append("6. If output is correct, do a quick self-check and end the attempt (no extra speculative loops).")
    elif variant_id == "A2":
        lines.append("1. Read the routing packet before deciding which retrieved skill to open first.")
        lines.append("2. Prefer opening the most task-relevant retrieved skill before making edits.")
    elif variant_id == "A2_topk_guard":
        lines.append("1. Confirm the exact output targets and formats from the Output Contract section.")
        lines.append("2. Use the Routing Packet only as raw top-k retrieval guidance; do not assume affiliate cues.")
        lines.append("3. Produce the target output artifact exactly as specified, then self-check.")
    elif variant_id == "A6":
        lines.append("1. Confirm the output contract before implementation.")
        lines.append("2. Use retrieved skills only when they directly help with execution.")
        lines.append("3. Produce the target output artifact exactly as specified.")
    else:
        if contract.output_paths or contract.module_paths:
            lines.append("1. Match the verifier's expected output targets exactly.")
    return "\n".join(lines).strip()


def _describe_execution_guard() -> str:
    lines = ["## Execution Guard", ""]
    lines.append("- Check runtime/tool availability before solving.")
    lines.append("- If target path is known, create a minimal expected output scaffold early.")
    lines.append("- For multi-tool tasks, enumerate required tool categories before solving.")
    lines.append("- Write intermediate progress checkpoints for long-running tasks.")
    lines.append("- Run a cheap schema/numeric self-check before final answer.")
    return "\n".join(lines).strip()


def build_front_packet(
    *,
    variant_spec: VariantSpec,
    task_id: str,
    selected_skill_contexts: list[object],
    routing_notes: dict[str, dict[str, object]],
    contract: TaskOutputContract,
    affiliated_cue_rows: list[dict[str, object]],
    budget_tokens: int = DEFAULT_FRONT_PACKET_TOKEN_BUDGET,
    subunit_degree_map: dict[str, int] | None = None,
    affiliate_refine_v2_enabled: bool = False,
    affiliate_refine_v2_gated_reason: str | None = None,
) -> dict[str, object]:
    candidate_sections: dict[str, str] = {}
    affiliate_refine_skill_count = 0
    affiliate_refine_subunit_count = 0
    affiliate_refine_v2_card_count = 0
    affiliate_refine_compact_card_count = 0
    affiliate_refine_compact_gated_reason = "disabled"
    if variant_spec.include_output_contract:
        if variant_spec.variant_id == "A3_contract_probe":
            candidate_sections[SECTION_OUTPUT_CONTRACT_PROBE] = _describe_output_contract_probe(contract)
        else:
            candidate_sections[SECTION_OUTPUT_CONTRACT] = _describe_output_contract(contract)
    if affiliate_refine_compact_enabled_for_variant(variant_spec.variant_id):
        candidate_sections[SECTION_MAIN_TASK_INSTRUCTION] = _describe_main_task_instruction(task_id, contract)
        candidate_sections[SECTION_SELECTED_SKILLS] = _describe_selected_skills(selected_skill_contexts, routing_notes)
    if variant_spec.include_routing_packet:
        if not affiliate_refine_compact_enabled_for_variant(variant_spec.variant_id):
            candidate_sections[SECTION_ROUTING_PACKET] = _describe_routing(selected_skill_contexts, routing_notes)
    if variant_spec.include_retrieved_summary:
        candidate_sections[SECTION_RETRIEVED_SUMMARY] = _describe_retrieved_summary(selected_skill_contexts)
    if variant_spec.include_l0_mode != "none":
        l0_cards = select_l0_cards(
            affiliated_cue_rows,
            contract,
            mode=variant_spec.include_l0_mode,
            use_affiliate_ordering=variant_spec.use_affiliate_ordering,
            subunit_degree_map=subunit_degree_map,
        )
        if affiliate_refine_compact_enabled_for_variant(variant_spec.variant_id):
            l0_text, affiliate_refine_skill_count, affiliate_refine_compact_card_count, affiliate_refine_compact_gated_reason = _describe_affiliate_refined_l0_cards_compact(
                task_id=task_id,
                card_rows=l0_cards,
                selected_skill_contexts=selected_skill_contexts,
                routing_notes=routing_notes,
                contract=contract,
            )
            affiliate_refine_subunit_count = affiliate_refine_compact_card_count
            if affiliate_refine_compact_card_count > 0:
                candidate_sections[SECTION_OPTIONAL_EXECUTION_HINTS] = l0_text
        elif affiliate_refine_v2_enabled_for_variant(variant_spec.variant_id):
            if affiliate_refine_v2_enabled:
                l0_text, affiliate_refine_skill_count, affiliate_refine_v2_card_count = _describe_affiliate_refined_l0_cards_v2(
                    card_rows=l0_cards,
                    selected_skill_contexts=selected_skill_contexts,
                    routing_notes=routing_notes,
                    contract=contract,
                )
                affiliate_refine_subunit_count = affiliate_refine_v2_card_count
                if affiliate_refine_v2_card_count > 0:
                    candidate_sections[SECTION_OPTIONAL_EXECUTION_HINTS] = l0_text
        elif affiliate_refine_enabled_for_variant(variant_spec.variant_id):
            l0_text, affiliate_refine_skill_count, affiliate_refine_subunit_count = _describe_affiliate_refined_l0_cards(
                card_rows=l0_cards,
                selected_skill_contexts=selected_skill_contexts,
                routing_notes=routing_notes,
                contract=contract,
            )
            candidate_sections[SECTION_L0_EVIDENCE] = l0_text
        else:
            candidate_sections[SECTION_L0_EVIDENCE] = _describe_l0_cards(l0_cards, mode=variant_spec.include_l0_mode)
    if variant_spec.include_verifier_cautions:
        candidate_sections[SECTION_VERIFIER_CAUTIONS] = _describe_verifier_cautions(contract)
    if variant_spec.include_execution_checklist:
        candidate_sections[SECTION_CHECKLIST] = _describe_checklist(variant_spec.variant_id, selected_skill_contexts, contract)
    if execution_guard_enabled_for_variant(variant_spec.variant_id):
        candidate_sections[SECTION_EXECUTION_GUARD] = _describe_execution_guard()

    rendered_sections: list[str] = []
    section_tokens: dict[str, int] = {}
    section_texts: dict[str, str] = {}
    remaining = budget_tokens
    tokenizer_name = "missing"

    header = f"# READ FIRST: Coordinator Packet for {task_id}\n\n"
    header_text, header_tokens, tokenizer_name = truncate_to_tokens(header, budget_tokens)
    if header_text:
        rendered_sections.append(header_text.strip())
        remaining -= header_tokens

    for section_name in variant_spec.priority_sections:
        full_text = candidate_sections.get(section_name)
        if not full_text or remaining <= 0:
            continue
        max_tokens = min(SECTION_UPPER_BOUNDS.get(section_name, remaining), remaining)
        if (
            affiliate_refine_compact_enabled_for_variant(variant_spec.variant_id)
            and section_name == SECTION_SELECTED_SKILLS
        ):
            max_tokens = min(max_tokens, 128)
        if (
            section_name == SECTION_OPTIONAL_EXECUTION_HINTS
            and affiliate_refine_compact_enabled_for_variant(variant_spec.variant_id)
        ):
            full_tokens, tokenizer_name = count_tokens(full_text)
            if full_tokens > max_tokens:
                affiliate_refine_compact_card_count = 0
                affiliate_refine_subunit_count = 0
                affiliate_refine_compact_gated_reason = "skipped_incomplete_card"
                continue
            clipped_text = full_text.strip()
            clipped_tokens = full_tokens
        else:
            clipped_text, clipped_tokens, tokenizer_name = truncate_to_tokens(full_text, max_tokens)
        if not clipped_text:
            continue
        rendered_sections.append(clipped_text)
        section_tokens[section_name] = clipped_tokens
        section_texts[section_name] = clipped_text
        remaining -= clipped_tokens

    packet_text = "\n\n".join(section for section in rendered_sections if section).strip()
    total_tokens, tokenizer_name = count_tokens(packet_text)
    return {
        "text": packet_text + "\n" if packet_text else "",
        "total_tokens": total_tokens,
        "section_tokens": section_tokens,
        "section_texts": section_texts,
        "tokenizer_name": tokenizer_name,
        "budget_tokens": budget_tokens,
        "affiliate_refine_skill_count": affiliate_refine_skill_count,
        "affiliate_refine_subunit_count": affiliate_refine_subunit_count,
        "affiliate_refine_v2_enabled": affiliate_refine_v2_enabled,
        "affiliate_refine_v2_card_count": affiliate_refine_v2_card_count,
        "affiliate_refine_v2_gated_reason": affiliate_refine_v2_gated_reason,
        "A3_refine_compact_enabled": affiliate_refine_compact_enabled_for_variant(variant_spec.variant_id),
        "A3_refine_compact_card_count": affiliate_refine_compact_card_count,
        "A3_refine_compact_gated_reason": affiliate_refine_compact_gated_reason,
    }


def _decode_json_escaped(text: str) -> str:
    return bytes(text, "utf-8").decode("unicode_escape", "ignore")


def parse_codex_trace(codex_trace_path: Path) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    if not codex_trace_path.exists():
        return [], [], []
    command_events: list[dict[str, object]] = []
    skill_reads: list[dict[str, object]] = []
    file_reads: list[dict[str, object]] = []
    command_idx = 0
    for line_no, line in enumerate(codex_trace_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        match = COMMAND_RE.search(line)
        if not match:
            continue
        command_idx += 1
        command = _decode_json_escaped(match.group(1))
        command_events.append(
            {
                "command_index": command_idx,
                "line_no": line_no,
                "command": command,
            }
        )
        for skill_match in SKILL_PATH_RE.finditer(command):
            skill_reads.append(
                {
                    "command_index": command_idx,
                    "line_no": line_no,
                    "skill": skill_match.group(1),
                    "relative_path": skill_match.group(2),
                    "is_skill_md": skill_match.group(2) == "SKILL.md",
                    "command": command,
                }
            )
        for path_match in ABSOLUTE_TARGET_RE.finditer(command):
            file_reads.append(
                {
                    "command_index": command_idx,
                    "line_no": line_no,
                    "path": path_match.group(1),
                    "command": command,
                }
            )
        for root in ("/root/READ_FIRST.md", "/root/OUTPUT_CONTRACT.json", "/root/COORDINATOR_PACKET.json"):
            if root in command:
                file_reads.append(
                    {
                        "command_index": command_idx,
                        "line_no": line_no,
                        "path": root,
                        "command": command,
                    }
                )
    return command_events, skill_reads, file_reads


def trace_metrics_from_artifacts(
    *,
    trial_path: Path | None,
    variant_manifest: dict[str, object],
    retrieved_skills_ranked: list[str],
) -> dict[str, object]:
    if trial_path is None:
        return {
            "actual_skill_md_files_opened": [],
            "non_skill_context_files_opened": [],
            "first_skill_opened": None,
            "coordinator_opened": False,
            "target_retrieved_skills_opened": [],
            "context_consumed": False,
        }

    _, skill_reads, file_reads = parse_codex_trace(trial_path / "agent" / "codex.txt")
    actual_skill_md_files_opened = [f"{read['skill']}/{read['relative_path']}" for read in skill_reads if read["is_skill_md"]]
    opened_skill_ids = [read["skill"] for read in skill_reads if read["is_skill_md"]]
    non_skill_paths = sorted({read["path"] for read in file_reads})
    first_skill_opened = opened_skill_ids[0] if opened_skill_ids else None

    coordinator_type = variant_manifest.get("coordinator_kind")
    coordinator_path = variant_manifest.get("coordinator_task_local_path")
    synthesized_skill_name = variant_manifest.get("synthesized_skill_name")
    coordinator_opened = False
    if coordinator_type == "skill_file" and synthesized_skill_name:
        coordinator_opened = synthesized_skill_name in opened_skill_ids
    elif coordinator_type == "front_packet" and isinstance(coordinator_path, str):
        coordinator_opened = any(path.endswith("/READ_FIRST.md") or path.endswith("\\READ_FIRST.md") or path == "/root/READ_FIRST.md" for path in non_skill_paths)

    target_retrieved = sorted({skill_id for skill_id in opened_skill_ids if skill_id in set(retrieved_skills_ranked)})
    return {
        "actual_skill_md_files_opened": actual_skill_md_files_opened,
        "non_skill_context_files_opened": non_skill_paths,
        "first_skill_opened": first_skill_opened,
        "coordinator_opened": coordinator_opened,
        "target_retrieved_skills_opened": target_retrieved,
        "context_consumed": bool(coordinator_opened or target_retrieved),
    }


def load_output_files_created(trial_path: Path | None) -> list[dict[str, object]]:
    if trial_path is None:
        return []
    output_path = trial_path / "agent" / "output_files.json"
    if not output_path.exists():
        return []
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    changed = payload.get("changed")
    if isinstance(changed, list):
        return [row for row in changed if isinstance(row, dict)]
    return []


def compute_target_output_created(output_contract_targets: list[str], output_files_created: list[dict[str, object]]) -> bool:
    created_paths = {str(row.get("path")).replace("\\", "/") for row in output_files_created if row.get("path")}

    def _is_dir_target(path: str) -> bool:
        normalized = path.rstrip("/").strip()
        if not normalized:
            return False
        if Path(normalized).suffix:
            return False
        return Path(normalized).suffix == "" or normalized.endswith("/")

    for target in output_contract_targets:
        target_path = target.rstrip("/").replace("\\", "/")
        if target_path in created_paths:
            return True
        if _is_dir_target(target_path):
            prefix = target_path + "/"
            if any(path == target_path or path.startswith(prefix) for path in created_paths):
                return True
        normalized_target = _normalize_output_path(target_path)
        if not normalized_target:
            continue
        for created in created_paths:
            normalized_created = _normalize_output_path(created)
            if normalized_created.startswith(f"{normalized_target}/") or normalized_created == normalized_target:
                return True
    return False


def _normalize_output_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    for prefix in KNOWN_OUTPUT_PATH_PREFIXES:
        normalized = normalized.replace(prefix, "")
    return normalized.lstrip("/")


def compute_contract_probe_state(
    output_contract_targets: list[str],
    output_files_created: list[dict[str, object]],
) -> str:
    if not output_contract_targets:
        return "na"
    if not output_files_created:
        return "missing"
    created_paths = {str(row.get("path")).replace("\\", "/") for row in output_files_created if row.get("path")}
    if not created_paths:
        return "missing"

    def _is_dir_target(path: str) -> bool:
        normalized = path.rstrip("/").strip()
        if not normalized:
            return False
        if Path(normalized).suffix:
            return False
        return Path(normalized).suffix == "" or normalized.endswith("/")

    matched = 0
    for target in output_contract_targets:
        target_path = target.rstrip("/").strip()
        if target_path in created_paths:
            matched += 1
            continue
        if _is_dir_target(target_path):
            prefix = target_path + "/"
            if any(path == target_path or path.startswith(prefix) for path in created_paths):
                matched += 1
                continue
        normalized_target = _normalize_output_path(target)
        if not normalized_target:
            continue
        if any(created.endswith(normalized_target) for created in created_paths):
            matched += 1
    if matched == len(output_contract_targets):
        return "ok"
    if matched > 0:
        return "partial"
    return "missing"


def has_timeout_or_exception(exception: object) -> bool:
    return bool(str(exception or "").strip())


def classify_failure_bucket(row: dict[str, object]) -> str | None:
    reward = row.get("reward")
    if isinstance(reward, (int, float)) and reward > 0:
        return None
    if not row.get("setup_success") and not row.get("verifier_started"):
        return "setup_environment_issue"
    if not row.get("context_consumed"):
        return "context_not_consumed"
    exception = str(row.get("exception") or "")
    if TIMEOUT_RE.search(exception):
        return "context_consumed_but_timeout"
    verifier_failure = str(row.get("verifier_failure_message") or "")
    if OUTPUT_CONTRACT_FAILURE_RE.search(verifier_failure):
        return "output_contract_or_format_issue"
    return "context_consumed_but_wrong_answer"


def load_variant_manifest(task_dir: Path) -> dict[str, object]:
    manifest_path = task_dir / "coordinator_variant_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def summarize_variant_run(run_dir: Path) -> dict[str, object]:
    run_dir = run_dir.resolve()
    mirror_manifest_path = run_dir / "mirror_manifest.json"
    summary_rows = sorted((run_dir / "rows").glob("*.json"))
    mirror_manifest = json.loads(mirror_manifest_path.read_text(encoding="utf-8"))
    mirror_rows = {row["task_id"]: row for row in mirror_manifest.get("rows", [])}
    rows: list[dict[str, object]] = []
    for row_path in summary_rows:
        row = json.loads(row_path.read_text(encoding="utf-8"))
        task = row["task"]
        mirror_row = mirror_rows.get(task, {})
        trial_path = Path(row["trial_path"]) if row.get("trial_path") else None
        variant_manifest = load_variant_manifest(Path(row["mirror_path"]))
        trace_metrics = trace_metrics_from_artifacts(
            trial_path=trial_path,
            variant_manifest=variant_manifest,
            retrieved_skills_ranked=mirror_row.get("retrieved_skills_ranked", []),
        )
        output_files_created = load_output_files_created(trial_path)
        contract_targets = [
            *variant_manifest.get("output_contract_targets", []),
            *variant_manifest.get("output_contract_module_targets", []),
        ]
        contract_probe_targets = list(
            variant_manifest.get("contract_probe_targets")
            if isinstance(variant_manifest.get("contract_probe_targets"), list)
            else contract_targets
        )
        verifier_failure_message = None
        if trial_path is not None:
            verifier_stdout = trial_path / "verifier" / "test-stdout.txt"
            if verifier_stdout.exists():
                verifier_failure_message = verifier_stdout.read_text(encoding="utf-8", errors="replace")[:4000]
        enriched = {
            **row,
            "variant_id": variant_manifest.get("variant_id"),
            "injected_skill_list": variant_manifest.get("injected_skill_list", mirror_row.get("injected_skill_order", [])),
            "coordinator_path": variant_manifest.get("coordinator_task_local_path"),
            "retrieved_skill_paths": variant_manifest.get("retrieved_skill_paths", []),
            "front_packet_path": variant_manifest.get("front_packet_task_local_path"),
            "front_packet_total_tokens": variant_manifest.get("front_packet_total_tokens"),
            "front_packet_section_tokens": variant_manifest.get("front_packet_section_tokens", {}),
            "front_packet_tokenizer": variant_manifest.get("front_packet_tokenizer"),
            "output_contract_targets": variant_manifest.get("output_contract_targets", []),
            "output_contract_module_targets": variant_manifest.get("output_contract_module_targets", []),
            "contract_probe_targets": contract_probe_targets,
            "contract_probe_state": compute_contract_probe_state(
                contract_probe_targets,
                output_files_created,
            ),
            "actual_skill_md_files_opened": trace_metrics["actual_skill_md_files_opened"],
            "non_skill_context_files_opened": trace_metrics["non_skill_context_files_opened"],
            "first_skill_opened": trace_metrics["first_skill_opened"],
            "coordinator_opened": trace_metrics["coordinator_opened"],
            "target_retrieved_skills_opened": trace_metrics["target_retrieved_skills_opened"],
            "context_consumed": trace_metrics["context_consumed"],
            "output_files_created": output_files_created,
            "target_output_created": compute_target_output_created(contract_targets, output_files_created),
            "verifier_reward": row.get("reward"),
            "timeout_or_exception": has_timeout_or_exception(row.get("exception")),
            "verifier_failure_message": verifier_failure_message,
        }
        enriched["failure_bucket"] = classify_failure_bucket(enriched)
        rows.append(enriched)

    rewards = [float(row["reward"]) for row in rows if isinstance(row.get("reward"), (int, float))]
    return {
        "run_dir": str(run_dir),
        "variant_id": rows[0].get("variant_id") if rows else None,
        "rows": rows,
        "reward_sum": round(sum(rewards), 6),
        "reward_mean": round(sum(rewards) / len(rewards), 6) if rewards else None,
        "reward_gt_0_count": sum(1 for row in rows if isinstance(row.get("reward"), (int, float)) and float(row["reward"]) > 0.0),
        "reward_eq_1_count": sum(1 for row in rows if row.get("reward") == 1.0),
        "timeout_count": sum(1 for row in rows if TIMEOUT_RE.search(str(row.get("exception") or ""))),
        "null_count": sum(1 for row in rows if row.get("reward") is None),
        "output_contract_failure_count": sum(
            1 for row in rows if row.get("failure_bucket") == "output_contract_or_format_issue"
        ),
        "skill_open_rate": round(
            sum(1 for row in rows if row.get("target_retrieved_skills_opened")) / len(rows),
            6,
        ) if rows else None,
        "coordinator_open_rate": round(
            sum(1 for row in rows if row.get("coordinator_opened")) / len(rows),
            6,
        ) if rows else None,
        "target_output_created_rate": round(
            sum(1 for row in rows if row.get("target_output_created")) / len(rows),
            6,
        ) if rows else None,
        "prompt_context_cost_mean": round(
            sum(int(row.get("front_packet_total_tokens") or 0) for row in rows) / len(rows),
            6,
        ) if rows else None,
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
