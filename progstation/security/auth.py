"""Authentication, roles and audit logging (SRS sections 6 and 17).

Passwords are stored as PBKDF2-HMAC-SHA256 with a per-user random salt, encoded
as ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``.  Nothing in the system
ever stores or transmits a plaintext password.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config import SecurityConfig
from ..db.database import Database, utc_now
from ..errors import AuthError, PermissionDeniedError, StationError

ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"
ROLES = (ROLE_OPERATOR, ROLE_ADMIN)

_ALGORITHM = "pbkdf2_sha256"


def hash_password(password: str, *, iterations: int = 240_000, salt: bytes | None = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        [
            _ALGORITHM,
            str(iterations),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_b64, hash_b64 = encoded.split("$")
        if algorithm != _ALGORITHM:
            return False
        expected = base64.b64decode(hash_b64)
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, candidate)


def generate_password(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@dataclass(frozen=True)
class Session:
    user_id: int
    username: str
    full_name: str
    role: str
    must_change_password: bool = False
    started_at: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def require_admin(self) -> None:
        if not self.is_admin:
            raise PermissionDeniedError(
                f"user '{self.username}' is not an administrator"
            )


class AuthManager:
    """User lifecycle plus login throttling."""

    def __init__(self, db: Database, config: Optional[SecurityConfig] = None):
        self.db = db
        self.config = config or SecurityConfig()

    # ------------------------------------------------------------- bootstrap
    def ensure_default_admin(self, username: str = "admin") -> Optional[str]:
        """Create a first administrator if the user table has none.

        Returns the generated one-time password, or ``None`` when an admin
        already exists.  The account is flagged ``MustChangePw`` so the station
        forces a change at first login.
        """
        if self.db.count_admins() > 0:
            return None
        password = generate_password(12)
        self.create_user(
            username,
            password,
            ROLE_ADMIN,
            full_name="Default Administrator",
            actor="system",
            must_change_password=True,
        )
        return password

    # ------------------------------------------------------------------ CRUD
    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        *,
        full_name: str = "",
        actor: str = "",
        must_change_password: bool = False,
    ) -> int:
        username = (username or "").strip()
        if not username:
            raise AuthError("username must not be empty")
        if role not in ROLES:
            raise AuthError(f"unknown role '{role}'")
        self._check_password_policy(password)
        if self.db.get_user(username):
            raise AuthError(f"user '{username}' already exists")
        user_id = self.db.execute(
            "INSERT INTO Users (Username, FullName, Role, PasswordHash, MustChangePw)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                username,
                full_name,
                role,
                hash_password(password, iterations=self.config.pbkdf2_iterations),
                1 if must_change_password else 0,
            ),
        )
        self.db.audit(actor, "user.create", username, f"role={role}")
        return int(user_id)

    def set_password(self, username: str, password: str, *, actor: str = "") -> None:
        self._check_password_policy(password)
        if not self.db.get_user(username):
            raise AuthError(f"unknown user '{username}'")
        self.db.execute(
            "UPDATE Users SET PasswordHash = ?, MustChangePw = 0, FailedLogins = 0,"
            " LockedUntil = NULL WHERE Username = ?",
            (hash_password(password, iterations=self.config.pbkdf2_iterations), username),
        )
        self.db.audit(actor or username, "user.password_change", username)

    def set_role(self, username: str, role: str, *, actor: str = "") -> None:
        if role not in ROLES:
            raise AuthError(f"unknown role '{role}'")
        user = self.db.get_user(username)
        if not user:
            raise AuthError(f"unknown user '{username}'")
        if user["Role"] == ROLE_ADMIN and role != ROLE_ADMIN and self.db.count_admins() <= 1:
            raise AuthError("cannot demote the last administrator")
        self.db.execute("UPDATE Users SET Role = ? WHERE Username = ?", (role, username))
        self.db.audit(actor, "user.set_role", username, f"role={role}")

    def set_active(self, username: str, active: bool, *, actor: str = "") -> None:
        user = self.db.get_user(username)
        if not user:
            raise AuthError(f"unknown user '{username}'")
        if not active and user["Role"] == ROLE_ADMIN and self.db.count_admins() <= 1:
            raise AuthError("cannot deactivate the last administrator")
        self.db.execute(
            "UPDATE Users SET Active = ? WHERE Username = ?",
            (1 if active else 0, username),
        )
        self.db.audit(actor, "user.activate" if active else "user.deactivate", username)

    # ----------------------------------------------------------------- login
    def login(self, username: str, password: str) -> Session:
        user = self.db.get_user((username or "").strip())
        if not user or not user["Active"]:
            # Spend the same work as a real verification so a wrong username is
            # not distinguishable by timing.
            hash_password(password, iterations=self.config.pbkdf2_iterations)
            self.db.audit(username, "login.failed", detail="unknown or inactive user")
            raise AuthError("invalid username or password")

        locked_until = user["LockedUntil"]
        if locked_until and locked_until > utc_now():
            raise AuthError(f"account locked until {locked_until}")

        if not verify_password(password, user["PasswordHash"]):
            failed = int(user["FailedLogins"]) + 1
            lock_value = None
            if self.config.max_failed_logins and failed >= self.config.max_failed_logins:
                lock_value = (
                    datetime.now(timezone.utc) + timedelta(minutes=self.config.lockout_minutes)
                ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                failed = 0
            self.db.execute(
                "UPDATE Users SET FailedLogins = ?, LockedUntil = ? WHERE UserId = ?",
                (failed, lock_value, user["UserId"]),
            )
            self.db.audit(username, "login.failed", detail=f"attempt {failed}")
            raise AuthError("invalid username or password")

        now = utc_now()
        self.db.execute(
            "UPDATE Users SET FailedLogins = 0, LockedUntil = NULL, LastLoginAt = ?"
            " WHERE UserId = ?",
            (now, user["UserId"]),
        )
        self.db.audit(username, "login.ok", detail=f"role={user['Role']}")
        return Session(
            user_id=int(user["UserId"]),
            username=user["Username"],
            full_name=user["FullName"],
            role=user["Role"],
            must_change_password=bool(user["MustChangePw"]),
            started_at=now,
        )

    def logout(self, session: Optional[Session]) -> None:
        if session:
            self.db.audit(session.username, "logout")

    # ---------------------------------------------------------------- helper
    def _check_password_policy(self, password: str) -> None:
        if len(password or "") < self.config.min_password_length:
            raise AuthError(
                f"password must be at least {self.config.min_password_length} characters"
            )
