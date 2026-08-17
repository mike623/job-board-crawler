"""Run the dashboard: `python -m dashboard`.

Binds to loopback by design. The service can start scans, so it must not be reachable from
the network; --host is deliberately not exposed.
"""
from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m dashboard", description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true", help="restart on code and template changes")
    args = parser.parse_args()

    print(f"Dashboard on http://127.0.0.1:{args.port}")
    uvicorn.run(
        "dashboard.app:app",
        host="127.0.0.1",
        port=args.port,
        reload=args.reload,
        reload_dirs=["dashboard"] if args.reload else None,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
