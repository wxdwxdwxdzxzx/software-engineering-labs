"""Composition root used by the GUI, CLI, independent tests and demo."""

from .accounts import Accounts
from .addresses import Addresses
from .auth import Auth
from .bookings import Bookings
from .db import Store
from .delivery import Deliveries
from .domain import SystemClock
from .handoff import Handoff
from .messaging import Messaging
from .places import Places
from .products import Products
from .queries import Queries
from .reports import Reports
from .reviews import Reviews
from .social import Social


class Market:
    """Wire services around one database; no singleton or hidden test database."""

    def __init__(self, database, clock=None):
        self.store = Store(database)
        self.clock = clock or SystemClock()
        self.auth = Auth(self.store, self.clock)
        self.addresses = Addresses(self.store, self.auth, self.clock)
        self.delivery = Deliveries(self.store, self.auth, self.clock)
        self.places = Places(self.store, self.auth, self.clock)
        self.products = Products(self.store, self.auth, self.clock)
        self.bookings = Bookings(self.store, self.auth, self.clock)
        self.reports = Reports(self.store, self.auth, self.clock)
        self.queries = Queries(self.store, self.auth, self.clock)
        self.accounts = Accounts(self.store, self.auth, self.clock)
        self.social = Social(self.store, self.auth, self.clock)
        self.messaging = Messaging(self.store, self.auth, self.clock)
        self.reviews = Reviews(self.store, self.auth, self.clock)
        self.handoff = Handoff(self.store, self.auth, self.clock)

    def health_check(self):
        """Useful during tests and local diagnostics; no raw passwords returned."""
        from .db import assert_consistent

        with self.store.connect() as conn:
            for row in conn.execute("SELECT id FROM products"):
                assert_consistent(conn, row[0])
            return conn.execute("PRAGMA integrity_check").fetchone()[0]
