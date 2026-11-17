"""Read models with SQL-side ownership filters and no contact-field leakage."""

from datetime import datetime

from .base import Service
from .domain import BUSINESS_TZ, BusinessError, cents, clean_text, day_bounds
from .domain import ProductState as P

PUBLIC_PRODUCT = (
    "p.id,p.seller_id,p.title,p.category,p.description,"
    "CASE WHEN p.price_missing=1 THEN NULL ELSE p.price_cents END AS price_cents,p.state,p.version,"
    "p.created_at,p.published_at,p.meeting_region,p.c"
    "ondition,p.brand,p.handoff_method,p.price_missing,"
    "p.freight_cents,p.free_shipping,p.origin_city,p.origin_district,p.listing_blocked,"
    "CASE WHEN p.origin_city<>'' THEN p.origin_city || ' ' || p.origin_district "
    "ELSE p.meeting_region END AS location"
)
COVER = (
    "(SELECT thumbnail FROM product_photos WHERE product_id=p.id AND position=0) AS cover_thumbnail"
)
SEARCH_ORDERS = {
    "latest": "p.published_at DESC,p.id DESC",
    "price_asc": "p.price_cents ASC,p.id DESC",
    "price_desc": "p.price_cents DESC,p.id DESC",
}
REVIEW_FIELDS = "p.submitted,p.review_reason,p.reviewer_id,p.reviewed_at"


def literal_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def region_expression(column):
    expression = column
    for char in (32, 9, 10, 13, 12288, 160):
        expression = f"replace({expression},char({char}),'')"
    return expression


def public_row(row):
    result = dict(row)
    if "location" in result:
        parts = ["".join(result.get(key, "").split()) for key in ("origin_city", "origin_district")]
        result["location"] = " ".join(p for p in parts if p) or " ".join(
            result.get("meeting_region", "").split()
        )
    return result


def checked_product_id(value):
    if isinstance(value, str):
        value = value.strip()
        if not value.isascii() or not value.isdecimal() or len(value) > 19:
            raise BusinessError("商品编号应为有效的正整数")
        value = int(value)
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise BusinessError("商品编号超出有效范围")
    return value


class Queries(Service):
    """All reads return fresh database data, not UI-held object references."""

    def search(
        self,
        token,
        keyword="",
        category="",
        minimum="",
        maximum="",
        offset=0,
        *,
        product_id="",
        sort="latest",
        state="已发布",
        condition="",
        region="",
        delivery_mode="",
    ):
        with self.store.connect() as conn:
            self.auth.require(conn, token)
            self.sweep(conn)
            conn.execute("BEGIN")
            return self._search(
                conn,
                keyword,
                category,
                minimum,
                maximum,
                offset,
                product_id,
                sort,
                state,
                condition,
                region,
                delivery_mode,
            )

    def categories(self, token):
        with self.store.connect() as conn:
            self.auth.require(conn, token)
            return [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT category FROM products WHERE state='已发布' ORDER BY category"
                )
            ]

    @staticmethod
    def _search(
        conn,
        keyword,
        category,
        minimum,
        maximum,
        offset,
        product_id,
        sort,
        state="已发布",
        condition="",
        region="",
        delivery_mode="",
    ):
        """One read connection per request; expiry writes still own transactions."""
        if state not in ("已发布", "已售出"):
            raise BusinessError("公开搜索仅支持在售或已售出商品")
        where = ["p.state=?", "p.listing_blocked=0"]
        if delivery_mode not in ("", "邮寄", "面交"):
            raise BusinessError("交付方式筛选无效")
        args = [state]
        if delivery_mode:
            where.append(
                "p.handoff_method IN (?,?)"
                if delivery_mode == "邮寄"
                else "p.handoff_method IN (?,?,?,?)"
            )
            args += (
                ["仅邮寄", "邮寄或面交"]
                if delivery_mode == "邮寄"
                else ["仅面交", "邮寄或面交", "公共地点面交", "双方协商面交"]
            )
        if state == "已发布":
            where.append(
                "EXISTS(SELECT 1 FROM users owner WHERE owner.id=p.seller_id "
                "AND owner.status='正常')"
            )
        if condition:
            from .products import CONDITIONS

            if condition not in CONDITIONS:
                raise BusinessError("请选择有效成色")
            where.append("p.condition=?")
            args.append(condition)
        region = "".join(clean_text(region, "地区", 120, 0).split())
        if region:
            expressions = [
                region_expression(column)
                for column in ("p.meeting_region", "p.origin_city || p.origin_district")
            ]
            where.append(
                "(" + " OR ".join(f"{expr} LIKE ? ESCAPE '\\'" for expr in expressions) + ")"
            )
            args += [f"%{literal_like(region)}%"] * len(expressions)
        if sort not in SEARCH_ORDERS:
            raise BusinessError("请选择有效的排序方式")
        order = SEARCH_ORDERS[sort]
        terms = keyword.split()
        if len(keyword) > 240 or len(terms) > 12:
            raise BusinessError("关键词最多 12 个，共 240 字")
        for term in terms:
            escaped = literal_like(term)
            where.append(
                "(p.title LIKE ? ESCAPE '\\' OR p.description LIKE ? ESCAPE '\\' OR "
                "p.brand LIKE ? ESCAPE '\\')"
            )
            args += [f"%{escaped}%"] * 3
        if str(product_id).strip():
            where.append("p.id=?")
            args.append(checked_product_id(product_id))
        if category.strip():
            where.append("p.category=?")
            args.append(category.strip())
        low = cents(minimum, allow_zero=True) if str(minimum).strip() else None
        high = cents(maximum, allow_zero=True) if str(maximum).strip() else None
        if low is not None and high is not None and low > high:
            raise BusinessError("最低价格不能高于最高价格")
        for value, operator in [(low, ">="), (high, "<=")]:
            if value is not None:
                where.append(f"p.price_cents{operator}?")
                args.append(value)
        if not isinstance(offset, int) or not 0 <= offset <= 9_223_372_036_854_775_807:
            raise BusinessError("分页参数无效")
        clause = " AND ".join(where)
        total = conn.execute(f"SELECT COUNT(*) FROM products p WHERE {clause}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT {PUBLIC_PRODUCT},{COVER},u.username AS seller_name FROM products p "
            "JOIN users u ON u.id=p.seller_id WHERE p.id IN ("
            f"SELECT p.id FROM products p WHERE {clause} "
            f"ORDER BY {order} LIMIT 100 OFFSET ?) ORDER BY {order}",
            (*args, offset),
        )
        return {"items": [public_row(r) for r in rows], "total": total}

    def own_products(self, token, state=""):
        self.sweep()
        with self.store.connect() as conn:
            user = self.auth.require(conn, token, "user")
            clause = " AND state=?" if state else ""
            args = (user["id"], state) if state else (user["id"],)
            return [
                dict(r)
                for r in conn.execute(
                    f"SELECT {PUBLIC_PRODUCT},{REVIEW_FIELDS},{COVER} FROM products p "
                    f"WHERE seller_id=?{clause} ORDER BY id DESC",
                    args,
                )
            ]

    def detail(self, token, product_id, *, include_photos=False):
        self.sweep()
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            user = self.auth.require(conn, token)
            item = conn.execute(
                f"SELECT {PUBLIC_PRODUCT},{COVER},u.username AS seller_name,"
                "(u.status='正常') AS accepting_new FROM products p "
                "JOIN users u ON u.id=p.seller_id WHERE p.id=?",
                (product_id,),
            ).fetchone()
            if item is None:
                raise BusinessError("商品不存在或已删除")
            item = public_row(item)
            participant = conn.execute(
                "SELECT 1 FROM reservations WHERE product_id=? AND buyer_id=? "
                "AND state IN ('待确认','待交易')",
                (product_id, user["id"]),
            ).fetchone()
            if (
                user["role"] != "admin"
                and item["seller_id"] != user["id"]
                and (item["state"] not in (P.PUBLISHED, P.SOLD) or item["listing_blocked"])
                and not participant
            ):
                raise BusinessError("商品已不在售或无权查看，请刷新列表")
            if user["role"] == "admin" or item["seller_id"] == user["id"]:
                item.update(
                    conn.execute(
                        f"SELECT {REVIEW_FIELDS} FROM products p WHERE p.id=?", (product_id,)
                    ).fetchone()
                )
            if include_photos:
                item["photos"] = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id,content FROM product_photos "
                        "WHERE product_id=? ORDER BY position",
                        (product_id,),
                    )
                ]
            return item

    def seller_catalog(self, token, seller_id):
        return self.seller_profile(token, seller_id)["products"]

    def seller_profile(self, token, seller_id):
        """Public identity has an explicit whitelist, even when the catalog is empty."""
        if not isinstance(seller_id, int) or not 0 < seller_id <= 9_223_372_036_854_775_807:
            raise BusinessError("卖家编号无效")
        self.sweep()
        with self.store.connect() as conn:
            self.auth.require(conn, token)
            seller = conn.execute(
                "SELECT id,username,nickname,bio,region,avatar_thumb FROM users "
                "WHERE id=? AND role='user'",
                (seller_id,),
            ).fetchone()
            if seller is None:
                raise BusinessError("卖家不存在")
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id,title,category,price_cents,state,meeting_region FROM products "
                    "WHERE seller_id=? AND listing_blocked=0 AND state IN "
                    "('已发布','已售出') ORDER BY id DESC",
                    (seller_id,),
                )
            ]
            return {**dict(seller), "seller_id": seller["id"], "products": rows}

    def reservations(self, token, *, seller=False):
        self.sweep()
        with self.store.connect() as conn:
            user = self.auth.require(conn, token, "user")
            field = "p.seller_id" if seller else "r.buyer_id"
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT r.id,r.product_id,r.buyer_id,r.state,r.created_at,r.deadline,"
                    "r.finished_at,CASE WHEN r.state IN ('待确认','待交易') THEN r.proposed_time "
                    "ELSE '' END AS proposed_time,r.agreed_at,r.decision_reason,"
                    "r.snapshot_title,r.snapshot_category,r.sn"
                    "apshot_description,r.snapshot_price_cents,"
                    "r.snapshot_origin,r.snapshot_title AS title,p.state AS product_state,"
                    "p.version AS product_version,p.seller_id,r.snapshot_buyer_name AS buyer_name,"
                    "r.snapshot_seller_name AS seller_name,d.mode AS "
                    "delivery_mode,d.phase AS fulfillment_phase,d.legacy AS delivery_legacy "
                    "FROM reservations r JOIN products p ON p.id=r.product_id JOIN "
                    "delivery_orders d ON d.reservation_id=r.id "
                    f"WHERE {field}=? ORDER BY r.id DESC",
                    (user["id"],),
                )
            ]

    def pending_products(self, token):
        with self.store.connect() as conn:
            self.auth.require(conn, token, "admin")
            return [
                dict(r)
                for r in conn.execute(
                    f"SELECT {PUBLIC_PRODUCT},{REVIEW_FIELDS},{COVER},"
                    "u.username AS seller_name FROM products p "
                    "JOIN users u ON u.id=p.seller_id WHERE p.state='待审核' ORDER BY p.id"
                )
            ]

    def reports(self, token):
        self.sweep()
        with self.store.connect() as conn:
            user = self.auth.require(conn, token)
            clause, args = (
                ("", ()) if user["role"] == "admin" else ("WHERE r.reporter_id=?", (user["id"],))
            )
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT r.*,r.snapshot_title AS title,p.state AS product_state,"
                    "p.version AS product_version "
                    f"FROM reports r JOIN products p ON p.id=r.product_id {clause} "
                    "ORDER BY r.id DESC",
                    args,
                )
            ]

    def notifications(self, token):
        with self.store.connect() as conn:
            user = self.auth.require(conn, token, "user")
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT id,report_id,content,created_at FROM notifications WHERE user_id=? "
                    "ORDER BY id DESC",
                    (user["id"],),
                )
            ]

    def statistics(self, token, day=None):
        day = day or datetime.fromtimestamp(self.clock.now_ms() / 1000, BUSINESS_TZ).strftime(
            "%Y-%m-%d"
        )
        start, end = day_bounds(day)
        self.sweep()
        with self.store.connect() as conn:
            self.auth.require(conn, token, "admin")
            daily = conn.execute(
                "SELECT COUNT(DISTINCT product_id) FROM audit_logs "
                "WHERE action='审核通过' AND created_at>=? AND created_at<?",
                (start, end),
            ).fetchone()[0]
            categories = [
                dict(r)
                for r in conn.execute(
                    "SELECT category,COUNT(*) AS count FROM products WHERE state='已发布' "
                    "GROUP BY category ORDER BY category"
                )
            ]
            counts = {}
            for name, table, field in [
                ("reservations", "reservations", "created_at"),
                ("completed", "reservations", "finished_at"),
                ("reports_handled", "reports", "handled_at"),
            ]:
                extra = " AND state='已完成'" if name == "completed" else ""
                counts[name] = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {field}>=? AND {field}<?{extra}",
                    (start, end),
                ).fetchone()[0]
            counts["reports_pending"] = conn.execute(
                "SELECT COUNT(*) FROM reports WHERE state='待处理'"
            ).fetchone()[0]
            return {"day": day, "daily_new": daily, "categories": categories, **counts}

    def logs(self, token, product_id="", actor="", start_day="", end_day=""):
        where, args = [], []
        if str(product_id).strip():
            where.append("product_id=?")
            args.append(checked_product_id(product_id))
        if actor.strip():
            where.append("actor=?")
            args.append(actor.strip())
        if start_day:
            where.append("created_at>=?")
            args.append(day_bounds(start_day)[0])
        if end_day:
            where.append("created_at<?")
            args.append(day_bounds(end_day)[1])
        if start_day and end_day and start_day > end_day:
            raise BusinessError("日志起始日期不能晚于结束日期")
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self.store.connect() as conn:
            self.auth.require(conn, token, "admin")
            return [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM audit_logs{clause} ORDER BY id DESC LIMIT 1000", args
                )
            ]

    def managed_products(self, token, state=""):
        with self.store.connect() as conn:
            self.auth.require(conn, token, "admin")
            if state and state not in tuple(P):
                raise BusinessError("商品状态无效")
            return [
                dict(r)
                for r in conn.execute(
                    f"SELECT {PUBLIC_PRODUCT},{REVIEW_FIELDS},{COVER} FROM products p "
                    + ("WHERE p.state=? " if state else "")
                    + "ORDER BY p.id DESC LIMIT 1000",
                    (state,) if state else (),
                )
            ]

    def review_history(self, token, product_id):
        with self.store.connect() as conn:
            user = self.auth.require(conn, token)
            row = conn.execute(
                "SELECT seller_id FROM products WHERE id=?", (product_id,)
            ).fetchone()
            if row is None or (user["role"] != "admin" and row[0] != user["id"]):
                raise BusinessError("只有卖家本人或管理员可查询审核历史")
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT approved,reason,created_at,reviewer_id "
                    "FROM product_reviews WHERE product_id=? ORDER BY id",
                    (product_id,),
                )
            ]
