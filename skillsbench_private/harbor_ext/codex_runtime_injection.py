from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import tarfile
import tempfile
from pathlib import Path


DEFAULT_RUNTIME_DIR_RELATIVE = Path("deployment") / "codex_runtime"
DEFAULT_RUNTIME_ARCHIVE_NAME = "codex-runtime-current.tar.gz"
DEFAULT_RUNTIME_MANIFEST_NAME = "codex-runtime-current.json"
CONTAINER_RUNTIME_ROOT = "/opt/codex-runtime"
CONTAINER_RUNTIME_BIN = f"{CONTAINER_RUNTIME_ROOT}/bin"


def _repo_root(explicit_repo_root: Path | None = None) -> Path:
    if explicit_repo_root is not None:
        return explicit_repo_root.resolve()
    return Path(__file__).resolve().parents[2]


def _resolve_path(raw_path: str, repo_root: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _default_runtime_root(repo_root: Path) -> Path:
    return repo_root / DEFAULT_RUNTIME_DIR_RELATIVE


def _manifest_path_for_archive(archive_path: Path) -> Path:
    if archive_path.name.endswith(".tar.gz"):
        return archive_path.with_name(f"{archive_path.name[:-7]}.json")
    return archive_path.with_suffix(".json")


def _archive_path_for_manifest(manifest_path: Path) -> Path:
    if manifest_path.suffix == ".json":
        return manifest_path.with_name(f"{manifest_path.stem}.tar.gz")
    return manifest_path.with_suffix(".tar.gz")


def _assert_supported_host_platform() -> dict[str, str]:
    host_os = platform.system().lower()
    host_arch_raw = platform.machine().lower()
    host_arch = {
        "amd64": "x86_64",
        "x86-64": "x86_64",
    }.get(host_arch_raw, host_arch_raw)

    if host_os != "linux":
        raise RuntimeError(
            f"Codex runtime injection currently supports Linux hosts only, got: {host_os}"
        )
    if host_arch != "x86_64":
        raise RuntimeError(
            "Codex runtime injection currently supports x86_64 hosts only, "
            f"got: {host_arch_raw}"
        )

    return {
        "host_os": host_os,
        "host_arch": host_arch,
    }


def get_configured_codex_runtime_paths(
    repo_root: Path | None = None,
) -> tuple[Path, Path]:
    resolved_repo_root = _repo_root(repo_root)
    archive_env = os.environ.get("SKILLSBENCH_CODEX_RUNTIME_ARCHIVE")
    manifest_env = os.environ.get("SKILLSBENCH_CODEX_RUNTIME_MANIFEST")

    if archive_env:
        archive_path = _resolve_path(archive_env, resolved_repo_root)
        manifest_path = (
            _resolve_path(manifest_env, resolved_repo_root)
            if manifest_env
            else _manifest_path_for_archive(archive_path)
        )
        return archive_path, manifest_path

    if manifest_env:
        manifest_path = _resolve_path(manifest_env, resolved_repo_root)
        archive_path = _archive_path_for_manifest(manifest_path)
        return archive_path, manifest_path

    runtime_root = _default_runtime_root(resolved_repo_root)
    return (
        runtime_root / DEFAULT_RUNTIME_ARCHIVE_NAME,
        runtime_root / DEFAULT_RUNTIME_MANIFEST_NAME,
    )


def _resolve_host_codex_runtime() -> tuple[Path, Path, Path, Path, dict[str, object]]:
    platform_info = _assert_supported_host_platform()

    codex_shim = shutil.which("codex")
    if not codex_shim:
        raise FileNotFoundError("Host codex binary not found in PATH")

    codex_shim_path = Path(codex_shim).resolve()
    package_root = codex_shim_path.parents[1]
    package_json_path = package_root / "package.json"
    if not package_json_path.exists():
        raise FileNotFoundError(f"Codex package.json not found at {package_json_path}")

    vendor_root = (
        package_root
        / "node_modules"
        / "@openai"
        / "codex-linux-x64"
        / "vendor"
        / "x86_64-unknown-linux-musl"
    )
    codex_binary = vendor_root / "codex" / "codex"
    rg_binary = vendor_root / "path" / "rg"
    if not codex_binary.exists():
        raise FileNotFoundError(f"Static codex binary not found at {codex_binary}")
    if not rg_binary.exists():
        raise FileNotFoundError(f"Bundled rg binary not found at {rg_binary}")

    package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
    manifest = {
        **platform_info,
        "host_codex_shim": str(codex_shim_path),
        "host_package_root": str(package_root),
        "host_static_codex_binary": str(codex_binary),
        "host_bundled_rg": str(rg_binary),
        "codex_version": package_json.get("version"),
        "package_name": package_json.get("name"),
        "runtime_strategy": "host-prepared-runtime-injection",
        "container_runtime_root": CONTAINER_RUNTIME_ROOT,
        "container_codex_path": f"{CONTAINER_RUNTIME_BIN}/codex",
        "container_static_codex_binary": f"{CONTAINER_RUNTIME_BIN}/codex-real",
        "container_bundled_rg": f"{CONTAINER_RUNTIME_BIN}/rg",
    }
    return codex_shim_path, package_root, codex_binary, rg_binary, manifest


def _bundle_fingerprint(
    codex_shim_path: Path,
    package_root: Path,
    codex_binary: Path,
    rg_binary: Path,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"host-codex-runtime-injection-v3")
    for path in (
        codex_shim_path,
        package_root / "package.json",
        codex_binary,
        rg_binary,
    ):
        stat = path.stat()
        digest.update(str(path).encode("utf-8"))
        digest.update(str(stat.st_mtime_ns).encode("utf-8"))
        digest.update(str(stat.st_size).encode("utf-8"))
    return digest.hexdigest()[:12]


def _write_wrapper(path: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)\n'
        'export PATH="$SCRIPT_DIR:$PATH"\n'
        'exec "$SCRIPT_DIR/codex-real" "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _refresh_alias(alias_path: Path, target_path: Path) -> None:
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    if alias_path == target_path:
        return

    if alias_path.exists() or alias_path.is_symlink():
        alias_path.unlink()

    relative_target = os.path.relpath(target_path, alias_path.parent)
    try:
        alias_path.symlink_to(relative_target)
    except OSError:
        shutil.copy2(target_path, alias_path)


def _load_manifest(manifest_path: Path) -> dict[str, object]:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _validate_manifest_platform(manifest: dict[str, object]) -> dict[str, object]:
    platform_info = _assert_supported_host_platform()
    manifest_host_os = manifest.get("host_os")
    manifest_host_arch = manifest.get("host_arch")

    if manifest_host_os and manifest_host_os != platform_info["host_os"]:
        raise RuntimeError(
            "Prepared Codex runtime bundle host_os does not match current host: "
            f"bundle={manifest_host_os} current={platform_info['host_os']}"
        )
    if manifest_host_arch and manifest_host_arch != platform_info["host_arch"]:
        raise RuntimeError(
            "Prepared Codex runtime bundle host_arch does not match current host: "
            f"bundle={manifest_host_arch} current={platform_info['host_arch']}"
        )

    return manifest


def prepare_codex_runtime_bundle(
    repo_root: Path | None = None,
    *,
    force: bool = False,
) -> tuple[Path, Path, dict[str, object]]:
    resolved_repo_root = _repo_root(repo_root)
    requested_archive_path, requested_manifest_path = get_configured_codex_runtime_paths(
        resolved_repo_root
    )
    runtime_root = requested_archive_path.parent
    runtime_root.mkdir(parents=True, exist_ok=True)

    codex_shim_path, package_root, codex_binary, rg_binary, manifest = _resolve_host_codex_runtime()
    fingerprint = _bundle_fingerprint(
        codex_shim_path=codex_shim_path,
        package_root=package_root,
        codex_binary=codex_binary,
        rg_binary=rg_binary,
    )

    fingerprinted_archive_path = runtime_root / f"codex-runtime-{fingerprint}.tar.gz"
    fingerprinted_manifest_path = runtime_root / f"codex-runtime-{fingerprint}.json"
    bundle_manifest = {
        **manifest,
        "fingerprint": fingerprint,
        "bundle_path": str(fingerprinted_archive_path),
        "manifest_path": str(fingerprinted_manifest_path),
        "runtime_root": str(runtime_root),
    }

    if (
        force
        or not fingerprinted_archive_path.exists()
        or not fingerprinted_manifest_path.exists()
    ):
        with tempfile.TemporaryDirectory(dir=runtime_root) as tmp_dir:
            staging_root = Path(tmp_dir) / "runtime"
            bin_dir = staging_root / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)

            shutil.copy2(codex_binary, bin_dir / "codex-real")
            shutil.copy2(rg_binary, bin_dir / "rg")
            _write_wrapper(bin_dir / "codex")
            _write_json(staging_root / "manifest.json", bundle_manifest)

            tmp_archive_path = Path(tmp_dir) / fingerprinted_archive_path.name
            with tarfile.open(tmp_archive_path, "w:gz") as tar:
                tar.add(staging_root, arcname=".")

            shutil.move(tmp_archive_path, fingerprinted_archive_path)
            _write_json(fingerprinted_manifest_path, bundle_manifest)
    else:
        bundle_manifest = json.loads(
            fingerprinted_manifest_path.read_text(encoding="utf-8")
        )

    _refresh_alias(requested_archive_path, fingerprinted_archive_path)
    _refresh_alias(requested_manifest_path, fingerprinted_manifest_path)

    resolved_manifest = {
        **bundle_manifest,
        "requested_archive_path": str(requested_archive_path),
        "requested_manifest_path": str(requested_manifest_path),
        "fingerprinted_archive_path": str(fingerprinted_archive_path),
        "fingerprinted_manifest_path": str(fingerprinted_manifest_path),
    }
    return requested_archive_path, requested_manifest_path, resolved_manifest


def get_host_codex_runtime_bundle(
    repo_root: Path | None = None,
    *,
    require_prepared: bool = False,
) -> tuple[Path, Path, dict[str, object]]:
    resolved_repo_root = _repo_root(repo_root)
    archive_path, manifest_path = get_configured_codex_runtime_paths(resolved_repo_root)

    if archive_path.exists() and manifest_path.exists():
        manifest = _validate_manifest_platform(_load_manifest(manifest_path))
        manifest.setdefault("requested_archive_path", str(archive_path))
        manifest.setdefault("requested_manifest_path", str(manifest_path))
        return archive_path, manifest_path, manifest

    if require_prepared:
        raise FileNotFoundError(
            "Prepared Codex runtime bundle not found. "
            f"Expected archive={archive_path} manifest={manifest_path}"
        )

    return prepare_codex_runtime_bundle(resolved_repo_root)


def validate_prepared_codex_runtime_bundle(
    repo_root: Path | None = None,
) -> tuple[Path, Path, dict[str, object]]:
    resolved_repo_root = _repo_root(repo_root)
    archive_path, manifest_path = get_configured_codex_runtime_paths(resolved_repo_root)

    if not archive_path.exists() or not manifest_path.exists():
        raise FileNotFoundError(
            "Prepared Codex runtime bundle not found. "
            f"Expected archive={archive_path} manifest={manifest_path}"
        )

    manifest = _validate_manifest_platform(_load_manifest(manifest_path))
    manifest.setdefault("requested_archive_path", str(archive_path))
    manifest.setdefault("requested_manifest_path", str(manifest_path))
    return archive_path, manifest_path, manifest
