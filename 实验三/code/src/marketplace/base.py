"""Transactional command orchestration and shared state consistency rules."""

from contextlib import nullcontext

from .db import active_reservation, assert_consistent, audit, product
from .domain import BusinessError
from .domain import ProductState as P
from .domain import ReservationState as R


class Service:
    """Shared storage/session/clock dependencies, not a UI event handler."""

    def __init__(self, store, auth, clock):
        self.store = store
        self.auth = auth
        self.clock = clock

    @staticmethod
    def check_version(item, version):
        if item["version"] != version:
            raise BusinessError("商品状态已变更，请刷新页面后重试")

    @staticmethod
    def own(user, item):
        if user["role"] != "user" or item["seller_id"] != user["id"]:
            raise BusinessError("权限不足，只能管理自己发布的商品")

    @staticmethod
    def change(
        conn,
        actor,
        action,
        item,
        new_state,
        now,
        reservation=None,
        new_r=None,
        *,
        field_changes=None,
    ):
        """One paired state mutation and log; caller owns the transaction."""
        before = {"product": item["state"], "version": item["version"]}
        after = {"product": str(new_state), "version": item["version"] + 1}
        if field_changes is not None:
            before["fields"], after["fields"] = field_changes
        if reservation is not None:
            before.update(reservation=reservation["state"], reservation_id=reservation["id"])
            after.update(reservation=str(new_r), reservation_id=reservation["id"])
            finished = None if new_r in (R.WAITING, R.TRADING) else now
            conn.execute(
                "UPDATE reservations SET state=?,finished_at=? WHERE id=?",
                (str(new_r), finished, reservation["id"]),
            )
        if reservation is not None:
            from .delivery import sync_terminal

            sync_terminal(conn, reservation["id"], new_r)
        conn.execute(
            "UPDATE products SET state=?,version=version+1 WHERE id=?",
            (str(new_state), item["id"]),
        )
        audit(conn, actor, action, item["id"], f"product:{item['id']}", before, after, now)
        from .messaging import notify

        recipients = [item["seller_id"]]
        if reservation is not None:
            recipients.append(reservation["buyer_id"])
            if new_r not in (R.WAITING, R.TRADING):
                conn.execute(
                    "UPDATE handoff_changes SET state='已失效',handled_at=? "
                    "WHERE reservation_id=? AND state='待确认'",
                    (now, reservation["id"]),
                )
        notify(
            conn,
            recipients,
            f"product:{item['id']}:v{item['version'] + 1}",
            f"商品 #{item['id']}：{action}，当前{new_state}。",
            item["id"],
            now,
        )

    def _expire(self, conn, item, now, only_id=None):
        current = active_reservation(conn, item["id"])
        if current and current["state"] == R.WAITING and current["deadline"] <= now:
            if only_id is not None and current["id"] != only_id:
                return False
            self.change(conn, "SYSTEM", "预约超时取消", item, P.PUBLISHED, now, current, R.TIMEOUT)
            assert_consistent(conn, item["id"])
            return True
        return False

    def expire_reservation(self, reservation_id):
        """System operation tied to one historical ID, never to a replacement."""
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT product_id FROM reservations WHERE id=?", (reservation_id,)
            ).fetchone()
            if row is None:
                return False
            return self._expire(conn, product(conn, row[0]), self.clock.now_ms(), reservation_id)

    def sweep(self, connection=None):
        """A timer may lag; every relevant write also checks the deadline."""
        with self.store.connect() if connection is None else nullcontext(connection) as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM reservations WHERE state='待确认' AND deadline<=?",
                    (self.clock.now_ms(),),
                )
            ]
        return sum(self.expire_reservation(rid) for rid in ids)

    def execute(
        self,
        token,
        product_id,
        version,
        operation,
        *,
        role="user",
        authorize=None,
        allow_restricted=False,
    ):
        """Preserve due expiry while rolling back a refused business operation."""
        error = None
        result = None
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, role)
            if role == "user" and not allow_restricted:
                from .accounts import writable

                writable(user)
            item = product(conn, product_id)
            if authorize is not None:
                authorize(conn, user, item)
            now = self.clock.now_ms()
            self._expire(conn, item, now)
            conn.execute("SAVEPOINT business_command")
            try:
                item = product(conn, product_id)
                if version is not None:
                    self.check_version(item, version)
                result = operation(conn, user, item, now)
                if conn.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone():
                    assert_consistent(conn, product_id)
            except BusinessError as exc:
                conn.execute("ROLLBACK TO business_command")
                error = exc
            conn.execute("RELEASE business_command")
        if error is not None:
            raise error
        return result
