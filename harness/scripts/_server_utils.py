from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def pids_listening_on_port(port: int) -> list[int]:
    completed = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    pids: set[int] = set()
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line or "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local_address = parts[1]
        state = parts[3]
        pid = parts[4]
        if state != "LISTENING":
            continue
        if local_address.endswith(f":{port}"):
            try:
                pids.add(int(pid))
            except ValueError:
                continue
    return sorted(pids)


def kill_process_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def ensure_port_free(port: int, owner_pid: int | None = None, timeout_sec: float = 15.0) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        pids = pids_listening_on_port(port)
        if owner_pid is not None:
            pids = [pid for pid in pids if pid != owner_pid]
        if not pids:
            return
        for pid in pids:
            kill_process_tree(pid)
        time.sleep(0.5)
    remaining = pids_listening_on_port(port)
    if owner_pid is not None:
        remaining = [pid for pid in remaining if pid != owner_pid]
    if remaining:
        raise RuntimeError(f"port {port} is still busy: {remaining}")


def wait_for_port(host: str, port: int, timeout_sec: float = 45.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.5)
    return False


def start_server(port: int, stdout_path: Path, stderr_path: Path) -> subprocess.Popen:
    ensure_port_free(port)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_handle = stdout_path.open("w", encoding="utf-8")
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            str(PYTHON),
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        stdout=stdout_handle,
        stderr=stderr_handle,
        text=True,
    )
    process._codex_stdout_handle = stdout_handle
    process._codex_stderr_handle = stderr_handle
    process._codex_port = port
    if process.poll() is not None:
        stop_server(process)
        raise RuntimeError(f"server exited before binding to port {port}")
    if not wait_for_port("127.0.0.1", port):
        stop_server(process)
        raise RuntimeError(f"server did not start on port {port}")
    return process


def stop_server(process: subprocess.Popen | None) -> None:
    if not process:
        return
    try:
        if process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        port = getattr(process, "_codex_port", None)
        if port:
            ensure_port_free(int(port), owner_pid=process.pid)
    finally:
        for attr in ("_codex_stdout_handle", "_codex_stderr_handle"):
            handle = getattr(process, attr, None)
            if handle and not handle.closed:
                handle.close()
