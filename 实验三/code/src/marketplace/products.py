"""Seller product lifecycle and administrator review commands."""

from .base import Service
from .db import active_reservation, audit, product
from .domain import BusinessError, cents, clean_text, display_money
from .domain import ProductState as P
from .media import prepare_photos, replace_photos
from .places import owned_place


def product_result(conn, product_id):
    result = product(conn, product_id)
    result.pop("meeting_point")
    result.pop("meeting_note")
    return result


CONDITIONS = ("全新未使用", "几乎全新", "成色良好", "有使用痕迹", "有瑕疵需说明")


class Products(Service):
    """All mutable product fields are validated here, including forged input."""

    @staticmethod
    def fields(title, category, description, price, *, incomplete=False):
        return {
            "title": clean_text(title, "标题", 80, 0 if incomplete else 1),
            "category": clean_text(category, "分类", 30, 0 if incomplete else 1),
            "description": clean_text(description, "描述", 2000, 0 if incomplete else 1),
            "price_cents": 1 if incomplete and not str(price).strip() else cents(price),
        }

    @staticmethod
    def save_place(conn, product_id, owner_id, place_id):
        place = owned_place(conn, owner_id, place_id) if place_id is not None else None
        conn.execute(
            "UPDATE products SET meeting_region=?,meeting_point=?,meeting_note=? WHERE id=?",
            (place["region"], place["point"], place["note"], product_id)
            if place
            else ("", "", "", product_id),
        )

    def create_draft(
        self,
        token,
        title="",
        category="",
        description="",
        price="",
        *,
        photos=(),
        meeting_place_id=None,
        condition="成色良好",
        brand="",
        region="",
        handoff_method="仅面交",
        free_shipping=True,
        shipping_fee="0",
        origin_city="",
        origin_district="",
    ):
        fields = self.fields(title, category, description, price, incomplete=True)
        extras = self.extra_fields(condition, brand, region, handoff_method)
        photos = prepare_photos(photos)
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            from .accounts import writable

            writable(user)
            now = self.clock.now_ms()
            cur = conn.execute(
                "INSERT INTO products(seller_id,title,category,description,price_cents,state,"
                "created_at) VALUES(?,?,?,?,?,'草稿',?)",
                (
                    user["id"],
                    fields["title"],
                    fields["category"],
                    fields["description"],
                    fields["price_cents"],
                    now,
                ),
            )
            replace_photos(conn, cur.lastrowid, photos)
            self.save_place(conn, cur.lastrowid, user["id"], meeting_place_id)
            conn.execute(
                "UPDATE products SET condition=?,brand=?,meeting_region=CASE WHEN ?!='' "
                "THEN ? ELSE meeting_region END,handoff_method=?,price_missing=? WHERE id=?",
                (
                    extras[0],
                    extras[1],
                    extras[2],
                    extras[2],
                    extras[3],
                    int(not str(price).strip()),
                    cur.lastrowid,
                ),
            )
            self.save_delivery_fields(
                conn, cur.lastrowid, free_shipping, shipping_fee, origin_city, origin_district
            )
            audit(
                conn,
                user["id"],
                "创建草稿",
                cur.lastrowid,
                f"product:{cur.lastrowid}",
                {},
                {"product": P.DRAFT, "version": 1},
                now,
            )
            return product_result(conn, cur.lastrowid)

    def edit_product(self, token, product_id, version, **fields):
        allowed = {
            "title",
            "category",
            "description",
            "price",
            "photos",
            "meeting_place_id",
            "condition",
            "brand",
            "region",
            "handoff_method",
            "free_shipping",
            "shipping_fee",
            "origin_city",
            "origin_district",
        }
        if not fields or set(fields) - allowed:
            raise BusinessError("不支持修改该字段")

        def operation(conn, user, item, now):
            if item["state"] not in (P.DRAFT, P.REJECTED, P.PUBLISHED):
                raise BusinessError("当前状态不能编辑；预约中或已下架商品不可修改")
            if "photos" in fields:
                replace_photos(conn, product_id, prepare_photos(fields["photos"]))
            if "meeting_place_id" in fields:
                self.save_place(conn, product_id, user["id"], fields["meeting_place_id"])
            combined = dict(item)
            combined["price"] = "" if item["price_missing"] else display_money(item["price_cents"])
            combined.update(fields)
            values = self.fields(
                *(combined[k] for k in ("title", "category", "description", "price")),
                incomplete=item["state"] in (P.DRAFT, P.REJECTED),
            )
            conn.execute(
                "UPDATE products SET title=?,category=?,description=?,price_cents=? WHERE id=?",
                (*values.values(), product_id),
            )
            extra = self.extra_fields(
                combined["condition"],
                combined["brand"],
                combined.get("region", combined["meeting_region"]),
                combined["handoff_method"],
            )
            conn.execute(
                "UPDATE products SET condition=?,brand=?,handoff_method=?,price_missing=? "
                "WHERE id=?",
                (extra[0], extra[1], extra[3], int(not str(combined["price"]).strip()), product_id),
            )
            if "region" in fields:
                conn.execute(
                    "UPDATE products SET meeting_region=? WHERE id=?", (extra[2], product_id)
                )
            self.save_delivery_fields(
                conn,
                product_id,
                combined["free_shipping"],
                combined.get("shipping_fee", display_money(combined["freight_cents"])),
                combined["origin_city"],
                combined["origin_district"],
            )
            if item["state"] == P.PUBLISHED:
                self.validate_submission(conn, product(conn, product_id))
            new_state = P.REVIEW if item["state"] == P.PUBLISHED else item["state"]
            saved = product(conn, product_id)
            public_fields = (
                "title",
                "category",
                "description",
                "price_cents",
                "price_missing",
                "condition",
                "brand",
                "handoff_method",
                "meeting_region",
                "free_shipping",
                "freight_cents",
                "origin_city",
                "origin_district",
            )
            changed = [name for name in public_fields if item[name] != saved[name]]
            old_fields = {name: item[name] for name in changed}
            new_fields = {name: saved[name] for name in changed}
            if "photos" in fields:
                new_fields["pictures_changed"] = True
            if "meeting_place_id" in fields:
                new_fields["private_handoff_changed"] = True
            self.change(
                conn,
                user["id"],
                "修改并重审" if new_state == P.REVIEW else "修改商品",
                item,
                new_state,
                now,
                field_changes=(old_fields, new_fields),
            )
            return product_result(conn, product_id)

        return self.execute(
            token, product_id, version, operation, authorize=lambda _c, u, p: self.own(u, p)
        )

    def delete_draft(self, token, product_id, version):
        def operation(conn, user, item, now):
            if item["state"] != P.DRAFT or item["submitted"]:
                raise BusinessError("仅从未提交审核的草稿可以彻底删除")
            audit(
                conn,
                user["id"],
                "删除草稿",
                product_id,
                f"product:{product_id}",
                {"product": item["state"]},
                {"deleted": True},
                now,
            )
            conn.execute("DELETE FROM products WHERE id=?", (product_id,))
            return True

        return self.execute(
            token, product_id, version, operation, authorize=lambda _c, u, p: self.own(u, p)
        )

    def submit(self, token, product_id, version):
        def operation(conn, user, item, now):
            if item["state"] not in (P.DRAFT, P.REJECTED):
                raise BusinessError("仅草稿或审核驳回商品可以提交审核")
            self.validate_submission(conn, item)
            conn.execute("UPDATE products SET submitted=1 WHERE id=?", (product_id,))
            self.change(conn, user["id"], "提交审核", item, P.REVIEW, now)
            return product_result(conn, product_id)

        return self.execute(
            token, product_id, version, operation, authorize=lambda _c, u, p: self.own(u, p)
        )

    def review(self, token, product_id, version, approve, reason=None):
        reason = clean_text(
            reason if reason is not None else ("审核通过" if approve else "审核不通过"),
            "审核理由",
            1000,
        )

        def operation(conn, user, item, now):
            if item["state"] != P.REVIEW:
                raise BusinessError("商品已不处于待审核状态，请刷新")
            if approve:
                self.validate_submission(conn, item)
                if conn.execute(
                    "SELECT 1 FROM reports WHERE product_id=? AND state='待处理'", (product_id,)
                ).fetchone():
                    raise BusinessError("商品仍有待处理举报，请先核实举报再审核发布")
            self.change(
                conn,
                user["id"],
                "审核通过" if approve else "审核驳回",
                item,
                P.PUBLISHED if approve else P.REJECTED,
                now,
            )
            conn.execute(
                "UPDATE products SET review_reason=?,reviewer_id=?,reviewed_at=?,"
                "published_at=CASE WHEN ? THEN ? ELSE published_at END WHERE id=?",
                (reason, user["id"], now, approve, now, product_id),
            )
            conn.execute(
                "INSERT INTO product_reviews(product_id,reviewer_id,approved,reason,created_at) "
                "VALUES(?,?,?,?,?)",
                (product_id, user["id"], int(approve), reason, now),
            )
            return product_result(conn, product_id)

        return self.execute(token, product_id, version, operation, role="admin")

    def take_down(self, token, product_id, version):
        def operation(conn, user, item, now):
            if item["state"] != P.PUBLISHED or active_reservation(conn, product_id):
                raise BusinessError("只有无有效预约的已发布商品可主动下架")
            self.change(conn, user["id"], "主动下架", item, P.OFFLINE, now)
            return product_result(conn, product_id)

        return self.execute(
            token, product_id, version, operation, authorize=lambda _c, u, p: self.own(u, p)
        )

    def relist(self, token, product_id, version):
        def operation(conn, user, item, now):
            if conn.execute(
                "SELECT 1 FROM delivery_orders d JOIN reservations r ON "
                "r.id=d.reservation_id WHERE r.product_id=? AND d.phase='丢失结案'",
                (product_id,),
            ).fetchone():
                raise BusinessError("丢失结案商品不能重新出售")
            if item["state"] != P.OFFLINE:
                raise BusinessError("仅普通下架商品可申请重新审核；违规下架需提交恢复申请")
            self.validate_submission(conn, item)
            self.change(conn, user["id"], "申请重新上架", item, P.REVIEW, now)
            return product_result(conn, product_id)

        return self.execute(
            token, product_id, version, operation, authorize=lambda _c, u, p: self.own(u, p)
        )

    @staticmethod
    def extra_fields(condition, brand, region, method):
        if condition not in CONDITIONS:
            raise BusinessError("请选择统一的商品成色")
        if method not in ("公共地点面交", "双方协商面交", "仅邮寄", "仅面交", "邮寄或面交"):
            raise BusinessError("请选择仅邮寄、仅面交或邮寄或面交")
        return (
            condition,
            clean_text(brand, "品牌或型号", 80, 0),
            clean_text(region, "所在区域", 40, 0),
            method,
        )

    @staticmethod
    def save_delivery_fields(conn, pid, free_shipping, shipping_fee, city, district):
        if type(free_shipping) not in (int, bool) or free_shipping not in (0, 1):
            raise BusinessError("包邮选项无效")
        freight = cents(shipping_fee, allow_zero=True)
        city = clean_text(city, "商品/发货城市", 40, 0)
        district = clean_text(district, "商品所在区县", 40, 0)
        conn.execute(
            "UPDATE products SET free_shipping=?,freight_cen"
            "ts=?,origin_city=?,origin_district=? WHERE id=?",
            (int(free_shipping), freight, city, district, pid),
        )

    def validate_submission(self, conn, item):
        if (
            conn.execute("SELECT status FROM users WHERE id=?", (item["seller_id"],)).fetchone()[0]
            != "正常"
        ):
            raise BusinessError("卖家账号已受限，暂不能发布商品")
        self.fields(
            item["title"],
            item["category"],
            item["description"],
            "" if item["price_missing"] else display_money(item["price_cents"]),
        )
        if not conn.execute(
            "SELECT 1 FROM categories WHERE name=? AND enabled=1", (item["category"],)
        ).fetchone():
            raise BusinessError("请选择当前启用的分类后再提交")

    def request_restore(self, token, product_id, version, reason):
        reason = clean_text(reason, "恢复申请说明", 1000)

        def operation(conn, user, item, now):
            if item["state"] != P.VIOLATION:
                raise BusinessError("仅违规下架商品需要提交恢复申请")
            old = conn.execute(
                "SELECT id FROM restoration_requests WHERE product_id=? AND state='待处理'",
                (product_id,),
            ).fetchone()
            if old:
                return old[0]
            rid = conn.execute(
                "INSERT INTO restoration_requests(product_id,reason,created_at) VALUES(?,?,?)",
                (product_id, reason, now),
            ).lastrowid
            audit(
                conn,
                user["id"],
                "申请违规商品恢复",
                product_id,
                f"restoration:{rid}",
                {},
                {"reason": reason},
                now,
            )
            return rid

        return self.execute(
            token, product_id, version, operation, authorize=lambda _c, u, p: self.own(u, p)
        )

    def restoration_requests(self, token):
        with self.store.connect() as conn:
            user = self.auth.require(conn, token)
            clause = "" if user["role"] == "admin" else "WHERE p.seller_id=?"
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT x.*,p.title,p.version FROM restoration_requests x JOIN products p "
                    f"ON p.id=x.product_id {clause} ORDER BY x.id DESC",
                    () if user["role"] == "admin" else (user["id"],),
                )
            ]

    def resolve_restore(self, token, request_id, version, approve, reason):
        reason = clean_text(reason, "恢复处理理由", 1000)
        with self.store.connect() as conn:
            request = conn.execute(
                "SELECT product_id FROM restoration_requests WHERE id=?", (request_id,)
            ).fetchone()
            if request is None:
                raise BusinessError("恢复申请不存在")
            pid = request[0]

        def operation(conn, user, item, now):
            row = conn.execute(
                "SELECT state FROM restoration_requests WHERE id=?", (request_id,)
            ).fetchone()
            if conn.execute(
                "SELECT 1 FROM delivery_orders d JOIN reservations r ON "
                "r.id=d.reservation_id WHERE r.product_id=? AND d.phase='丢失结案'",
                (pid,),
            ).fetchone():
                raise BusinessError("丢失结案商品禁止恢复出售")
            if row[0] != "待处理" or item["state"] != P.VIOLATION:
                raise BusinessError("申请或商品状态已改变，请刷新")
            if (
                approve
                and conn.execute(
                    "SELECT 1 FROM reports WHERE product_id=? AND state='待处理'", (pid,)
                ).fetchone()
            ):
                raise BusinessError("请先处理该商品全部待处理举报")
            result = "已同意" if approve else "已拒绝"
            conn.execute(
                "UPDATE restoration_requests SET state=?,handled_by=?,handled_at=?,"
                "decision_reason=? WHERE id=?",
                (result, user["id"], now, reason, request_id),
            )
            if approve:
                conn.execute("UPDATE products SET listing_blocked=0 WHERE id=?", (pid,))
                self.change(conn, user["id"], "批准恢复并重审", item, P.REVIEW, now)
            audit(
                conn,
                user["id"],
                "处理恢复申请",
                pid,
                f"restoration:{request_id}",
                {},
                {"state": result, "reason": reason},
                now,
            )
            from .messaging import notify

            notify(
                conn,
                [item["seller_id"]],
                f"restoration:{request_id}",
                f"恢复申请{result}：{reason}。同意后仍须通过商品审核。",
                pid,
                now,
            )
            return result

        return self.execute(token, pid, version, operation, role="admin")
