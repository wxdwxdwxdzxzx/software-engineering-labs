"""Run the desktop or provision a local administrator with an interactive password."""

import argparse
import getpass
import logging
import os
import sys
from pathlib import Path

from .app import Market
from .domain import BusinessError


def default_database():
    """Never write user data into PyInstaller's internal or executable directory."""
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "ShiguangMarket" / "market.sqlite"
    return Path.home() / ".secondhand-market" / "market.sqlite"


def main():
    parser = argparse.ArgumentParser(description="拾光二手交易桌面系统")
    parser.add_argument("--db", default=str(default_database()))
    parser.add_argument("--create-admin", metavar="NAME", help="受控创建管理员，交互输入密码")
    parser.add_argument("--health", action="store_true", help="校验持久化状态一致性后退出")
    args = parser.parse_args()
    database = Path(args.db).resolve()
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=database.with_suffix(".log"),
            level=logging.WARNING,
            encoding="utf-8",
            format="%(asctime)s %(levelname)s %(message)s",
        )
        market = Market(database)
        if args.create_admin and not getattr(sys, "frozen", False):
            password = getpass.getpass("管理员密码（至少8位）: ")
            if password != getpass.getpass("再次输入密码: "):
                raise BusinessError("两次输入密码不一致")
            market.auth.provision_admin(args.create_admin, password)
            print("管理员已创建；普通用户注册不能获得该权限。")
        elif args.health:
            print(market.health_check())
        else:
            import tkinter as tk

            from .ui import Desktop

            root = tk.Tk()
            if market.auth.needs_admin():
                from .ui_setup import first_admin

                first_admin(
                    root, market, lambda: Desktop(root, market), args.create_admin or "admin"
                )
            else:
                Desktop(root, market)
            root.mainloop()
    except BusinessError as exc:
        if not getattr(sys, "frozen", False):
            parser.exit(1, f"{exc}\n")
        show_startup_error(str(exc), database)
    except Exception:
        logging.exception("Application startup failed")
        if not getattr(sys, "frozen", False):
            raise
        show_startup_error("程序启动失败，请检查数据目录和运行文件是否完整", database)


def show_startup_error(message, database):
    """Windowed builds have no terminal, so startup failures need a visible dialog."""
    from .ui_setup import startup_error

    startup_error(message, database)


if __name__ == "__main__":
    main()
