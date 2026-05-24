#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path


PRESSURE_FILE = Path("/proc/pressure/io")


def log(message: str) -> None:
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


def parse_pressure(path: Path) -> dict[str, dict[str, float]]:
    data: dict[str, dict[str, float]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        parts = raw_line.split()
        scope = parts[0]
        metrics: dict[str, float] = {}
        for token in parts[1:]:
            key, value = token.split("=", 1)
            metrics[key] = float(value)
        data[scope] = metrics
    return data


def free_gb(path: Path) -> float:
    stat = os.statvfs(path)
    return (stat.f_bavail * stat.f_frsize) / (1024 ** 3)


def resolve_block_stat_path(device: str | None) -> Path | None:
    if not device:
        return None
    sys_path = Path("/sys/dev/block") / device
    try:
        resolved = sys_path.resolve(strict=True)
    except FileNotFoundError:
        return None
    stat_path = Path("/sys/class/block") / resolved.name / "stat"
    return stat_path if stat_path.exists() else None


def read_block_stats(stat_path: Path | None) -> dict[str, int] | None:
    if stat_path is None:
        return None
    try:
        fields = [int(part) for part in stat_path.read_text().split()]
    except Exception:
        return None
    if len(fields) < 11:
        return None
    return {
        "read_ios": fields[0],
        "read_sectors": fields[2],
        "read_ms": fields[3],
        "write_ios": fields[4],
        "write_sectors": fields[6],
        "write_ms": fields[7],
        "in_flight": fields[8],
        "io_ms": fields[9],
        "weighted_io_ms": fields[10],
    }


def compute_w_await_ms(
    previous: dict[str, int] | None,
    current: dict[str, int] | None,
) -> float:
    if previous is None or current is None:
        return 0.0
    delta_writes = current["write_ios"] - previous["write_ios"]
    delta_write_ms = current["write_ms"] - previous["write_ms"]
    if delta_writes <= 0 or delta_write_ms < 0:
        return 0.0
    return delta_write_ms / delta_writes


def compute_device_metrics(
    previous: dict[str, int] | None,
    current: dict[str, int] | None,
    elapsed_seconds: float,
) -> dict[str, float]:
    metrics = {
        "w_await_ms": 0.0,
        "util_pct": 0.0,
        "avg_qdepth": 0.0,
        "read_mib_s": 0.0,
        "write_mib_s": 0.0,
    }
    if previous is None or current is None or elapsed_seconds <= 0:
        return metrics

    elapsed_ms = elapsed_seconds * 1000.0
    delta_io_ms = current["io_ms"] - previous["io_ms"]
    delta_weighted_io_ms = current["weighted_io_ms"] - previous["weighted_io_ms"]
    delta_read_sectors = current["read_sectors"] - previous["read_sectors"]
    delta_write_sectors = current["write_sectors"] - previous["write_sectors"]

    metrics["w_await_ms"] = compute_w_await_ms(previous, current)
    if delta_io_ms > 0:
        metrics["util_pct"] = min(100.0, (delta_io_ms / elapsed_ms) * 100.0)
    if delta_weighted_io_ms > 0:
        metrics["avg_qdepth"] = delta_weighted_io_ms / elapsed_ms
    if delta_read_sectors > 0:
        metrics["read_mib_s"] = (delta_read_sectors * 512.0) / (1024.0 ** 2) / elapsed_seconds
    if delta_write_sectors > 0:
        metrics["write_mib_s"] = (delta_write_sectors * 512.0) / (1024.0 ** 2) / elapsed_seconds
    return metrics


def pressure_value(pressure: dict[str, dict[str, float]], scope: str, metric: str) -> float:
    return pressure.get(scope, {}).get(metric, 0.0)


def is_safe_legacy(
    pressure: dict[str, dict[str, float]],
    free_space_gb: float,
    w_await_ms: float,
    args: argparse.Namespace,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    some_avg10 = pressure_value(pressure, "some", "avg10")
    some_avg60 = pressure_value(pressure, "some", "avg60")
    full_avg10 = pressure_value(pressure, "full", "avg10")
    full_avg60 = pressure_value(pressure, "full", "avg60")

    if some_avg10 > args.max_some_avg10:
        reasons.append(f"global_some_avg10={some_avg10:.2f}>{args.max_some_avg10:.2f}")
    if some_avg60 > args.max_some_avg60:
        reasons.append(f"global_some_avg60={some_avg60:.2f}>{args.max_some_avg60:.2f}")
    if full_avg10 > args.max_full_avg10:
        reasons.append(f"global_full_avg10={full_avg10:.2f}>{args.max_full_avg10:.2f}")
    if full_avg60 > args.max_full_avg60:
        reasons.append(f"global_full_avg60={full_avg60:.2f}>{args.max_full_avg60:.2f}")
    if args.max_w_await_ms > 0 and w_await_ms > args.max_w_await_ms:
        reasons.append(f"device_w_await={w_await_ms:.2f}>{args.max_w_await_ms:.2f}")
    if free_space_gb < args.min_free_gb:
        reasons.append(f"free_gb={free_space_gb:.1f}<{args.min_free_gb:.1f}")

    return not reasons, reasons


def is_safe_prelaunch(
    pressure: dict[str, dict[str, float]],
    free_space_gb: float,
    device_metrics: dict[str, float],
    device_metrics_available: bool,
    args: argparse.Namespace,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    some_avg10 = pressure_value(pressure, "some", "avg10")
    some_avg60 = pressure_value(pressure, "some", "avg60")
    full_avg10 = pressure_value(pressure, "full", "avg10")
    full_avg60 = pressure_value(pressure, "full", "avg60")

    if free_space_gb < args.min_free_gb:
        reasons.append(f"free_gb={free_space_gb:.1f}<{args.min_free_gb:.1f}")

    if args.hard_max_some_avg10 > 0 and some_avg10 > args.hard_max_some_avg10:
        reasons.append(f"global_some_avg10={some_avg10:.2f}>{args.hard_max_some_avg10:.2f}")
    if args.hard_max_some_avg60 > 0 and some_avg60 > args.hard_max_some_avg60:
        reasons.append(f"global_some_avg60={some_avg60:.2f}>{args.hard_max_some_avg60:.2f}")
    if args.hard_max_full_avg10 > 0 and full_avg10 > args.hard_max_full_avg10:
        reasons.append(f"global_full_avg10={full_avg10:.2f}>{args.hard_max_full_avg10:.2f}")
    if args.hard_max_full_avg60 > 0 and full_avg60 > args.hard_max_full_avg60:
        reasons.append(f"global_full_avg60={full_avg60:.2f}>{args.hard_max_full_avg60:.2f}")

    if device_metrics_available:
        if args.max_device_w_await_ms > 0 and device_metrics["w_await_ms"] > args.max_device_w_await_ms:
            reasons.append(
                f"device_w_await={device_metrics['w_await_ms']:.2f}>{args.max_device_w_await_ms:.2f}"
            )
        if args.max_device_util_pct > 0 and device_metrics["util_pct"] > args.max_device_util_pct:
            reasons.append(
                f"device_util={device_metrics['util_pct']:.2f}>{args.max_device_util_pct:.2f}"
            )
        if args.max_device_qdepth > 0 and device_metrics["avg_qdepth"] > args.max_device_qdepth:
            reasons.append(
                f"device_qdepth={device_metrics['avg_qdepth']:.2f}>{args.max_device_qdepth:.2f}"
            )
    elif args.fallback_to_global_if_device_missing:
        safe, fallback_reasons = is_safe_legacy(
            pressure=pressure,
            free_space_gb=free_space_gb,
            w_await_ms=device_metrics["w_await_ms"],
            args=args,
        )
        if not safe:
            reasons.extend(f"fallback_{reason}" for reason in fallback_reasons)

    return not reasons, reasons


def is_safe(
    pressure: dict[str, dict[str, float]],
    free_space_gb: float,
    device_metrics: dict[str, float],
    device_metrics_available: bool,
    args: argparse.Namespace,
) -> tuple[bool, list[str]]:
    if args.mode == "prelaunch":
        return is_safe_prelaunch(
            pressure=pressure,
            free_space_gb=free_space_gb,
            device_metrics=device_metrics,
            device_metrics_available=device_metrics_available,
            args=args,
        )
    return is_safe_legacy(
        pressure=pressure,
        free_space_gb=free_space_gb,
        w_await_ms=device_metrics["w_await_ms"],
        args=args,
    )


def format_metrics(
    pressure: dict[str, dict[str, float]],
    free_space_gb: float,
    device_metrics: dict[str, float],
    device_metrics_available: bool,
) -> str:
    device_status = "ok" if device_metrics_available else "missing"
    return (
        f"global_some_avg10={pressure_value(pressure, 'some', 'avg10'):.2f} "
        f"global_some_avg60={pressure_value(pressure, 'some', 'avg60'):.2f} "
        f"global_full_avg10={pressure_value(pressure, 'full', 'avg10'):.2f} "
        f"global_full_avg60={pressure_value(pressure, 'full', 'avg60'):.2f} "
        f"device_metrics={device_status} "
        f"device_w_await={device_metrics['w_await_ms']:.2f} "
        f"device_util={device_metrics['util_pct']:.2f} "
        f"device_qdepth={device_metrics['avg_qdepth']:.2f} "
        f"device_read_mib_s={device_metrics['read_mib_s']:.2f} "
        f"device_write_mib_s={device_metrics['write_mib_s']:.2f} "
        f"free_gb={free_space_gb:.1f}"
    )


def wait_for_budget(args: argparse.Namespace) -> int:
    if not PRESSURE_FILE.exists():
        log(f"IO_GUARD_SKIP label={args.label} reason=missing_pressure_file path={PRESSURE_FILE}")
        return 0

    if not args.path.exists():
        log(f"IO_GUARD_SKIP label={args.label} reason=missing_path path={args.path}")
        return 0

    stable = 0
    start = time.monotonic()
    stat_path = resolve_block_stat_path(args.device)
    previous_block_stats = read_block_stats(stat_path)
    previous_sample_time = time.monotonic()

    while True:
        pressure = parse_pressure(PRESSURE_FILE)
        free_space_gb = free_gb(args.path)
        now = time.monotonic()
        current_block_stats = read_block_stats(stat_path)
        device_metrics = compute_device_metrics(
            previous=previous_block_stats,
            current=current_block_stats,
            elapsed_seconds=now - previous_sample_time,
        )
        previous_block_stats = current_block_stats
        previous_sample_time = now
        device_metrics_available = current_block_stats is not None
        safe, reasons = is_safe(
            pressure,
            free_space_gb,
            device_metrics,
            device_metrics_available,
            args,
        )
        metrics = format_metrics(pressure, free_space_gb, device_metrics, device_metrics_available)

        if safe:
            stable += 1
            if stable >= args.stable_samples:
                waited_s = time.monotonic() - start
                log(
                    f"IO_GUARD_READY label={args.label} mode={args.mode} waited_s={waited_s:.1f} "
                    f"stable={stable}/{args.stable_samples} {metrics}"
                )
                return 0
            log(
                f"IO_GUARD_STABLE label={args.label} mode={args.mode} "
                f"stable={stable}/{args.stable_samples} {metrics}"
            )
        else:
            stable = 0
            waited_s = time.monotonic() - start
            log(
                f"IO_GUARD_WAIT label={args.label} mode={args.mode} waited_s={waited_s:.1f} "
                f"reason={'|'.join(reasons)} {metrics}"
            )

        if args.max_wait_seconds > 0 and (time.monotonic() - start) >= args.max_wait_seconds:
            log(
                f"IO_GUARD_TIMEOUT label={args.label} mode={args.mode} max_wait_s={args.max_wait_seconds} {metrics}"
            )
            return 1

        time.sleep(args.sample_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wait until global IO pressure is below safety thresholds."
    )
    parser.add_argument("--label", default="task", help="Label written into logs.")
    parser.add_argument(
        "--mode",
        choices=("legacy", "prelaunch", "teardown"),
        default="legacy",
        help="Guard mode. prelaunch prefers device-local metrics; teardown/legacy use conservative global checks.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("/mnt/data"),
        help="Filesystem path whose free space should be checked.",
    )
    parser.add_argument("--sample-seconds", type=float, default=5.0)
    parser.add_argument("--stable-samples", type=int, default=2)
    parser.add_argument("--max-wait-seconds", type=float, default=0.0)
    parser.add_argument("--max-some-avg10", type=float, default=1.0)
    parser.add_argument("--max-some-avg60", type=float, default=2.0)
    parser.add_argument("--max-full-avg10", type=float, default=0.2)
    parser.add_argument("--max-full-avg60", type=float, default=0.5)
    parser.add_argument("--device", default="", help="Block device major:minor used for optional w_await checks.")
    parser.add_argument("--max-w-await-ms", type=float, default=0.0)
    parser.add_argument("--max-device-w-await-ms", type=float, default=0.0)
    parser.add_argument("--max-device-util-pct", type=float, default=0.0)
    parser.add_argument("--max-device-qdepth", type=float, default=0.0)
    parser.add_argument("--hard-max-some-avg10", type=float, default=0.0)
    parser.add_argument("--hard-max-some-avg60", type=float, default=0.0)
    parser.add_argument("--hard-max-full-avg10", type=float, default=0.0)
    parser.add_argument("--hard-max-full-avg60", type=float, default=0.0)
    parser.add_argument(
        "--fallback-to-global-if-device-missing",
        type=int,
        choices=(0, 1),
        default=1,
        help="If device stats cannot be read in prelaunch mode, fall back to legacy global thresholds.",
    )
    parser.add_argument("--min-free-gb", type=float, default=50.0)
    args = parser.parse_args()

    if args.stable_samples < 1:
        print("--stable-samples must be >= 1", file=sys.stderr)
        return 2
    if args.sample_seconds <= 0:
        print("--sample-seconds must be > 0", file=sys.stderr)
        return 2

    return wait_for_budget(args)


if __name__ == "__main__":
    raise SystemExit(main())
