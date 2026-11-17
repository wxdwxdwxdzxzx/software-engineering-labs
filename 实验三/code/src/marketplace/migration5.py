"""Add fulfillment without rewriting original transactions or their snapshots."""


def upgrade(conn):
    conn.execute("ALTER TABLE reservation_requests ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''")
    for column in (
        "freight_cents INTEGER NOT NULL DEFAULT 0 CHECK(freight_cents BETWEEN 0 AND 99999999)",
        "free_shipping INTEGER NOT NULL DEFAULT 1 CHECK(free_shipping IN (0,1))",
        "origin_city TEXT NOT NULL DEFAULT ''",
        "origin_district TEXT NOT NULL DEFAULT ''",
        "listing_blocked INTEGER NOT NULL DEFAULT 0 CHECK(listing_blocked IN (0,1))",
    ):
        conn.execute(f"ALTER TABLE products ADD COLUMN {column}")
    for column in ("city TEXT NOT NULL DEFAULT ''", "district TEXT NOT NULL DEFAULT ''"):
        conn.execute(f"ALTER TABLE meeting_places ADD COLUMN {column}")
    statements = [
        "CREATE TABLE addresses(id INTEGER PRIMARY KEY,owner_id INTEGER NOT NULL REFERENCES "
        "users(id),kind TEXT NOT NULL CHECK(kind IN ('收货','发货')),recipient TEXT NOT NULL,"
        "telephone TEXT NOT NULL,province TEXT NOT NULL,city TEXT NOT NULL,district TEXT NOT NULL,"
        "detail TEXT NOT NULL,is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)))",
        "CREATE UNIQUE INDEX default_address ON addresses(owner_id,kind) WHERE is_default=1",
        "CREATE TABLE delivery_orders(reservation_id INTEGER PRIMARY KEY "
        "REFERENCES reservations(id),"
        "mode TEXT NOT NULL CHECK(mode IN ('邮寄','面交')),phase TEXT NOT NULL,"
        "legacy INTEGER NOT NULL "
        "DEFAULT 0 CHECK(legacy IN (0,1)),goods_cents "
        "INTEGER,freight_cents INTEGER NOT NULL DEFAULT 0,"
        "receiver_json TEXT NOT NULL DEFAULT '{}',shipped_at INTEGER,received_at INTEGER,"
        "handoff_requested_at INTEGER)",
        "CREATE TABLE delivery_requests(reservation_id INTEGER NOT NULL "
        "REFERENCES reservations(id),"
        "actor_id INTEGER NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,"
        "fingerprint TEXT NOT NULL,result_json TEXT NOT NULL,"
        "PRIMARY KEY(reservation_id,actor_id,request_key))",
        "CREATE TABLE address_changes(id INTEGER PRIMARY KEY,reservation_id INTEGER NOT NULL "
        "REFERENCES reservations(id),proposer_id INTEGER NOT NULL REFERENCES users(id),"
        "address_json TEXT NOT NULL,state TEXT NOT NULL DEFAULT '待确认',"
        "created_at INTEGER NOT NULL,"
        "handled_at INTEGER,handled_by INTEGER REFERENCES users(id))",
        "CREATE UNIQUE INDEX pending_address ON address_changes(reservation_id) "
        "WHERE state='待确认'",
        "CREATE TABLE shipment_events(id INTEGER PRIMARY KEY,reservation_id INTEGER NOT NULL "
        "REFERENCES reservations(id),actor_id INTEGER NOT NULL REFERENCES users(id),"
        "kind TEXT NOT NULL,carrier TEXT NOT NULL,tracking_number TEXT NOT NULL,"
        "reason TEXT NOT NULL,sender_json TEXT NOT NULL,created_at INTEGER NOT NULL)",
        "CREATE TABLE delivery_issues(id INTEGER PRIMARY KEY,reservation_id INTEGER NOT NULL "
        "REFERENCES reservations(id),opened_by INTEGER NOT NULL REFERENCES users(id),"
        "category TEXT NOT NULL,description TEXT NOT NULL,state TEXT NOT NULL DEFAULT '协商中',"
        "created_at INTEGER NOT NULL,proposal TEXT NOT NULL DEFAULT '',proposed_by INTEGER "
        "REFERENCES users(id),seller_received INTEGER NOT NULL DEFAULT 0,handled_at INTEGER,"
        "handled_by INTEGER REFERENCES users(id))",
        "CREATE UNIQUE INDEX open_delivery_issue ON delivery_issues(reservation_id) "
        "WHERE state='协商中'",
    ]
    for statement in statements:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO delivery_orders(reservation_id,mode,phase,legacy,goods_cents) "
        "SELECT id,'面交',CASE state WHEN '待确认' THEN '待卖家确认' "
        "WHEN '待交易' THEN '面交中' ELSE state END,1,snapshot_price_cents FROM reservations"
    )
    conn.execute(
        "CREATE TRIGGER protect_delivery_snapshot BEFORE UPDATE OF mode,legacy,goods_cents,"
        "freight_cents,receiver_json ON delivery_orders BEGIN "
        "SELECT RAISE(ABORT,'delivery snapshot is immutable'); END"
    )
    for table in (
        "delivery_orders",
        "delivery_requests",
        "address_changes",
        "shipment_events",
        "delivery_issues",
    ):
        conn.execute(
            f"CREATE TRIGGER keep_{table} BEFORE DELETE ON {table} BEGIN "
            "SELECT RAISE(ABORT,'delivery history cannot be deleted'); END"
        )
    conn.execute(
        "CREATE TRIGGER keep_shipment_events_update BEFORE UPDATE ON shipment_events BEGIN "
        "SELECT RAISE(ABORT,'shipment history is immutable'); END"
    )
    conn.execute("PRAGMA user_version=5")
