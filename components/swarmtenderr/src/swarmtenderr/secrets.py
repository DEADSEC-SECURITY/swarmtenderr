"""Application-owned encryption key; no secret values enter deployment artifacts."""
import base64
import os
from pathlib import Path
import stat
from cryptography.fernet import Fernet


class SecretVault:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as target:
                target.write(Fernet.generate_key())
                target.flush()
                os.fsync(target.fileno())
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.geteuid()):
            raise ValueError("Application encryption key needs private ownership and mode 0600")
        self.key = path.read_bytes().strip()
        self.fernet = Fernet(self.key)
        self.signing_key = base64.urlsafe_b64decode(self.key)

    def encrypt(self, value):
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value):
        return self.fernet.decrypt(value.encode()).decode()
