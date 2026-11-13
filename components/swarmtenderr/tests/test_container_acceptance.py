"""Exercise the installed image against disposable SQLite and PostgreSQL state."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    image = sys.argv[1]
    tests = str(Path(__file__).resolve().parent)
    prefix = "swarmtenderr-test-" + uuid.uuid4().hex[:10]
    postgres_image = "postgres:18@sha256:979bccfde35445db618c65107d27d08c6c21339d8dc3ad4c830c6633947be0e4"
    created = []
    network = volume = False
    try:
        subprocess.run(["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp", "--entrypoint", "python", "--mount", f"type=bind,src={tests},dst=/tests,readonly", image, "-B", "-m", "unittest", "discover", "-s", "/tests", "-v"], check=True)
        docker("network", "create", prefix)
        network = True
        docker("volume", "create", prefix)
        volume = True
        postgres = prefix + "-postgres"
        docker("run", "-d", "--name", postgres, "--network", prefix, "--network-alias", "postgres", "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-e", "POSTGRES_DB=swarmtenderr", "--tmpfs", "/var/lib/postgresql", postgres_image)
        created.append(postgres)
        for _ in range(60):
            ready = subprocess.run(["docker", "exec", postgres, "pg_isready", "-U", "postgres", "-d", "swarmtenderr"], capture_output=True)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable PostgreSQL did not become ready")
        subprocess.run(["docker", "run", "--rm", "--network", prefix, "--entrypoint", "python", "--mount", f"type=bind,src={tests},dst=/tests,readonly", "-e", "SWARMTENDERR_TEST_DATABASE_URL=postgresql://postgres@postgres/swarmtenderr", image, "-B", "/tests/test_postgres_acceptance.py"], check=True)
        for database in ("sqlite", "postgresql"):
            name = prefix + "-" + database
            args = ["run", "-d", "--name", name, "--network", prefix, "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "-e", "SWARMTENDERR_DEMO=true", "-p", "127.0.0.1::8787", "--mount", f"type=volume,src={prefix},dst=/data"]
            if database == "postgresql":
                args += ["-e", "SWARMTENDERR_DATABASE_URL=postgresql://postgres@postgres/swarmtenderr"]
            docker(*args, image)
            created.append(name)
            host_port = docker("port", name, "8787/tcp").rsplit(":", 1)[1]
            url = f"http://127.0.0.1:{host_port}"
            for _ in range(60):
                try:
                    with urllib.request.urlopen(url + "/api/v1/status", timeout=2) as response:
                        result = json.load(response)
                    if result["snapshot"]:
                        break
                except OSError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError(f"Packaged {database} dashboard did not become ready")
            assert result["database"] == database and not result["stale"]
            assert result["snapshot"]["demo"] and not result["snapshot"]["controls"]
            assert len(result["snapshot"]["torrents"]) == 6
            for asset in ("/", "/app.js", "/style.css", "/logo.svg", "/healthz"):
                with urllib.request.urlopen(url + asset, timeout=3) as response:
                    assert response.status == 200
            subprocess.run(["docker", "run", "--rm", "--network", prefix, "--entrypoint", "python", "--mount", f"type=bind,src={tests},dst=/tests,readonly", image, "-B", "/tests/test_websocket_acceptance.py", f"ws://{name}:8787/api/v1/live"], check=True)
            docker("restart", name)
            host_port = docker("port", name, "8787/tcp").rsplit(":", 1)[1]
            url = f"http://127.0.0.1:{host_port}"
            for _ in range(30):
                try:
                    with urllib.request.urlopen(url + "/api/v1/status", timeout=2) as response:
                        assert json.load(response)["snapshot"]
                    break
                except OSError:
                    time.sleep(1)
            else:
                raise RuntimeError("Packaged dashboard failed its restart check")
            print(f"Packaged {database} HTTP dashboard, assets and restart verified", flush=True)
        with tempfile.TemporaryDirectory() as temporary:
            credential = Path(temporary) / "credential"
            credential.write_text("test-only-dashboard-pass")
            credential.chmod(0o444)
            Path(temporary).chmod(0o755)
            name = prefix + "-authenticated"
            docker("run", "-d", "--name", name, "--network", prefix, "--tmpfs", "/tmp", "-e", "SWARMTENDERR_DATABASE_URL=sqlite:////tmp/auth.db", "-e", "QBITTORRENT_PASSWORD_FILE=/run/secrets/password", "-e", "SWARMTENDERR_PASSWORD_FILE=/run/secrets/password", "--mount", f"type=bind,src={credential},dst=/run/secrets/password,readonly", image)
            created.append(name)
            for _ in range(30):
                if subprocess.run(["docker", "exec", name, "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8787/healthz').close()"], capture_output=True).returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Authenticated dashboard did not start")
            subprocess.run(["docker", "run", "--rm", "--network", prefix, "--entrypoint", "python", "--mount", f"type=bind,src={tests},dst=/tests,readonly", image, "-B", "/tests/test_websocket_acceptance.py", f"ws://{name}:8787/api/v1/live", "authenticated"], check=True)
    finally:
        for name in reversed(created):
            subprocess.run(["docker", "rm", "-fv", name], stdout=subprocess.DEVNULL, check=False)
        if volume:
            subprocess.run(["docker", "volume", "rm", prefix], stdout=subprocess.DEVNULL, check=False)
        if network:
            subprocess.run(["docker", "network", "rm", prefix], stdout=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    main()
