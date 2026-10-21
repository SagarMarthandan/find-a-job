from __future__ import annotations

import argparse
import json
import os
import sys
import time
import threading
import urllib.error
import urllib.request
import webbrowser


from pathlib import Path
from typing import Any

def _missing_packages() -> list[str]:
    import importlib.util

    modules = ("fastapi", "uvicorn", "multipart", "pypdf", "docx", "openpyxl", "pptx", "striprtf", "reportlab", "playwright")
    return [name for name in modules if importlib.util.find_spec(name) is None]


def _check_chromium() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            installed = Path(playwright.chromium.executable_path).is_file()
        print(f"Playwright Chromium: {'installed' if installed else 'missing; run uv run playwright install chromium'}")
        return installed
    except Exception as exc:
        print(f"Playwright Chromium: unavailable ({exc})")
        return False


def _doctor(_: argparse.Namespace) -> None:
    missing = _missing_packages()
    python_ok = sys.version_info[:2] == (3, 12)
    display_ok = bool(os.environ.get("DISPLAY"))
    print(f"Python: {sys.version.split()[0]} ({'supported' if python_ok else 'requires Python 3.12'})")
    print(f"DISPLAY: {os.environ.get('DISPLAY') or 'missing; headed browser unavailable'}")
    print(f"Dependencies: {'installed' if not missing else 'missing ' + ', '.join(missing)}")
    chromium_ok = _check_chromium()
    if missing or not python_ok or not display_ok or not chromium_ok:
        raise SystemExit(1)



def _data_dir() -> Path:
    return Path(os.path.expandvars(os.environ.get("FIND_A_JOB_DATA_DIR", "~/.local/share/find-a-job"))).expanduser().resolve()


def _token() -> str:
    try:
        return (_data_dir() / "session-token").read_text().strip()
    except OSError as exc:
        raise SystemExit("Local service has not created a session yet; run `find-a-job serve` first.") from exc


def _request(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(f"http://127.0.0.1:8765{path}", data=body,
                                     headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Service rejected request ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach local service: {exc.reason}") from exc


def _serve(args: argparse.Namespace) -> None:
    import uvicorn
    from .api import app, _initialize

    _initialize()
    url = f"http://127.0.0.1:8765/#token={(_data_dir() / 'session-token').read_text().strip()}"
    print("Local dashboard starting at http://127.0.0.1:8765 (session token remains private).", flush=True)
    if args.open:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning", access_log=False)


def _claim(args: argparse.Namespace) -> None:
    while True:
        result = _request("/api/harness/claim", {"worker_id": args.worker_id, "model": args.model})
        task = result.get("task")
        if task:
            print(json.dumps(task, ensure_ascii=False, indent=2))
            return
        if not args.wait:
            print(json.dumps({"task": None}))
            return
        time.sleep(args.interval)


def _complete(args: argparse.Namespace) -> None:
    try:
        result = json.loads(Path(args.result_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read result JSON: {exc}") from exc
    response = _request(f"/api/harness/tasks/{args.task_id}/complete", {"worker_id": args.worker_id, "result": result})
    print(json.dumps(response, ensure_ascii=False, indent=2))


def _fail(args: argparse.Namespace) -> None:
    result = _request(f"/api/harness/tasks/{args.task_id}/fail", {"worker_id": args.worker_id, "error": args.error})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _task(args: argparse.Namespace) -> None:
    print(json.dumps(_request("/api/state"), ensure_ascii=False, indent=2))


def _context(args: argparse.Namespace) -> None:
    from .cache import ContextUnavailableError, context_cached

    try:
        payload = context_cached(_data_dir(), args.intake_id, refresh=args.refresh)
    except ContextUnavailableError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _heartbeat(_: argparse.Namespace) -> None:
    print(json.dumps(_request("/api/harness/heartbeat", {}), ensure_ascii=False))


def _open_when_ready(url: str) -> bool:
    for _ in range(300):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=0.2):
                return webbrowser.open(url)
        except urllib.error.URLError:
            time.sleep(0.1)
    return False


def _open(_: argparse.Namespace) -> None:
    token = _token()
    if not _open_when_ready(f"http://127.0.0.1:8765/#token={token}"):
        raise SystemExit("Local service is not ready or no browser could be opened.")



def main() -> None:
    parser = argparse.ArgumentParser(prog="find-a-job", description="Local-first, harness-driven job research and resume workflow")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start authenticated localhost dashboard and task service")
    serve.add_argument("--open", action="store_true", help="Open the dashboard in the default browser")
    serve.set_defaults(run=_serve)
    claim = commands.add_parser("claim", help="Claim one queued model task and print its exact structured input")
    claim.add_argument("--worker-id", required=True)
    claim.add_argument("--model", help="Model identifier selected by your host; never changes model selection")
    claim.add_argument("--wait", action="store_true", help="Wait until a model task is available")
    claim.add_argument("--interval", type=float, default=2.0)
    claim.set_defaults(run=_claim)
    complete = commands.add_parser("complete", help="Submit validated model output from a JSON file")
    complete.add_argument("--worker-id", required=True)
    complete.add_argument("--task-id", required=True)
    complete.add_argument("--result-file", required=True)
    complete.set_defaults(run=_complete)
    fail = commands.add_parser("fail", help="Mark an owned model task failed with an actionable error")
    fail.add_argument("--worker-id", required=True)
    fail.add_argument("--task-id", required=True)
    fail.add_argument("--error", required=True)
    fail.set_defaults(run=_fail)
    state = commands.add_parser("state", help="Print authenticated task state")
    state.set_defaults(run=_task)
    context = commands.add_parser("context", help="Print an approved intake's local inference context without starting the service")
    context.add_argument("--intake-id", required=True)
    context.add_argument("--refresh", action="store_true", help="Rebuild the cached context for its current approved revision")
    context.set_defaults(run=_context)
    heartbeat = commands.add_parser("heartbeat", help="Refresh harness connection status during long-running model work")
    heartbeat.set_defaults(run=_heartbeat)
    doctor = commands.add_parser("doctor", help="Check required packages, DISPLAY, and Playwright Chromium")
    doctor.set_defaults(run=_doctor)
    open_dashboard = commands.add_parser("open", help="Open dashboard with the locally stored session token")
    open_dashboard.set_defaults(run=_open)
    args = parser.parse_args()
    if getattr(args, "interval", 1) <= 0:
        parser.error("--interval must be positive")
    args.run(args)


if __name__ == "__main__":
    main()
