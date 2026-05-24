#!/usr/bin/env python3

import argparse
import json
import math
import os
import signal
import sys
import time
from pathlib import Path


STOP = False


def handle_stop(_signum, _frame):
    global STOP
    STOP = True


def getenv_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def getenv_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def read_io_full_avg10() -> float:
    try:
        for line in Path("/proc/pressure/io").read_text().splitlines():
            if line.startswith("full "):
                parts = dict(part.split("=", 1) for part in line.split()[1:])
                return float(parts["avg10"])
    except Exception:
        return 0.0
    return 0.0


def resolve_block_stat_path(device: str) -> Path | None:
    sys_path = Path("/sys/dev/block") / device
    try:
        resolved = sys_path.resolve(strict=True)
    except FileNotFoundError:
        return None
    stat_path = Path("/sys/class/block") / resolved.name / "stat"
    return stat_path if stat_path.exists() else None


def read_write_stats(stat_path: Path | None) -> tuple[int, int] | None:
    if stat_path is None:
        return None
    try:
        fields = [int(part) for part in stat_path.read_text().split()]
    except Exception:
        return None
    if len(fields) < 8:
        return None
    return fields[4], fields[7]


def compute_w_await_ms(previous: tuple[int, int] | None, current: tuple[int, int] | None) -> float:
    if previous is None or current is None:
        return 0.0
    delta_writes = current[0] - previous[0]
    delta_write_ms = current[1] - previous[1]
    if delta_writes <= 0 or delta_write_ms < 0:
        return 0.0
    return delta_write_ms / delta_writes


def write_io_max(io_max_path: Path, device: str, rbps: str, wbps: int, riops: str, wiops: str) -> None:
    io_max_path.write_text(
        f"{device} rbps={rbps} wbps={wbps} riops={riops} wiops={wiops}\n"
    )


def write_state(state_path: Path, payload: dict) -> None:
    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, sort_keys=True) + "\n")
    tmp_path.replace(state_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Adaptive wbps controller for a cgroup v2 io.max budget.")
    parser.add_argument("--cgroup-path", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--emergency-flag-path", required=True)
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    cgroup_path = Path(args.cgroup_path)
    io_max_path = cgroup_path / "io.max"
    state_path = Path(args.state_path)
    emergency_flag_path = Path(args.emergency_flag_path)

    interval_seconds = getenv_int("IO_ADAPTIVE_INTERVAL_SECONDS", 5)
    min_wbps = getenv_int("IO_ADAPTIVE_MIN_WBPS", 10485760)
    max_wbps = getenv_int("IO_ADAPTIVE_MAX_WBPS", 25165824)
    initial_wbps = clamp(getenv_int("IO_ADAPTIVE_INITIAL_WBPS", 12582912), min_wbps, max_wbps)
    down_pct = getenv_int("IO_ADAPTIVE_DOWN_PCT", 25)
    up_pct = getenv_int("IO_ADAPTIVE_UP_PCT", 10)
    pressure_high = getenv_float("IO_ADAPTIVE_PRESSURE_HIGH_FULL_AVG10", 3.0)
    pressure_emergency = getenv_float("IO_ADAPTIVE_PRESSURE_EMERGENCY_FULL_AVG10", 5.0)
    w_await_high = getenv_float("IO_ADAPTIVE_W_AWAIT_HIGH_MS", 30.0)
    w_await_emergency = getenv_float("IO_ADAPTIVE_W_AWAIT_EMERGENCY_MS", 80.0)
    healthy_pressure = getenv_float("IO_ADAPTIVE_HEALTHY_FULL_AVG10", 0.8)
    healthy_w_await = getenv_float("IO_ADAPTIVE_HEALTHY_W_AWAIT_MS", 8.0)
    healthy_samples_for_up = getenv_int("IO_ADAPTIVE_HEALTHY_SAMPLES_FOR_UP", 4)
    healthy_seconds_to_exit_emergency = getenv_int("IO_ADAPTIVE_HEALTHY_SECONDS_TO_EXIT_EMERGENCY", 30)

    rbps = os.environ.get("IO_MAX_RBPS", "max")
    riops = os.environ.get("IO_MAX_RIOPS", "max")
    wiops = os.environ.get("IO_MAX_WIOPS", "max")

    stat_path = resolve_block_stat_path(args.device)
    current_wbps = initial_wbps
    emergency = False
    emergency_bad_streak = 0
    healthy_streak = 0
    healthy_seconds_in_emergency = 0
    previous_write_stats = read_write_stats(stat_path)

    write_io_max(io_max_path, args.device, rbps, current_wbps, riops, wiops)
    print(
        f"[IO_CGROUP_ADAPTIVE] start cgroup={cgroup_path} device={args.device} "
        f"wbps={current_wbps} stat_path={stat_path or 'unavailable'}",
        flush=True,
    )

    try:
        while not STOP:
            time.sleep(interval_seconds)
            full_avg10 = read_io_full_avg10()
            current_write_stats = read_write_stats(stat_path)
            w_await_ms = compute_w_await_ms(previous_write_stats, current_write_stats)
            previous_write_stats = current_write_stats

            is_healthy = full_avg10 <= healthy_pressure and w_await_ms <= healthy_w_await
            is_high = full_avg10 >= pressure_high or w_await_ms >= w_await_high
            is_emergency = full_avg10 >= pressure_emergency or w_await_ms >= w_await_emergency

            emergency_bad_streak = emergency_bad_streak + 1 if is_emergency else 0
            next_wbps = current_wbps
            action = "hold"

            if emergency_bad_streak >= 2:
                emergency = True
                healthy_seconds_in_emergency = 0
                healthy_streak = 0
                next_wbps = min_wbps
                action = "emergency_floor"
            elif emergency:
                next_wbps = min_wbps
                if is_healthy:
                    healthy_seconds_in_emergency += interval_seconds
                    if healthy_seconds_in_emergency >= healthy_seconds_to_exit_emergency:
                        emergency = False
                        healthy_seconds_in_emergency = 0
                        emergency_bad_streak = 0
                        action = "exit_emergency"
                else:
                    healthy_seconds_in_emergency = 0
            elif is_high:
                healthy_streak = 0
                next_wbps = max(min_wbps, math.floor(current_wbps * (100 - down_pct) / 100))
                action = "decrease"
            elif is_healthy:
                healthy_streak += 1
                if healthy_streak >= healthy_samples_for_up:
                    next_wbps = min(max_wbps, math.ceil(current_wbps * (100 + up_pct) / 100))
                    healthy_streak = 0
                    action = "increase"
            else:
                healthy_streak = 0

            next_wbps = clamp(next_wbps, min_wbps, max_wbps)
            if next_wbps != current_wbps:
                write_io_max(io_max_path, args.device, rbps, next_wbps, riops, wiops)
                current_wbps = next_wbps
                print(
                    f"[IO_CGROUP_ADAPTIVE] action={action} wbps={current_wbps} "
                    f"full_avg10={full_avg10:.2f} w_await_ms={w_await_ms:.2f} emergency={int(emergency)}",
                    flush=True,
                )

            if emergency:
                emergency_flag_path.write_text("1\n")
            elif emergency_flag_path.exists():
                emergency_flag_path.unlink()

            write_state(
                state_path,
                {
                    "action": action,
                    "current_wbps": current_wbps,
                    "device": args.device,
                    "emergency": emergency,
                    "emergency_bad_streak": emergency_bad_streak,
                    "full_avg10": round(full_avg10, 4),
                    "healthy_seconds_in_emergency": healthy_seconds_in_emergency,
                    "healthy_streak": healthy_streak,
                    "stat_path": str(stat_path) if stat_path else None,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "w_await_ms": round(w_await_ms, 4),
                },
            )
    finally:
        if emergency_flag_path.exists():
            emergency_flag_path.unlink()

    return 0


if __name__ == "__main__":
    sys.exit(main())
