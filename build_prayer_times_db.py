#!/usr/bin/env python3
"""
Build prayer-times.db from scraped Diyanet JSON under turkiye/{city}/{district}.json.

Each JSON file is a date -> {imsak, gunes, ogle, ikindi, aksam, yatsi} map with
ISO timestamps. Times are stored as HH:MM.

Usage:
    python build_prayer_times_db.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TURKIYE_DIR = BASE_DIR / "turkiye"
DB_PATH = BASE_DIR / "prayer-times.db"

PRAYER_KEYS = ("imsak", "gunes", "ogle", "ikindi", "aksam", "yatsi")


def iso_to_hhmm(value: str) -> str:
    """Extract HH:MM from '2026-07-17T03:47:00+03:00'."""
    if len(value) >= 16 and value[10] == "T":
        return value[11:16]
    raise ValueError(f"unexpected time format: {value!r}")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS prayer_times;

        CREATE TABLE prayer_times (
            city     TEXT NOT NULL,
            district TEXT NOT NULL,
            date     TEXT NOT NULL,
            imsak    TEXT NOT NULL,
            gunes    TEXT NOT NULL,
            ogle     TEXT NOT NULL,
            ikindi   TEXT NOT NULL,
            aksam    TEXT NOT NULL,
            yatsi    TEXT NOT NULL,
            PRIMARY KEY (city, district, date)
        );

        CREATE INDEX idx_prayer_times_city_date
            ON prayer_times (city, date);
        """
    )


def iter_json_files() -> list[tuple[str, str, Path]]:
    """Yield (city, district, path) for every JSON under turkiye/."""
    if not TURKIYE_DIR.is_dir():
        raise FileNotFoundError(f"missing directory: {TURKIYE_DIR}")

    rows: list[tuple[str, str, Path]] = []
    for city_dir in sorted(p for p in TURKIYE_DIR.iterdir() if p.is_dir()):
        city = city_dir.name
        for path in sorted(city_dir.glob("*.json")):
            district = path.stem  # e.g. kucukcekmece, or istanbul for city file
            rows.append((city, district, path))
    return rows


def load_file_rows(city: str, district: str, path: Path) -> list[tuple]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    out: list[tuple] = []
    for date, times in data.items():
        try:
            values = [iso_to_hhmm(times[k]) for k in PRAYER_KEYS]
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"{path}: bad entry for {date!r}: {e}") from e
        out.append((city, district, date, *values))
    return out


def main() -> int:
    files = iter_json_files()
    print(f"Found {len(files)} district JSON files under {TURKIYE_DIR}")

    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Removed existing {DB_PATH.name}")

    conn = sqlite3.connect(DB_PATH)
    try:
        create_schema(conn)
        total_rows = 0
        errors = 0

        insert_sql = """
            INSERT INTO prayer_times
                (city, district, date, imsak, gunes, ogle, ikindi, aksam, yatsi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        for city, district, path in files:
            try:
                batch = load_file_rows(city, district, path)
                conn.executemany(insert_sql, batch)
                total_rows += len(batch)
            except Exception as e:
                errors += 1
                print(f"ERROR {path}: {e}", file=sys.stderr)

        conn.commit()

        cities = conn.execute("SELECT COUNT(DISTINCT city) FROM prayer_times").fetchone()[0]
        districts = conn.execute(
            "SELECT COUNT(DISTINCT city || '/' || district) FROM prayer_times"
        ).fetchone()[0]
        size_mb = DB_PATH.stat().st_size / (1024 * 1024)

        print(f"Wrote {DB_PATH}")
        print(f"  rows:      {total_rows}")
        print(f"  cities:    {cities}")
        print(f"  districts: {districts}")
        print(f"  size:      {size_mb:.2f} MB")
        if errors:
            print(f"  errors:    {errors}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
