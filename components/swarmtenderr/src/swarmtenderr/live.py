"""ASGI transport, authenticated WebSockets and standards-based OIDC login."""
import asyncio
from contextlib import asynccontextmanager
import io
import os
import ssl
import time
from urllib.parse import urlsplit

from authlib.integrations.starlette_client import OAuth
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from .app import create_app as create_http_app
from .auth import AuthError, COOKIE, ROLES


def create_app(settings=None, start_observer=True):
    http = create_http_app(settings, start_observer=False)

    @asynccontextmanager
    async def lifespan(app):
        thread = http.observer.start() if start_observer else None
        try:
            yield
        finally:
            http.observer.stop.set()
            http.observer.wake.set()
            if thread:
                await asyncio.to_thread(thread.join, 20)

    async def endpoint(request):
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 16384:
                return JSONResponse({"error": "Request body exceeds limit"}, status_code=400)
        environ = {"PATH_INFO": request.url.path, "QUERY_STRING": request.url.query,
                   "REQUEST_METHOD": request.method, "CONTENT_LENGTH": str(len(body)),
                   "CONTENT_TYPE": request.headers.get("content-type", ""),
                   "HTTP_COOKIE": request.headers.get("cookie", ""),
                   "HTTP_X_CSRF_TOKEN": request.headers.get("x-csrf-token", ""),
                   "HTTP_ORIGIN": request.headers.get("origin", ""),
                   "REMOTE_ADDR": request.client.host if request.client else "local", "wsgi.input": io.BytesIO(body)}

        def handle():
            response = []
            content = b"".join(http(environ, lambda status, headers: response.extend((status, headers))))
            return Response(content, status_code=int(response[0].split()[0]), headers=dict(response[1]))
        return await asyncio.to_thread(handle)

    async def oidc_client(config):
        context = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None)
        oauth = OAuth()
        client = oauth.register("sso", client_id=config["sso_client_id"], client_secret=config["sso_client_secret"],
            server_metadata_url=config["sso_issuer"].rstrip("/") + "/.well-known/openid-configuration",
            client_kwargs={"scope": "openid profile email", "code_challenge_method": "S256", "timeout": 10,
                           "verify": context, "trust_env": False})
        metadata = await client.load_server_metadata()
        if str(metadata.get("issuer", "")).rstrip("/") != config["sso_issuer"].rstrip("/"):
            raise ValueError("OIDC discovery issuer mismatch")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            target = urlsplit(metadata.get(field, ""))
            allowed_http = http.settings.allow_http_oidc and target.hostname in {"127.0.0.1", "localhost"}
            if (target.scheme != "https" and not allowed_http) or not target.hostname or target.username or target.password:
                raise ValueError("OIDC endpoints must use verified HTTPS")
        return client

    async def oidc_login(request):
        try:
            config = await asyncio.to_thread(http.runtime.values)
            if not config["sso_enabled"]:
                return RedirectResponse("/?auth_error=sso_disabled", status_code=303)
            client = await oidc_client(config)
            return await client.authorize_redirect(request, http.settings.public_url + "/auth/oidc/callback")
        except Exception:
            request.session.clear()
            return RedirectResponse("/?auth_error=sso_unavailable", status_code=303)

    async def oidc_callback(request):
        try:
            config = await asyncio.to_thread(http.runtime.values)
            if not config["sso_enabled"]:
                raise ValueError("SSO disabled")
            client = await oidc_client(config)
            token = await client.authorize_access_token(request, leeway=30,
                claims_options={"iss": {"essential": True, "value": client.server_metadata["issuer"]},
                                "aud": {"essential": True, "value": config["sso_client_id"]}})
            claims = token.get("userinfo")
            if not claims:
                raise ValueError("Verified identity token required")
            session = await asyncio.to_thread(http.auth.sso_session, claims, config)
            response = RedirectResponse(http.settings.public_url + "/", status_code=303)
            response.headers.append(*http.session_cookie(session))
            request.session.clear()
            return response
        except Exception:
            request.session.clear()
            return RedirectResponse("/?auth_error=sso_denied", status_code=303)

    async def test_sso(request):
        try:
            session = request.cookies.get(COOKIE, "")
            user = await asyncio.to_thread(http.auth.current, session)
            http.require(user, "admin")
            if request.headers.get("origin") != http.settings.public_url:
                raise AuthError("Origin mismatch", 403)
            import hmac
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), http.auth.csrf(session)):
                raise AuthError("Security token expired", 403)
            if int(request.headers.get("content-length", "0")) > 16384:
                raise ValueError("Request body too large")
            raw = b""
            async for chunk in request.stream():
                raw += chunk
                if len(raw) > 16384:
                    raise ValueError("Request body too large")
            import json
            changes = json.loads(raw)
            if not isinstance(changes, dict) or set(changes) - {"sso_issuer", "sso_client_id", "sso_client_secret"}:
                raise ValueError("Unknown SSO connection field")
            http.runtime.validate(changes)
            config = await asyncio.to_thread(http.runtime.values)
            config.update({key: value for key, value in changes.items() if value})
            await oidc_client(config)
            return JSONResponse({"ok": True, "message": "Discovery succeeded. Complete a sign-in to verify client credentials and access rules."})
        except AuthError as error:
            return JSONResponse({"error": str(error)}, status_code=error.status)
        except Exception:
            return JSONResponse({"error": "SSO discovery failed; check issuer, endpoints and TLS trust"}, status_code=400)

    async def live(websocket):
        session = websocket.cookies.get(COOKIE, "")
        user = await asyncio.to_thread(http.auth.current, session)
        if websocket.headers.get("origin") != http.settings.public_url or not user:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        observed, last_sent = None, 0
        try:
            while True:
                user = await asyncio.to_thread(http.auth.current, session)
                if not user:
                    await websocket.close(code=4401, reason="Session expired")
                    return
                status = await asyncio.to_thread(http.status, user)
                stamp = ((status.get("snapshot") or {}).get("observed_at"), status["stale"], status["error"],
                         status["configuration_revision"], status["activity_revision"], user["role"], user["display_name"])
                if stamp != observed:
                    activity = await asyncio.to_thread(http.store.history, user["role"] == "admin")
                    statistics = await asyncio.to_thread(http.analytics.summary, status["timezone"])
                    await websocket.send_json({"type": "snapshot", "status": status, "activity": activity, "statistics": statistics})
                    observed, last_sent = stamp, time.monotonic()
                elif time.monotonic() - last_sent >= 15:
                    await websocket.send_json({"type": "heartbeat"})
                    last_sent = time.monotonic()
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                await websocket.close(code=1011, reason="Observation stream unavailable")
            except RuntimeError:
                pass

    app = Starlette(lifespan=lifespan, routes=[
        Route("/auth/oidc/login", oidc_login), Route("/auth/oidc/callback", oidc_callback),
        Route("/api/v1/settings/test-sso", test_sso, methods=["POST"]),
        WebSocketRoute("/api/v1/live", live),
        Route("/{path:path}", endpoint, methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"])])
    app.add_middleware(SessionMiddleware, secret_key=http.vault.key.decode(), session_cookie="swarmtenderr_oidc",
                       max_age=600, same_site="lax", https_only=http.settings.public_url.startswith("https:"))
    app.state.service = http
    return app
