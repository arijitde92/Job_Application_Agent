"""
app.cli
-------
Console entrypoints exposed via ``[project.scripts]`` in pyproject.toml.

Run from the ``backend/`` directory:

    uv run api          # start the FastAPI server (reload)
    uv run api-prod     # start the FastAPI server (no reload, multiple workers)
    uv run frontend     # start the Vite dev server (npm)
    uv run dev          # run db-proxy + api + frontend together
    uv run db-proxy     # start the Cloud SQL Auth Proxy (127.0.0.1:3306)
    uv run crew --url ...   # run the CrewAI pipeline CLI

These are thin wrappers around uvicorn / npm / cloud-sql-proxy so the long
invocations live in one place.
"""

import os
import subprocess
import sys
from pathlib import Path

# backend/app/cli.py → parents[1] == backend/, parents[2] == repo root
BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPO_ROOT / "frontend"

DEFAULT_API_HOST = os.environ.get("API_HOST", "127.0.0.1")
DEFAULT_API_PORT = os.environ.get("API_PORT", "8000")


def crew() -> None:
    """Run the CrewAI pipeline CLI (delegates to app.services.crew.__main__)."""
    from app.services.crew.__main__ import main

    main()


def _cloud_sql_proxy_cmd() -> tuple[list[str], str, str]:
    """
    Build the Cloud SQL Auth Proxy command from settings/env.

    The instance connection name is ``<project>:<region>:<instance>``. It is
    taken from CLOUD_SQL_INSTANCE if set, otherwise built from GCP_PROJECT_ID,
    GCP_LOCATION and CLOUD_SQL_INSTANCE_NAME (default: job-applier-mysql).

    Tunables (env vars):
        CLOUD_SQL_INSTANCE        full connection name (overrides the parts below)
        CLOUD_SQL_INSTANCE_NAME   instance id (default: job-applier-mysql)
        CLOUD_SQL_PROXY_BIN       proxy binary (default: cloud-sql-proxy)
        MYSQL_PORT                local port to bind (default: 3306)

    Returns:
        (cmd, instance_connection_name, port)

    Raises:
        SystemExit: if the instance connection name cannot be determined.
    """
    from app.core.config import get_settings

    settings = get_settings()

    instance = os.environ.get("CLOUD_SQL_INSTANCE")
    if not instance:
        project = settings.GCP_PROJECT_ID
        region = settings.GCP_LOCATION
        name = os.environ.get("CLOUD_SQL_INSTANCE_NAME", "job-applier-mysql")
        if not project or not region:
            print(
                "Cannot build the Cloud SQL instance connection name: set "
                "GCP_PROJECT_ID and GCP_LOCATION in .env, or set CLOUD_SQL_INSTANCE "
                "to '<project>:<region>:<instance>'.",
                file=sys.stderr,
            )
            sys.exit(1)
        instance = f"{project}:{region}:{name}"

    proxy_bin = os.environ.get("CLOUD_SQL_PROXY_BIN", "cloud-sql-proxy")
    port = str(settings.MYSQL_PORT)
    cmd = [proxy_bin, "--address=127.0.0.1", f"--port={port}", instance]
    return cmd, instance, port


def db_proxy() -> None:
    """
    Start the Cloud SQL Auth Proxy so the app can reach the MySQL instance on
    127.0.0.1:3306 (matching MYSQL_HOST/MYSQL_PORT in .env).
    """
    cmd, instance, port = _cloud_sql_proxy_cmd()
    print(f"Starting Cloud SQL Auth Proxy → 127.0.0.1:{port} for {instance}")
    try:
        sys.exit(subprocess.call(cmd))
    except FileNotFoundError:
        print(
            f"'{cmd[0]}' not found. Install the Cloud SQL Auth Proxy or set "
            "CLOUD_SQL_PROXY_BIN to its path.",
            file=sys.stderr,
        )
        sys.exit(1)
    except KeyboardInterrupt:
        pass


def api() -> None:
    """Start the FastAPI app with autoreload (development)."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=DEFAULT_API_HOST,
        port=int(DEFAULT_API_PORT),
        reload=True,
    )


def api_prod() -> None:
    """Start the FastAPI app without reload (production-style)."""
    import uvicorn

    workers = int(os.environ.get("API_WORKERS", "4"))
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("API_HOST", "0.0.0.0"),
        port=int(DEFAULT_API_PORT),
        workers=workers,
    )


def frontend() -> None:
    """Start the Vite dev server (npm run dev)."""
    if not (FRONTEND_DIR / "node_modules").exists():
        print("frontend/node_modules missing — run `npm install` in frontend/ first.", file=sys.stderr)
        sys.exit(1)
    sys.exit(subprocess.call(["npm", "run", "dev"], cwd=FRONTEND_DIR))


def _wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    """Block until ``host:port`` accepts a TCP connection, or timeout elapses."""
    import socket
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _spawn(cmd: list[str], cwd: Path) -> subprocess.Popen:
    """
    Start a child process in its own session/process group so that on teardown
    we can signal the whole group — npm spawns Vite, and uvicorn --reload spawns
    a worker, both of which would otherwise orphan and keep their ports bound.
    """
    return subprocess.Popen(cmd, cwd=cwd, start_new_session=True)


def _terminate(procs: list[subprocess.Popen]) -> None:
    """Stop each process and its whole group; escalate to SIGKILL if needed."""
    import signal as _signal
    import time

    for p in procs:
        if p.poll() is not None:
            continue
        try:
            os.killpg(os.getpgid(p.pid), _signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), _signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                p.kill()


def dev() -> None:
    """
    Run the Cloud SQL Auth Proxy, the API, and the frontend dev server together;
    Ctrl-C stops all three.

    The proxy starts first; the API only launches once 127.0.0.1:<MYSQL_PORT> is
    accepting connections, so its startup DB connection succeeds. Set
    DEV_SKIP_PROXY=1 to skip the proxy (e.g. when MYSQL_HOST is a direct IP).
    """
    import time

    if not (FRONTEND_DIR / "node_modules").exists():
        print("frontend/node_modules missing — run `npm install` in frontend/ first.", file=sys.stderr)
        sys.exit(1)

    skip_proxy = os.environ.get("DEV_SKIP_PROXY") in ("1", "true", "True")
    db_port = 3306

    procs: list[subprocess.Popen] = []
    try:
        # 1. Cloud SQL Auth Proxy (first, so the DB is reachable before the API).
        if not skip_proxy:
            proxy_cmd, instance, port_str = _cloud_sql_proxy_cmd()
            db_port = int(port_str)
            try:
                procs.append(_spawn(proxy_cmd, BACKEND_DIR))
            except FileNotFoundError:
                print(
                    f"'{proxy_cmd[0]}' not found. Install the Cloud SQL Auth Proxy, set "
                    "CLOUD_SQL_PROXY_BIN, or run with DEV_SKIP_PROXY=1.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"  DB proxy → 127.0.0.1:{db_port} for {instance}  (waiting…)")
            if not _wait_for_port("127.0.0.1", db_port):
                print(f"Cloud SQL proxy did not open 127.0.0.1:{db_port} in time.", file=sys.stderr)
                raise SystemExit(1)

        # 2. API: run via uvicorn in a subprocess so it sits alongside the frontend.
        procs.append(
            _spawn(
                [
                    sys.executable, "-m", "uvicorn", "app.main:app",
                    "--host", DEFAULT_API_HOST, "--port", DEFAULT_API_PORT, "--reload",
                ],
                BACKEND_DIR,
            )
        )
        # 3. Frontend dev server.
        procs.append(_spawn(["npm", "run", "dev"], FRONTEND_DIR))

        if not skip_proxy:
            print(f"\n  DB proxy → 127.0.0.1:{db_port}")
        print(f"  API      → http://{DEFAULT_API_HOST}:{DEFAULT_API_PORT}")
        print("  Frontend → http://localhost:5173")
        print("  Press Ctrl-C to stop all.\n")

        # Exit if any process dies.
        while True:
            for p in procs:
                code = p.poll()
                if code is not None:
                    raise SystemExit(code)
            time.sleep(0.5)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        _terminate(procs)
