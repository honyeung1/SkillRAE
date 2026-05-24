from __future__ import annotations

import argparse
import os
import re
from pathlib import Path, PurePosixPath


_OCTAL_ESCAPE_RE = re.compile(r"\\([0-7]{3})")


def _unescape_mountinfo_field(value: str) -> str:
    return _OCTAL_ESCAPE_RE.sub(
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def _normalize_posix_path(value: str | os.PathLike[str]) -> str:
    return str(PurePosixPath(os.fspath(value)))


def translate_path_prefix(
    path: str | os.PathLike[str],
    src_prefix: str | os.PathLike[str],
    dst_prefix: str | os.PathLike[str],
) -> Path | None:
    normalized_path = _normalize_posix_path(path)
    normalized_src = _normalize_posix_path(src_prefix)
    normalized_dst = _normalize_posix_path(dst_prefix)

    if normalized_path == normalized_src:
        return Path(normalized_dst)
    if normalized_path.startswith(f"{normalized_src}/"):
        suffix = normalized_path[len(normalized_src) + 1 :]
        if suffix:
            return Path(PurePosixPath(normalized_dst) / suffix)
        return Path(normalized_dst)
    return None


def resolve_bind_source_path(
    target_path: str | os.PathLike[str],
    mountinfo_text: str | None = None,
) -> Path | None:
    normalized_target = _normalize_posix_path(target_path)
    lines = (
        mountinfo_text.splitlines()
        if mountinfo_text is not None
        else Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    )

    best_match: tuple[int, Path] | None = None
    for raw_line in lines:
        pre_fields, separator, post_fields = raw_line.partition(" - ")
        if not separator:
            continue

        pre = pre_fields.split()
        post = post_fields.split()
        if len(pre) < 5 or len(post) < 2:
            continue

        mount_root = _normalize_posix_path(_unescape_mountinfo_field(pre[3]))
        mount_point = _normalize_posix_path(_unescape_mountinfo_field(pre[4]))
        if not mount_root.startswith("/"):
            continue
        if normalized_target != mount_point and not normalized_target.startswith(
            f"{mount_point}/"
        ):
            continue

        suffix = (
            ""
            if normalized_target == mount_point
            else normalized_target[len(mount_point) + 1 :]
        )
        candidate = Path(mount_root) if not suffix else Path(mount_root) / suffix
        score = len(mount_point)
        if best_match is None or score > best_match[0]:
            best_match = (score, candidate)

    return best_match[1] if best_match else None


def remap_container_path_to_host(
    path: str | os.PathLike[str],
    *,
    container_root: str | os.PathLike[str] | None = None,
    host_root: str | os.PathLike[str] | None = None,
    mountinfo_text: str | None = None,
) -> Path | None:
    if container_root is not None and host_root is not None:
        translated = translate_path_prefix(path, container_root, host_root)
        if translated is not None:
            return translated

    return resolve_bind_source_path(path, mountinfo_text=mountinfo_text)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve runner container paths to host bind-source paths."
    )
    parser.add_argument("--target", required=True, help="Container-visible path to map")
    parser.add_argument(
        "--container-root",
        help="Optional container-side root prefix for direct translation",
    )
    parser.add_argument(
        "--host-root",
        help="Optional host-side root prefix paired with --container-root",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    resolved = remap_container_path_to_host(
        args.target,
        container_root=args.container_root,
        host_root=args.host_root,
    )
    if resolved is None:
        return 1
    print(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
