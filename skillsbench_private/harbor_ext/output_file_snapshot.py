#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


EXCLUDED_DIR_NAMES = {
    ".git",
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "node_modules",
}
EXCLUDED_PREFIXES = (
    "/root/.cache/",
    "/root/.cargo/",
    "/root/.npm/",
    "/root/.local/share/",
    "/root/.codex/",
    "/root/.agents/",
    "/root/.claude/",
    "/root/.goose/",
    "/root/.gemini/",
    "/root/.factory/",
)


def _should_skip(path: Path) -> bool:
    path_text = str(path)
    if any(path_text.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return True
    return any(part in EXCLUDED_DIR_NAMES for part in path.parts)


def _snapshot(roots: list[Path]) -> dict[str, dict[str, int | str]]:
    state: dict[str, dict[str, int | str]] = {}
    for root in roots:
        if not root.exists():
            continue
        for current_root, dirnames, filenames in os.walk(root):
            current_path = Path(current_root)
            dirnames[:] = [dirname for dirname in dirnames if not _should_skip(current_path / dirname)]
            for filename in filenames:
                file_path = current_path / filename
                if _should_skip(file_path):
                    continue
                try:
                    stat = file_path.stat()
                except OSError:
                    continue
                state[str(file_path)] = {
                    "size": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                }
    return state


def _load_state(path: Path) -> dict[str, dict[str, int | str]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def command_pre(snapshot_path: Path, roots: list[Path]) -> int:
    _write_json(snapshot_path, {"roots": [str(root) for root in roots], "state": _snapshot(roots)})
    return 0


def command_post(snapshot_path: Path, output_path: Path, roots: list[Path]) -> int:
    before_payload = _load_state(snapshot_path)
    before_state = before_payload.get("state", {}) if isinstance(before_payload, dict) else {}
    after_state = _snapshot(roots)
    changed: list[dict[str, object]] = []
    for path, after_row in sorted(after_state.items()):
        before_row = before_state.get(path)
        status = None
        if before_row is None:
            status = "created"
        elif (
            int(before_row.get("size", -1)) != int(after_row.get("size", -1))
            or int(before_row.get("mtime_ns", -1)) != int(after_row.get("mtime_ns", -1))
        ):
            status = "modified"
        if status is None:
            continue
        changed.append(
            {
                "path": path,
                "status": status,
                "size": after_row.get("size"),
                "mtime_ns": after_row.get("mtime_ns"),
            }
        )
    _write_json(
        output_path,
        {
            "roots": [str(root) for root in roots],
            "snapshot_path": str(snapshot_path),
            "changed": changed,
        },
    )
    return 0


def main() -> int:
    if len(sys.argv) < 4:
        print(
            "usage: output_file_snapshot.py pre <snapshot_path> <root...> | "
            "output_file_snapshot.py post <snapshot_path> <output_path> <root...>",
            file=sys.stderr,
        )
        return 2

    mode = sys.argv[1]
    if mode == "pre":
        snapshot_path = Path(sys.argv[2])
        roots = [Path(arg) for arg in sys.argv[3:]]
        return command_pre(snapshot_path, roots)
    if mode == "post":
        if len(sys.argv) < 5:
            return 2
        snapshot_path = Path(sys.argv[2])
        output_path = Path(sys.argv[3])
        roots = [Path(arg) for arg in sys.argv[4:]]
        return command_post(snapshot_path, output_path, roots)

    print(f"unknown mode: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
