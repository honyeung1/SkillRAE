#!/usr/bin/env python3

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _container_linter_source(payload: dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    return f"""
import csv
import json
import os
import shutil
import zipfile
from pathlib import Path

PAYLOAD = json.loads({payload_json!r})
CONTRACT = PAYLOAD.get("contract") if isinstance(PAYLOAD.get("contract"), dict) else {{}}
VERSION = str(PAYLOAD.get("version") or "v1")
REPAIR_ENABLED = bool(PAYLOAD.get("repair_enabled"))
MAX_SEARCH = int(PAYLOAD.get("max_search") or 200)

MANIFEST_PATHS = [
    Path("/logs/agent/contract_linter_manifest.json"),
    Path("/logs/artifacts/contract_linter_manifest.json"),
]

def as_str_list(value):
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if str(x).strip()]

def dedupe(items):
    seen = set()
    out = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out

TARGETS = dedupe(as_str_list(CONTRACT.get("output_paths")) + as_str_list(CONTRACT.get("module_paths")))
FORMATS = [x.lower() for x in dedupe(as_str_list(CONTRACT.get("formats")))]

findings = []
repair_actions = []
parse_errors = []
missing_targets = []
checked_targets = []

def candidates_for(target):
    p = Path(target)
    if p.is_absolute():
        return [p]
    cwd = Path.cwd()
    candidates = [cwd / p, Path("/root") / p, Path("/workspace") / p]
    out = []
    seen = set()
    for c in candidates:
        s = str(c)
        if s not in seen:
            seen.add(s)
            out.append(c)
    return out

def path_has_content(path):
    try:
        if path.is_dir():
            return any(path.iterdir())
        return path.is_file() and path.stat().st_size > 0
    except Exception:
        return False

def first_existing(candidates):
    for c in candidates:
        if path_has_content(c):
            return c
    return None

def safe_roots():
    roots = [Path.cwd(), Path("/root"), Path("/workspace")]
    out = []
    seen = set()
    for root in roots:
        try:
            root = root.resolve()
        except Exception:
            continue
        if root.exists() and str(root) not in seen:
            seen.add(str(root))
            out.append(root)
    return out

def find_unique_same_basename(target):
    name = Path(target).name
    if not name:
        return None, "empty_basename"
    matches = []
    for root in safe_roots():
        try:
            for p in root.rglob(name):
                if len(matches) >= MAX_SEARCH:
                    return None, "too_many_matches"
                if "/tests/" in str(p) or "/logs/" in str(p):
                    continue
                if path_has_content(p):
                    matches.append(p)
        except Exception:
            continue
    unique = []
    seen = set()
    for m in matches:
        s = str(m)
        if s not in seen:
            seen.add(s)
            unique.append(m)
    if len(unique) == 1:
        return unique[0], "unique_basename_match"
    if not unique:
        return None, "no_basename_match"
    return None, "ambiguous_basename_match"

def copy_path(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            return False, "destination_exists"
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return True, "copied"

def validate_format(path, target):
    suffix = path.suffix.lower().lstrip(".")
    expected = set(FORMATS)
    if suffix:
        expected.add(suffix)
    try:
        if "json" in expected and path.is_file() and path.suffix.lower() == ".json":
            json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if "csv" in expected and path.is_file() and path.suffix.lower() == ".csv":
            with path.open(newline="", encoding="utf-8", errors="replace") as fh:
                reader = csv.reader(fh)
                first = next(reader, None)
                if not first:
                    raise ValueError("empty csv")
        if ("xlsx" in expected or suffix == "xlsx") and path.is_file():
            if not zipfile.is_zipfile(path):
                raise ValueError("xlsx is not a zip container")
        if ("pptx" in expected or suffix == "pptx") and path.is_file():
            if not zipfile.is_zipfile(path):
                raise ValueError("pptx is not a zip container")
    except Exception as exc:
        parse_errors.append({{"target": target, "path": str(path), "error": str(exc)[:300]}})

for target in TARGETS:
    candidates = candidates_for(target)
    existing = first_existing(candidates)
    repaired = False
    if existing is None and REPAIR_ENABLED:
        src, reason = find_unique_same_basename(target)
        if src is not None:
            dst = candidates[0]
            ok, action = copy_path(src, dst)
            if ok:
                repair_actions.append({{"target": target, "source": str(src), "destination": str(dst), "action": action}})
                repaired = True
                existing = dst
            else:
                findings.append({{"target": target, "type": "repair_skipped", "reason": action, "source": str(src), "destination": str(dst)}})
        else:
            findings.append({{"target": target, "type": "repair_not_available", "reason": reason}})
    if existing is None:
        missing_targets.append(target)
        checked_targets.append({{"target": target, "exists": False, "candidates": [str(c) for c in candidates]}})
    else:
        checked_targets.append({{"target": target, "exists": True, "path": str(existing), "repaired": repaired}})
        if existing.is_file():
            validate_format(existing, target)

manifest = {{
    "contract_linter_enabled": True,
    "contract_linter_version": VERSION,
    "contract_linter_checked": True,
    "contract_linter_repair_enabled": REPAIR_ENABLED,
    "contract_linter_target_count": len(TARGETS),
    "contract_linter_checked_targets": checked_targets,
    "contract_linter_missing_targets": missing_targets,
    "contract_linter_parse_errors": parse_errors,
    "contract_linter_findings": findings,
    "contract_repair_applied": bool(repair_actions),
    "contract_repair_actions": repair_actions,
    "contract_linter_passed": not missing_targets and not parse_errors,
}}

for path in MANIFEST_PATHS:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    except Exception:
        pass
print(json.dumps({{"contract_linter_passed": manifest["contract_linter_passed"], "repair_actions": len(repair_actions), "missing_targets": len(missing_targets)}}))
"""


async def run_contract_linter_repair(trial: Any) -> None:
    if os.environ.get("SKILLSBENCH_CONTRACT_LINTER_ENABLE", "0") != "1":
        return

    contract_file = os.environ.get("SKILLSBENCH_OUTPUT_CONTRACT_FILE", "").strip()
    contract = _load_json(Path(contract_file)) if contract_file else {}
    output_paths = _dedupe_keep_order(
        _as_str_list(contract.get("output_paths")) + _as_str_list(contract.get("module_paths"))
    )
    formats = _dedupe_keep_order(_as_str_list(contract.get("formats")))

    payload = {
        "contract": contract,
        "version": os.environ.get("SKILLSBENCH_CONTRACT_LINTER_VERSION", "v1"),
        "repair_enabled": os.environ.get("SKILLSBENCH_CONTRACT_REPAIR_ENABLE", "1") == "1",
        "max_search": int(os.environ.get("SKILLSBENCH_CONTRACT_LINTER_MAX_SEARCH", "200") or "200"),
    }
    source = _container_linter_source(payload)
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    command = (
        "if command -v python3 >/dev/null 2>&1; then PY=python3; "
        "elif command -v python >/dev/null 2>&1; then PY=python; "
        "else mkdir -p /logs/agent /logs/artifacts; "
        "printf '%s\\n' '{\"contract_linter_enabled\":true,\"contract_linter_checked\":false,"
        "\"contract_linter_passed\":null,\"contract_linter_findings\":[{\"type\":\"python_missing\"}]}' "
        "> /logs/agent/contract_linter_manifest.json; exit 0; fi; "
        "$PY - <<'PY'\n"
        "import base64\n"
        f"exec(base64.b64decode({encoded!r}).decode('utf-8'))\n"
        "PY"
    )

    # If a task has no machine-readable target signal, still write a manifest
    # for observability.  The container script handles the empty target list.
    _ = output_paths, formats
    await trial._environment.exec(command=command)
