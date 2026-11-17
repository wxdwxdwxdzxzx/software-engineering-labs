"""Password verification and opaque in-memory sessions; no UI-supplied role."""

import hashlib
import hmac
import secrets
import sqlite3
import threading

from .db import audit
from .domain import BusinessError, clean_text, phone_number

ITERATIONS = 260_000


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise BusinessError("密码长度应为8至128个字符")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, rounds, salt, expected = encoded.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError, AttributeError):
        return False


class Auth:
    """One auth service per application; sessions are invalid after restart."""

    def __init__(self, store, clock):
        self.store = store
        self.clock = clock
        self._sessions = {}
        self._lock = threading.Lock()

    def register(self, phone: str, username: str, password: str, contact: str) -> int:
        phone = phone_number(phone)
        username = clean_text(username, "用户名", 24, 2)
        contact = clean_text(contact, "联系方式", 100)
        encoded = hash_password(password)
        with self.store.transaction() as conn:
            for column, value, label in [
                ("phone", phone, "手机号"),
                ("username", username, "用户名"),
            ]:
                if conn.execute(f"SELECT 1 FROM users WHERE {column}=?", (value,)).fetchone():
                    raise BusinessError(f"{label}已被使用")
            cursor = conn.execute(
                "INSERT INTO users(phone,username,password_hash,contact,role) "
                "VALUES(?,?,?,?,'user')",
                (phone, username, encoded, contact),
            )
            user_id = cursor.lastrowid
            conn.execute("UPDATE users SET nickname=? WHERE id=?", (username, user_id))
            audit(
                conn,
                user_id,
                "注册账号",
                None,
                f"user:{user_id}",
                {},
                {"username": username},
                self.clock.now_ms(),
            )
            return user_id

    def needs_admin(self) -> bool:
        """Only a new local installation may offer the first-admin wizard."""
        with self.store.connect() as conn:
            return conn.execute("SELECT 1 FROM users WHERE role='admin'").fetchone() is None

    def provision_admin(self, username: str, password: str, *, first_only=False) -> int:
        """Trusted local setup; first-only validation also runs inside the write lock."""
        username = clean_text(username, "管理员登录名", 24, 2)
        encoded = hash_password(password)
        with self.store.transaction() as conn:
            if first_only and conn.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                raise BusinessError("管理员已初始化，请使用管理员入口登录")
            if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise BusinessError("用户名已被使用")
            cursor = conn.execute(
                "INSERT INTO users(username,password_hash,contact,role) VALUES(?,?,?,'admin')",
                (username, encoded, "管理员账号不公开联系方式"),
            )
            conn.execute("UPDATE users SET nickname=? WHERE id=?", (username, cursor.lastrowid))
            audit(
                conn,
                "SYSTEM",
                "受控创建管理员",
                None,
                f"user:{cursor.lastrowid}",
                {},
                {"username": username},
                self.clock.now_ms(),
            )
            return cursor.lastrowid

    def login(self, identity: str, password: str, *, admin: bool = False) -> str:
        if not isinstance(password, str) or len(password) > 128:
            raise BusinessError("账号或密码不正确")
        role = "admin" if admin else "user"
        column = "username" if admin else "phone"
        with self.store.connect() as conn:
            row = conn.execute(
                f"SELECT * FROM users WHERE {column}=? AND role=?", (identity.strip(), role)
            ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise BusinessError("账号或密码不正确，请确认登录入口")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = (row["id"], row["session_epoch"])
        return token

    def logout(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def require(self, conn: sqlite3.Connection, token: str, role: str | None = None) -> dict:
        with self._lock:
            session = self._sessions.get(token)
        row = conn.execute(
            "SELECT id,username,role,status,session_epoch FROM users WHERE id=?",
            (session[0] if session else None,),
        ).fetchone()
        if row is None or row["session_epoch"] != session[1]:
            raise BusinessError("请先登录，会话已失效")
        if role is not None and row["role"] != role:
            raise BusinessError("权限不足，无法执行该操作")
        return dict(row)

    def me(self, token: str) -> dict:
        with self.store.connect() as conn:
            return self.require(conn, token)
