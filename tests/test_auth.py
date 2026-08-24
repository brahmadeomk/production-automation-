import pytest

from progstation.config import SecurityConfig
from progstation.errors import AuthError, PermissionDeniedError
from progstation.security.auth import (
    AuthManager,
    hash_password,
    verify_password,
)


@pytest.fixture
def auth(db):
    return AuthManager(db, SecurityConfig(pbkdf2_iterations=1000, max_failed_logins=3))


def test_password_hash_is_salted_and_verifiable():
    a, b = hash_password("secret", iterations=1000), hash_password("secret", iterations=1000)
    assert a != b                                  # different salts
    assert verify_password("secret", a)
    assert not verify_password("wrong", a)
    assert "secret" not in a


def test_verify_rejects_malformed_hash():
    assert not verify_password("secret", "not-a-hash")


def test_bootstrap_creates_one_admin_only(auth, db):
    password = auth.ensure_default_admin()
    assert password and db.count_admins() == 1
    assert auth.ensure_default_admin() is None


def test_first_login_requires_password_change(auth):
    password = auth.ensure_default_admin()
    assert auth.login("admin", password).must_change_password
    auth.set_password("admin", "newpass123", actor="admin")
    assert not auth.login("admin", "newpass123").must_change_password


def test_roles_gate_admin_actions(auth):
    auth.create_user("op1", "secret123", "operator")
    session = auth.login("op1", "secret123")
    assert not session.is_admin
    with pytest.raises(PermissionDeniedError):
        session.require_admin()


def test_lockout_after_repeated_failures(auth):
    auth.create_user("op1", "secret123", "operator")
    for _ in range(3):
        with pytest.raises(AuthError):
            auth.login("op1", "wrong")
    with pytest.raises(AuthError, match="locked"):
        auth.login("op1", "secret123")


def test_successful_login_clears_the_failure_count(auth, db):
    auth.create_user("op1", "secret123", "operator")
    with pytest.raises(AuthError):
        auth.login("op1", "wrong")
    auth.login("op1", "secret123")
    assert int(db.get_user("op1")["FailedLogins"]) == 0


def test_inactive_user_cannot_log_in(auth):
    auth.create_user("op1", "secret123", "operator")
    auth.create_user("boss", "secret123", "admin")
    auth.set_active("op1", False, actor="boss")
    with pytest.raises(AuthError):
        auth.login("op1", "secret123")


def test_cannot_remove_the_last_admin(auth):
    auth.ensure_default_admin()
    with pytest.raises(AuthError, match="last administrator"):
        auth.set_role("admin", "operator", actor="admin")
    with pytest.raises(AuthError, match="last administrator"):
        auth.set_active("admin", False, actor="admin")


def test_password_policy_enforced(auth):
    with pytest.raises(AuthError, match="at least"):
        auth.create_user("op2", "abc", "operator")


def test_duplicate_username_rejected(auth):
    auth.create_user("op1", "secret123", "operator")
    with pytest.raises(AuthError, match="already exists"):
        auth.create_user("OP1", "secret123", "operator")


def test_actions_are_audited(auth, db):
    auth.create_user("op1", "secret123", "operator", actor="admin")
    auth.login("op1", "secret123")
    actions = [row["Action"] for row in db.list_audit()]
    assert "user.create" in actions and "login.ok" in actions
