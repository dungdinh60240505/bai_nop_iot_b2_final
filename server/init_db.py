"""Khoi tao schema MongoDB cho Bai thuc hanh so 2.

Chay 1 lan (chay lai nhieu lan van an toan):
    python init_db.py
    python init_db.py --reset     # XOA toan bo database roi tao lai

Schema:
  sensor_raw        time-series  timeField=ts  metaField=meta{device_id, room}
  sensor_processed  time-series  timeField=ts  metaField=meta{device_id, room, window}
  latency_log       collection thuong + TTL index tren recv_at
  rejected_messages collection thuong + TTL index tren recv_at
  device_status     1 document / thiet bi (trang thai, bo dem mat/trung goi)
  preprocess_runs   nhat ky tien xu ly
Retention policy: time-series dung expireAfterSeconds, collection thuong dung TTL index.
"""
from __future__ import annotations

import argparse

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

from common import (DEVICES, LATENCY, PROCESSED, RAW, REJECTED, RUNS, get_db,
                    settings, setup_logging)

log = setup_logging("init_db")
DAY = 24 * 3600


def ensure_timeseries(db: Database, name: str, retention_days: int) -> None:
    expire = retention_days * DAY
    if name in db.list_collection_names():
        # Collection da ton tai -> chi cap nhat retention
        db.command("collMod", name, expireAfterSeconds=expire)
        log.info("Cap nhat retention %s = %d ngay", name, retention_days)
    else:
        db.create_collection(
            name,
            timeseries={"timeField": "ts", "metaField": "meta", "granularity": "seconds"},
            expireAfterSeconds=expire,
        )
        log.info("Tao time-series collection %s (retention %d ngay)", name, retention_days)
    db[name].create_index([("meta.device_id", ASCENDING), ("ts", DESCENDING)])


def ensure_ttl(db: Database, name: str, field: str, retention_days: int) -> None:
    coll = db[name]
    index_name = f"ttl_{field}"
    existing = coll.index_information()
    if index_name in existing:
        db.command("collMod", name, index={"name": index_name, "expireAfterSeconds": retention_days * DAY})
    else:
        coll.create_index([(field, ASCENDING)], name=index_name, expireAfterSeconds=retention_days * DAY)
    log.info("TTL %s.%s = %d ngay", name, field, retention_days)


def init(db: Database) -> None:
    ensure_timeseries(db, RAW, settings.raw_retention_days)
    ensure_timeseries(db, PROCESSED, settings.processed_retention_days)

    ensure_ttl(db, LATENCY, "recv_at", settings.latency_retention_days)
    db[LATENCY].create_index([("device_id", ASCENDING), ("recv_at", DESCENDING)])

    ensure_ttl(db, REJECTED, "recv_at", settings.rejected_retention_days)

    db[RUNS].create_index([("run_at", DESCENDING)])
    # device_status dung _id = device_id nen khong can index them
    db[DEVICES]  # noqa: B018 - collection tu tao khi upsert lan dau
    log.info("Xong. Collections: %s", sorted(db.list_collection_names()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Khoi tao schema MongoDB")
    parser.add_argument("--reset", action="store_true", help="xoa database truoc khi tao")
    args = parser.parse_args()

    db = get_db()
    db.client.admin.command("ping")
    log.info("Ket noi MongoDB OK: %s", settings.mongo_db)
    if args.reset:
        db.client.drop_database(db.name)
        log.warning("Da xoa database %s", db.name)
    init(db)


if __name__ == "__main__":
    main()
