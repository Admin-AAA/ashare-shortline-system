#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def _wait_for_url(url: str, timeout_seconds: float) -> float:
    started_at = time.perf_counter()
    deadline = started_at + timeout_seconds
    last_error = ""
    while time.perf_counter() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return time.perf_counter() - started_at
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(0.1)
    raise TimeoutError(f"wait for {url} timed out: {last_error}")


def _wait_for_tcp(host: str, port: int, timeout_seconds: float) -> float:
    started_at = time.perf_counter()
    deadline = started_at + timeout_seconds
    last_error = ""
    while time.perf_counter() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        try:
            sock.connect((host, port))
            return time.perf_counter() - started_at
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(0.1)
        finally:
            sock.close()
    raise TimeoutError(f"wait for tcp://{host}:{port} timed out: {last_error}")


def _get_json(url: str, timeout_seconds: float = 120.0) -> tuple[dict | list, float]:
    started_at = time.perf_counter()
    with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload, time.perf_counter() - started_at


def _start_process(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _terminate_process(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _measure_page(frontend_url: str, backend_root: Path) -> dict[str, object]:
    playwright_entry = backend_root / "node_modules" / "playwright" / "index.mjs"
    if not playwright_entry.exists():
        return {"skipped": True, "reason": f"playwright not found: {playwright_entry}"}

    js = """
import {{ chromium }} from {playwright};

const url = {url};
const startedAt = Date.now();
const browser = await chromium.launch({{ headless: true }});
const page = await browser.newPage();
await page.goto(url, {{ waitUntil: "domcontentloaded", timeout: 30000 }});
await page.waitForSelector(".desk-grid", {{ timeout: 30000 }});
await page.waitForSelector(".run-btn", {{ timeout: 30000 }});
const elapsedSeconds = (Date.now() - startedAt) / 1000;
const resources = await page.evaluate(() =>
  performance
    .getEntriesByType("resource")
    .filter((entry) => entry.name.includes("/api/") || entry.initiatorType === "script")
    .map((entry) => ({{
      name: entry.name,
      initiatorType: entry.initiatorType,
      durationMs: Math.round(entry.duration),
    }}))
);
await browser.close();
console.log(JSON.stringify({{
  skipped: false,
  elapsed_seconds: Number(elapsedSeconds.toFixed(3)),
  resources,
}}, null, 2));
""".format(
        playwright=json.dumps(playwright_entry.as_uri()),
        url=json.dumps(frontend_url),
    )
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", js],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _measure_first_api_wave(backend_root: Path, backend_python: Path, backend_env: dict[str, str], port: int, timeout: float) -> dict[str, object]:
    backend_proc: subprocess.Popen | None = None
    try:
        backend_proc = _start_process(
            [
                str(backend_python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=backend_root,
            env=backend_env,
        )
        ready_seconds = _wait_for_url(f"http://127.0.0.1:{port}/api/health", timeout_seconds=timeout)
        auth_payload, auth_seconds = _get_json(f"http://127.0.0.1:{port}/api/auth/status")
        initial_payload, initial_seconds = _get_json(f"http://127.0.0.1:{port}/api/home/initial")
        return {
            "backend_ready_seconds": round(ready_seconds, 3),
            "auth_seconds": round(auth_seconds, 3),
            "initial_seconds": round(initial_seconds, 3),
            "total_seconds": round(auth_seconds + initial_seconds, 3),
            "auth_enabled": bool(auth_payload.get("enabled")),
            "preset_count": len(initial_payload.get("presets") or []),
            "run_count": len(initial_payload.get("runs") or []),
            "skill_count": len(initial_payload.get("skills") or []),
            "data_source_count": len(initial_payload.get("data_sources") or []),
        }
    finally:
        _terminate_process(backend_proc)


def _measure_first_page(
    backend_root: Path,
    frontend_root: Path,
    backend_python: Path,
    backend_env: dict[str, str],
    frontend_env: dict[str, str],
    backend_port: int,
    frontend_port: int,
    timeout: float,
) -> dict[str, object]:
    backend_proc: subprocess.Popen | None = None
    frontend_proc: subprocess.Popen | None = None
    try:
        backend_proc = _start_process(
            [
                str(backend_python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(backend_port),
            ],
            cwd=backend_root,
            env=backend_env,
        )
        backend_ready_seconds = _wait_for_url(
            f"http://127.0.0.1:{backend_port}/api/health",
            timeout_seconds=timeout,
        )
        frontend_proc = _start_process(
            ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(frontend_port)],
            cwd=frontend_root,
            env=frontend_env,
        )
        frontend_ready_seconds = _wait_for_tcp("127.0.0.1", frontend_port, timeout_seconds=timeout)
        page_result = _measure_page(f"http://127.0.0.1:{frontend_port}", backend_root)
        return {
            "backend_ready_seconds": round(backend_ready_seconds, 3),
            "frontend_ready_seconds": round(frontend_ready_seconds, 3),
            "frontend_port": frontend_port,
            **page_result,
        }
    finally:
        _terminate_process(frontend_proc)
        _terminate_process(backend_proc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure cold-start first-visit latency.")
    parser.add_argument("--backend-port", type=int, default=18765)
    parser.add_argument("--frontend-port", type=int, default=15173)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()

    backend_root = Path(__file__).resolve().parents[1]
    project_root = backend_root.parent
    frontend_root = project_root / "frontend"
    backend_python = backend_root / ".venv" / "bin" / "python"

    if not backend_python.exists():
        raise FileNotFoundError(f"backend venv python not found: {backend_python}")

    backend_env = os.environ.copy()
    frontend_env = os.environ.copy()
    frontend_env["ASHARE_API_PROXY"] = f"http://127.0.0.1:{args.backend_port}"

    result = {
        "backend_port": args.backend_port,
        "first_api_wave": _measure_first_api_wave(
            backend_root=backend_root,
            backend_python=backend_python,
            backend_env=backend_env,
            port=args.backend_port,
            timeout=args.timeout,
        ),
        "first_page": (
            {"skipped": True, "reason": "frontend skipped"}
            if args.skip_frontend
            else _measure_first_page(
                backend_root=backend_root,
                frontend_root=frontend_root,
                backend_python=backend_python,
                backend_env=backend_env,
                frontend_env=frontend_env,
                backend_port=args.backend_port,
                frontend_port=args.frontend_port,
                timeout=args.timeout,
            )
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
