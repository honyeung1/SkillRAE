#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from experiments.retrieval_tasks_backend.coordinator_ablation import (
    classify_failure_bucket,
    compute_contract_probe_state,
    compute_target_output_created,
    has_timeout_or_exception,
    load_output_files_created,
    load_variant_manifest,
    trace_metrics_from_artifacts,
)

TOOL_PROBE_RE = re.compile(r"EG_TOOL_PROBE_DONE|runtime/tool availability|tool availability", re.IGNORECASE)
SCAFFOLD_RE = re.compile(r"EG_SCAFFOLD_CREATED|minimal .*scaffold|output scaffold", re.IGNORECASE)
STAGED_COMMIT_RE = re.compile(r"EG_STAGE_COMMIT|checkpoint|intermediate progress", re.IGNORECASE)
SELF_CHECK_PASS_RE = re.compile(r"EG_SELF_CHECK\s*=\s*pass|self-check.*(pass|ok)", re.IGNORECASE)
SELF_CHECK_FAIL_RE = re.compile(r"EG_SELF_CHECK\s*=\s*fail|self-check.*fail", re.IGNORECASE)
TOOL_COVERAGE_FULL_RE = re.compile(r"EG_TOOL_COVERAGE\s*=\s*full", re.IGNORECASE)
TOOL_COVERAGE_PARTIAL_RE = re.compile(r"EG_TOOL_COVERAGE\s*=\s*partial", re.IGNORECASE)
TOOL_COVERAGE_NONE_RE = re.compile(r"EG_TOOL_COVERAGE\s*=\s*none", re.IGNORECASE)
AFFILIATE_REFINE_PROMPT_RE = re.compile(r"Action card|what to do|when useful|concrete hint|Action:|Hint:", re.IGNORECASE)
AFFILIATED_CUE_READ_RE = re.compile(r"AFFILIATED_CUES\.md", re.IGNORECASE)
SETUP_BUILD_NETWORK_PATTERNS = [
    ("failed to solve", re.compile(r"failed to solve", re.IGNORECASE)),
    ("tls handshake", re.compile(r"TLS handshake", re.IGNORECASE)),
    ("docker token", re.compile(r"docker token", re.IGNORECASE)),
    ("pull access", re.compile(r"pull access", re.IGNORECASE)),
    ("network create", re.compile(r"network create", re.IGNORECASE)),
    ("compose build", re.compile(r"compose build", re.IGNORECASE)),
    ("docker compose", re.compile(r"docker compose", re.IGNORECASE)),
    ("image pull", re.compile(r"image pull", re.IGNORECASE)),
]


def parse_iso8601(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def duration_seconds(started_at, finished_at):
    start_dt = parse_iso8601(started_at)
    finish_dt = parse_iso8601(finished_at)
    if not start_dt or not finish_dt:
        return None
    return round((finish_dt - start_dt).total_seconds(), 6)


def phase_duration_seconds(phase):
    if not isinstance(phase, dict):
        return None
    return duration_seconds(phase.get("started_at"), phase.get("finished_at"))


def phase_finished(phase: object) -> bool:
    return isinstance(phase, dict) and bool(phase.get("started_at")) and bool(phase.get("finished_at"))


def read_return_code(path: Path | None):
    if path is None or not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except Exception:
        return None


def read_text_limited(path: Path | None, max_bytes: int = 256_000) -> str:
    if path is None or not path.exists() or not path.is_file():
        return ""
    try:
        with path.open("rb") as fh:
            data = fh.read(max_bytes)
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def load_state_consistency_overlay(trial_path: Path | None, job_path: Path | None) -> tuple[dict[str, Any], str | None]:
    candidates: list[Path] = []
    if trial_path:
        candidates.append(trial_path / "state_consistency_overlay.json")
    if job_path:
        candidates.append(job_path / "state_consistency_overlay.json")
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload, str(path)
        except Exception:
            continue
    return {}, None


def load_contract_closure_manifest(mirror_path: Path) -> dict[str, Any]:
    path = mirror_path / "contract_closure_manifest.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_contract_linter_manifest(trial_path: Path | None) -> dict[str, Any]:
    if not trial_path:
        return {}
    candidates = [
        trial_path / "agent" / "contract_linter_manifest.json",
        trial_path / "artifacts" / "contract_linter_manifest.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            payload.setdefault("contract_linter_manifest_path", str(path))
            return payload
    return {}


def load_agentskillos_runtime_debug(trial_path: Path | None) -> dict[str, Any]:
    if not trial_path:
        return {}
    path = trial_path / "agent" / "agentskillos_codex_runtime_debug.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    payload.setdefault("agentskillos_runtime_debug_path", str(path))
    return payload


def derive_termination_reason(
    *,
    exception: str | None,
    command_1_return_code: int | None,
    setup_return_code: int | None,
    trial_exists: bool,
) -> str:
    text = (exception or "").lower()
    if "timeout" in text:
        return "runner_timeout"
    if "killed" in text:
        return "runner_killed"
    if setup_return_code is not None and setup_return_code != 0:
        return "agent_setup_nonzero_return"
    if command_1_return_code == 0:
        return "agent_command_completed"
    if command_1_return_code is not None and command_1_return_code != 0:
        return "agent_command_nonzero_return"
    if not trial_exists:
        return "missing_trial_dir"
    return "missing_command1_return_code"


def detect_setup_build_network_issue(
    *,
    task_name: str,
    job_name: str,
    trial_path: Path | None,
    job_path: Path | None,
    run_dir: Path | None,
    exception_snippet: str,
) -> tuple[bool, dict[str, Any]]:
    sources: list[tuple[str, str]] = []
    if exception_snippet:
        sources.append(("exception_snippet", exception_snippet))
    if trial_path:
        sources.append(("trial/exception.txt", read_text_limited(trial_path / "exception.txt")))
        sources.append(("trial/job.log", read_text_limited(trial_path / "job.log")))
        sources.append(("trial/trial.log", read_text_limited(trial_path / "trial.log")))
        sources.append(("trial/result.json", read_text_limited(trial_path / "result.json")))
    if job_path:
        sources.append(("job/result.json", read_text_limited(job_path / "result.json")))
    if run_dir and job_name:
        sources.append((f"run/{job_name}.log", read_text_limited(run_dir / f"{job_name}.log")))
    if run_dir:
        master_text = read_text_limited(run_dir / "master.log")
        if master_text:
            scoped_lines = []
            for line in master_text.splitlines():
                if task_name in line or job_name in line:
                    scoped_lines.append(line)
            if scoped_lines:
                sources.append(("run/master.log(task-scoped)", "\n".join(scoped_lines)))

    for source_name, text in sources:
        if not text:
            continue
        for label, pattern in SETUP_BUILD_NETWORK_PATTERNS:
            if pattern.search(text):
                return True, {"source": source_name, "match": label}

    return False, {}


def classify_failure_bucket_v2(
    *,
    row: dict[str, Any],
    trial_path: Path | None,
    job_path: Path | None,
    run_dir: Path | None,
    setup_return_code: int | None,
    command_1_return_code: int | None,
) -> tuple[str, str, dict[str, Any]]:
    setup_issue_detected, setup_issue_evidence = detect_setup_build_network_issue(
        task_name=str(row.get("task") or ""),
        job_name=str(row.get("job_name") or ""),
        trial_path=trial_path,
        job_path=job_path,
        run_dir=run_dir,
        exception_snippet=str(row.get("exception") or ""),
    )

    # A. setup/build/network issue
    if setup_issue_detected:
        return "setup_build_network_issue", "keyword_match", setup_issue_evidence

    # B. agent setup issue
    if setup_return_code is None:
        return "agent_setup_issue", "missing_setup_return_code", {"path": "agent/setup/return-code.txt"}
    if setup_return_code != 0:
        return "agent_setup_issue", "nonzero_setup_return_code", {"setup_return_code": setup_return_code}

    # E. success (placed before C/D so true positives are not downgraded)
    reward = row.get("reward")
    if isinstance(reward, (int, float)) and reward > 0:
        return "success", "reward_gt_zero", {"reward": reward}

    # If Harbor produced a result/verifier artifact, this is no longer a pure
    # "no result" case even when command-1/return-code.txt was not flushed.
    # Keep the legacy failure_bucket unchanged; failure_bucket_v2 is diagnostic.
    result_artifact_exists = bool(row.get("trial_result")) or bool(row.get("job_result"))
    result_or_verifier_reached = result_artifact_exists or bool(row.get("verifier_started"))
    if command_1_return_code is None and result_or_verifier_reached:
        return (
            "execution_or_contract_failure",
            "missing_command1_but_result_or_verifier_exists",
            {
                "trial_result": bool(row.get("trial_result")),
                "job_result": bool(row.get("job_result")),
                "verifier_started": bool(row.get("verifier_started")),
                "target_output_created": bool(row.get("target_output_created")),
                "reward": reward,
            },
        )

    # C. true agent timeout/no result
    codex_path = (trial_path / "agent/codex.txt") if trial_path else None
    codex_exists = bool(codex_path and codex_path.exists())
    codex_size = codex_path.stat().st_size if codex_exists else 0
    if command_1_return_code is None and codex_exists and codex_size > 0:
        return (
            "true_agent_timeout_or_no_result",
            "missing_command1_with_nonempty_codex",
            {"codex_path": str(codex_path), "codex_size": codex_size},
        )

    # D. command-1 completed but execution/contract failed
    verifier_failed = bool(row.get("verifier_started")) and not (isinstance(reward, (int, float)) and reward > 0)
    output_or_contract_failed = (
        not bool(row.get("target_output_created"))
        or str(row.get("contract_probe_state") or "") in {"missing", "partial"}
    )
    if command_1_return_code == 0 and (verifier_failed or output_or_contract_failed):
        return (
            "execution_or_contract_failure",
            "command1_ok_but_verifier_or_output_failed",
            {
                "verifier_started": bool(row.get("verifier_started")),
                "target_output_created": bool(row.get("target_output_created")),
                "contract_probe_state": row.get("contract_probe_state"),
                "reward": reward,
            },
        )

    # F. fallback
    return "inconclusive", "no_rule_matched", {}


def summarize_one(task: str, job_name: str, mirror_path: Path, jobs_dir: Path, agent_log_basename: str) -> dict[str, object]:
    job = jobs_dir / job_name
    run_dir = mirror_path.parent.parent if mirror_path.parent.name == "mirrors" else None
    trial_dirs = [p for p in job.iterdir() if p.is_dir() and "__" in p.name] if job.exists() else []
    trial = trial_dirs[0] if len(trial_dirs) == 1 else None
    setup_return_code_path = (trial / "agent/setup/return-code.txt") if trial else None
    command_0_return_code_path = (trial / "agent/command-0/return-code.txt") if trial else None
    command_1_return_code_path = (trial / "agent/command-1/return-code.txt") if trial else None

    reward = None
    job_mean = None
    exception = None
    trial_started_at = None
    trial_finished_at = None
    trial_duration_seconds_value = None
    environment_setup_duration_seconds = None
    agent_setup_duration_seconds = None
    agent_execution_duration_seconds = None
    verifier_duration_seconds = None
    token_source = None
    total_tokens_used = None
    token_thread_count = None
    agent_result_input_tokens = None
    agent_result_cache_tokens = None
    agent_result_output_tokens = None
    command_0_return_code = read_return_code(command_0_return_code_path)
    command_1_return_code = read_return_code(command_1_return_code_path)
    command_1_return_code_source = (
        "agent/command-1/return-code.txt" if command_1_return_code is not None else "missing"
    )
    setup_return_code = read_return_code(setup_return_code_path)
    setup_success = setup_return_code == 0
    entered_loop = bool(trial and (trial / "agent" / agent_log_basename).exists())
    agent_success = command_1_return_code == 0 if command_1_return_code is not None else None
    verifier_started = bool(trial and (trial / "verifier").exists() and any((trial / "verifier").iterdir()))
    verifier_started_source = (
        "trial/verifier_dir" if verifier_started else ("trial/verifier_absent" if trial else "missing_trial_dir")
    )
    trial_result = bool(trial and (trial / "result.json").exists())
    job_result = bool((job / "result.json").exists())
    state_consistency_overlay, state_consistency_overlay_path = load_state_consistency_overlay(trial, job)
    state_consistency_enabled_overlay = bool(state_consistency_overlay.get("state_consistency_enabled"))
    state_consistency_version_overlay = state_consistency_overlay.get("state_consistency_version")
    state_consistency_backfill_applied = False

    if trial and (trial / "exception.txt").exists():
        exception = (trial / "exception.txt").read_text(errors="replace")[:1200]

    if command_1_return_code is None:
        overlay_backfill_rc = state_consistency_overlay.get("command_1_return_code_backfill")
        if isinstance(overlay_backfill_rc, int):
            command_1_return_code = overlay_backfill_rc
            command_1_return_code_source = str(
                state_consistency_overlay.get("command_1_return_code_source") or "state_consistency_overlay"
            )
            agent_success = command_1_return_code == 0
            state_consistency_backfill_applied = True

    if not verifier_started and "verifier_started_backfill" in state_consistency_overlay:
        overlay_verifier_started = bool(state_consistency_overlay.get("verifier_started_backfill"))
        verifier_started = overlay_verifier_started
        verifier_started_source = str(
            state_consistency_overlay.get("verifier_started_source") or "state_consistency_overlay"
        )
        state_consistency_backfill_applied = True

    if trial_result:
        try:
            data = json.loads((trial / "result.json").read_text())
            reward = ((data.get("verifier_result") or {}).get("rewards") or {}).get("reward")
            trial_started_at = data.get("started_at")
            trial_finished_at = data.get("finished_at")
            trial_duration_seconds_value = duration_seconds(trial_started_at, trial_finished_at)
            environment_setup_duration_seconds = phase_duration_seconds(data.get("environment_setup"))
            agent_setup_duration_seconds = phase_duration_seconds(data.get("agent_setup"))
            agent_execution_duration_seconds = phase_duration_seconds(data.get("agent_execution"))
            verifier_duration_seconds = phase_duration_seconds(data.get("verifier"))
            if setup_return_code is None:
                setup_success = phase_finished(data.get("environment_setup")) and phase_finished(data.get("agent_setup"))
            agent_result = data.get("agent_result") or {}
            agent_result_input_tokens = agent_result.get("n_input_tokens")
            agent_result_cache_tokens = agent_result.get("n_cache_tokens")
            agent_result_output_tokens = agent_result.get("n_output_tokens")
        except Exception as exc:
            reward = f"PARSE_ERROR: {exc}"

    if job_result:
        try:
            data = json.loads((job / "result.json").read_text())
            job_mean = (data.get("stats") or {}).get("mean")
        except Exception as exc:
            job_mean = f"PARSE_ERROR: {exc}"

    if trial:
        state_paths = sorted((trial / "agent").glob("state_*.sqlite"))
        if state_paths:
            try:
                with sqlite3.connect(state_paths[-1]) as conn:
                    row = conn.execute("select coalesce(sum(tokens_used), 0), count(*) from threads").fetchone()
                if row is not None:
                    total_tokens_used = int(row[0]) if row[0] is not None else None
                    token_thread_count = int(row[1]) if row[1] is not None else None
                    token_source = "agent_state_threads"
            except Exception:
                pass

    if total_tokens_used is None:
        token_components = [
            agent_result_input_tokens,
            agent_result_cache_tokens,
            agent_result_output_tokens,
        ]
        numeric_components = [value for value in token_components if isinstance(value, (int, float))]
        if numeric_components:
            total_tokens_used = int(sum(numeric_components))
            token_source = "trial_agent_result"

    variant_manifest = load_variant_manifest(mirror_path)
    contract_closure_manifest = load_contract_closure_manifest(mirror_path)
    contract_linter_manifest = load_contract_linter_manifest(trial)
    agentskillos_debug = load_agentskillos_runtime_debug(trial)
    retrieved_skill_ids = list(variant_manifest.get("retrieved_skill_ids", []))
    trace_metrics = trace_metrics_from_artifacts(
        trial_path=trial,
        variant_manifest=variant_manifest,
        retrieved_skills_ranked=retrieved_skill_ids,
    )
    output_files_created = load_output_files_created(trial)
    codex_trace_text = ""
    if trial:
        codex_trace_path = trial / "agent" / agent_log_basename
        if codex_trace_path.exists():
            codex_trace_text = codex_trace_path.read_text(encoding="utf-8", errors="replace")
    output_contract_targets = [
        *variant_manifest.get("output_contract_targets", []),
        *variant_manifest.get("output_contract_module_targets", []),
    ]
    contract_probe_targets = list(
        variant_manifest.get("contract_probe_targets")
        if isinstance(variant_manifest.get("contract_probe_targets"), list)
        else output_contract_targets
    )
    target_output_created = compute_target_output_created(output_contract_targets, output_files_created)
    contract_probe_state = compute_contract_probe_state(contract_probe_targets, output_files_created)
    execution_guard_enabled = bool(variant_manifest.get("execution_guard_enabled")) or str(
        variant_manifest.get("variant_id") or ""
    ).endswith("_guard")
    variant_id = str(variant_manifest.get("variant_id") or "")
    affiliate_refine_enabled = bool(variant_manifest.get("affiliate_refine_enabled")) or variant_id.endswith(
        "_affiliate_refine"
    ) or variant_id.endswith("_affiliate_refine_v2") or variant_id.endswith("_refine_compact")
    affiliate_refine_version = variant_manifest.get("affiliate_refine_version")
    affiliate_refine_subunit_count = variant_manifest.get("affiliate_refine_subunit_count")
    affiliate_refine_v2_enabled_raw = variant_manifest.get("affiliate_refine_v2_enabled")
    if affiliate_refine_v2_enabled_raw is None:
        affiliate_refine_v2_enabled = variant_id.endswith("_affiliate_refine_v2")
    else:
        affiliate_refine_v2_enabled = bool(affiliate_refine_v2_enabled_raw)
    affiliate_refine_v2_card_count = variant_manifest.get("affiliate_refine_v2_card_count")
    affiliate_refine_v2_gated_reason = variant_manifest.get("affiliate_refine_v2_gated_reason")
    a3_refine_compact_enabled_raw = variant_manifest.get("A3_refine_compact_enabled")
    if a3_refine_compact_enabled_raw is None:
        a3_refine_compact_enabled = variant_id.endswith("_refine_compact")
    else:
        a3_refine_compact_enabled = bool(a3_refine_compact_enabled_raw)
    a3_refine_compact_card_count = variant_manifest.get("A3_refine_compact_card_count")
    a3_refine_compact_gated_reason = variant_manifest.get("A3_refine_compact_gated_reason")
    state_consistency_enabled_manifest = bool(variant_manifest.get("state_consistency_enabled"))
    state_consistency_version_manifest = variant_manifest.get("state_consistency_version")
    state_consistency_enabled = state_consistency_enabled_manifest or state_consistency_enabled_overlay
    state_consistency_version = state_consistency_version_overlay or state_consistency_version_manifest
    termination_reason = str(state_consistency_overlay.get("termination_reason") or "").strip()
    termination_reason_source = str(state_consistency_overlay.get("termination_reason_source") or "").strip()
    if not termination_reason:
        termination_reason = derive_termination_reason(
            exception=exception,
            command_1_return_code=command_1_return_code,
            setup_return_code=setup_return_code,
            trial_exists=bool(trial),
        )
        termination_reason_source = "derived_from_row"
    if affiliate_refine_enabled:
        if AFFILIATED_CUE_READ_RE.search(codex_trace_text):
            affiliate_refine_read_signal = "cue_file_opened"
        elif "## L0 Evidence" in codex_trace_text and AFFILIATE_REFINE_PROMPT_RE.search(codex_trace_text):
            affiliate_refine_read_signal = "prompt_section_present"
        else:
            affiliate_refine_read_signal = "not_detected"
    else:
        affiliate_refine_read_signal = "disabled"
    tool_probe_done = bool(TOOL_PROBE_RE.search(codex_trace_text))
    scaffold_created = bool(SCAFFOLD_RE.search(codex_trace_text)) or bool(target_output_created)
    staged_commit_seen = bool(STAGED_COMMIT_RE.search(codex_trace_text))
    if TOOL_COVERAGE_FULL_RE.search(codex_trace_text):
        tool_coverage_state = "full"
    elif TOOL_COVERAGE_PARTIAL_RE.search(codex_trace_text):
        tool_coverage_state = "partial"
    elif TOOL_COVERAGE_NONE_RE.search(codex_trace_text):
        tool_coverage_state = "none"
    else:
        tool_coverage_state = "unknown"
    if SELF_CHECK_PASS_RE.search(codex_trace_text):
        self_check_state = "pass"
    elif SELF_CHECK_FAIL_RE.search(codex_trace_text):
        self_check_state = "fail"
    else:
        self_check_state = "unknown"
    contract_closure_enabled = bool(contract_closure_manifest.get("contract_closure_enabled"))
    if contract_closure_enabled:
        if "Contract Closure Check" in codex_trace_text:
            contract_closure_read_signal = "prompt_section_present"
        elif "OUTPUT_CONTRACT" in codex_trace_text or "output contract" in codex_trace_text.lower():
            contract_closure_read_signal = "output_contract_mentioned"
        else:
            contract_closure_read_signal = "not_detected"
    else:
        contract_closure_read_signal = "disabled"
    verifier_failure_message = None
    if trial:
        verifier_stdout = trial / "verifier" / "test-stdout.txt"
        if verifier_stdout.exists():
            verifier_failure_message = verifier_stdout.read_text(encoding="utf-8", errors="replace")[:4000]

    row = {
        "task": task,
        "job_name": job_name,
        "mirror_path": str(mirror_path),
        "job_path": str(job),
        "trial_path": str(trial) if trial else None,
        "trial_started_at": trial_started_at,
        "trial_finished_at": trial_finished_at,
        "trial_duration_seconds": trial_duration_seconds_value,
        "environment_setup_duration_seconds": environment_setup_duration_seconds,
        "agent_setup_duration_seconds": agent_setup_duration_seconds,
        "agent_execution_duration_seconds": agent_execution_duration_seconds,
        "verifier_duration_seconds": verifier_duration_seconds,
        "token_source": token_source,
        "total_tokens_used": total_tokens_used,
        "token_thread_count": token_thread_count,
        "agent_result_input_tokens": agent_result_input_tokens,
        "agent_result_cache_tokens": agent_result_cache_tokens,
        "agent_result_output_tokens": agent_result_output_tokens,
        "setup_success": setup_success,
        "entered_loop": entered_loop,
        "command_0_return_code": command_0_return_code,
        "command_1_return_code": command_1_return_code,
        "command1_return_code": command_1_return_code,
        "command_1_return_code_source": command_1_return_code_source,
        "agent_success": agent_success,
        "verifier_started": verifier_started,
        "verifier_started_source": verifier_started_source,
        "trial_result": trial_result,
        "job_result": job_result,
        "reward": reward,
        "job_mean": job_mean,
        "exception": exception,
        "termination_reason": termination_reason,
        "termination_reason_source": termination_reason_source,
        "variant_id": variant_manifest.get("variant_id"),
        "injected_skill_list": variant_manifest.get("injected_skill_list", []),
        "coordinator_path": variant_manifest.get("coordinator_task_local_path"),
        "retrieved_skill_paths": variant_manifest.get("retrieved_skill_paths", []),
        "front_packet_path": variant_manifest.get("front_packet_task_local_path"),
        "front_packet_total_tokens": variant_manifest.get("front_packet_total_tokens"),
        "front_packet_section_tokens": variant_manifest.get("front_packet_section_tokens", {}),
        "front_packet_tokenizer": variant_manifest.get("front_packet_tokenizer"),
        "skillrouter_compilation_enabled": variant_manifest.get("skillrouter_compilation_enabled", False),
        "skillrouter_compilation_mode": variant_manifest.get("skillrouter_compilation_mode"),
        "skillrouter_compilation_version": variant_manifest.get("skillrouter_compilation_version"),
        "skillrouter_compilation_skill_count": variant_manifest.get("skillrouter_compilation_skill_count"),
        "skillrouter_compilation_packet_path": variant_manifest.get("skillrouter_compilation_packet_path"),
        "skillrouter_compact_compilation_enabled": variant_manifest.get("skillrouter_compact_compilation_enabled", False),
        "skillrouter_compact_card_count": variant_manifest.get("skillrouter_compact_card_count"),
        "skillrouter_compact_gated_reason": variant_manifest.get("skillrouter_compact_gated_reason"),
        "agentskillos_compilation_enabled": agentskillos_debug.get("agentskillos_compilation_enabled", False),
        "agentskillos_compilation_mode": agentskillos_debug.get("agentskillos_compilation_mode"),
        "agentskillos_compilation_version": agentskillos_debug.get("agentskillos_compilation_version"),
        "agentskillos_compilation_skill_count": agentskillos_debug.get("agentskillos_compilation_skill_count"),
        "agentskillos_compilation_packet_path": agentskillos_debug.get("agentskillos_compilation_packet_path"),
        "agentskillos_compilation_total_tokens": agentskillos_debug.get("agentskillos_compilation_total_tokens"),
        "agentskillos_compact_compilation_enabled": agentskillos_debug.get("agentskillos_compact_compilation_enabled", False),
        "agentskillos_compact_card_count": agentskillos_debug.get("agentskillos_compact_card_count"),
        "agentskillos_compact_gated_reason": agentskillos_debug.get("agentskillos_compact_gated_reason"),
        "agentskillos_runtime_debug_path": agentskillos_debug.get("agentskillos_runtime_debug_path"),
        "execution_guard_enabled": execution_guard_enabled,
        "execution_guard_version": variant_manifest.get("execution_guard_version"),
        "state_consistency_enabled": state_consistency_enabled,
        "state_consistency_version": state_consistency_version,
        "state_consistency_backfill_applied": state_consistency_backfill_applied,
        "state_consistency_overlay_path": state_consistency_overlay_path,
        "contract_closure_enabled": contract_closure_enabled,
        "contract_closure_version": contract_closure_manifest.get("contract_closure_version"),
        "contract_closure_overlay_path": contract_closure_manifest.get("contract_closure_overlay_path"),
        "contract_closure_manifest_path": contract_closure_manifest.get("contract_closure_manifest_path"),
        "contract_closure_target_count": contract_closure_manifest.get("contract_closure_target_count"),
        "contract_closure_format_count": contract_closure_manifest.get("contract_closure_format_count"),
        "contract_closure_caution_count": contract_closure_manifest.get("contract_closure_caution_count"),
        "contract_closure_has_contract_signal": contract_closure_manifest.get("contract_closure_has_contract_signal"),
        "contract_closure_read_signal": contract_closure_read_signal,
        "contract_linter_enabled": contract_linter_manifest.get("contract_linter_enabled", False),
        "contract_linter_version": contract_linter_manifest.get("contract_linter_version"),
        "contract_linter_manifest_path": contract_linter_manifest.get("contract_linter_manifest_path"),
        "contract_linter_checked": contract_linter_manifest.get("contract_linter_checked"),
        "contract_linter_passed": contract_linter_manifest.get("contract_linter_passed"),
        "contract_linter_target_count": contract_linter_manifest.get("contract_linter_target_count"),
        "contract_linter_missing_targets": contract_linter_manifest.get("contract_linter_missing_targets"),
        "contract_linter_parse_errors": contract_linter_manifest.get("contract_linter_parse_errors"),
        "contract_linter_findings": contract_linter_manifest.get("contract_linter_findings"),
        "contract_repair_enabled": contract_linter_manifest.get("contract_linter_repair_enabled"),
        "contract_repair_applied": contract_linter_manifest.get("contract_repair_applied"),
        "contract_repair_actions": contract_linter_manifest.get("contract_repair_actions"),
        "affiliate_refine_enabled": affiliate_refine_enabled,
        "affiliate_refine_version": affiliate_refine_version,
        "affiliate_refine_subunit_count": affiliate_refine_subunit_count,
        "affiliate_refine_read_signal": affiliate_refine_read_signal,
        "affiliate_refine_v2_enabled": affiliate_refine_v2_enabled,
        "affiliate_refine_v2_card_count": affiliate_refine_v2_card_count,
        "affiliate_refine_v2_gated_reason": affiliate_refine_v2_gated_reason,
        "A3_refine_compact_enabled": a3_refine_compact_enabled,
        "A3_refine_compact_card_count": a3_refine_compact_card_count,
        "A3_refine_compact_gated_reason": a3_refine_compact_gated_reason,
        "tool_probe_done": tool_probe_done,
        "scaffold_created": scaffold_created,
        "staged_commit_seen": staged_commit_seen,
        "tool_coverage_state": tool_coverage_state,
        "self_check_state": self_check_state,
        "actual_skill_md_files_opened": trace_metrics["actual_skill_md_files_opened"],
        "non_skill_context_files_opened": trace_metrics["non_skill_context_files_opened"],
        "first_skill_opened": trace_metrics["first_skill_opened"],
        "coordinator_opened": trace_metrics["coordinator_opened"],
        "target_retrieved_skills_opened": trace_metrics["target_retrieved_skills_opened"],
        "output_contract_targets": variant_manifest.get("output_contract_targets", []),
        "output_contract_module_targets": variant_manifest.get("output_contract_module_targets", []),
        "contract_probe_targets": contract_probe_targets,
        "contract_probe_state": contract_probe_state,
        "output_files_created": output_files_created,
        "target_output_created": target_output_created,
        "target_output_created_source": "output_files_snapshot",
        "verifier_reward": reward,
        "timeout_or_exception": has_timeout_or_exception(exception) or termination_reason in {"runner_timeout", "runner_killed"},
        "verifier_failure_message": verifier_failure_message,
        "context_consumed": trace_metrics["context_consumed"],
    }
    failure_bucket_v2, failure_bucket_v2_reason, failure_bucket_v2_evidence = classify_failure_bucket_v2(
        row=row,
        trial_path=trial,
        job_path=job,
        run_dir=run_dir,
        setup_return_code=setup_return_code,
        command_1_return_code=command_1_return_code,
    )
    row["failure_bucket_v2"] = failure_bucket_v2
    row["failure_bucket_v2_reason"] = failure_bucket_v2_reason
    row["failure_bucket_v2_evidence"] = failure_bucket_v2_evidence
    row["failure_bucket"] = classify_failure_bucket(row)
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--mirror-path", type=Path, required=True)
    parser.add_argument("--jobs-dir", type=Path, required=True)
    parser.add_argument("--agent-log-basename", default="codex.txt")
    args = parser.parse_args()
    print(
        json.dumps(
            summarize_one(
                task=args.task,
                job_name=args.job_name,
                mirror_path=args.mirror_path,
                jobs_dir=args.jobs_dir,
                agent_log_basename=args.agent_log_basename,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
