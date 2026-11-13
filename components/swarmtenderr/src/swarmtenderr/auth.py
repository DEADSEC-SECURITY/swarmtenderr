"""Local identity, salted password hashes, revocable sessions and role administration."""
import base64
import hashlib
import hmac
import re
import secrets
import time
import uuid


ROLES = {"viewer": 0, "operator": 1, "admin": 2}
COOKIE = "swarmtenderr_session"


class AuthError(Exception):
    def __init__(self, message, status=401):
        self.status = status
        super().__init__(message)


def password_hash(password):
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000)
    return "pbkdf2_sha256$600000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(derived).decode()


def verify_password(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != 600000:
            return False
        derived = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(rounds))
        return hmac.compare_digest(derived, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def validate_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError("Passwords must contain 12 to 256 characters")


def public_user(row):
    return {key: row[key] for key in ("id", "username", "display_name", "role", "active", "created", "last_login")} | {"provider": "sso" if row["issuer"] else "local"}


class Auth:
    def __init__(self, store, vault):
        self.store, self.vault = store, vault
        self.dummy_hash = password_hash(secrets.token_urlsafe(24))

    def bootstrap(self, settings):
        with self.store.connection(serialize=True) as c:
            if self.store.execute(c, "SELECT COUNT(*) FROM swarmtenderr_users").fetchone()[0]:
                return
            username = "demo" if settings.demo else settings.app_user
            password = "demo" if settings.demo else settings.app_password
            if not settings.demo:
                validate_password(password)
            self._create(c, username, "Demo administrator" if settings.demo else "Administrator", "admin", password)
            self.store.audit("user.bootstrap", "system", {"username": username}, c)

    def _create(self, c, username, display_name, role, password="", issuer="", subject=""):
        if not isinstance(username, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", username):
            raise ValueError("Username needs 3–64 lowercase letters, numbers, dots, dashes or underscores")
        if not isinstance(role, str) or role not in ROLES or not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 100:
            raise ValueError("Choose a valid role and display name")
        if self.store.execute(c, "SELECT 1 FROM swarmtenderr_users WHERE username=?", (username,)).fetchone():
            raise ValueError("That username is already in use")
        identity = uuid.uuid4().hex
        self.store.execute(c, "INSERT INTO swarmtenderr_users VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 0)",
                           (identity, username, display_name.strip(), role, password_hash(password) if password else "", issuer, subject or username, time.time()))
        return identity

    def create_user(self, payload, actor):
        if set(payload) != {"username", "display_name", "role", "password"}:
            raise ValueError("Provide username, display name, role and password")
        validate_password(payload["password"])
        with self.store.connection(serialize=True) as c:
            identity = self._create(c, payload["username"], payload["display_name"], payload["role"], payload["password"])
            self.store.audit("user.created", actor, {"user": identity, "username": payload["username"], "role": payload["role"]}, c)
        return identity

    def users(self):
        with self.store.connection() as c:
            return [public_user(row) for row in self.store.objects(self.store.execute(c, "SELECT * FROM swarmtenderr_users ORDER BY created"))]

    def update_user(self, identity, changes, actor):
        if not changes or set(changes) - {"display_name", "role", "active"}:
            raise ValueError("Invalid account update")
        with self.store.connection(serialize=True) as c:
            rows = self.store.objects(self.store.execute(c, "SELECT * FROM swarmtenderr_users WHERE id=?", (identity,)))
            if not rows:
                raise ValueError("Account not found")
            old = rows[0]
            new = {**old, **changes}
            if not isinstance(new["role"], str) or new["role"] not in ROLES or type(new["active"]) not in (bool, int) or new["active"] not in (0, 1):
                raise ValueError("Invalid role or active state")
            if not isinstance(new["display_name"], str) or not 1 <= len(new["display_name"].strip()) <= 100:
                raise ValueError("Display name is required")
            count = self.store.execute(c, "SELECT COUNT(*) FROM swarmtenderr_users WHERE role='admin' AND active=1").fetchone()[0]
            if old["role"] == "admin" and old["active"] and count <= 1 and (new["role"] != "admin" or not new["active"]):
                raise ValueError("The last active administrator cannot be disabled or demoted")
            local_admins = self.store.execute(c, "SELECT COUNT(*) FROM swarmtenderr_users WHERE role='admin' AND active=1 AND issuer=''").fetchone()[0]
            if old["role"] == "admin" and old["active"] and not old["issuer"] and local_admins <= 1 and (new["role"] != "admin" or not new["active"]):
                raise ValueError("Keep one active local administrator for recovery")
            self.store.execute(c, "UPDATE swarmtenderr_users SET display_name=?, role=?, active=? WHERE id=?",
                               (new["display_name"].strip(), new["role"], int(new["active"]), identity))
            if new["role"] != old["role"] or new["active"] != old["active"]:
                self.store.execute(c, "DELETE FROM swarmtenderr_sessions WHERE user_id=?", (identity,))
            self.store.audit("user.updated", actor, {"user": identity, "changes": changes}, c)

    def set_password(self, identity, password, actor, current=None):
        validate_password(password)
        with self.store.connection(serialize=True) as c:
            rows = self.store.objects(self.store.execute(c, "SELECT * FROM swarmtenderr_users WHERE id=?", (identity,)))
            if not rows or rows[0]["issuer"]:
                raise ValueError("Password changes require a local account")
            if current is not None and not verify_password(current, rows[0]["password_hash"]):
                raise ValueError("Current password is incorrect")
            self.store.execute(c, "UPDATE swarmtenderr_users SET password_hash=? WHERE id=?", (password_hash(password), identity))
            self.store.execute(c, "DELETE FROM swarmtenderr_sessions WHERE user_id=?", (identity,))
            self.store.audit("user.password_changed", actor, {"user": identity}, c)

    def login(self, username, password, address):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 100 or len(password) > 256:
            raise AuthError("Incorrect username or password")
        username = username.lower()
        identity = hashlib.sha256((username + ":" + address).encode()).hexdigest()
        now = time.time()
        failed = False
        with self.store.connection(serialize=True) as c:
            self.store.execute(c, "DELETE FROM swarmtenderr_login_attempts WHERE started<?", (now - 900,))
            attempts = self.store.execute(c, "SELECT attempts FROM swarmtenderr_login_attempts WHERE identity=?", (identity,)).fetchone()
            if attempts and attempts[0] >= 10:
                raise AuthError("Too many login attempts. Try again in 15 minutes.", 429)
            rows = self.store.objects(self.store.execute(c, "SELECT * FROM swarmtenderr_users WHERE username=?", (username,)))
            row = rows[0] if rows else None
            valid = verify_password(password, row["password_hash"] if row and not row["issuer"] else self.dummy_hash)
            if not row or not row["active"] or row["issuer"] or not valid:
                self.store.execute(c, "INSERT INTO swarmtenderr_login_attempts VALUES (?, ?, 1) ON CONFLICT(identity) DO UPDATE SET attempts=swarmtenderr_login_attempts.attempts+1", (identity, now))
                failed = True
            else:
                self.store.execute(c, "DELETE FROM swarmtenderr_login_attempts WHERE identity=?", (identity,))
                token = self._session(c, row)
        if failed:
            raise AuthError("Incorrect username or password")
        return token

    def _session(self, c, row):
        now, token = time.time(), secrets.token_urlsafe(32)
        self.store.execute(c, "DELETE FROM swarmtenderr_sessions WHERE expires<? OR seen<?", (now, now - 3600))
        self.store.execute(c, "INSERT INTO swarmtenderr_sessions VALUES (?, ?, ?, ?, ?)",
                           (hashlib.sha256(token.encode()).hexdigest(), row["id"], now, now + 43200, now))
        self.store.execute(c, "UPDATE swarmtenderr_users SET last_login=? WHERE id=?", (now, row["id"]))
        self.store.audit("user.login", row["username"], {"provider": "sso" if row["issuer"] else "local"}, c)
        return token

    def current(self, token):
        if not token or len(token) > 256:
            return None
        now, digest = time.time(), hashlib.sha256(token.encode()).hexdigest()
        with self.store.connection() as c:
            rows = self.store.objects(self.store.execute(c,
                "SELECT u.*, s.seen FROM swarmtenderr_sessions s JOIN swarmtenderr_users u ON u.id=s.user_id WHERE s.digest=? AND s.expires>? AND s.seen>? AND u.active=1",
                (digest, now, now - 3600)))
            if not rows:
                return None
            if now - rows[0]["seen"] > 60:
                self.store.execute(c, "UPDATE swarmtenderr_sessions SET seen=? WHERE digest=?", (now, digest))
        return public_user(rows[0])

    def csrf(self, token):
        return hmac.new(self.vault.signing_key, ("csrf:" + token).encode(), hashlib.sha256).hexdigest()

    def revoke(self, token):
        with self.store.connection() as c:
            self.store.execute(c, "DELETE FROM swarmtenderr_sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def sso_session(self, claims, config):
        issuer, subject = claims.get("iss"), claims.get("sub")
        if not isinstance(issuer, str) or issuer.rstrip("/") != config["sso_issuer"].rstrip("/") or not isinstance(subject, str) or not subject:
            raise AuthError("SSO identity could not be verified", 403)
        groups = claims.get("groups", [])
        if config["sso_allowed_groups"] and (not isinstance(groups, list) or not set(groups) & set(config["sso_allowed_groups"])):
            raise AuthError("This SSO account is not allowed access", 403)
        with self.store.connection(serialize=True) as c:
            rows = self.store.objects(self.store.execute(c, "SELECT * FROM swarmtenderr_users WHERE issuer=? AND subject=?", (issuer, subject)))
            if not rows:
                base = re.sub(r"[^a-z0-9._-]", "-", str(claims.get("preferred_username", claims.get("name", "sso-user"))).lower())[:45].strip("-.") or "sso-user"
                username = (base if len(base) >= 3 else "sso-" + base) + "-" + hashlib.sha256((issuer + subject).encode()).hexdigest()[:8]
                identity = self._create(c, username, str(claims.get("name") or username)[:100], config["sso_default_role"], issuer=issuer, subject=subject)
                rows = self.store.objects(self.store.execute(c, "SELECT * FROM swarmtenderr_users WHERE id=?", (identity,)))
            if not rows[0]["active"]:
                raise AuthError("This account is disabled", 403)
            return self._session(c, rows[0])
