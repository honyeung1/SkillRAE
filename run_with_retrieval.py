import argparse
import shutil
import json
import subprocess
import datetime
import sys
import os
import threading
from pathlib import Path
from typing import List, Dict, Any, IO
from dirhash import dirhash
from retrieval import SkillRetriever

# Configuration
REPO_ROOT = Path(__file__).parent.resolve()
ORIGINAL_TASKS_DIR = REPO_ROOT / "tasks"
GLOBAL_SKILL_POOL = REPO_ROOT / "global_skill_pool"
RUNS_DIR = Path("/mnt/data/xiangcheng/runs")

def log(step: str, message: str) -> None:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{step}] {message}")

_retriever = None

def retrieve_skills(query: str) -> List[Path]:
    global _retriever

    if _retriever is None:
        log("RETRIEVAL", "Initializing SkillRetriever...")
        _retriever = SkillRetriever()

    results = _retriever.retrieve(query, k=5)

    skill_paths: List[Path] = []

    for r in results:
        skill_name = r["skill_name"]
        path = GLOBAL_SKILL_POOL / skill_name

        if path.exists():
            skill_paths.append(path)
        else:
            log("WARNING", f"Skill not found in pool: {skill_name}")

    return skill_paths

def get_dir_hash(path: Path) -> str:
    """Calculate hash of a directory for cache awareness."""
    if not path.exists():
        return "empty"
    return dirhash(str(path), "sha256")

def check_harbor_installed() -> None:
    """Verify harbor is installed and available."""
    try:
        subprocess.run(["harbor", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        log("ERROR", "Harbor is not installed or not in PATH.")
        sys.exit(1)

def verify_task_integrity(task_id: str, task_dir: Path) -> None:
    """Perform safety checks on the source task directory."""
    if not (task_dir / "instruction.md").exists():
        log("ERROR", f"Task {task_id} missing instruction.md")
        sys.exit(1)
    
    if not (task_dir / "environment" / "Dockerfile").exists():
        log("ERROR", f"Task {task_id} missing environment/Dockerfile")
        sys.exit(1)

def create_run_directory(task_id: str) -> Path:
    """Create a unique run directory with timestamp."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # Use microseconds to avoid collisions in tight loops
    microseconds = datetime.datetime.now().strftime("%f")
    run_dir = RUNS_DIR / f"{timestamp}_{microseconds}__{task_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir

def materialize_task_in_run(task_id: str, retrieved_skill_paths: List[Path], run_dir: Path) -> Path:
    """Materialize the task directly inside the run directory for isolation."""
    src_task_dir = ORIGINAL_TASKS_DIR / task_id
    
    # Define the materialized task path inside the run directory
    # e.g., runs/2023.../task_materialized
    dest_task_dir = run_dir / "task_materialized"
    
    verify_task_integrity(task_id, src_task_dir)
    
    # 1. Copy base task
    log("MATERIALIZE", f"Copying task from {src_task_dir} to {dest_task_dir}")
    shutil.copytree(src_task_dir, dest_task_dir)
    
    # 2. Prepare skills directory
    dest_skills_dir = dest_task_dir / "environment" / "skills"
    if dest_skills_dir.exists():
        shutil.rmtree(dest_skills_dir)
    dest_skills_dir.mkdir(parents=True)
    
    # 3. Inject retrieved skills (Deterministic)
    manifest: Dict[str, str] = {}
    
    # Sort paths for deterministic ordering
    sorted_skill_paths = sorted(retrieved_skill_paths, key=lambda p: p.name)
    
    for skill_path in sorted_skill_paths:
        if not skill_path.exists():
            log("WARNING", f"Skill path not found: {skill_path}")
            continue
            
        skill_name = skill_path.name
        target_path = dest_skills_dir / skill_name
        
        if target_path.exists():
            log("ERROR", f"Duplicate skill name detected: {skill_name}")
            sys.exit(1)
            
        shutil.copytree(skill_path, target_path)
        manifest[skill_name] = str(skill_path.resolve())
        
    # 4. Write manifest
    with open(dest_task_dir / "retrieval_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
        
    return dest_task_dir

def stream_reader(pipe: IO[str], sink: IO[str], file_handle: IO[str]):
    """Reads from a pipe and writes to both sink and file until EOF."""
    try:
        for line in iter(pipe.readline, ''):
            sink.write(line)
            sink.flush()
            file_handle.write(line)
            file_handle.flush()
    except Exception:
        pass
    finally:
        pipe.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id", help="ID of the task to run")
    parser.add_argument("-a", "--agent", default="oracle", help="Agent to run")
    args = parser.parse_args()
    
    # Pre-flight checks
    check_harbor_installed()
    
    # 1. Create Run Directory (ISOLATION START)
    run_dir = create_run_directory(args.task_id)
    log("INIT", f"Run directory created: {run_dir}")
    print(f"RUN_DIR={run_dir.resolve()}")  # Machine-readable output for batch runner
    
    # 2. Read Query
    instruction_path = ORIGINAL_TASKS_DIR / args.task_id / "instruction.md"
    if not instruction_path.exists():
        log("ERROR", f"Task {args.task_id} not found at {instruction_path}")
        sys.exit(1)
        
    query = instruction_path.read_text()
    
    # 3. Retrieve
    log("RETRIEVAL", f"Retrieving skills for {args.task_id}...")
    skills = retrieve_skills(query)
    log("RETRIEVAL", f"Found {len(skills)} skills")
    
    # 4. Materialize (In Run Dir)
    task_path = materialize_task_in_run(args.task_id, skills, run_dir)
    
    # 5. Cache Awareness
    skills_dir = task_path / "environment" / "skills"
    current_hash = get_dir_hash(skills_dir)
    log("MATERIALIZE", f"Environment skills hash: {current_hash}")
    
    # 6. Execute
    cmd = [
        "harbor", "run",
        "-p", str(task_path),
        "-a", args.agent
    ]
    
    log("EXECUTE", f"Running command: {' '.join(cmd)}")
    
    stdout_path = run_dir / "harbor_stdout.log"
    stderr_path = run_dir / "harbor_stderr.log"
    
    # Open files for logging
    with open(stdout_path, "w") as stdout_file, open(stderr_path, "w") as stderr_file:
        try:
            # Use Popen with pipes
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1  # Line buffered
            )
            
            # Start threads to consume stdout and stderr concurrently
            t_out = threading.Thread(target=stream_reader, args=(process.stdout, sys.stdout, stdout_file))
            t_err = threading.Thread(target=stream_reader, args=(process.stderr, sys.stderr, stderr_file))
            
            t_out.start()
            t_err.start()
            
            # Wait for process to finish
            return_code = process.wait()
            
            # Wait for threads to finish reading remaining output
            t_out.join()
            t_err.join()
            
            if return_code != 0:
                log("RESULT", f"Harbor failed with exit code {return_code}")
                sys.exit(return_code)
            else:
                log("RESULT", "Run completed successfully")
                
        except Exception as e:
            log("ERROR", f"Execution failed: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
