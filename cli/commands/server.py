from __future__ import annotations

from cli.output import info


def start(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn
    info(f"Starting Neamt dashboard on http://{host}:{port}")
    uvicorn.run("dashboard.server:app", host=host, port=port, reload=False)


def stop() -> None:
    import subprocess, signal, os
    ret = subprocess.run(
        ["pgrep", "-f", "dashboard.server:app"],
        capture_output=True, text=True
    )
    pids = ret.stdout.strip().splitlines()
    if not pids:
        from cli.output import warning
        warning("No dashboard process found")
        return
    for pid in pids:
        os.kill(int(pid), signal.SIGTERM)
    from cli.output import success
    success(f"Stopped {len(pids)} process(es)")


def restart(host: str = "127.0.0.1", port: int = 8000) -> None:
    stop()
    start(host, port)
