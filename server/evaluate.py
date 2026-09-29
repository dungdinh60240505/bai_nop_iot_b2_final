"""Xuat so lieu danh gia cho bao cao: do tre, chat luong du lieu, dung luong luu tru.

    python evaluate.py                 # 60 phut gan nhat
    python evaluate.py --minutes 180 --out ket_qua_danh_gia.md

Ket qua in ra man hinh dang bang Markdown (copy thang vao bao cao Word)
va luu vao file --out; mau do tre tho xuat them ra latency_samples.csv.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import pandas as pd

from common import (DEVICES, LATENCY, PROCESSED, RAW, REJECTED, RUNS, get_db, settings,
                    setup_logging)

log = setup_logging("evaluate")


def md(df: pd.DataFrame) -> str:
    """Bang Markdown don gian (khong can thu vien tabulate)."""
    if df.empty:
        return "_(khong co du lieu)_"
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in row.tolist()) + " |")
    return "\n".join(lines)


def latency_section(db, start) -> tuple[str, pd.DataFrame]:
    lat = pd.DataFrame(list(db[LATENCY].find({"recv_at": {"$gte": start}}, {"_id": 0})))
    if lat.empty:
        return "## 1. Do tre end-to-end\n\n_(chua co mau)_\n", lat
    parts = []
    for dev, g in lat.groupby("device_id"):
        for col, name in [("net_ms", "Mang (ESP32 -> collector)"), ("db_ms", "Ghi MongoDB"),
                          ("e2e_ms", "End-to-end")]:
            s = g[col]
            parts.append({"device": dev, "thanh phan": name, "n": len(s), "mean": round(s.mean(), 1),
                          "min": round(s.min(), 1), "p50": round(s.quantile(.5), 1),
                          "p95": round(s.quantile(.95), 1), "p99": round(s.quantile(.99), 1),
                          "max": round(s.max(), 1), "std": round(s.std(), 1)})
    return "## 1. Do tre end-to-end (ms)\n\n" + md(pd.DataFrame(parts)) + "\n", lat


def quality_section(db, start) -> str:
    devs = pd.DataFrame(list(db[DEVICES].find({})))
    out = "## 2. Chat luong du lieu (luy ke tu khi chay collector)\n\n"
    if not devs.empty:
        for c in ["received", "stored", "duplicates", "lost", "rejected", "reboots"]:
            devs[c] = devs.get(c, 0)
            devs[c] = devs[c].fillna(0).astype(int)
        expected = devs["stored"] + devs["lost"] + devs["rejected"]
        devs["loss_%"] = (100 * devs["lost"] / expected.where(expected > 0)).round(2)
        devs["dup_%"] = (100 * devs["duplicates"] / devs["received"].where(devs["received"] > 0)).round(2)
        out += md(devs[["_id", "received", "stored", "duplicates", "lost", "rejected", "reboots",
                        "loss_%", "dup_%"]].rename(columns={"_id": "device"})) + "\n"
    n_raw = db[RAW].count_documents({"ts": {"$gte": start}})
    n_null = db[RAW].count_documents({"ts": {"$gte": start},
                                      "$or": [{"temperature": None}, {"humidity": None}, {"distance_cm": None}]})
    n_srv = db[RAW].count_documents({"ts": {"$gte": start}, "ts_source": "server"})
    out += (f"\nTrong khoang danh gia: {n_raw} ban ghi tho, {n_null} ban ghi co gia tri null, "
            f"{n_srv} ban ghi phai dung thoi gian server (ESP32 chua dong bo NTP), "
            f"{db[REJECTED].count_documents({'recv_at': {'$gte': start}})} goi bi tu choi.\n")

    run = db[RUNS].find_one(sort=[("run_at", -1)])
    if run:
        rows = []
        for dev, s in run["devices"].items():
            rows.append({"device": dev, "raw": s["rows_raw"], "trung": s["duplicates_removed"],
                         "mat_seq": s["seq_gaps_lost"], **{f"null_{k}": v for k, v in s["null_values"].items()},
                         **{f"outlier_{k}": v for k, v in s["outliers"].items()},
                         "cua_so": s["windows_total"], "noi_suy": s["values_interpolated"],
                         "bo": s["windows_dropped"], "ra": s["rows_out"]})
        out += (f"\n### Lan tien xu ly gan nhat ({run['run_id']}, method={run['params']['method']}, "
                f"window={run['params']['window']}, {run.get('duration_ms')} ms)\n\n" + md(pd.DataFrame(rows)) + "\n")
    return out


def storage_section(db) -> str:
    rows = []
    for name in [RAW, PROCESSED, LATENCY, REJECTED, RUNS, DEVICES]:
        try:
            s = db.command("collStats", name)
        except Exception:  # noqa: BLE001
            continue
        count = db[name].count_documents({})
        rows.append({"collection": name, "documents": count,
                     "size_KB": round(s.get("size", 0) / 1024, 1),
                     "storage_KB": round(s.get("storageSize", 0) / 1024, 1),
                     "index_KB": round(s.get("totalIndexSize", 0) / 1024, 1),
                     "bytes/doc (luu tru)": round(s.get("storageSize", 0) / count, 1) if count else None})
    return "## 3. Dung luong luu tru\n\n" + md(pd.DataFrame(rows)) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description="Xuat so lieu danh gia cho bao cao")
    p.add_argument("--minutes", type=int, default=60)
    p.add_argument("--out", default="ket_qua_danh_gia.md")
    args = p.parse_args()

    db = get_db()
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=args.minutes)
    header = (f"# Ket qua danh gia he thong\n\nKhoang thoi gian: {args.minutes} phut, den "
              f"{pd.Timestamp(now).tz_convert(settings.timezone):%Y-%m-%d %H:%M:%S} ({settings.timezone})\n")
    lat_md, lat = latency_section(db, start)
    report = "\n".join([header, lat_md, quality_section(db, start), storage_section(db)])
    print(report)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(report)
    if not lat.empty:
        lat.to_csv("latency_samples.csv", index=False)
    log.info("Da ghi %s%s", args.out, " va latency_samples.csv" if not lat.empty else "")


if __name__ == "__main__":
    main()
