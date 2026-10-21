#!/usr/bin/env python3
"""Container HEALTHCHECK (V1-T43): reads the status JSON the client writes
every watchdog tick (CTRADER_HEALTH_FILE, see csys/ctrader_api/client.py).

Unhealthy (exit 1) when:
  - the file is missing or older than STALE_S  → event loop hung / not started
  - ``fatal`` is set                            → unrecoverable auth error
  - disconnected for longer than DOWN_S         → network / broker outage
Being connected without live bars is fine (weekends, market closed); the
client's own watchdog already forces reconnects on silent sockets.
Stdlib only; prints one line (shown by ``docker inspect``).
"""

import json
import os
import sys
import time

STALE_S = 60.0
DOWN_S = 300.0


def main() -> int:
    path = os.getenv("CTRADER_HEALTH_FILE", "/app/state/health.json")
    try:
        with open(path) as f:
            h = json.load(f)
    except (OSError, ValueError) as e:
        print(f"unhealthy: no health file ({e})")
        return 1
    now = time.time()
    age = now - float(h.get("ts", 0))
    if age > STALE_S:
        print(f"unhealthy: health file {age:.0f}s old (event loop stuck?)")
        return 1
    if h.get("fatal"):
        print(f"unhealthy: fatal: {h['fatal']}")
        return 1
    down = h.get("down_since")
    if not h.get("connected") and down and now - down > DOWN_S:
        print(f"unhealthy: disconnected for {now - down:.0f}s")
        return 1
    print(
        f"healthy: connected={h.get('connected')} ready={h.get('ready')} "
        f"trading={h.get('trading')} since_rx={h.get('since_rx')}s connects={h.get('connects')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
