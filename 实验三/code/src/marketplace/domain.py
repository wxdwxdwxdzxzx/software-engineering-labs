"""States, input rules and injectable clocks shared by all application layers."""

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, DecimalException, InvalidOperation
from enum import StrEnum
from typing import Protocol

BUSINESS_TZ = timezone(timedelta(hours=8))
DAY_MS = 86_400_000


class BusinessError(Exception):
    """An expected, user-readable failure without implementation details."""


class ProductState(StrEnum):
    DRAFT = "草稿"
    REVIEW = "待审核"
    REJECTED = "审核驳回"
    PUBLISHED = "已发布"
    OFFLINE = "已下架"
    RESERVED = "已预约"
    TRADING = "待交易"
    SOLD = "已售出"
    VIOLATION = "违规下架"


class ReservationState(StrEnum):
    WAITING = "待确认"
    TRADING = "待交易"
    REJECTED = "已拒绝"
    CANCELLED = "已取消"
    TIMEOUT = "超时取消"
    COMPLETE = "已完成"


class Clock(Protocol):
    """Return UTC epoch milliseconds; evaluated after taking the write lock."""

    def now_ms(self) -> int: ...


class SystemClock:
    """Production clock; unaffected by UI input."""

    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000


class ManualClock:
    """Deterministic test/demo clock; never selected by the production CLI."""

    def __init__(self, value: int = 1_788_192_000_000):
        self.value = value
        self.lock = threading.Lock()

    def now_ms(self) -> int:
        with self.lock:
            return self.value

    def advance(self, milliseconds: int) -> None:
        with self.lock:
            self.value += milliseconds


def clean_text(value: str, label: str, maximum: int, minimum: int = 1) -> str:
    """Validate textual form input without silently truncating it."""
    if not isinstance(value, str):
        raise BusinessError(f"{label}必须是文本")
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise BusinessError(f"{label}长度应为{minimum}至{maximum}个字符")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise BusinessError(f"{label}包含无效控制字符")
    return value


def phone_number(value: str) -> str:
    """Local prototype accepts an eleven-digit Chinese mobile identifier."""
    value = clean_text(value, "手机号", 11)
    if not re.fullmatch(r"1[0-9]{10}", value):
        raise BusinessError("手机号应为以1开头的11位数字")
    return value


def cents(value: str, *, allow_zero: bool = False) -> int:
    """Convert decimal input to integer cents, rejecting NaN and rounding."""
    try:
        text = str(value).strip()
        if len(text) > 128:
            raise BusinessError("金额输入过长")
        amount = Decimal(text)
        if not amount.is_finite():
            raise InvalidOperation
        minimum = Decimal(0) if allow_zero else Decimal("0.01")
        if not minimum <= amount <= Decimal("999999.99"):
            raise BusinessError("金额应在允许范围内，最高999999.99元")
        if amount and amount.adjusted() < -2:
            raise BusinessError("金额最多保留两位小数")
        scaled = amount * 100
        if scaled != scaled.to_integral_value():
            raise BusinessError("金额最多保留两位小数")
        result = int(scaled)
    except (DecimalException, ValueError, OverflowError) as exc:
        raise BusinessError("请输入合法金额") from exc
    minimum = 0 if allow_zero else 1
    if not minimum <= result <= 99_999_999:
        raise BusinessError("金额须大于0且不超过999999.99元" if minimum else "价格范围无效")
    return result


def display_money(value: int | None) -> str:
    if value is None:
        return "未填写"
    return f"{value // 100}.{value % 100:02d}"


def display_time(value: int | None) -> str:
    if value is None:
        return "—"
    return datetime.fromtimestamp(value / 1000, BUSINESS_TZ).strftime("%Y-%m-%d %H:%M:%S")


def day_bounds(day: str) -> tuple[int, int]:
    if not isinstance(day, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
        raise BusinessError("日期格式应为 YYYY-MM-DD")
    try:
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=BUSINESS_TZ)
        end = start + timedelta(days=1)
        return int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    except (ValueError, OverflowError, OSError) as exc:
        raise BusinessError("日期无效或超出可查询范围") from exc
