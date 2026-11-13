"""Async WebSocket delivery alongside the existing HTTP contract."""

import asyncio
import io
import time

from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from .app import create_app as create_http_app


def create_app():
    http = create_http_app()

    async def endpoint(request):
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 1024:
                return Response('{"error":"Request body exceeds limit"}', status_code=400, media_type="application/json")
        environ = {"PATH_INFO": request.url.path, "REQUEST_METHOD": request.method,
                   "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": request.headers.get("content-type", ""),
                   "HTTP_AUTHORIZATION": request.headers.get("authorization", ""),
                   "HTTP_ORIGIN": request.headers.get("origin", ""), "wsgi.input": io.BytesIO(body)}

        def handle():
            response = []
            content = b"".join(http(environ, lambda status, headers: response.extend((status, headers))))
            return Response(content, status_code=int(response[0].split()[0]), headers=dict(response[1]))

        return await asyncio.to_thread(handle)

    async def live(websocket):
        # Native browser Basic auth is reused on this same-origin handshake.
        # No credential-bearing URL, localStorage token or client auth message.
        if (websocket.headers.get("origin") != http.settings.public_url
                or not http.authorized({"HTTP_AUTHORIZATION": websocket.headers.get("authorization", "")})):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        observed = None
        last_sent = 0
        try:
            while True:
                status = await asyncio.to_thread(http.status)
                stamp = ((status.get("snapshot") or {}).get("observed_at"), status["stale"], status["error"])
                if stamp != observed:
                    activity = await asyncio.to_thread(http.store.history)
                    await websocket.send_json({"type": "snapshot", "status": status, "activity": activity})
                    observed = stamp
                    last_sent = time.monotonic()
                elif time.monotonic() - last_sent >= 15:
                    await websocket.send_json({"type": "heartbeat"})
                    last_sent = time.monotonic()
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep errors sanitized; the reconnecting client retains its last view.
            await websocket.close(code=1011, reason="Observation stream unavailable")

    return Starlette(routes=[WebSocketRoute("/api/v1/live", live),
                            Route("/{path:path}", endpoint, methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"])])
