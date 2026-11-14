"""Editable deployment defaults, optimistic writes and write-only secrets."""
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .settings import origin, read_secret
from swarmtenderr_policy.tuning import POLICY_DEFAULTS, RULES, Policy, fields


SECRET_KEYS = {"qbit_password", "sso_client_secret"}
NUMBERS = {"poll_seconds": (1, 3600), "retention_days": (1, 365),
           "download_slots": (1, 100), "minimum_free_gib": (1, 100000),
           "work_budget_gib": (1, 100000), "library_reserve_gib": (1, 100000)}
NUMBERS.update({key: RULES[key][:2] for key in POLICY_DEFAULTS})
OPTIMIZER_KEYS = set(POLICY_DEFAULTS) | {"policy_mode", "download_slots", "minimum_free_gib", "work_budget_gib", "library_reserve_gib", "managed_categories", "excluded_tags"}
LISTS = {"managed_categories", "excluded_tags", "sso_allowed_groups"}


class Runtime:
    def __init__(self, settings, store, vault):
        self.settings, self.store, self.vault = settings, store, vault
        if settings.optimizer_control_socket and not settings.optimizer_state:
            raise ValueError("An optimizer control connection requires its external observer feed")
        self.defaults = {
            "qbit_url": settings.qbit_url, "qbit_username": settings.qbit_user,
            "poll_seconds": settings.poll_seconds, "retention_days": 30,
            "timezone": settings.timezone, "controls_enabled": True,
            "policy_mode": "observe", "download_slots": 3,
            "managed_categories": [], "excluded_tags": ["swarmtenderr-keep"],
            "minimum_free_gib": 50, "work_budget_gib": 500, "library_reserve_gib": 50,
            **POLICY_DEFAULTS,
            "sso_enabled": False, "sso_issuer": "", "sso_client_id": "",
            "sso_label": "Sign in with SSO", "sso_allowed_groups": [], "sso_default_role": "viewer",
        }
        if set(settings.defaults) - set(self.defaults):
            raise ValueError("Unknown or secret deployment configuration keys")
        self.defaults.update(settings.defaults)
        self.validate(self.defaults)
        Policy.from_values(self.defaults)
        if set(settings.locked_settings) - (set(self.defaults) | SECRET_KEYS):
            raise ValueError("Unknown locked setting")
        Policy.from_values(self.values())

    def values(self, snapshot=None):
        revision, saved, policy_revision, ack_revision = self.store.config(include_policy=True) if snapshot is None else snapshot
        values = {**self.defaults, **{key: value for key, value in saved.items() if key not in SECRET_KEYS}}
        values["qbit_password"] = self.vault.decrypt(saved["qbit_password"]) if "qbit_password" in saved else read_secret(self.settings.qbit_password_file)
        values["sso_client_secret"] = self.vault.decrypt(saved["sso_client_secret"]) if "sso_client_secret" in saved else read_secret(self.settings.oidc_secret_file)
        values["revision"] = revision
        values["policy_revision"] = policy_revision
        values["policy_ack_revision"] = ack_revision
        # Manual intent is authorized by account role, independently of the
        # automatic scheduler and deprecated deployment control switches.
        values["controls_enabled"] = not self.settings.demo
        if self.settings.demo or self.settings.optimizer_state:
            values["policy_mode"] = "observe"
        return values

    def public(self):
        snapshot = self.store.config(include_policy=True)
        revision, saved = snapshot[:2]
        values = self.values(snapshot)
        locked = set()
        if self.settings.demo:
            locked |= {"qbit_url", "qbit_username", "qbit_password", "sso_enabled",
                       "sso_issuer", "sso_client_id", "sso_client_secret", "controls_enabled", "policy_mode"}
        if self.settings.optimizer_state:
            locked |= OPTIMIZER_KEYS
        return {"revision": revision,
                "values": {key: values[key] for key in self.defaults},
                "secrets": {key: bool(values[key]) for key in SECRET_KEYS},
                "sources": {key: "ui" if key in saved and key not in locked else "deployment" for key in self.defaults | dict.fromkeys(SECRET_KEYS)},
                "locked": sorted(locked),
                "optimizer_fields": fields(set(POLICY_DEFAULTS), self.defaults),
                "deployment": {"database": "postgresql" if self.store.postgres else "sqlite",
                               "public_url": self.settings.public_url, "external_optimizer": bool(self.settings.optimizer_state),
                               "optimizer_control": bool(self.settings.optimizer_control_socket),
                               "demo": self.settings.demo}}

    def validate(self, changes):
        for key, value in changes.items():
            if key not in self.defaults.keys() | SECRET_KEYS:
                raise ValueError("Unknown setting: " + key)
            if key in NUMBERS:
                low, high = NUMBERS[key]
                if type(value) is not int or not low <= value <= high:
                    raise ValueError(f"{key} must be a whole number between {low} and {high}")
            elif key in LISTS:
                if not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) or not 1 <= len(v) <= 80 for v in value):
                    raise ValueError(key + " must be a list of names")
            elif key in {"controls_enabled", "sso_enabled"}:
                if type(value) is not bool:
                    raise ValueError(key + " must be true or false")
            elif not isinstance(value, str) or len(value) > (4096 if key in SECRET_KEYS else 500):
                raise ValueError("Invalid text setting: " + key)
        if "qbit_url" in changes:
            origin(changes["qbit_url"], empty=True)
        if "timezone" in changes:
            try:
                ZoneInfo(changes["timezone"])
            except (ValueError, KeyError):
                raise ValueError("Choose a valid IANA timezone") from None
        if changes.get("policy_mode", "observe") not in {"observe", "enforce"}:
            raise ValueError("Policy mode must be observe or enforce")
        if changes.get("sso_default_role", "viewer") not in {"viewer", "operator"}:
            raise ValueError("New SSO accounts may be viewers or operators")
        if "sso_issuer" in changes and changes["sso_issuer"]:
            issuer = urlsplit(changes["sso_issuer"])
            local_test = self.settings.allow_http_oidc and issuer.hostname in {"127.0.0.1", "localhost"}
            if ((issuer.scheme != "https" and not local_test) or not issuer.hostname or issuer.username
                    or issuer.password or issuer.query or issuer.fragment):
                raise ValueError("SSO issuer must use HTTPS without credentials or query parameters")

    def save(self, changes, revision, actor, reset=None):
        reset = [] if reset is None else reset
        if not isinstance(changes, dict) or not isinstance(reset, list) or any(not isinstance(key, str) for key in reset):
            raise ValueError("Invalid settings update")
        if type(revision) is not int or revision < 0 or set(changes) & set(reset):
            raise ValueError("Provide a valid revision and do not save and reset the same field")
        changes = {key: value for key, value in changes.items() if key not in SECRET_KEYS or value}
        if "qbit_url" in changes:
            changes["qbit_url"] = origin(changes["qbit_url"], empty=True)
        self.validate(changes)
        if set(reset) - (self.defaults.keys() | SECRET_KEYS):
            raise ValueError("Unknown setting to reset")
        locked = set(self.public()["locked"])
        if (set(changes) | set(reset)) & locked:
            raise PermissionError("One or more settings are locked by this deployment")
        current = self.values()
        candidate = {**current, **changes}
        for key in reset:
            candidate[key] = (read_secret(self.settings.qbit_password_file) if key == "qbit_password" else
                              read_secret(self.settings.oidc_secret_file) if key == "sso_client_secret" else self.defaults[key])
        Policy.from_values(candidate)
        if candidate["sso_enabled"] and not all(candidate[key] for key in ("sso_issuer", "sso_client_id", "sso_client_secret")):
            raise ValueError("SSO requires an issuer, client ID and client secret")
        if candidate["policy_mode"] == "enforce" and not candidate["managed_categories"]:
            raise ValueError("Choose managed categories before enabling enforcement")
        encoded = {key: self.vault.encrypt(value) if key in SECRET_KEYS else value
                   for key, value in changes.items() if key not in SECRET_KEYS or value}
        return self.store.save_config(encoded, reset, revision, actor)
