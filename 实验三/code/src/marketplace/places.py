"""Owner-scoped address book for public in-person handoff points."""

from .base import Service
from .domain import BusinessError, clean_text


def owned_place(conn, owner_id, place_id):
    if type(place_id) is not int or not 0 < place_id <= 2**63 - 1:
        raise BusinessError("交接地点编号无效")
    row = conn.execute(
        "SELECT id,region,point,note,city,district FROM meeting_places WHERE id=? AND owner_id=?",
        (place_id, owner_id),
    ).fetchone()
    if row is None:
        raise BusinessError("交接地点不存在或不属于当前用户")
    return dict(row)


class Places(Service):
    def list(self, token):
        with self.store.connect() as conn:
            user = self.auth.require(conn, token, "user")
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT id,region,point,note,city,district FROM meeting_places "
                    "WHERE owner_id=? ORDER BY id",
                    (user["id"],),
                )
            ]

    def save(self, token, region, point, *, place_id=None, note="", city="", district=""):
        city = clean_text(city, "城市", 40, 0)
        district = clean_text(district, "区县", 40, 0)
        region = clean_text((city + " " + district).strip() or region, "城市及区县", 85)
        point = clean_text(point, "公共交接点", 120)
        note = clean_text(note, "交接说明", 300, 0)
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            if place_id is None:
                place_id = conn.execute(
                    "INSERT INTO meeting_places(owner_id,region,point,note) VALUES(?,?,?,?)",
                    (user["id"], region, point, note),
                ).lastrowid
            else:
                owned_place(conn, user["id"], place_id)
            conn.execute(
                "UPDATE meeting_places SET region=?,point=?,note=? WHERE id=?",
                (region, point, note, place_id),
            )
            conn.execute(
                "UPDATE meeting_places SET city=?,district=? WHERE id=?", (city, district, place_id)
            )
            return place_id

    def delete(self, token, place_id):
        with self.store.transaction() as conn:
            user = self.auth.require(conn, token, "user")
            owned_place(conn, user["id"], place_id)
            conn.execute("DELETE FROM meeting_places WHERE id=?", (place_id,))
            return True
