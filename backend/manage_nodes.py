#!/usr/bin/env python3
"""Manage BananaGuard's local node registry (no HTTP write endpoint).

Examples:
    python backend/manage_nodes.py list
    python backend/manage_nodes.py set bg-n01 --name "North Plot" --block B1
    python backend/manage_nodes.py disable bg-n01
"""
from __future__ import annotations

import argparse
import json

from server import DEFAULT_DB, DEVICE_ID_RE, open_db


def device_id(value: str) -> str:
    if not DEVICE_ID_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("invalid device_id")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="print registry rows as JSON")

    set_cmd = commands.add_parser("set", help="create or replace node metadata")
    set_cmd.add_argument("device_id", type=device_id)
    set_cmd.add_argument("--name", dest="display_name")
    set_cmd.add_argument("--block")
    set_cmd.add_argument("--latitude", type=float)
    set_cmd.add_argument("--longitude", type=float)
    set_cmd.add_argument("--disabled", action="store_true")

    for name in ("enable", "disable", "remove"):
        command = commands.add_parser(name)
        command.add_argument("device_id", type=device_id)

    args = parser.parse_args()
    conn = open_db(args.db)
    try:
        if args.command == "list":
            rows = conn.execute(
                "SELECT device_id, display_name, block, latitude, longitude, "
                "enabled FROM nodes ORDER BY device_id").fetchall()
            print(json.dumps([
                {**dict(row), "enabled": bool(row["enabled"])} for row in rows
            ], indent=2))
            return

        if args.command == "set":
            if args.latitude is not None and not -90 <= args.latitude <= 90:
                parser.error("--latitude must be between -90 and 90")
            if args.longitude is not None and not -180 <= args.longitude <= 180:
                parser.error("--longitude must be between -180 and 180")
            existing = conn.execute(
                "SELECT enabled FROM nodes WHERE device_id = ?",
                (args.device_id,)).fetchone()
            enabled = 0 if args.disabled else (
                existing["enabled"] if existing is not None else 1)
            conn.execute(
                "INSERT INTO nodes (device_id, display_name, block, latitude, "
                "longitude, enabled) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET "
                "display_name=excluded.display_name, block=excluded.block, "
                "latitude=excluded.latitude, longitude=excluded.longitude, "
                "enabled=excluded.enabled",
                (args.device_id, args.display_name, args.block, args.latitude,
                 args.longitude, enabled))
        elif args.command in ("enable", "disable"):
            cursor = conn.execute(
                "UPDATE nodes SET enabled = ? WHERE device_id = ?",
                (1 if args.command == "enable" else 0, args.device_id))
            if not cursor.rowcount:
                parser.error(f"node {args.device_id!r} is not registered")
        else:
            cursor = conn.execute("DELETE FROM nodes WHERE device_id = ?",
                                  (args.device_id,))
            if not cursor.rowcount:
                parser.error(f"node {args.device_id!r} is not registered")
        conn.commit()
        print(f"{args.command}: {args.device_id}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
