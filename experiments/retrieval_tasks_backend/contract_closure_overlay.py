#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
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


def build_contract_section(contract: dict[str, Any], *, max_cautions: int) -> tuple[str, dict[str, Any]]:
    output_paths = _dedupe_keep_order(
        _as_str_list(contract.get("output_paths")) + _as_str_list(contract.get("module_paths"))
    )
    formats = _dedupe_keep_order(_as_str_list(contract.get("formats")))
    cautions = _dedupe_keep_order(_as_str_list(contract.get("verifier_cautions")))[:max(0, max_cautions)]

    lines: list[str] = [
        "## Contract Closure Check",
        "",
        "Before final response, close the output contract. Do not stop at analysis notes.",
    ]
    if output_paths:
        lines.append("")
        lines.append("Required output targets:")
        for path in output_paths[:8]:
            lines.append(f"- `{path}`")
    else:
        lines.append("")
        lines.append("Required output targets: not explicit; follow `instruction.md` and verifier-sensitive checks.")

    if formats:
        lines.append("")
        lines.append("Required formats:")
        lines.append("- " + ", ".join(f"`{item}`" for item in formats[:8]))

    if cautions:
        lines.append("")
        lines.append("Verifier-sensitive checks:")
        for caution in cautions:
            one_line = " ".join(caution.split())
            if len(one_line) > 180:
                one_line = one_line[:177] + "..."
            lines.append(f"- {one_line}")

    lines.extend(
        [
            "",
            "Cheap self-check before finishing:",
            "- Confirm every required file exists and is non-empty.",
            "- If JSON/CSV/text is required, parse or inspect it once and fix obvious schema/path mistakes.",
        ]
    )

    meta = {
        "target_count": len(output_paths),
        "format_count": len(formats),
        "caution_count": len(cautions),
        "has_contract_signal": bool(output_paths or formats or cautions),
    }
    return "\n".join(lines).rstrip() + "\n", meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror-path", type=Path, required=True)
    parser.add_argument("--input-overlay", type=Path, required=True)
    parser.add_argument("--output-overlay", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--version", default="v1")
    parser.add_argument("--max-cautions", type=int, default=8)
    args = parser.parse_args()

    source_text = args.input_overlay.read_text(encoding="utf-8", errors="replace")
    contract = _load_json(args.mirror_path / "OUTPUT_CONTRACT.json")
    section, meta = build_contract_section(contract, max_cautions=args.max_cautions)

    args.output_overlay.parent.mkdir(parents=True, exist_ok=True)
    args.output_overlay.write_text(source_text.rstrip() + "\n\n" + section, encoding="utf-8")

    payload = {
        "contract_closure_enabled": True,
        "contract_closure_version": args.version,
        "contract_closure_overlay_path": str(args.output_overlay),
        "contract_closure_manifest_path": str(args.manifest_out),
        "contract_closure_target_count": meta["target_count"],
        "contract_closure_format_count": meta["format_count"],
        "contract_closure_caution_count": meta["caution_count"],
        "contract_closure_has_contract_signal": meta["has_contract_signal"],
    }
    args.manifest_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(args.output_overlay))


if __name__ == "__main__":
    main()
