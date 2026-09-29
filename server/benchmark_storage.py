"""So sanh hieu nang luu tru: Time-Series Collection vs Collection thuong (MongoDB).

    python benchmark_storage.py              # 50 000 ban ghi
    python benchmark_storage.py --n 200000

Do: toc do ghi (insert_one tung ban ghi va insert_many theo lo), dung luong tren dia,
toc do truy van khoang thoi gian va aggregate trung binh theo phut.
Dung database rieng (<MONGO_DB>_bench) va tu xoa sau khi chay (tru khi --keep).
"""
from __future__ import annotations

import argparse
import random
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from common import get_db, settings, setup_logging
from evaluate import md

log = setup_logging("benchmark")


def make_docs(n: int, devices: int = 5, interval_s: int = 5) -> list[dict]:
    rng = random.Random(42)
    start = datetime.now(timezone.utc) - timedelta(seconds=n // devices * interval_s)
    docs = []
    for i in range(n):
        dev = i % devices
        docs.append({
            "ts": start + timedelta(seconds=(i // devices) * interval_s),
            "meta": {"device_id": f"bench-{dev}", "room": f"room{dev}"},
            "seq": i // devices, "temperature": round(27 + rng.gauss(0, 1), 2),
            "humidity": round(65 + rng.gauss(0, 3), 2), "distance_cm": round(rng.uniform(5, 380), 2),
            "alarm": False, "rssi": rng.randint(-80, -40), "uptime_s": i // devices * interval_s,
        })
    return docs


def bench(db, name: str, timeseries: bool, docs: list[dict], one_by_one: int) -> dict:
    db.drop_collection(name)
    if timeseries:
        db.create_collection(name, timeseries={"timeField": "ts", "metaField": "meta", "granularity": "seconds"})
    else:
        db.create_collection(name)
        db[name].create_index([("meta.device_id", 1), ("ts", -1)])
    coll = db[name]

    t = time.perf_counter()
    for d in docs[:one_by_one]:
        coll.insert_one(dict(d))
    one_ms = (time.perf_counter() - t) * 1000 / one_by_one

    t = time.perf_counter()
    rest = [dict(d) for d in docs[one_by_one:]]
    for i in range(0, len(rest), 1000):
        coll.insert_many(rest[i:i + 1000], ordered=False)
    many_rate = len(rest) / (time.perf_counter() - t)

    end = max(d["ts"] for d in docs)
    q_start = end - timedelta(hours=1)
    t = time.perf_counter()
    n_found = len(list(coll.find({"meta.device_id": "bench-0", "ts": {"$gte": q_start}})))
    find_ms = (time.perf_counter() - t) * 1000

    t = time.perf_counter()
    list(coll.aggregate([
        {"$match": {"ts": {"$gte": end - timedelta(hours=6)}}},
        {"$group": {"_id": {"d": "$meta.device_id",
                            "m": {"$dateTrunc": {"date": "$ts", "unit": "minute"}}},
                    "t": {"$avg": "$temperature"}, "h": {"$avg": "$humidity"}}},
    ]))
    agg_ms = (time.perf_counter() - t) * 1000

    stats = db.command("collStats", name)
    return {"loai": "Time-series" if timeseries else "Thuong", "documents": len(docs),
            "insert_one (ms/doc)": round(one_ms, 3), "insert_many (doc/s)": round(many_rate),
            "storage (KB)": round(stats.get("storageSize", 0) / 1024, 1),
            "index (KB)": round(stats.get("totalIndexSize", 0) / 1024, 1),
            "find 1h (ms)": round(find_ms, 1), "rows 1h": n_found,
            "aggregate 6h/phut (ms)": round(agg_ms, 1)}


def main() -> None:
    p = argparse.ArgumentParser(description="Benchmark luu tru MongoDB")
    p.add_argument("--n", type=int, default=50_000)
    p.add_argument("--one-by-one", type=int, default=2_000, help="so ban ghi ghi bang insert_one")
    p.add_argument("--keep", action="store_true", help="giu lai database benchmark")
    args = p.parse_args()

    db = get_db(db_name=f"{settings.mongo_db}_bench")
    docs = make_docs(args.n)
    log.info("Sinh %d ban ghi, bat dau do...", len(docs))
    rows = [bench(db, "bench_timeseries", True, docs, args.one_by_one),
            bench(db, "bench_regular", False, docs, args.one_by_one)]
    df = pd.DataFrame(rows)
    ratio = df.loc[1, "storage (KB)"] / df.loc[0, "storage (KB)"] if df.loc[0, "storage (KB)"] else float("nan")
    text = (f"## So sanh Time-series vs Collection thuong ({args.n} ban ghi)\n\n{md(df)}\n\n"
            f"Collection thuong ton dung luong gap {ratio:.1f} lan time-series.\n")
    print(text)
    with open("ket_qua_benchmark.md", "w", encoding="utf-8") as fh:
        fh.write(text)
    if not args.keep:
        db.client.drop_database(db.name)
    log.info("Da ghi ket_qua_benchmark.md")


if __name__ == "__main__":
    main()
