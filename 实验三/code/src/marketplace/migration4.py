"""Transactional additive upgrade; all pictures remain inside the backed-up database."""


def upgrade(conn):
    additions = {
        "users": [
            "nickname TEXT NOT NULL DEFAULT ''",
            "bio TEXT NOT NULL DEFAULT ''",
            "region TEXT NOT NULL DEFAULT ''",
            "avatar BLOB",
            "avatar_thumb BLOB",
            "status TEXT NOT NULL DEFAULT '正常'",
            "status_reason TEXT NOT NULL DEFAULT ''",
            "session_epoch INTEGER NOT NULL DEFAULT 0",
        ],
        "products": [
            "condition TEXT NOT NULL DEFAULT '成色良好'",
            "brand TEXT NOT NULL DEFAULT ''",
            "handoff_method TEXT NOT NULL DEFAULT '公共地点面交'",
            "price_missing INTEGER NOT NULL DEFAULT 0",
            "meeting_note TEXT NOT NULL DEFAULT ''",
        ],
        "reservations": [
            "proposed_time TEXT NOT NULL DEFAULT ''",
            "agreed_time TEXT NOT NULL DEFAULT ''",
            "decision_reason TEXT NOT NULL DEFAULT ''",
            "agreed_at INTEGER",
            "meeting_note TEXT NOT NULL DEFAULT ''",
        ],
        "meeting_places": ["note TEXT NOT NULL DEFAULT ''"],
        "reports": ["snapshot_title TEXT NOT NULL DEFAULT ''"],
    }
    for table, columns in additions.items():
        for column in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
    conn.execute("UPDATE users SET nickname=username")
    conn.execute("UPDATE reports SET snapshot_title='历史举报商品 #' || product_id")
    conn.execute("ALTER TABLE product_photos RENAME TO product_photos_v3")
    conn.execute(
        "CREATE TABLE product_photos (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,"
        "position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 5),"
        "content BLOB NOT NULL,thumbnail BLOB NOT NULL,UNIQUE(product_id,position))"
    )
    conn.execute("INSERT INTO product_photos SELECT * FROM product_photos_v3")
    conn.execute("DROP TABLE product_photos_v3")
    statements = [
        "CREATE TABLE categories(name TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1)",
        "CREATE TABLE favorites(user_id INTEGER REFERENCES users(id),product_id INTEGER "
        "REFERENCES products(id) ON DELETE CASCADE,created_at INTEGER NOT NULL,"
        "PRIMARY KEY(user_id,product_id))",
        "CREATE TABLE browsing(user_id INTEGER REFERENCES users(id),product_id INTEGER "
        "REFERENCES products(id) ON DELETE CASCADE,viewed_at INTEGER NOT NULL,"
        "PRIMARY KEY(user_id,product_id))",
        "CREATE TABLE follows(user_id INTEGER REFERENCES users(id),seller_id INTEGER "
        "REFERENCES users(id),created_at INTEGER NOT NULL,PRIMARY KEY(user_id,seller_id),"
        "CHECK(user_id!=seller_id))",
        "CREATE TABLE conversations(id INTEGER PRIMARY KEY,product_id INTEGER NOT NULL "
        "REFERENCES products(id),buyer_id INTEGER NOT NULL REFERENCES users(id),"
        "seller_id INTEGER NOT NULL REFERENCES users(id),created_at INTEGER NOT NULL,"
        "snapshot_title TEXT NOT NULL,"
        "UNIQUE(product_id,buyer_id),CHECK(buyer_id!=seller_id))",
        "CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id INTEGER NOT NULL "
        "REFERENCES conversations(id),sender_id INTEGER NOT NULL REFERENCES users(id),"
        "content TEXT NOT NULL,created_at INTEGER NOT NULL,read_at INTEGER,"
        "request_key TEXT NOT NULL,UNIQUE(sender_id,request_key))",
        "CREATE INDEX message_page ON messages(conversation_id,id)",
        "CREATE TABLE business_notices(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL "
        "REFERENCES users(id),event_key TEXT NOT NULL,content TEXT NOT NULL,"
        "product_id INTEGER REFERENCES products(id),created_at INTEGER NOT NULL,read_at INTEGER,"
        "UNIQUE(user_id,event_key))",
        "CREATE TABLE reservation_photos(reservation_id INTEGER NOT NULL "
        "REFERENCES reservations(id),"
        "position INTEGER NOT NULL,content BLOB NOT NULL,thumbnail BLOB NOT NULL,"
        "PRIMARY KEY(reservation_id,position))",
        "CREATE TABLE handoff_changes(id INTEGER PRIMARY KEY,reservation_id INTEGER NOT NULL "
        "REFERENCES reservations(id),proposer_id INTEGER NOT NULL REFERENCES users(id),"
        "region TEXT NOT NULL,point TEXT NOT NULL,note TEXT NOT NULL,meeting_time TEXT NOT NULL,"
        "state TEXT NOT NULL DEFAULT '待确认',created_at INTEGER NOT NULL,handled_at INTEGER)",
        "CREATE UNIQUE INDEX one_pending_handoff ON handoff_changes(reservation_id) "
        "WHERE state='待确认'",
        "CREATE TABLE trade_reviews(id INTEGER PRIMARY KEY,reservation_id INTEGER NOT NULL "
        "REFERENCES reservations(id),author_id INTEGER NOT NULL REFERENCES users(id),"
        "target_id INTEGER NOT NULL REFERENCES users(id),score INTEGER NOT NULL "
        "CHECK(score BETWEEN 1 AND 5),content TEXT NOT NULL,created_at INTEGER NOT NULL,"
        "visible INTEGER NOT NULL DEFAULT "
        "1,UNIQUE(reservation_id,author_id),CHECK(author_id!=target_id))",
        "CREATE TABLE review_reports(id INTEGER PRIMARY KEY,review_id INTEGER NOT NULL "
        "REFERENCES trade_reviews(id),reporter_id INTEGER NOT NULL REFERENCES users(id),"
        "reason TEXT NOT NULL,state TEXT NOT NULL DEFAULT '待处理',created_at INTEGER NOT NULL,"
        "handled_by INTEGER REFERENCES users(id),handled_at INTEGER,decision_reason TEXT NOT NULL "
        "DEFAULT '',UNIQUE(review_id,reporter_id))",
        "CREATE TABLE restoration_requests(id INTEGER PRIMARY KEY,product_id INTEGER NOT NULL "
        "REFERENCES products(id),reason TEXT NOT NULL,state TEXT NOT NULL DEFAULT '待处理',"
        "created_at INTEGER NOT NULL,handled_by INTEGER REFERENCES users(id),handled_at INTEGER,"
        "decision_reason TEXT NOT NULL DEFAULT '')",
        "CREATE UNIQUE INDEX one_pending_restoration ON restoration_requests(product_id) "
        "WHERE state='待处理'",
    ]
    for statement in statements:
        conn.execute(statement)
    for action in ("DELETE", "UPDATE"):
        conn.execute(
            f"CREATE TRIGGER protect_reservation_photos_{action.lower()} "
            f"BEFORE {action} ON reservation_photos BEGIN "
            "SELECT RAISE(ABORT,'reservation pictures are immutable'); END"
        )
    conn.executemany(
        "INSERT OR IGNORE INTO categories(name) VALUES(?)",
        [(name,) for name in ("数码", "图书", "生活", "服饰", "运动", "其他")],
    )
    conn.execute(
        "INSERT OR IGNORE INTO categories(name) SELECT DISTINCT category "
        "FROM products WHERE category!=''"
    )
    conn.execute("PRAGMA user_version=4")
