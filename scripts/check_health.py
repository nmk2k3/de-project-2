#!/usr/bin/env python3
"""Read-only systemd, crawler-progress and disk check; standard library only."""
import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.health import STATUS_FILE, assess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--service", default="de-project-2-etl.service")
    parser.add_argument("--stall-after", type=float, default=900, help="seconds (default: 900)")
    parser.add_argument("--grace", type=float, default=60, help="seconds beyond expected waits")
    parser.add_argument("--min-free-mib", type=int, default=1024)
    args = parser.parse_args()
    if (not math.isfinite(args.stall_after) or args.stall_after <= 0
            or not math.isfinite(args.grace) or args.grace < 0 or args.min_free_mib < 0):
        parser.error("thresholds must be finite and nonnegative; stall-after must be positive")
    try:
        paths = [args.root] + [path for path in (args.root / "output", args.root / "logs")
                               if path.exists()]
        free = min(shutil.disk_usage(path).free for path in paths)
        # Report low disk even if systemd is unavailable.
        if free < args.min_free_mib * 1024 ** 2:
            print(f"LOW_DISK: {free} bytes free")
            return 2
        result = subprocess.run(
            ["systemctl", "show", "--", args.service],
            capture_output=True, text=True, timeout=10, check=True,
        )
        service = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if service.get("LoadState") != "loaded":
            print("UNKNOWN: service not loaded")
            return 3
        try:
            status = json.loads((args.root / STATUS_FILE).read_text(encoding="utf-8"))
            if not isinstance(status, dict):
                status = {}
        except (OSError, ValueError):
            status = {}
        code, message = assess(service, status, now=time.time(), free_bytes=free,
                               min_free_bytes=args.min_free_mib * 1024 ** 2,
                               stall_after=args.stall_after, grace=args.grace)
        print(message)
        return code
    except (OSError, subprocess.SubprocessError) as error:
        print(f"UNKNOWN: {error}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
