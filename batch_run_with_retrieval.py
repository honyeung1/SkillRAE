import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from string import Template

# --- Configuration ---
REPO_ROOT = Path(__file__).parent.resolve()
TASKS_DIR = REPO_ROOT / "tasks"
GLOBAL_SKILL_POOL = REPO_ROOT / "global_skill_pool"
RUNS_DIR = Path(os.environ.get("SKILLSBENCH_RUNS_ROOT", "/mnt/data/skillsbench/runs"))
CACHE_DIR = RUNS_DIR / "retrieval_cache"
AIDER_AGENT_IMPORT_PATH = "skillsbench_private.harbor_ext.aider_agent:AiderAgent"
DEFAULT_CODEX_MODEL = "gpt-5.2"

# Cache Versioning
CACHE_SCHEMA_VERSION = 1
RETRIEVER_FINGERPRINT = "SkillRetriever_v1_k5"
TASK_COPY_IGNORE_NAMES = (".claude", ".claude.json")
DEFAULT_RETRIEVAL_EXPOSURE_MODE = "off"
DEFAULT_RETRIEVAL_SOURCE_MODE = "normal"
DEFAULT_RETRIEVAL_OVERLAY_TEMPLATE = (
    REPO_ROOT
    / "experiments"
    / "retrieval_alignment"
    / "retrieval_skill_overlay.txt"
)

# Ensure directories exist
RUNS_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# --- Logging ---
def log(step: str, message: str) -> None:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{step}] {message}")

# --- Phase A: Retrieval & Materialization ---

def get_instruction_hash(instruction_text: str) -> str:
    return hashlib.sha256(instruction_text.encode("utf-8")).hexdigest()

def load_cached_skills(instr_hash: str) -> Optional[List[str]]:
    cache_path = CACHE_DIR / f"{instr_hash}.json"
    if cache_path.exists():
        try:
            with open(cache_path, "r") as f:
                data = json.load(f)
            
            # Version Check
            if data.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
                return None
            if data.get("retriever_fingerprint") != RETRIEVER_FINGERPRINT:
                return None
            if data.get("k") != 5: # Assuming k=5 is fixed for now or passed in context
                return None
                
            return data.get("skills")
        except Exception:
            return None
    return None

def save_cached_skills(instr_hash: str, skills: List[str]) -> None:
    cache_path = CACHE_DIR / f"{instr_hash}.json"
    data = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "retriever_fingerprint": RETRIEVER_FINGERPRINT,
        "k": 5,
        "query_hash": instr_hash,
        "skills": skills,
        "updated_at": datetime.datetime.now().isoformat()
    }
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)

def _write_retrieved_skills_inventory(
    dest_task_dir: Path,
    task_id: str,
    ranked_skills: List[str],
) -> Path:
    inventory_path = dest_task_dir / "RETRIEVED_SKILLS.md"
    recommended = ranked_skills[:2]
    lines = [
        f"# Retrieved Skills for {task_id}",
        "",
        "Retrieved skill inventory for this task materialization.",
        "",
        "## Recommended First Checks",
    ]
    if recommended:
        for idx, skill_name in enumerate(recommended, start=1):
            lines.append(f"{idx}. `{skill_name}`")
    else:
        lines.append("- No retrieved skills.")
    lines.extend(
        [
            "",
            "## Ranked Retrieval Results",
        ]
    )
    if ranked_skills:
        for idx, skill_name in enumerate(ranked_skills, start=1):
            lines.append(f"{idx}. `{skill_name}`")
    else:
        lines.append("- No retrieved skills.")
    inventory_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return inventory_path


def load_oracle_curated_skills(task_id: str) -> List[str]:
    skills_dir = TASKS_DIR / task_id / "environment" / "skills"
    if not skills_dir.exists():
        return []
    return sorted([p.name for p in skills_dir.iterdir() if p.is_dir()])


def _write_retrieval_instruction_overlay(
    run_dir: Path,
    task_id: str,
    ranked_skills: List[str],
    template_path: Path,
) -> Path:
    recommended = ranked_skills[:2]
    template_text = template_path.read_text(encoding="utf-8")
    overlay_text = Template(template_text).safe_substitute(
        task_id=task_id,
        top1=recommended[0] if len(recommended) >= 1 else "N/A",
        top2=recommended[1] if len(recommended) >= 2 else "N/A",
        recommended_skills=", ".join(recommended) if recommended else "none",
        ranked_skill_inventory="\n".join(
            f"{idx}. {skill_name}" for idx, skill_name in enumerate(ranked_skills, start=1)
        )
        if ranked_skills
        else "none",
    ).strip()
    overlay_path = run_dir / "retrieval_instruction_overlay.txt"
    overlay_path.write_text(overlay_text + "\n", encoding="utf-8")
    return overlay_path


def materialize_task(
    task_id: str,
    skill_names_ranked: List[str],
    run_id: str,
    instr_hash: str,
    cache_hit: bool,
    retrieval_exposure_mode: str = DEFAULT_RETRIEVAL_EXPOSURE_MODE,
    retrieval_source_mode: str = DEFAULT_RETRIEVAL_SOURCE_MODE,
    overlay_template_path: Path | None = None,
) -> Dict[str, Any]:
    """Materializes a task into a unique run directory."""
    src_task_dir = TASKS_DIR / task_id
    run_dir = RUNS_DIR / f"{run_id}__{task_id}"
    
    # Safety: Ensure we don't overwrite an existing run dir (though timestamp makes this unlikely)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    
    task_dir_name = task_id if retrieval_source_mode == "oracle_curated" else "task_materialized"
    dest_task_dir = run_dir / task_dir_name
    
    # Copy base task
    shutil.copytree(
        src_task_dir,
        dest_task_dir,
        ignore=shutil.ignore_patterns(*TASK_COPY_IGNORE_NAMES),
    )
    
    # Inject skills
    dest_skills_dir = dest_task_dir / "environment" / "skills"
    if dest_skills_dir.exists():
        shutil.rmtree(dest_skills_dir)
    dest_skills_dir.mkdir(parents=True)
    
    manifest_skills = {}
    
    # Determinism: Deduplicate and Sort
    unique_sorted_skills = sorted(list(set(skill_names_ranked)))
    
    for skill_name in unique_sorted_skills:
        src_skill = GLOBAL_SKILL_POOL / skill_name
        if src_skill.exists():
            shutil.copytree(src_skill, dest_skills_dir / skill_name)
            manifest_skills[skill_name] = str(src_skill)
        else:
            log("WARN", f"Skill {skill_name} not found for task {task_id}")
            
    # Write Retrieval Manifest (Task Level)
    inventory_path = None
    if retrieval_source_mode != "oracle_curated":
        inventory_path = _write_retrieved_skills_inventory(
            dest_task_dir=dest_task_dir,
            task_id=task_id,
            ranked_skills=skill_names_ranked,
        )

    overlay_path = None
    if (
        retrieval_source_mode != "oracle_curated"
        and retrieval_exposure_mode == "ranked_inventory"
        and overlay_template_path is not None
    ):
        overlay_path = _write_retrieval_instruction_overlay(
            run_dir=run_dir,
            task_id=task_id,
            ranked_skills=skill_names_ranked,
            template_path=overlay_template_path,
        )

    task_manifest = {
        "task_id": task_id,
        "query_hash": instr_hash,
        "skills": unique_sorted_skills,
        "retrieved_skills_ranked": skill_names_ranked,
        "recommended_skills": skill_names_ranked[:2],
        "created_at": datetime.datetime.now().isoformat(),
        "cache_hit": cache_hit,
        "skill_paths": manifest_skills,
        "retrieval_source_mode": retrieval_source_mode,
        "retrieval_exposure_mode": retrieval_exposure_mode,
        "inventory_path": str(inventory_path) if inventory_path else None,
        "instruction_overlay_path": str(overlay_path) if overlay_path else None,
    }
    
    with open(run_dir / "task_manifest.json", "w") as f:
        json.dump(task_manifest, f, indent=2)
        
    return {
        "run_dir": run_dir,
        "materialized_path": dest_task_dir,
        "instruction_overlay_path": overlay_path,
    }

def phase_a_prepare_jobs(
    target_tasks: List[str],
    use_cache: bool = True,
    retrieval_exposure_mode: str = DEFAULT_RETRIEVAL_EXPOSURE_MODE,
    retrieval_source_mode: str = DEFAULT_RETRIEVAL_SOURCE_MODE,
    overlay_template_path: Path | None = None,
) -> List[Dict[str, Any]]:
    """
    Centralized retrieval and materialization.
    Initializes the embedding model ONCE.
    """
    log("PHASE_A", "Starting centralized retrieval and materialization...")
    
    # Lazy import to avoid cost if not running Phase A
    retriever = None
    if retrieval_source_mode == "normal":
        try:
            from retrieval import SkillRetriever
            retriever = SkillRetriever()
        except Exception as e:
            log("ERROR", f"Failed to initialize SkillRetriever: {type(e).__name__}: {e}")
            sys.exit(1)

    jobs = []
    
    # Generate Batch ID
    batch_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    
    for i, task_id in enumerate(target_tasks):
        instr_path = TASKS_DIR / task_id / "instruction.md"
        dockerfile_path = TASKS_DIR / task_id / "environment" / "Dockerfile"
        
        # Validation
        if not instr_path.exists():
            log("WARN", f"Skipping {task_id}: instruction.md missing")
            continue
        if not dockerfile_path.exists():
            log("WARN", f"Skipping {task_id}: environment/Dockerfile missing")
            continue
            
        instruction_text = instr_path.read_text()
        instr_hash = get_instruction_hash(instruction_text)
        
        # 1. Retrieval (Check Cache first)
        skills = None
        cache_hit = False
        if retrieval_source_mode == "oracle_curated":
            skills = load_oracle_curated_skills(task_id)
            log("ORACLE", f"Using curated skills for {task_id}: {skills}")
        else:
            if use_cache:
                skills = load_cached_skills(instr_hash)
                if skills:
                    log("RETRIEVAL", f"Cache hit for {task_id}")
                    cache_hit = True
                else:
                    skills = None

            if skills is None:
                log("RETRIEVAL", f"Computing for {task_id}...")
                results = retriever.retrieve(instruction_text, k=5)
                skills = [r["skill_name"] for r in results]
                save_cached_skills(instr_hash, skills)
            
        # 2. Materialize
        # Use timestamp + microseconds for safety
        microseconds = datetime.datetime.now().strftime("%f")
        unique_run_id = f"{batch_timestamp}_{microseconds}"
        
        materialized = materialize_task(
            task_id=task_id,
            skill_names_ranked=skills,
            run_id=unique_run_id,
            instr_hash=instr_hash,
            cache_hit=cache_hit,
            retrieval_exposure_mode=retrieval_exposure_mode,
            retrieval_source_mode=retrieval_source_mode,
            overlay_template_path=overlay_template_path,
        )
        
        jobs.append({
            "task_id": task_id,
            "run_dir": materialized["run_dir"],
            "materialized_path": materialized["materialized_path"],
            "instruction_overlay_path": materialized["instruction_overlay_path"],
        })
        
    log("PHASE_A", f"Prepared {len(jobs)} jobs.")
    return jobs, batch_timestamp



# 0304
import re
def parse_reward_from_stdout(stdout_path: Path):
    """
    Parse reward from harbor stdout summary table.
    Looks for 'Mean                │ 1.000'
    """
    try:
        text = stdout_path.read_text()

        match = re.search(r"Mean\s+│\s+([0-9.]+)", text)
        if match:
            return float(match.group(1))

    except Exception:
        pass

    return None
#0304


# --- Phase B: Execution ---

def run_harbor_job(job: Dict[str, Any], agent: str, model: Optional[str] = None) -> Dict[str, Any]:
    """
    Lightweight execution worker.
    Runs 'harbor run' in a subprocess.
    """
    task_id = job["task_id"]
    run_dir = job["run_dir"]
    path = job["materialized_path"]
    
    log("EXECUTE", f"Starting {task_id} in {run_dir}")
    
    # Use the run_dir name as the unique Harbor job ID to prevent concurrency conflicts
    job_name = Path(run_dir).name
    
    effective_model = model
    if agent == "codex" and not effective_model:
        effective_model = DEFAULT_CODEX_MODEL

    cmd = [
        "uv",
        "run",
        "harbor",
        "run",
        "-p", str(path),
        "-e", "docker",
    ]
    if agent == "aider":
        cmd.extend(["--agent-import-path", AIDER_AGENT_IMPORT_PATH])
    else:
        cmd.extend(["-a", agent])
    if agent in {"claude-code", "aider", "codex"} and effective_model:
        cmd.extend(["-m", effective_model])
    cmd.extend(["--job-name", job_name, "-o", "jobs"])
    
    start_time = time.time()
    result = {
        "task_id": task_id,
        "run_dir": str(run_dir),
        "exit_code": -1,
        "status": "UNKNOWN",
        "reward": None # 0304
    }
    
    stdout_path = run_dir / "harbor_stdout.log"
    stderr_path = run_dir / "harbor_stderr.log"
    
    try:
        child_env = dict(os.environ)
        existing_pythonpath = child_env.get("PYTHONPATH")
        child_env["PYTHONPATH"] = (
            str(REPO_ROOT)
            if not existing_pythonpath
            else f"{REPO_ROOT}:{existing_pythonpath}"
        )
        overlay_path = job.get("instruction_overlay_path")
        if overlay_path:
            child_env["SKILLSBENCH_CODEX_INSTRUCTION_OVERLAY_FILE"] = str(overlay_path)
        with open(stdout_path, "w") as out_f, open(stderr_path, "w") as err_f:
            proc = subprocess.run(
                cmd,
                stdout=out_f,
                stderr=err_f,
                text=True,
                cwd=REPO_ROOT,
                env=child_env,
            )
            result["exit_code"] = proc.returncode
            result["status"] = "SUCCESS" if proc.returncode == 0 else "FAILURE"
            # Parse reward from stdout log 0304
            reward = parse_reward_from_stdout(stdout_path)
            result["reward"] = reward
            #0304

    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)
        
    result["duration"] = time.time() - start_time
    return result

def main():
    parser = argparse.ArgumentParser(description="Optimized Batch Runner for SkillsBench")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Run all tasks")
    group.add_argument("--tasks", nargs="+", help="Specific task IDs")
    group.add_argument("--tasks-file", type=Path, help="File with task IDs")
    
    parser.add_argument("--agent", default="oracle", help="Agent name")
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model path for claude-code/aider agent"
    )
    parser.add_argument("--jobs", type=int, default=2, help="Execution concurrency")
    parser.add_argument("--no-cache", action="store_true", help="Disable retrieval cache")
    parser.add_argument(
        "--retrieval-exposure-mode",
        choices=["off", "ranked_inventory"],
        default=DEFAULT_RETRIEVAL_EXPOSURE_MODE,
        help="Retrieval-only skill exposure patch mode",
    )
    parser.add_argument(
        "--retrieval-source-mode",
        choices=["normal", "oracle_curated"],
        default=DEFAULT_RETRIEVAL_SOURCE_MODE,
        help="Source of skills injected into retrieval pipeline",
    )
    parser.add_argument(
        "--retrieval-overlay-template",
        type=Path,
        default=DEFAULT_RETRIEVAL_OVERLAY_TEMPLATE,
        help="Template used to build retrieval instruction overlays",
    )
    
    args = parser.parse_args()
    
    # 1. Identify Tasks
    target_tasks = []
    if args.all:
        for item in TASKS_DIR.iterdir():
            if item.is_dir():
                target_tasks.append(item.name)
        target_tasks.sort()
    elif args.tasks:
        target_tasks = args.tasks
    elif args.tasks_file:
        with open(args.tasks_file) as f:
            target_tasks = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            
    if not target_tasks:
        print("No tasks found.")
        sys.exit(0)
        
    # 2. Phase A: Prepare
    overlay_template_path = args.retrieval_overlay_template
    if args.retrieval_exposure_mode != "off" and not overlay_template_path.exists():
        raise FileNotFoundError(
            f"Retrieval overlay template not found at {overlay_template_path}"
        )

    jobs, batch_id = phase_a_prepare_jobs(
        target_tasks,
        use_cache=not args.no_cache,
        retrieval_exposure_mode=args.retrieval_exposure_mode,
        retrieval_source_mode=args.retrieval_source_mode,
        overlay_template_path=overlay_template_path,
    )
    
    if not jobs:
        print("No valid jobs prepared (check logs for missing instructions/dockerfiles).")
        sys.exit(0)

    if args.agent in {"claude-code", "aider"} and not args.model:
        log("WARN", f"Agent '{args.agent}' selected without --model; running without -m argument.")
    if args.agent == "codex" and not args.model:
        log("PHASE_B", f"Agent 'codex' selected without --model; defaulting to {DEFAULT_CODEX_MODEL}.")
    
    # 3. Phase B: Execute
    log("PHASE_B", f"Executing jobs with {args.jobs} workers...")
    
    results = []
    failed = []
    
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(run_harbor_job, job, args.agent, args.model): job for job in jobs}
        
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            
            icon = "✅" if res["status"] == "SUCCESS" else "❌"
            #print(f"{icon} {res['task_id']} (Exit: {res['exit_code']}, Time: {res['duration']:.1f}s)")
            print(f"{icon} {res['task_id']} (reward={res.get('reward')}, time={res['duration']:.1f}s)")
            if res["status"] != "SUCCESS":
                failed.append(res)

    # 4. Write Batch Manifest
    batch_manifest = {
        "batch_id": batch_id,
        "agent": args.agent,
        "retriever_fingerprint": RETRIEVER_FINGERPRINT,
        "k": 5,
        "retrieval_source_mode": args.retrieval_source_mode,
        "retrieval_exposure_mode": args.retrieval_exposure_mode,
        "tasks": [r["task_id"] for r in results],
        "run_dirs": [r["run_dir"] for r in results],
        "failed_tasks": [r["task_id"] for r in failed]
    }
    
    with open(RUNS_DIR / f"batch_manifest_{batch_id}.json", "w") as f:
        json.dump(batch_manifest, f, indent=2)

    # 5. Summary
    print("\n" + "="*60)
    solved = sum(1 for r in results if (r.get("reward") or 0) > 0)
    print(f"Total: {len(results)} | Processed: {len(results)-len(failed)} | Successed: {solved} | Failed: {len(results)-len(failed)-solved}")
    if failed:
        print("Failures:")
        for f in failed:
            print(f"- {f['task_id']} -> {f['run_dir']}")
    print("="*60)
    
    if failed:
        sys.exit(1)

if __name__ == "__main__":
    main()
