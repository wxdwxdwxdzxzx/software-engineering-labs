"""Private recipient and sender address books; transaction snapshots own copies."""

import json
import re

from .base import Service
from .domain import BusinessError, clean_text

ADDRESS_FIELDS = ("recipient", "telephone", "province", "city", "district", "detail")


def contact_phone(value):
    text = clean_text(value, "联系电话", 32)
    digits = re.sub(r"[ +()\-]", "", text)
    if not digits.isascii() or not digits.isdigit() or not 6 <= len(digits) <= 20:
        raise BusinessError("联系电话须含6至20位数字，可使用+、空格、括号或短横线")
    return text


def owned_address(conn, user_id, address_id, kind):
    if type(address_id) is not int:
        raise BusinessError(f"请选择自己的{kind}地址")
    row = conn.execute(
        "SELECT * FROM addresses WHERE id=? AND owner_id=? AND kind=?",
        (address_id, user_id, kind),
    ).fetchone()
    if row is None:
        raise BusinessError(f"{kind}地址不存在或不属于当前账号")
    return {k: row[k] for k in ADDRESS_FIELDS}


def snapshot_address(conn, user_id, address_id, kind="收货"):
    return json.dumps(owned_address(conn, user_id, address_id, kind), ensure_ascii=False)


class Addresses(Service):
    def list(self, token, kind="收货"):
        if kind not in ("收货", "发货"):
            raise BusinessError("地址用途无效")
        with self.store.connect() as conn:
            user = self.auth.require(conn, token, "user")
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM addresses WHERE owner_id=? AND kind=? "
                    "ORDER BY is_default DESC,id DESC",
                    (user["id"], kind),
                )
            ]

    def save(self, token, *, kind="收货", address_id=None, is_default=False, **fields):
        if kind not in ("收货", "发货") or set(fields) != set(ADDRESS_FIELDS):
            raise BusinessError("请完整填写地址字段并选择收货或发货用途")
        values = {
            k: clean_text(fields[k], label, limit)
            for k, label, limit in (
                ("recipient", "收件/发件人", 50),
                ("telephone", "联系电话", 32),
                ("province", "省份", 40),
                ("city", "城市", 40),
                ("district", "区县", 40),
                ("detail", "详细地址", 200),
            )
        }
        values["telephone"] = contact_phone(values["telephone"])
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            if address_id is not None:
                owned_address(conn, user["id"], address_id, kind)
            if is_default:
                conn.execute(
                    "UPDATE addresses SET is_default=0 WHERE owner_id=? AND kind=?",
                    (user["id"], kind),
                )
            if address_id is None:
                address_id = conn.execute(
                    "INSERT INTO addresses(owner_id,kind,recipient,tel"
                    "ephone,province,city,district,detail,is_default) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (user["id"], kind, *values.values(), int(bool(is_default))),
                ).lastrowid
            else:
                conn.execute(
                    "UPDATE addresses SET "
                    "recipient=?,telephone=?,province=?,city=?,district=?,detail=?,"
                    "is_default=? WHERE id=?",
                    (*values.values(), int(bool(is_default)), address_id),
                )
            return address_id

    def default(self, token, address_id, kind="收货"):
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            owned_address(conn, user["id"], address_id, kind)
            conn.execute(
                "UPDATE addresses SET is_default=0 WHERE owner_id=? AND kind=?", (user["id"], kind)
            )
            conn.execute("UPDATE addresses SET is_default=1 WHERE id=?", (address_id,))
            return True

    def delete(self, token, address_id, kind="收货"):
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            owned_address(conn, user["id"], address_id, kind)
            conn.execute("DELETE FROM addresses WHERE id=?", (address_id,))
            return True
