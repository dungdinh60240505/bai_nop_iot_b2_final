"""Xuat du lieu lich su tu MongoDB (Bai 2) thanh du_lieu_B2.csv cho Bai 3.

    python export_b2_csv.py --device esp32-room1 --minutes 60
    python export_b2_csv.py --device esp32-room1 --start 2026-10-27T08:00 --end 2026-10-27T09:00
    python export_b2_csv.py --device esp32-room1 --minutes 90 --no-outlier --out ../bai3/du_lieu_B2.csv

Chi doc collection sensor_raw (du lieu da qua collector: validate + chong trung).
Cac buoc ap dung khi xuat (ghi lai trong file .meta.json canh CSV):
  * loai goi trung (seq, device_ts_ms), sap xep theo thoi gian
  * (mac dinh) danh dau NaN cho gai nhieu phat hien bang IQR tren phan du median truot
    cua preprocess.py; tat bang --no-outlier
  * KHONG resample, KHONG noi suy, KHONG chuan hoa / lam muot: giu nguyen don vi
    vat ly C, %RH, cm de moi tham so hoc tu du lieu chi fit tren train o Bai 3.
  * Nhiet do khong bi bu -> temperature_observed = 1 cho moi dong.

Khong ghi MONGO_URI, mat khau hay token vao CSV.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from common import RAW, SENSOR_FIELDS, get_db, settings, setup_logging
from preprocess import IQR_K, detect_outliers, parse_time

log = setup_logging("export_b2")

COLUMNS = ["timestamp", "temperature", "humidity", "distance_cm", "device_id", "session_id",
           "sequence", "uptime_s", "rssi", "data_source", "temperature_observed"]


def assign_sessions(df: pd.DataFrame, prefix: str) -> pd.Series:
    """Phien moi khi sequence hoac uptime_s giam (ESP32 khoi dong lai)."""
    seq_drop = (df["sequence"].astype(float).diff() < 0)
    up_drop = (df["uptime_s"].astype(float).diff() < 0)
    new = (seq_drop | up_drop).astype(int).cumsum()
    return prefix + "-s" + (new + 1).astype(str)


def export(device: str, start: datetime, end: datetime, out: Path, source: str | None,
           remove_outliers: bool = True) -> dict:
    db = get_db()
    rows = list(db[RAW].find(
        {"meta.device_id": device, "ts": {"$gte": start, "$lt": end}},
        {"_id": 0, "ts": 1, "seq": 1, "device_ts_ms": 1, "uptime_s": 1, "rssi": 1,
         "ts_source": 1, **{f: 1 for f in SENSOR_FIELDS}}).sort("ts", 1))
    if not rows:
        raise SystemExit(f"Khong co du lieu cua {device} trong {start} -> {end}")
    df = pd.DataFrame(rows)
    n_raw = len(df)
    df = df.drop_duplicates(subset=["seq", "device_ts_ms"]).sort_values("ts").reset_index(drop=True)
    for f in SENSOR_FIELDS:
        df[f] = pd.to_numeric(df.get(f), errors="coerce")

    outliers = {}
    if remove_outliers:
        for f in SENSOR_FIELDS:
            mask = detect_outliers(df[f], f, "iqr", detrend_window=11, iqr_k=IQR_K[f])
            outliers[f] = int(mask.sum())
            df.loc[mask, f] = np.nan

    if source is None:
        source = "python_simulator_B2" if device.startswith("sim-") else "wokwi_esp32_mqtt_mongodb"
    out_df = pd.DataFrame({
        "timestamp": pd.to_datetime(df["ts"], utc=True).dt.as_unit("ms").astype("int64"),
        "temperature": df["temperature"].round(2),
        "humidity": df["humidity"].round(2),
        "distance_cm": df["distance_cm"].round(2),
        "device_id": device,
        "sequence": df["seq"].astype("Int64"),
        "uptime_s": pd.to_numeric(df.get("uptime_s"), errors="coerce").astype("Int64"),
        "rssi": pd.to_numeric(df.get("rssi"), errors="coerce").astype("Int64"),
        "data_source": source,
        "temperature_observed": 1,
    })
    out_df["session_id"] = assign_sessions(out_df, device)
    out_df = out_df[COLUMNS]
    out.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out, index=False)

    meta = {
        "device_id": device,
        "data_source": source,
        "mongo_db": settings.mongo_db,
        "collection": RAW,
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "first_timestamp": pd.Timestamp(int(out_df["timestamp"].min()), unit="ms", tz="UTC").isoformat(),
        "last_timestamp": pd.Timestamp(int(out_df["timestamp"].max()), unit="ms", tz="UTC").isoformat(),
        "send_interval_s": 5,
        "rows_raw": n_raw, "rows_exported": len(out_df),
        "duplicates_removed": n_raw - len(out_df),
        "sessions": sorted(out_df["session_id"].unique().tolist()),
        "outliers_set_nan": outliers,
        "preprocessing": ("dedup (seq, device_ts_ms); " +
                          ("IQR spike -> NaN; " if remove_outliers else "") +
                          "no resample, no interpolation, no scaling; units C, %RH, cm"),
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }
    out.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Da xuat %d dong (%d trung) -> %s", len(out_df), meta["duplicates_removed"], out)
    return meta


def main() -> None:
    p = argparse.ArgumentParser(description="Xuat du_lieu_B2.csv tu MongoDB cho Bai 3")
    p.add_argument("--device", required=True, help="device_id, vd esp32-room1")
    p.add_argument("--minutes", type=int, default=60)
    p.add_argument("--start", help="gio dia phuong, vd 2026-10-27T08:00")
    p.add_argument("--end")
    p.add_argument("--source", help="ghi de data_source (mac dinh tu suy ra tu device_id)")
    p.add_argument("--no-outlier", action="store_true", help="khong danh dau gai nhieu IQR")
    p.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "bai3" / "du_lieu_B2.csv"))
    args = p.parse_args()
    end = parse_time(args.end) if args.end else datetime.now(timezone.utc)
    start = parse_time(args.start) if args.start else end - timedelta(minutes=args.minutes)
    meta = export(args.device, start, end, Path(args.out), args.source, not args.no_outlier)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
