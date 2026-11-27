
"""Run the published acceptance reproductions against a clean, pinned checkout."""

import argparse
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


SUITE = Path(__file__).resolve().parent
POSTGRES_IMAGE = "postgres:16-alpine"
E2E_DB = "autoq_acceptance_e2e"
DIAGNOSE = "settlement_and_close or recall_and_departure or recall_fixture"


def command(args, *, cwd=None, env=None, capture=False):
    return subprocess.run(
        [str(arg) for arg in args], cwd=cwd, env=env, check=True,
        text=True, stdout=subprocess.PIPE if capture else None,
    )


def output(args, **kwargs):
    return command(args, capture=True, **kwargs).stdout.strip()


def check_checkout(checkout, candidate):
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise ValueError("--candidate requires the full lowercase Git commit ID")
    if Path(output(["git", "-C", checkout, "rev-parse", "--show-toplevel"])).resolve() != checkout:
        raise ValueError("--checkout must be the candidate repository root")
    if output(["git", "-C", checkout, "rev-parse", "HEAD"]) != candidate:
        raise ValueError("Candidate checkout HEAD does not match --candidate")
    if subprocess.run(["git", "-C", str(checkout), "symbolic-ref", "-q", "HEAD"],
                      stdout=subprocess.DEVNULL).returncode != 1:
        raise ValueError("Use a dedicated checkout with detached HEAD")
    command(["git", "-C", checkout, "diff", "--quiet", "HEAD", "--"])
    for folder in (checkout, checkout / "backend", checkout / "frontend", checkout / "e2e"):
        if any(folder.glob(".env*local")) or (folder / ".env").exists():
            raise ValueError("Use a dedicated checkout without a local dotenv file")
    tree = output(["git", "-C", checkout, "rev-parse", "HEAD^{tree}"])
    print(f"Candidate {candidate} tree {tree}", flush=True)


def clean_environment():
    # Keep ordinary platform settings and PATH. Ignore externally supplied app and test settings.
    blocked = (
        "AUTOQ_", "DATABASE_URL", "PG", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT", "UV_ACTIVE", "UVICORN_", "VITE_", "NODE_OPTIONS",
        "CORS_ORIGINS", "HTTPS", "DOCKER_HOST", "DOCKER_CONTEXT",
    )
    return {key: value for key, value in os.environ.items() if not key.upper().startswith(blocked)}


@contextmanager
def disposable_postgres(env):
    context = output(["docker", "context", "show"], env=env)
    host = output(["docker", "context", "inspect", context,
                   "--format", "{{.Endpoints.docker.Host}}"], env=env)
    if not host.startswith(("unix:///", "npipe:////./pipe/")):
        raise ValueError("A local Docker daemon is required")
    docker = ["docker", "--context", context]
    nonce = secrets.token_hex(16)
    password = secrets.token_hex(16)
    container_id = output([
        *docker, "run", "--detach", "--rm", "--pull", "never",
        "--name", f"autoq-acceptance-{nonce}", "--label", f"autoq.acceptance={nonce}",
        "--publish", "127.0.0.1::5432", "--env", "POSTGRES_USER=autoq",
        "--env", "POSTGRES_DB=postgres", "--env", f"POSTGRES_PASSWORD={password}",
        POSTGRES_IMAGE,
    ], env=env)
    try:
        binding = output([*docker, "port", container_id, "5432/tcp"], env=env)
        match = re.fullmatch(r"127\.0\.0\.1:(\d+)", binding)
        if not match:
            raise RuntimeError("Disposable PostgreSQL must bind one IPv4 loopback port")
        for _ in range(80):
            ready = subprocess.run(
                [*docker, "exec", container_id, "pg_isready", "-h", "127.0.0.1",
                 "-U", "autoq", "-d", "postgres"],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Disposable PostgreSQL failed to become ready")
        command([
            *docker, "exec", container_id, "psql", "-X", "-v", "ON_ERROR_STOP=1",
            "-U", "autoq", "-d", "postgres", "-c",
            f"CREATE TABLE acceptance_guard (nonce text NOT NULL); INSERT INTO acceptance_guard VALUES ('{nonce}');",
        ], env=env, capture=True)
        port = match.group(1)
        yield {
            **env, "AUTOQ_ACCEPTANCE_NONCE": nonce,
            "AUTOQ_TEST_ADMIN_URL": f"postgresql://autoq:{password}@127.0.0.1:{port}/postgres",
            "AUTOQ_DISABLE_RELAY": "1", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
        }
    finally:
        subprocess.run([*docker, "stop", container_id], check=True, env=env, stdout=subprocess.DEVNULL)


@contextmanager
def service(args, *, cwd, env):
    with subprocess.Popen([str(arg) for arg in args], cwd=cwd, env=env) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def loopback_ports():
    with socket.socket() as api, socket.socket() as web:
        api.bind(("127.0.0.1", 0))
        web.bind(("127.0.0.1", 0))
        return api.getsockname()[1], web.getsockname()[1]


def ready(process, url):
    opener = build_opener(ProxyHandler({}))
    until = time.monotonic() + 60
    while time.monotonic() < until:
        if process.poll() is not None:
            raise RuntimeError(f"Loopback service exited before readiness: {url}")
        try:
            with opener.open(url, timeout=2) as response:
                if response.status == 200 and process.poll() is None:
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Loopback readiness timed out: {url}")


def browser(checkout, python, env):
    api_port, web_port = loopback_ports()
    base_url = f"http://127.0.0.1:{web_port}"
    frontend = checkout / "frontend"
    backend = checkout / "backend"
    e2e = checkout / "e2e"
    with tempfile.TemporaryDirectory(prefix="autoq-acceptance-e2e-") as scratch, ExitStack() as stack:
        run = Path(scratch)
        database = env["AUTOQ_TEST_ADMIN_URL"].replace("postgresql://", "postgresql+psycopg://")
        browser_env = {
            **env, "AUTOQ_ACCEPTANCE_CHECKOUT": str(checkout), "AUTOQ_ACCEPTANCE_URL": base_url,
            "AUTOQ_ACCEPTANCE_RESULTS": str(run / "results"),
            "AUTOQ_E2E": "1", "DATABASE_URL": database.removesuffix("/postgres") + f"/{E2E_DB}",
            "AUTOQ_EVIDENCE_DIR": str(run / "evidence"),
            "AUTOQ_SIGNING_KEY_PATH": str(run / "e2e-signing.key"), "AUTOQ_SIGNING_KID": "aq-e2e-1",
            "AUTOQ_JWT_SECRET": secrets.token_hex(32), "CORS_ORIGINS": base_url,
        }
        command([python, "-B", SUITE / "database_guard.py"], cwd=backend, env=browser_env)
        command([python, "-B", "scripts/e2e_database.py"], cwd=backend, env=browser_env)
        api = stack.enter_context(service(
            [python, "-B", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", api_port, "--log-level", "warning", "--no-access-log"],
            cwd=backend, env=browser_env,
        ))
        ready(api, f"http://127.0.0.1:{api_port}/api/v1/health")
        web = stack.enter_context(service(
            ["node", "--dns-result-order=ipv4first", frontend / "node_modules/vite/bin/vite.js",
             "--host", "127.0.0.1", "--port", web_port, "--strictPort"], cwd=frontend,
            env={**env, "VITE_API_MODE": "live", "AUTOQ_API_PORT": str(api_port)},
        ))
        ready(web, base_url + "/api/v1/health")
        command(["node", e2e / "node_modules/@playwright/test/cli.js", "test",
                 "--config", SUITE / "browser.config.cjs"], cwd=e2e, env=browser_env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--group", choices=("backend", "diagnose", "browser", "keys"), default="backend")
    parser.add_argument("--case", help="pytest -k expression for backend or diagnose")
    parser.add_argument("--python", type=Path, help="optional Python executable with the candidate dependencies")
    parser.add_argument("--check-only", action="store_true", help="verify the checkout without starting services")
    args = parser.parse_args()
    if args.case and args.group not in ("backend", "diagnose"):
        parser.error("--case applies only to pytest groups")
    checkout = args.checkout.resolve()
    check_checkout(checkout, args.candidate)
    if args.check_only:
        return
    env = clean_environment()
    if args.group == "keys":
        frontend = checkout / "frontend"
        command(["node", frontend / "node_modules/vitest/vitest.mjs", "run",
                 "src/lib/interop.test.ts", "src/lib/token.test.ts", "src/scanner/offline.test.ts"],
                cwd=frontend, env=env)
        return
    backend = checkout / "backend"
    python = args.python.resolve(strict=True) if args.python else output(
        ["uv", "run", "--locked", "python", "-c", "import sys; print(sys.executable)"],
        cwd=backend, env=env,
    )
    with tempfile.TemporaryDirectory(prefix="autoq-acceptance-pytest-") as scratch, disposable_postgres(env) as test_env:
        if args.group == "browser":
            browser(checkout, python, test_env)
        else:
            test_env["PYTHONPATH"] = str(backend)
            command([python, "-B", "-m", "pytest", "--noconftest", "-p", "tests.conftest",
                     "-o", "addopts=", "-p", "no:cacheprovider", "-q", "--basetemp", scratch,
                     SUITE / "backend_cases.py", "-k", args.case or (DIAGNOSE if args.group == "diagnose" else f"not ({DIAGNOSE})")],
                    cwd=backend, env=test_env)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        # Child output contains the assertion; avoid echoing Docker arguments containing ephemeral secrets.
        raise SystemExit(exc.returncode) from None
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
