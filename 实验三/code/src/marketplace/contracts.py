"""Business interfaces used to guide implementation; UI owns no state rules."""

from typing import Protocol


class MarketCommands(Protocol):
    """Commands must authenticate, check current state and commit atomically."""

    def create_draft(self, token: str, **fields: object) -> dict: ...
    def edit_product(self, token: str, product_id: int, version: int, **fields: object) -> dict: ...
    def reserve(self, token: str, product_id: int, version: int, request_key: str) -> dict: ...
    def act_reservation(
        self, token: str, reservation_id: int, version: int, action: str
    ) -> dict: ...
    def report_product(
        self,
        token: str,
        product_id: int,
        version: int,
        reason: str,
        request_key: str | None = None,
        *,
        again: bool = False,
    ) -> int: ...
    def resolve_report(
        self,
        token: str,
        report_id: int,
        version: int,
        violation: bool,
        reason: str | None = None,
    ) -> str: ...
