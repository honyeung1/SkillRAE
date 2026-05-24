import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from run_phase1_claude_code_benchmark import (
    evaluate_task_outcome,
    ensure_task_runtime_layout,
    load_task_instruction,
    run_claude as run_claude_cli,
    run_verification as run_claude_verification,
    write_logs as write_claude_logs,
)

# --- Configuration ---
REPO_ROOT = Path(__file__).parent.resolve()
TASKS_DIR = REPO_ROOT / "tasks"
TASKS_NO_SKILLS_DIR = REPO_ROOT / "tasks-no-skills"
TASKS_NO_SKILLS_GENERATE_DIR = REPO_ROOT / "tasks_no_skills_generate"
GLOBAL_SKILL_POOL = REPO_ROOT / "global_skill_pool"
RUNS_DIR = REPO_ROOT / "runs"
CACHE_DIR = RUNS_DIR / "retrieval_cache"
AIDER_AGENT_IMPORT_PATH = "skillsbench_private.harbor_ext.aider_agent:AiderAgent"
DEFAULT_MODEL = "openai/qwen3.5-9b-awq"
DEFAULT_OPENAI_API_BASE = "http://172.17.0.1:9100/v1"
DEFAULT_OPENAI_API_KEY = "temp-key"
CLAUDE_CODE_PROXY_MODEL = "claude-3-5-sonnet-20241022"

# Cache Versioning
CACHE_SCHEMA_VERSION = 1
RETRIEVER_FINGERPRINT = "SkillRetriever_v1_k5"
DEFAULT_K = 5

# Ensure directories exist
RUNS_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def log(step: str, message: str) -> None:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{step}] {message}")


def parse_reward_from_stdout(stdout_path: Path) -> Optional[float]:
    """Parse reward from harbor stdout summary table line like: Mean │ 1.000."""
    try:
        text = stdout_path.read_text()
        match = re.search(r"Mean\s+│\s+([0-9.]+)", text)
        if match:
            return float(match.group(1))
    except Exception:
        pass
    return None


def load_harbor_result(job_name: str) -> Dict[str, Any]:
    """
    Parse Harbor result.json for richer status information.
    Returns:
      {
        "mean": Optional[float],
        "n_errors": int,
        "exception_types": List[str],
      }
    """
    result_path = REPO_ROOT / "jobs" / job_name / "result.json"
    out: Dict[str, Any] = {"mean": None, "n_errors": 0, "exception_types": []}
    if not result_path.exists():
        return out
    try:
        with open(result_path, "r") as f:
            data = json.load(f)
        stats = data.get("stats", {})
        out["n_errors"] = int(stats.get("n_errors") or 0)
        evals = stats.get("evals") or {}
        if evals:
            first_eval = next(iter(evals.values()))
            metrics = first_eval.get("metrics") or []
            if metrics and isinstance(metrics[0], dict):
                out["mean"] = metrics[0].get("mean")
            ex = first_eval.get("exception_stats") or {}
            out["exception_types"] = sorted(ex.keys())
    except Exception:
        return out
    return out


def get_instruction_hash(instruction_text: str) -> str:
    return hashlib.sha256(instruction_text.encode("utf-8")).hexdigest()


def load_cached_skills(instr_hash: str, k: int = DEFAULT_K) -> Optional[List[str]]:
    cache_path = CACHE_DIR / f"{instr_hash}.json"
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
        if data.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
            return None
        if data.get("retriever_fingerprint") != RETRIEVER_FINGERPRINT:
            return None
        if data.get("k") != k:
            return None
        return data.get("skills")
    except Exception:
        return None


def save_cached_skills(instr_hash: str, skills: List[str], k: int = DEFAULT_K) -> None:
    cache_path = CACHE_DIR / f"{instr_hash}.json"
    data = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "retriever_fingerprint": RETRIEVER_FINGERPRINT,
        "k": k,
        "query_hash": instr_hash,
        "skills": skills,
        "updated_at": datetime.datetime.now().isoformat(),
    }
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)


def read_target_tasks(args: argparse.Namespace) -> List[str]:
    target_tasks: List[str] = []
    if args.all:
        source_dir = resolve_source_dir(args.source)
        if not source_dir.exists():
            log("ERROR", f"Source dir not found: {source_dir}")
            return []
        for item in source_dir.iterdir():
            if item.is_dir():
                target_tasks.append(item.name)
        target_tasks.sort()
    elif args.tasks:
        target_tasks = args.tasks
    elif args.tasks_file:
        with open(args.tasks_file) as f:
            target_tasks = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return target_tasks


def resolve_source_dir(source: str) -> Path:
    mapping = {
        "tasks": TASKS_DIR,
        "tasks-no-skills": TASKS_NO_SKILLS_DIR,
        "tasks_no_skills_generate": TASKS_NO_SKILLS_GENERATE_DIR,
    }
    return mapping[source]


def materialize_retrieval_task(
    task_id: str,
    skill_names: List[str],
    run_id: str,
    instr_hash: str,
    cache_hit: bool,
) -> Tuple[Path, Path]:
    """Materialize a retrieval-injected task under runs/<run_id>__<task_id>."""
    src_task_dir = TASKS_DIR / task_id
    run_dir = RUNS_DIR / f"{run_id}__{task_id}"
    if run_dir.exists():
        shutil.rmtree(run_dir)

    dest_task_dir = run_dir / "task_materialized"
    shutil.copytree(src_task_dir, dest_task_dir)

    dest_skills_dir = dest_task_dir / "environment" / "skills"
    if dest_skills_dir.exists():
        shutil.rmtree(dest_skills_dir)
    dest_skills_dir.mkdir(parents=True)

    manifest_skills: Dict[str, str] = {}
    unique_sorted_skills = sorted(list(set(skill_names)))

    for skill_name in unique_sorted_skills:
        src_skill = GLOBAL_SKILL_POOL / skill_name
        if src_skill.exists():
            shutil.copytree(src_skill, dest_skills_dir / skill_name)
            manifest_skills[skill_name] = str(src_skill)
        else:
            log("WARN", f"Skill {skill_name} not found for task {task_id}")

    task_manifest = {
        "task_id": task_id,
        "source": "retrieval",
        "query_hash": instr_hash,
        "skills": unique_sorted_skills,
        "created_at": datetime.datetime.now().isoformat(),
        "cache_hit": cache_hit,
        "skill_paths": manifest_skills,
    }
    with open(run_dir / "task_manifest.json", "w") as f:
        json.dump(task_manifest, f, indent=2)

    return run_dir, dest_task_dir


def prepare_retrieval_jobs(
    target_tasks: List[str],
    use_cache: bool,
    k: int,
    batch_id: str,
) -> List[Dict[str, Any]]:
    log("PHASE_A", "Starting centralized retrieval and materialization...")
    try:
        from retrieval import SkillRetriever

        retriever = SkillRetriever()
    except Exception as e:
        log("ERROR", f"Failed to initialize SkillRetriever: {type(e).__name__}: {e}")
        sys.exit(1)

    jobs: List[Dict[str, Any]] = []

    for task_id in target_tasks:
        src_task_dir = TASKS_DIR / task_id
        instr_path = src_task_dir / "instruction.md"
        dockerfile_path = src_task_dir / "environment" / "Dockerfile"
        if not instr_path.exists():
            log("WARN", f"Skipping {task_id}: instruction.md missing")
            continue
        if not dockerfile_path.exists():
            log("WARN", f"Skipping {task_id}: environment/Dockerfile missing")
            continue

        instruction_text = instr_path.read_text()
        instr_hash = get_instruction_hash(instruction_text)

        skills = None
        cache_hit = False
        if use_cache:
            skills = load_cached_skills(instr_hash, k=k)
            if skills:
                cache_hit = True
                log("RETRIEVAL", f"Cache hit for {task_id}")

        if skills is None:
            log("RETRIEVAL", f"Computing for {task_id}...")
            results = retriever.retrieve(instruction_text, k=k)
            skills = [r["skill_name"] for r in results]
            save_cached_skills(instr_hash, skills, k=k)

        unique_run_id = f"{batch_id}_{datetime.datetime.now().strftime('%f')}"
        run_dir, run_task_path = materialize_retrieval_task(
            task_id=task_id,
            skill_names=skills,
            run_id=unique_run_id,
            instr_hash=instr_hash,
            cache_hit=cache_hit,
        )
        jobs.append(
            {
                "task_id": task_id,
                "run_dir": run_dir,
                "task_path": run_task_path,
                "source": "retrieval",
            }
        )

    log("PHASE_A", f"Prepared {len(jobs)} retrieval jobs.")
    return jobs


def prepare_direct_jobs(
    source: str,
    target_tasks: List[str],
    batch_id: str,
    materialize: bool = False,
) -> List[Dict[str, Any]]:
    source_dir = resolve_source_dir(source)
    jobs: List[Dict[str, Any]] = []
    log("PHASE_A", f"Preparing direct jobs from {source_dir} ...")

    for task_id in target_tasks:
        task_path = source_dir / task_id
        instr_path = task_path / "instruction.md"
        dockerfile_path = task_path / "environment" / "Dockerfile"
        if not task_path.exists():
            log("WARN", f"Skipping {task_id}: task dir missing under {source}")
            continue
        if not instr_path.exists():
            log("WARN", f"Skipping {task_id}: instruction.md missing")
            continue
        if not dockerfile_path.exists():
            log("WARN", f"Skipping {task_id}: environment/Dockerfile missing")
            continue

        unique_run_id = f"{batch_id}_{datetime.datetime.now().strftime('%f')}"
        run_dir = RUNS_DIR / f"{unique_run_id}__{source}__{task_id}"
        run_dir.mkdir(parents=True, exist_ok=False)
        resolved_task_path = task_path
        if materialize:
            resolved_task_path = run_dir / "task_materialized"
            shutil.copytree(task_path, resolved_task_path)
        with open(run_dir / "task_manifest.json", "w") as f:
            json.dump(
                {
                    "task_id": task_id,
                    "source": source,
                    "task_path": str(resolved_task_path),
                    "source_task_path": str(task_path),
                    "materialized": materialize,
                    "created_at": datetime.datetime.now().isoformat(),
                },
                f,
                indent=2,
            )

        jobs.append(
            {
                "task_id": task_id,
                "run_dir": run_dir,
                "task_path": resolved_task_path,
                "source": source,
            }
        )

    log("PHASE_A", f"Prepared {len(jobs)} direct jobs.")
    return jobs


def run_claude_code_job(
    job: Dict[str, Any],
    requested_model: Optional[str],
    runtime: str,
    docker_image: str,
) -> Dict[str, Any]:
    task_id = job["task_id"]
    run_dir = Path(job["run_dir"])
    task_path = Path(job["task_path"])
    source = job["source"]
    log_dir = run_dir / "logs" / task_id
    log_dir.mkdir(parents=True, exist_ok=True)
    runtime_task_path = task_path

    log("EXECUTE", f"Starting direct Claude run [{source}] {task_id} in {task_path}")
    start_time = time.time()
    result: Dict[str, Any] = {
        "task_id": task_id,
        "source": source,
        "task_path": str(task_path),
        "run_dir": str(run_dir),
        "status": "UNKNOWN",
        "exit_code": -1,
        "reward": None,
        "n_errors": 0,
        "exception_types": [],
        "llm_calls": 0,
        "tool_calls": 0,
        "reasoning_steps": 0,
        "requested_model": requested_model,
        "effective_model": requested_model or CLAUDE_CODE_PROXY_MODEL,
        "runtime": runtime,
        "docker_image": docker_image if runtime == "docker" else None,
        "command": [
            "claude",
            "--dangerously-skip-permissions",
            "--setting-sources",
            "local",
            "--model",
            CLAUDE_CODE_PROXY_MODEL,
            "--verbose",
            "--output-format",
            "stream-json",
            "-p",
            "<instruction>",
        ],
    }
    try:
        ensure_task_runtime_layout(task_path)
        prompt = load_task_instruction(runtime_task_path) if source == "tasks" else None
        model_name = requested_model or CLAUDE_CODE_PROXY_MODEL
        (
            claude_exit_code,
            stdout,
            stderr,
            runtime_sec,
            llm_calls,
            tool_calls,
            reasoning_steps,
        ) = run_claude_cli(
            runtime_task_path,
            log_dir,
            prompt=prompt,
            model_name=model_name,
            runtime=runtime,
            docker_image=docker_image,
        )
        verification = run_claude_verification(runtime_task_path)
        write_claude_logs(log_dir, stdout, stderr, verification)
        task_success_override, execution_flags = evaluate_task_outcome(
            runtime_task_path,
            runtime,
            claude_exit_code,
            stdout,
            stderr,
            verification[0],
        )
        with open(log_dir / "execution_flags.json", "w") as f:
            json.dump(execution_flags, f, indent=2)

        result["exit_code"] = claude_exit_code
        result["claude_exit_code"] = claude_exit_code
        result["runtime"] = runtime_sec
        result["duration"] = runtime_sec
        result["llm_calls"] = llm_calls
        result["tool_calls"] = tool_calls
        result["reasoning_steps"] = reasoning_steps
        result["success"] = task_success_override
        result["reward"] = 1.0 if task_success_override else 0.0
        result["artifact_valid"] = execution_flags["artifact_valid"]
        result["verifier_passed"] = execution_flags["verifier_passed"]
        result["rc_nonzero"] = execution_flags["rc_nonzero"]
        result["rc_nonzero_overridden"] = execution_flags["rc_nonzero_overridden"]
        result["read_test_bib_detected"] = execution_flags["read_test_bib_detected"]
        result["execution_health"] = execution_flags["execution_health"]
        result["execution_flags_path"] = str(log_dir / "execution_flags.json")
        result["status"] = "SUCCESS"
    except Exception as exc:
        result["status"] = "ERROR"
        result["error"] = str(exc)
        result["duration"] = time.time() - start_time
        return result

    result["duration"] = time.time() - start_time
    return result


def run_harbor_job(
    job: Dict[str, Any],
    agent: str,
    model: Optional[str],
    agent_import_path: Optional[str],
    env_overrides: Dict[str, str],
) -> Dict[str, Any]:
    task_id = job["task_id"]
    run_dir = Path(job["run_dir"])
    task_path = Path(job["task_path"])
    source = job["source"]

    job_name = run_dir.name
    cmd = ["harbor", "run", "-p", str(task_path)]
    if agent_import_path:
        cmd.extend(["--agent-import-path", agent_import_path])
    else:
        cmd.extend(["-a", agent])
    if model:
        cmd.extend(["-m", model])
    cmd.extend(["--job-name", job_name])

    log("EXECUTE", f"Starting [{source}] {task_id} in {run_dir}")
    start_time = time.time()
    result: Dict[str, Any] = {
        "task_id": task_id,
        "source": source,
        "task_path": str(task_path),
        "run_dir": str(run_dir),
        "status": "UNKNOWN",
        "exit_code": -1,
        "reward": None,
        "n_errors": 0,
        "exception_types": [],
        "command": cmd,
    }

    stdout_path = run_dir / "harbor_stdout.log"
    stderr_path = run_dir / "harbor_stderr.log"

    try:
        env = os.environ.copy()
        env.update(env_overrides)
        with open(stdout_path, "w") as out_f, open(stderr_path, "w") as err_f:
            proc = subprocess.run(cmd, stdout=out_f, stderr=err_f, text=True, env=env)
            result["exit_code"] = proc.returncode
            result["status"] = "SUCCESS" if proc.returncode == 0 else "FAILURE"
            result["reward"] = parse_reward_from_stdout(stdout_path)
            job_stats = load_harbor_result(job_name)
            result["n_errors"] = job_stats["n_errors"]
            result["exception_types"] = job_stats["exception_types"]
            if result["reward"] is None and job_stats["mean"] is not None:
                try:
                    result["reward"] = float(job_stats["mean"])
                except Exception:
                    pass
            if proc.returncode == 0 and result["n_errors"] > 0:
                result["status"] = "AGENT_ERROR"
    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)

    result["duration"] = time.time() - start_time
    return result


def summarize_and_exit(results: List[Dict[str, Any]]) -> None:
    processed = [r for r in results if r["status"] == "SUCCESS"]
    successed = [r for r in processed if (r.get("reward") or 0) > 0]
    failed = [r for r in processed if (r.get("reward") or 0) <= 0]
    setup_errors = [r for r in results if r["status"] in {"FAILURE", "ERROR", "AGENT_ERROR"}]

    print("\n" + "=" * 60)
    print(
        f"Total: {len(results)} | Processed: {len(processed)} | Successed: {len(successed)} | Failed: {len(failed)} | SetupErrors: {len(setup_errors)}"
    )
    if setup_errors:
        print("Setup/Runtime Errors:")
        for item in setup_errors:
            ex = ",".join(item.get("exception_types") or [])
            extra = f" exceptions={ex}" if ex else ""
            print(f"- [{item['source']}] {item['task_id']} status={item['status']}{extra} -> {item['run_dir']}")
    print("=" * 60)

    if setup_errors:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified parallel batch runner for retrieval and direct task sources."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Run all tasks under selected source")
    group.add_argument("--tasks", nargs="+", help="Specific task IDs")
    group.add_argument("--tasks-file", type=Path, help="File with task IDs")

    parser.add_argument(
        "--source",
        choices=["retrieval", "tasks", "tasks-no-skills", "tasks_no_skills_generate"],
        default="retrieval",
        help=(
            "Task source mode. 'retrieval' means tasks + retrieval materialization; "
            "others run direct task folders."
        ),
    )
    parser.add_argument(
        "--mode",
        dest="source",
        choices=["retrieval", "tasks", "tasks-no-skills", "tasks_no_skills_generate"],
        help="Alias for --source.",
    )
    parser.add_argument("--agent", default="aider", help="Harbor agent name when not using --agent-import-path")
    parser.add_argument(
        "--agent-import-path",
        default=AIDER_AGENT_IMPORT_PATH,
        help="Custom Harbor agent import path. Set empty string to use --agent instead.",
    )
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="Model name passed to Harbor")
    parser.add_argument("--jobs", type=int, default=2, help="Execution concurrency")
    parser.add_argument(
        "--launch-gap-sec",
        type=float,
        default=10.0,
        help="Sleep gap between launching claude-code Harbor subprocesses when jobs > 1.",
    )
    parser.add_argument(
        "--runtime",
        choices=["host", "docker"],
        default="host",
        help="Claude runtime for direct Claude execution.",
    )
    parser.add_argument("--docker-image", default="node:18-alpine", help="Docker image for Claude docker runtime.")
    parser.add_argument("--no-cache", action="store_true", help="Disable retrieval cache (retrieval mode only)")
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="Top-k retrieved skills (retrieval mode only)")
    parser.add_argument("--dry-test", action="store_true", help="Run only the first task, a single job, and print Harbor logs")
    parser.add_argument("--openai-api-base", default=DEFAULT_OPENAI_API_BASE, help="OPENAI_API_BASE for Harbor subprocesses")
    parser.add_argument("--openai-api-key", default=DEFAULT_OPENAI_API_KEY, help="OPENAI_API_KEY for Harbor subprocesses")

    args = parser.parse_args()
    if args.agent == "claude":
        args.agent = "claude-code"
    target_tasks = read_target_tasks(args)
    if not target_tasks:
        print("No tasks found.")
        sys.exit(0)

    if args.dry_test:
        target_tasks = target_tasks[:1]
        args.jobs = 1
        log("DRY_TEST", f"Running smoke test with task: {target_tasks[0]}")

    batch_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.source == "retrieval":
        jobs = prepare_retrieval_jobs(
            target_tasks=target_tasks,
            use_cache=not args.no_cache,
            k=args.k,
            batch_id=batch_id,
        )
    else:
        jobs = prepare_direct_jobs(
            source=args.source,
            target_tasks=target_tasks,
            batch_id=batch_id,
            materialize=args.agent == "claude-code" and args.source != "tasks",
        )

    if not jobs:
        print("No valid jobs prepared (check logs for missing instructions/dockerfiles).")
        sys.exit(0)

    env_overrides = {
        "PYTHONPATH": str(REPO_ROOT),
    }
    if args.agent != "claude-code":
        env_overrides.update(
            {
                "OPENAI_API_BASE": args.openai_api_base,
                "OPENAI_API_KEY": args.openai_api_key,
            }
        )
    agent_import_path = args.agent_import_path or None

    log("PHASE_B", f"Executing jobs with {args.jobs} workers...")
    results: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = []
        for index, job in enumerate(jobs):
            futures.append(
                executor.submit(
                    run_harbor_job,
                    job,
                    args.agent,
                    args.model,
                    agent_import_path if args.agent != "claude-code" else None,
                    env_overrides,
                )
            )
            if (
                args.agent == "claude-code"
                and args.jobs > 1
                and args.launch_gap_sec > 0
                and index < len(jobs) - 1
            ):
                log(
                    "STAGGER",
                    f"Sleeping {args.launch_gap_sec:.1f}s before launching next claude-code task.",
                )
                time.sleep(args.launch_gap_sec)
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            icon = "✅" if res["status"] == "SUCCESS" else "❌"
            err_suffix = ""
            if res.get("n_errors", 0) > 0:
                ex = ",".join(res.get("exception_types") or [])
                err_suffix = f", n_errors={res['n_errors']}, exceptions={ex or 'unknown'}"
            print(
                f"{icon} [{res['source']}] {res['task_id']} "
                f"(reward={res.get('reward')}, status={res['status']}, time={res['duration']:.1f}s{err_suffix})"
            )

    if args.dry_test and results:
        dry_result = results[0]
        if args.agent == "claude-code":
            task_log_dir = Path(dry_result["run_dir"]) / "logs" / dry_result["task_id"]
            stdout_path = task_log_dir / "claude_stdout.txt"
            stderr_path = task_log_dir / "claude_stderr.txt"
            interaction_path = task_log_dir / "interaction_log.txt"
        else:
            stdout_path = Path(dry_result["run_dir"]) / "harbor_stdout.log"
            stderr_path = Path(dry_result["run_dir"]) / "harbor_stderr.log"
            interaction_path = None
        print("\n" + "=" * 60)
        print("Dry test command:")
        print(" ".join(dry_result["command"]))
        print("\nDry test stdout:")
        if stdout_path.exists():
            print(stdout_path.read_text())
        print("\nDry test stderr:")
        if stderr_path.exists():
            print(stderr_path.read_text())
        if interaction_path and interaction_path.exists():
            print("\nDry test interaction log:")
            print(interaction_path.read_text())
        print("=" * 60)
        if args.agent == "claude-code":
            if dry_result["status"] != "SUCCESS":
                sys.exit(1)
        elif dry_result["exit_code"] != 0:
            sys.exit(1)

    batch_manifest = {
        "batch_id": batch_id,
        "source": args.source,
        "agent": args.agent,
        "agent_import_path": agent_import_path,
        "model": args.model,
        "k": args.k if args.source == "retrieval" else None,
        "retriever_fingerprint": RETRIEVER_FINGERPRINT if args.source == "retrieval" else None,
        "tasks": [r["task_id"] for r in results],
        "task_paths": [r["task_path"] for r in results],
        "run_dirs": [r["run_dir"] for r in results],
        "processed_tasks": [r["task_id"] for r in results if r["status"] == "SUCCESS"],
        "successed_tasks": [r["task_id"] for r in results if r["status"] == "SUCCESS" and (r.get("reward") or 0) > 0],
        "failed_tasks": [r["task_id"] for r in results if r["status"] == "SUCCESS" and (r.get("reward") or 0) <= 0],
        "setup_error_tasks": [r["task_id"] for r in results if r["status"] in {"FAILURE", "ERROR", "AGENT_ERROR"}],
    }
    with open(RUNS_DIR / f"batch_manifest_{batch_id}__{args.source}.json", "w") as f:
        json.dump(batch_manifest, f, indent=2)

    summarize_and_exit(results)


if __name__ == "__main__":
    main()
