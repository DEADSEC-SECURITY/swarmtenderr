"""Real WebSocket handshakes, push delivery, isolation and reconnection."""

import base64
import json
import sys


def main():
    from websockets.sync.client import connect
    from websockets.exceptions import InvalidStatus
    url = sys.argv[1]
    auth = len(sys.argv) > 2 and sys.argv[2] == "authenticated"
    origin = "http://localhost:8787"
    headers = {"Authorization": "Basic " + base64.b64encode(b"admin:test-only-dashboard-pass").decode()} if auth else {}
    with connect(url, origin=origin, additional_headers=headers, open_timeout=5) as socket:
        first = json.loads(socket.recv(timeout=10))
        assert first["type"] == "snapshot"
        if not auth:
            stamp = first["status"]["snapshot"]["observed_at"]
            second = json.loads(socket.recv(timeout=10))
            assert second["type"] == "snapshot" and second["status"]["snapshot"]["observed_at"] > stamp
    with connect(url, origin=origin, additional_headers=headers, open_timeout=5) as socket:
        assert json.loads(socket.recv(timeout=10))["type"] == "snapshot"
    for bad_origin, bad_headers in [("https://untrusted.example", headers)] + ([(origin, {})] if auth else []):
        try:
            with connect(url, origin=bad_origin, additional_headers=bad_headers, open_timeout=5):
                raise AssertionError("Unauthorized WebSocket handshake succeeded")
        except InvalidStatus as error:
            assert error.response.status_code == 403
    print("WebSocket authentication, origin isolation and reconnect passed" if auth else "WebSocket initial snapshot, pushed update, origin isolation and reconnect passed")


if __name__ == "__main__":
    main()
