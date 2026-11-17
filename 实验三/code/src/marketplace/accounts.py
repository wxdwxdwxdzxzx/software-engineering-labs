"""Editable profiles and administrator controls with explicit public projections."""

import re

from .auth import hash_password, verify_password
from .base import Service
from .db import audit
from .domain import BusinessError, clean_text
from .media import prepare_photo


def contact_text(value):
    value = clean_text(value, "交易联系方式", 100, 3)
    if not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", value):
        raise BusinessError("请填写可联系的电话或微信等信息")
    return value


def writable(user):
    if user.get("status") == "受限":
        raise BusinessError("账号已受限，暂不能发布、预约或发起新互动；已有交易仍可处理")


class Accounts(Service):
    def profile(self, token):
        with self.store.connect() as conn:
            user = self.auth.require(conn, token)
            return dict(
                conn.execute(
                    "SELECT id,username,phone,contact,nickname,bio,region,avatar_thumb,status,"
                    "status_reason FROM users WHERE id=?",
                    (user["id"],),
                ).fetchone()
            )

    def update(self, token, nickname, bio, region, contact, *, avatar=None, remove_avatar=False):
        values = (
            clean_text(nickname, "昵称", 30),
            clean_text(bio, "简介", 500, 0),
            clean_text(region, "所在区域", 40, 0),
            contact_text(contact),
        )
        picture = prepare_photo(avatar) if avatar is not None else None
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            writable(user)
            conn.execute(
                "UPDATE users SET nickname=?,bio=?,region=?,contact=? WHERE id=?",
                (*values, user["id"]),
            )
            if picture is not None or remove_avatar:
                conn.execute(
                    "UPDATE users SET avatar=?,avatar_thumb=? WHERE id=?",
                    (*(picture or (None, None)), user["id"]),
                )
            audit(
                conn,
                user["id"],
                "修改个人资料",
                None,
                f"user:{user['id']}",
                {},
                {"nickname": values[0], "region": values[2], "contact_changed": True},
                self.clock.now_ms(),
            )
            return True

    def change_password(self, token, old_password, new_password):
        encoded = hash_password(new_password)
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token)
            row = conn.execute(
                "SELECT password_hash FROM users WHERE id=?", (user["id"],)
            ).fetchone()
            if not verify_password(old_password, row[0]):
                raise BusinessError("原密码不正确，密码未修改")
            if verify_password(new_password, row[0]):
                raise BusinessError("新密码不能与原密码相同")
            conn.execute(
                "UPDATE users SET password_hash=?,session_epoch=session_epoch+1 WHERE id=?",
                (encoded, user["id"]),
            )
            audit(
                conn,
                user["id"],
                "修改密码并注销会话",
                None,
                f"user:{user['id']}",
                {},
                {},
                self.clock.now_ms(),
            )
        return True

    def users(self, token):
        with self.store.connect() as conn:
            self.auth.require(conn, token, "admin")
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT id,username,nickname,status,status_reason FROM users WHERE role='user' "
                    "ORDER BY id"
                )
            ]

    def set_status(self, token, user_id, restricted, reason):
        reason = clean_text(reason, "账号处理理由", 500)
        with self.store.transaction() as conn:
            admin = self.auth.require(conn, token, "admin")
            row = conn.execute(
                "SELECT status FROM users WHERE id=? AND role='user'", (user_id,)
            ).fetchone()
            if row is None:
                raise BusinessError("普通用户不存在")
            state = "受限" if restricted else "正常"
            if row[0] == state:
                return False
            conn.execute(
                "UPDATE users SET status=?,status_reason=? WHERE id=?", (state, reason, user_id)
            )
            audit(
                conn,
                admin["id"],
                "调整账号状态",
                None,
                f"user:{user_id}",
                {"status": row[0]},
                {"status": state, "reason": reason},
                self.clock.now_ms(),
            )
            return True

    def categories(self, token, *, all_states=False):
        with self.store.connect() as conn:
            self.auth.require(conn, token, "admin" if all_states else None)
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT name,enabled FROM categories "
                    + ("" if all_states else "WHERE enabled=1 ")
                    + "ORDER BY name"
                )
            ]

    def save_category(self, token, name, enabled=True):
        name = clean_text(name, "分类名称", 30)
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "admin")
            conn.execute(
                "INSERT INTO categories(name,enabled) VALUES(?,?) ON CONFLICT(name) "
                "DO UPDATE SET enabled=excluded.enabled",
                (name, int(enabled)),
            )
            audit(
                conn,
                user["id"],
                "维护分类",
                None,
                "category",
                {},
                {"name": name, "enabled": bool(enabled)},
                self.clock.now_ms(),
            )
            return True
