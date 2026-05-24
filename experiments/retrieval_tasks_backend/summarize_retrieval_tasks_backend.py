#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASKS_DIR = REPO_ROOT / "tasks"

COMMAND_RE = re.compile(r'"command":"(.*?)","aggregated_output":"(.*?)","exit_code":')
SKILL_PATH_RE = re.compile(r"/(?:root|logs/agent)/[^\"'\s]*skills(?:/\.system)?/([^/\s]+)/([^\s\"']+)")
NEW_JOB_RE = re.compile(r"retrieval-tasks-backend-\d+-[^-]+-(.*)-\d{8}_\d{6}$")
CURATED_JOB_RE = re.compile(r"risk-probe-\d+-(.*)-\d{8}_\d{6}$")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_tasks(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def decode_json_escaped(text: str) -> str:
    return bytes(text, "utf-8").decode("unicode_escape", "ignore")


def resolve_curated_skills_dir(tasks_dir: Path, task: str) -> Path:
    return tasks_dir / task / "environment" / "skills"


def parse_trace(codex_trace_path: Path) -> tuple[list[dict], list[dict]]:
    if not codex_trace_path.exists():
        return [], []

    command_events: list[dict] = []
    skill_reads: list[dict] = []
    command_idx = 0

    for line_no, line in enumerate(
        codex_trace_path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        match = COMMAND_RE.search(line)
        if not match:
            continue
        command_idx += 1
        command = decode_json_escaped(match.group(1))
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
    return command_events, skill_reads


def classify_binding(command_events: list[dict], skill_reads: list[dict], ranked_skills: list[str]) -> str:
    if not skill_reads:
        return "none"
    first_index = skill_reads[0]["command_index"]
    follow_ups = command_events[first_index:first_index + 4]
    relevant_skills = {read["skill"] for read in skill_reads[:2]} | set(ranked_skills[:2])
    strong_markers = [
        "/scripts/",
        "python3 /root/.codex/skills/",
        "python3 /root/.agents/skills/",
        "make descriptions",
        "matched_filter(",
        "highpass(",
        "whisper",
        "ffmpeg",
        "transcribe.py",
    ]
    weak_markers = ["/root/.codex/skills/", "/root/.agents/skills/", "python - <<'PY'"]
    if any(any(marker in event["command"] for marker in strong_markers) for event in follow_ups):
        return "strong"
    if any(any(skill in event["command"] for skill in relevant_skills) for event in follow_ups):
        return "strong"
    if any(any(marker in event["command"] for marker in weak_markers) for event in follow_ups):
        return "weak"
    return "weak"


def summarize_trace_metrics(task: str, codex_trace_path: Path, ranked_skills: list[str], tasks_dir: Path) -> dict:
    curated_dir = resolve_curated_skills_dir(tasks_dir, task)
    curated_skills = {p.name for p in curated_dir.iterdir() if p.is_dir()} if curated_dir.exists() else set()
    command_events, skill_reads = parse_trace(codex_trace_path)
    first_skill_read = skill_reads[0]["skill"] if skill_reads else None
    if not skill_reads:
        read_depth = "none"
    elif any(not event["is_skill_md"] for event in skill_reads):
        read_depth = "deeper_than_skill_md"
    else:
        read_depth = "skill_md_only"
    return {
        "any_skill_read": bool(skill_reads),
        "any_curated_read": any(read["skill"] in curated_skills for read in skill_reads),
        "first_skill_read": first_skill_read,
        "read_depth": read_depth,
        "wrong_first_read": bool(first_skill_read) and first_skill_read not in curated_skills,
        "post_read_action_binding": classify_binding(command_events, skill_reads, ranked_skills),
    }


def aggregate(rows: list[dict], prefix: str) -> dict:
    reward_key = f"{prefix}_reward"
    any_skill_key = f"{prefix}_any_skill_read"
    any_curated_key = f"{prefix}_any_curated_read"
    first_skill_key = f"{prefix}_first_skill_read"
    read_depth_key = f"{prefix}_read_depth"
    wrong_key = f"{prefix}_wrong_first_read"
    binding_key = f"{prefix}_post_read_action_binding"
    rewards = [row[reward_key] for row in rows if isinstance(row.get(reward_key), (int, float))]

    def count_depth(value: str) -> int:
        return sum(1 for row in rows if row.get(read_depth_key) == value)

    def count_binding(value: str) -> int:
        return sum(1 for row in rows if row.get(binding_key) == value)

    return {
        "n_tasks": len(rows),
        "mean_reward": (sum(rewards) / len(rewards)) if rewards else None,
        "success_count": sum(1 for reward in rewards if reward == 1.0),
        "any_skill_read": sum(1 for row in rows if row.get(any_skill_key)),
        "any_curated_read": sum(1 for row in rows if row.get(any_curated_key)),
        "first_skill_read_nonnull": sum(1 for row in rows if row.get(first_skill_key)),
        "read_depth": {
            "none": count_depth("none"),
            "skill_md_only": count_depth("skill_md_only"),
            "deeper_than_skill_md": count_depth("deeper_than_skill_md"),
        },
        "wrong_first_read": sum(1 for row in rows if row.get(wrong_key)),
        "post_read_action_binding": {
            "none": count_binding("none"),
            "weak": count_binding("weak"),
            "strong": count_binding("strong"),
        },
    }


def parse_curated_summary(summary_jsonl: Path, tasks_dir: Path) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for row in load_jsonl(summary_jsonl):
        match = CURATED_JOB_RE.match(row["job_name"])
        if not match:
            continue
        task = match.group(1)
        index[task] = {
            "reward": row.get("reward"),
            "any_skill_read": False,
            "any_curated_read": False,
            "first_skill_read": None,
            "read_depth": "none",
            "wrong_first_read": False,
            "post_read_action_binding": "none",
        }
        trial_path = Path(row["trial_path"]) if row.get("trial_path") else None
        if trial_path:
            trace_path = trial_path / "agent" / "codex.txt"
            index[task].update(summarize_trace_metrics(task, trace_path, [], tasks_dir))
    return index


def parse_new_summary(summary_jsonl: Path, mirror_manifest: Path, tasks_dir: Path) -> dict[str, dict]:
    manifest_rows = {row["task_id"]: row for row in load_json(mirror_manifest)["rows"]}
    index: dict[str, dict] = {}
    for row in load_jsonl(summary_jsonl):
        match = NEW_JOB_RE.match(row["job_name"])
        if not match:
            continue
        task = match.group(1)
        trace_metrics = {
            "any_skill_read": False,
            "any_curated_read": False,
            "first_skill_read": None,
            "read_depth": "none",
            "wrong_first_read": False,
            "post_read_action_binding": "none",
        }
        trial_path = Path(row["trial_path"]) if row.get("trial_path") else None
        if trial_path:
            trace_metrics = summarize_trace_metrics(
                task,
                trial_path / "agent" / "codex.txt",
                manifest_rows[task]["retrieved_skills_ranked"],
                tasks_dir,
            )
        index[task] = {
            "reward": row.get("reward"),
            **trace_metrics,
            "mirror_path": row.get("mirror_path"),
            "trial_path": row.get("trial_path"),
            "retrieved_skills_ranked": manifest_rows[task]["retrieved_skills_ranked"],
            "injected_skill_order": manifest_rows[task].get("injected_skill_order"),
            "generated_skill_path": manifest_rows[task].get("generated_skill_path"),
            "selected_highlighted_subunits": manifest_rows[task].get("selected_highlighted_subunits"),
            "rescued_subunits": manifest_rows[task].get("rescued_subunits"),
            "affiliated_rescued_subunits": manifest_rows[task].get("affiliated_rescued_subunits"),
            "affiliated_skill_ids": manifest_rows[task].get("affiliated_skill_ids"),
            "affiliated_cue_counts": manifest_rows[task].get("affiliated_cue_counts"),
            "affiliated_cue_files": manifest_rows[task].get("affiliated_cue_files"),
            "affiliation_manifest_path": manifest_rows[task].get("affiliation_manifest_path"),
        }
    return index


def legacy_main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks-dir", type=Path, default=DEFAULT_TASKS_DIR)
    parser.add_argument("--tasks-file", type=Path, required=True)
    parser.add_argument("--new-summary-jsonl", type=Path, required=True)
    parser.add_argument("--new-mirror-manifest", type=Path, required=True)
    parser.add_argument("--curated-summary-jsonl", type=Path, required=True)
    parser.add_argument("--retrieval-analysis-jsonl", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--output-report-json", type=Path, required=True)
    args = parser.parse_args()

    tasks_dir = args.tasks_dir.resolve()
    tasks = load_tasks(args.tasks_file)
    curated_index = parse_curated_summary(args.curated_summary_jsonl, tasks_dir)
    new_index = parse_new_summary(args.new_summary_jsonl, args.new_mirror_manifest, tasks_dir)

    retrieval_rows = load_jsonl(args.retrieval_analysis_jsonl)
    retrieval_index = {(row["task"], row["variant"]): row for row in retrieval_rows}

    rows: list[dict] = []
    for task in tasks:
        rb = retrieval_index[(task, "retrieval_baseline")]
        ns = retrieval_index[(task, "no_skills")]
        curated = curated_index[task]
        new = new_index[task]
        rows.append(
            {
                "task": task,
                "bucket": rb.get("bucket"),
                "curated_reward": curated.get("reward"),
                "curated_any_skill_read": curated.get("any_skill_read"),
                "curated_any_curated_read": curated.get("any_curated_read"),
                "curated_first_skill_read": curated.get("first_skill_read"),
                "curated_read_depth": curated.get("read_depth"),
                "curated_wrong_first_read": curated.get("wrong_first_read"),
                "curated_post_read_action_binding": curated.get("post_read_action_binding"),
                "retrieval_baseline_reward": rb.get("reward"),
                "retrieval_baseline_any_skill_read": rb.get("any_skill_read"),
                "retrieval_baseline_any_curated_read": rb.get("any_curated_read"),
                "retrieval_baseline_first_skill_read": rb.get("first_skill_read"),
                "retrieval_baseline_read_depth": rb.get("read_depth"),
                "retrieval_baseline_wrong_first_read": rb.get("wrong_first_read"),
                "retrieval_baseline_post_read_action_binding": rb.get("post_read_action_binding"),
                "no_skills_reward": ns.get("reward"),
                "no_skills_any_skill_read": ns.get("any_skill_read"),
                "no_skills_any_curated_read": ns.get("any_curated_read"),
                "no_skills_first_skill_read": ns.get("first_skill_read"),
                "no_skills_read_depth": ns.get("read_depth"),
                "no_skills_wrong_first_read": ns.get("wrong_first_read"),
                "no_skills_post_read_action_binding": ns.get("post_read_action_binding"),
                "retrieval_tasks_backend_reward": new.get("reward"),
                "retrieval_tasks_backend_any_skill_read": new.get("any_skill_read"),
                "retrieval_tasks_backend_any_curated_read": new.get("any_curated_read"),
                "retrieval_tasks_backend_first_skill_read": new.get("first_skill_read"),
                "retrieval_tasks_backend_read_depth": new.get("read_depth"),
                "retrieval_tasks_backend_wrong_first_read": new.get("wrong_first_read"),
                "retrieval_tasks_backend_post_read_action_binding": new.get("post_read_action_binding"),
                "retrieval_tasks_backend_mirror_path": new.get("mirror_path"),
                "retrieval_tasks_backend_trial_path": new.get("trial_path"),
            }
        )

    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.output_report_json.parent.mkdir(parents=True, exist_ok=True)
    with args.output_jsonl.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = {
        "tasks": tasks,
        "aggregate": {
            "curated": aggregate(rows, "curated"),
            "retrieval_baseline": aggregate(rows, "retrieval_baseline"),
            "no_skills": aggregate(rows, "no_skills"),
            "retrieval_tasks_backend": aggregate(rows, "retrieval_tasks_backend"),
        },
    }
    args.output_report_json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(str(args.output_jsonl))
    print(str(args.output_report_json))


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "pilot-ablation":
        from experiments.retrieval_tasks_backend.pilot_ablation_report import main as pilot_main

        pilot_main(argv[1:])
        return

    if any(arg == "--variant-source" or arg.startswith("--variant-source=") for arg in argv):
        from experiments.retrieval_tasks_backend.pilot_ablation_report import main as pilot_main

        pilot_main(argv)
        return

    legacy_main()


if __name__ == "__main__":
    main()
