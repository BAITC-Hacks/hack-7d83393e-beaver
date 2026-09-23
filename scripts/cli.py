#!/usr/bin/env python3
"""Small local command entry point used by the README.

Commands are intentionally explicit.  ``bootstrap`` installs Python
dependencies only; model downloads remain a separate online step.  ``doctor``
and ``test`` are local checks.  The ``e2e`` command runs only the available
fixture smoke test and labels that limitation in its output.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / ".run" / "uvicorn.pid"


def _run(command: list[str], *, env: dict[str, str] | None = None) -> int:
    process = subprocess.run(command, cwd=ROOT, env=env, check=False)
    return process.returncode


def _json_from_output(output: str, *, fallback: dict) -> dict:
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return fallback
    return value if isinstance(value, dict) else fallback


def _bootstrap(args: argparse.Namespace) -> int:
    if args.no_install:
        print("bootstrap: установка пропущена (--no-install)")
        return 0
    return _run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])


def _doctor(args: argparse.Namespace) -> int:
    preflight = subprocess.run(
        [sys.executable, "scripts/preflight.py", "--json", "--strict"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    manifest = subprocess.run(
        [sys.executable, "scripts/model_manifest.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    manifest_path = ROOT / "models" / "manifest.json"
    if args.json:
        preflight_report = _json_from_output(
            preflight.stdout,
            fallback={"ready": False, "unavailable": ["preflight output is not JSON"]},
        )
        manifest_report = _json_from_output(
            manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else "",
            fallback={"schema_version": None, "models": []},
        )
        print(
            json.dumps(
                {
                    "ready": bool(preflight_report.get("ready")) and manifest.returncode == 0,
                    "preflight": preflight_report,
                    "manifest": manifest_report,
                    "manifest_path": str(manifest_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        sys.stdout.write(preflight.stdout)
        sys.stderr.write(preflight.stderr)
        sys.stdout.write(manifest.stdout)
        sys.stderr.write(manifest.stderr)
    return preflight.returncode or manifest.returncode


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _process_cmdline(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return None


def _pid_alive(pid: int) -> bool:
    command = _process_cmdline(pid)
    if command is not None:
        # A zombie has already exited, even though kill(pid, 0) may succeed
        # until its parent reaps it.
        try:
            state = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split(") ", 1)[1].split()[0]
            return state != "Z"
        except (OSError, IndexError):
            return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _owned_server(pid: int) -> bool:
    command = _process_cmdline(pid)
    return bool(command and "uvicorn" in command and "app:app" in command)


def _run_server(args: argparse.Namespace) -> int:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists():
        pid = _read_pid()
        if pid is not None and _pid_alive(pid):
            owner = "этим приложением" if _owned_server(pid) else "другим процессом"
            print(f"сервер уже запущен ({owner}), pid={pid}; сначала выполните stop", file=sys.stderr)
            return 2
        PID_FILE.unlink(missing_ok=True)
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    process = subprocess.Popen(command, cwd=ROOT)
    PID_FILE.write_text(str(process.pid), encoding="ascii")
    print(f"server pid={process.pid} http://{args.host}:{args.port}", flush=True)
    try:
        return process.wait()
    finally:
        PID_FILE.unlink(missing_ok=True)


def _test(args: argparse.Namespace) -> int:
    checks = [
        [sys.executable, "check_examples.py"],
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
    ]
    for command in checks:
        result = _run(command)
        if result:
            return result
    return 0


def _e2e(args: argparse.Namespace) -> int:
    print("e2e: запускается только fixture smoke test; REAL audio/browser E2E = NOT RUN")
    return _run([sys.executable, "-m", "unittest", "tests.test_app", "-v"])


def _write_run_report(
    out: Path,
    *,
    scope: str,
    checks: list[dict],
    duration_seconds: float | None = None,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": out.name,
        "scope": scope,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration_seconds,
        "checks": checks,
        "artifacts": [],
        "manifests": {
            "models": str(ROOT / "models" / "manifest.json"),
            "data": str(ROOT / "reports" / "data-manifest.json")
            if (ROOT / "reports" / "data-manifest.json").is_file()
            else None,
        },
        "notice": "NOT RUN and FAIL are preserved; this report does not turn fixtures into REAL inference.",
    }
    (out / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failures = sum(item.get("status") == "FAIL" for item in checks)
    skipped = sum(item.get("status") == "NOT RUN" for item in checks)
    testcases = []
    for item in checks:
        test_id = str(item.get("test_id", item.get("requirement_id", "check")))
        status = item.get("status")
        attrs = f' classname="meeting-assistant" name="{escape(test_id)}"'
        if status == "FAIL":
            reason = escape(str(item.get("reason", "check failed")))
            testcases.append(f"  <testcase{attrs}><failure message=\"{reason}\"/></testcase>")
        elif status == "NOT RUN":
            reason = escape(str(item.get("reason", "not run")))
            testcases.append(f"  <testcase{attrs}><skipped message=\"{reason}\"/></testcase>")
        else:
            testcases.append(f"  <testcase{attrs}/>")
    junit = [
        f'<testsuite name="meeting-assistant" tests="{len(checks)}" failures="{failures}" skipped="{skipped}">',
        *testcases,
        "</testsuite>",
    ]
    (out / "junit.xml").write_text("\n".join(junit) + "\n", encoding="utf-8")


def _evaluate(args: argparse.Namespace) -> int:
    source = Path(args.input)
    out = Path(args.out)
    if not source.exists():
        _write_run_report(out, scope="EVALUATE", checks=[{"requirement_id": "input", "test_id": "input_exists", "status": "FAIL", "reason": "input path not found"}])
        return 1
    if source.is_dir() and not any(item.is_file() for item in source.iterdir()):
        _write_run_report(out, scope="EVALUATE", checks=[{"requirement_id": "input", "test_id": "input_nonempty", "status": "FAIL", "reason": "input directory is empty"}])
        return 1
    _write_run_report(out, scope="EVALUATE", checks=[{"requirement_id": "real_inference", "test_id": "evaluate_input", "status": "NOT RUN", "reason": "evaluation adapter requires a new local audio case and does not use fixtures"}])
    return 2


def _verify(args: argparse.Namespace) -> int:
    started = time.monotonic()
    out = Path(args.out)
    core_code = _test(args)
    checks = [{"requirement_id": "core", "test_id": "unit_contract", "status": "PASS" if core_code == 0 else "FAIL", "command": f"{sys.executable} -m unittest discover -s tests -v", "exit_code": core_code}]
    preflight = subprocess.run([sys.executable, "scripts/preflight.py", "--json"], cwd=ROOT, capture_output=True, text=True, check=False)
    try:
        report = json.loads(preflight.stdout)
    except json.JSONDecodeError:
        report = {"ready": False, "unavailable": ["preflight output is not JSON"]}
    checks.append({"requirement_id": "local_models", "test_id": "offline_preflight", "status": "PASS" if report.get("ready") else "NOT RUN", "command": f"{sys.executable} scripts/preflight.py --json", "exit_code": preflight.returncode, "reason": report.get("unavailable", [])})
    if args.profile == "full":
        checks.extend([
            {"requirement_id": "real_audio_ru", "test_id": "real_ru_e2e", "status": "NOT RUN", "reason": "requires a new local RU recording"},
            {"requirement_id": "real_audio_kk", "test_id": "real_kk_e2e", "status": "NOT RUN", "reason": "requires a new local KK recording"},
            {"requirement_id": "real_audio_mix", "test_id": "real_mix_e2e", "status": "NOT RUN", "reason": "requires a new local mixed recording"},
            {"requirement_id": "offline_browser", "test_id": "offline_browser_e2e", "status": "NOT RUN", "reason": "not executed by this command"},
        ])
    _write_run_report(
        out,
        scope=args.profile.upper(),
        checks=checks,
        duration_seconds=round(time.monotonic() - started, 3),
    )
    if core_code != 0:
        return 1
    if args.profile == "full" and not report.get("ready"):
        return 2
    return 0


def _stop(args: argparse.Namespace) -> int:
    if not PID_FILE.exists():
        print("server: не запущен (pid-файл отсутствует)")
        return 0
    try:
        pid = _read_pid()
        if pid is None:
            PID_FILE.unlink(missing_ok=True)
            print("server: некорректный pid-файл удалён")
            return 0
        if not _pid_alive(pid):
            PID_FILE.unlink(missing_ok=True)
            print("server: процесс уже завершён")
            return 0
        if not _owned_server(pid):
            print(f"pid={pid} не похож на процесс этого приложения; остановка отменена", file=sys.stderr)
            return 2
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError) as exc:
        print(f"не удалось остановить сервер: {exc}", file=sys.stderr)
        return 1
    for _ in range(20):
        if not _pid_alive(pid):
            PID_FILE.unlink(missing_ok=True)
            print(f"server pid={pid} остановлен")
            return 0
        time.sleep(0.1)
    print(f"server pid={pid} получил SIGTERM, завершение не подтверждено", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--yes", action="store_true", help="разрешение на установку уже дано пользователем")
    bootstrap.add_argument("--no-install", action="store_true")
    bootstrap.set_defaults(handler=_bootstrap)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--json", action="store_true", help="вывести единый JSON-отчёт")
    doctor.set_defaults(handler=_doctor)

    run = sub.add_parser("run")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8000)
    run.set_defaults(handler=_run_server)

    test = sub.add_parser("test")
    test.add_argument("--level", choices=("core", "full"), default="core")
    test.set_defaults(handler=_test)

    e2e = sub.add_parser("e2e")
    e2e.set_defaults(handler=_e2e)

    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--input", required=True)
    evaluate.add_argument("--out", default=str(ROOT / "reports" / "evaluate"))
    evaluate.set_defaults(handler=_evaluate)

    verify = sub.add_parser("verify")
    verify.add_argument("--profile", choices=("core", "full"), default="core")
    verify.add_argument("--offline", action="store_true")
    verify.add_argument("--out", default=str(ROOT / "reports" / "verify"))
    verify.set_defaults(handler=_verify)

    stop = sub.add_parser("stop")
    stop.set_defaults(handler=_stop)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
