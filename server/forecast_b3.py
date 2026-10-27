"""Bai 3 - Dich vu du bao nhiet do trung binh 60 s tiep theo (Ridge_6) tren du lieu MongoDB.

    python forecast_b3.py --device esp32-room1              # chay moi 30 s (Ctrl+C de dung)
    python forecast_b3.py --device esp32-room1 --once       # du bao 1 lan roi thoat

Model duoc huan luyen trong bai3/Bai3_Sinh_vien.ipynb (o 10 luu ket_qua_B3/model_ridge6.joblib).
Moi lan chay:
  1. Doc sensor_raw cua thiet bi; lay cua so 60 s vua HOAN TAT tinh theo moc thoi gian cua
     ban ghi moi nhat (khong dung dong ho server, tranh lech gio Wokwi/NTP).
  2. Kiem tra giong notebook: mien hop le -> NaN, luoi 5 s, toi da 1 o thieu moi kenh,
     khong vat qua reset (seq/uptime_s giam) hay khoang trong > 20 s.
  3. Tinh 21 dac trung (7 dac trung x 3 kenh), Pipeline tu chon 6 cot va du bao.
  4. Ghi collection forecast_b3 voi khoa temperature_forecast_next60s (KHONG ghi de
     telemetry 'temperature'); dashboard.py hien thi rieng o tab "Du bao (Bai 3)".

Luu y: neu model duoc huan luyen tren bo mau Python, du bao chi minh hoa luong tich hop;
hay chay lai notebook voi du_lieu_B2.csv cua nhom truoc khi dien giai ket qua.
"""
from __future__ import annotations

import argparse
import time
from datetime import timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from pymongo import ASCENDING
from pymongo.database import Database

from common import FORECAST, RAW, SENSOR_FIELDS, get_db, setup_logging

log = setup_logging("forecast_b3")

MODEL_PATH = Path(__file__).resolve().parent.parent / "bai3" / "ket_qua_B3" / "model_ridge6.joblib"
VALID_RANGE = {"temperature": (-40, 80), "humidity": (0, 100), "distance_cm": (2, 400)}
MAX_GAP_S = 20
STALE_AFTER_S = 30   # ban ghi moi nhat cu hon -> thiet bi offline, khong du bao


def channel_features(x, t_s, prefix: str) -> dict:
    """Giong het o 6 cua notebook."""
    x = np.asarray(x, dtype=float)
    t_s = np.asarray(t_s, dtype=float)
    return {
        f"{prefix}_mean": float(np.mean(x)),
        f"{prefix}_std": float(np.std(x, ddof=0)),
        f"{prefix}_min": float(np.min(x)),
        f"{prefix}_max": float(np.max(x)),
        f"{prefix}_range": float(np.ptp(x)),
        f"{prefix}_delta": float(x[-1] - x[0]),
        f"{prefix}_slope": float(np.polyfit(t_s, x, 1)[0]),
    }


def load_bundle(path: Path = MODEL_PATH) -> dict:
    if not path.exists():
        raise SystemExit(f"Chua co {path}. Hay chay bai3/Bai3_Sinh_vien.ipynb (o 10) truoc.")
    return joblib.load(path)


def ensure_indexes(db: Database) -> None:
    db[FORECAST].create_index([("meta.device_id", ASCENDING), ("ts", ASCENDING)], unique=True)


def latest_window(db: Database, device: str, sample_s: int, window_s: int) -> tuple[pd.DataFrame, pd.Timestamp] | None:
    last = db[RAW].find_one({"meta.device_id": device}, {"ts": 1}, sort=[("ts", -1)])
    if not last:
        log.warning("Chua co du lieu cua %s", device)
        return None
    last_ts = pd.Timestamp(last["ts"]).tz_convert("UTC")
    # Luoi 5 s neo tai ban ghi moi nhat: 12 o last_ts-55 s ... last_ts; cua so [end-60 s, end)
    end = last_ts + pd.Timedelta(seconds=sample_s)
    start = end - pd.Timedelta(seconds=window_s)
    rows = list(db[RAW].find(
        {"meta.device_id": device, "ts": {"$gte": (start - timedelta(seconds=MAX_GAP_S)).to_pydatetime(),
                                          "$lt": end.to_pydatetime()}},
        {"_id": 0, "ts": 1, "seq": 1, "uptime_s": 1, **{f: 1 for f in SENSOR_FIELDS}}).sort("ts", 1))
    return pd.DataFrame(rows), end


def predict_once(db: Database, device: str, bundle: dict) -> dict | None:
    sample_s, window_s = bundle["sample_s"], bundle["window_seconds"]
    n = window_s // sample_s
    got = latest_window(db, device, sample_s, window_s)
    if got is None:
        return None
    df, end = got
    start = end - pd.Timedelta(seconds=window_s)
    now = pd.Timestamp.now(tz="UTC")
    if df.empty:
        return None
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    if (now - df["ts"].max()).total_seconds() > STALE_AFTER_S:  # thiet bi offline -> bo qua
        log.info("Du lieu moi nhat cua %s da cu (%s), bo qua", device, df["ts"].max())
        return None
    for f in SENSOR_FIELDS:
        df[f] = pd.to_numeric(df.get(f), errors="coerce")
        lo, hi = VALID_RANGE[f]
        df.loc[~df[f].between(lo, hi), f] = np.nan
    # Khong vat qua reset hay mat ket noi dai (tinh ca doan dem truoc cua so)
    for col in ["seq", "uptime_s"]:
        if col in df and (pd.to_numeric(df[col], errors="coerce").diff() < 0).any():
            log.info("%s: phat hien reset trong cua so -> cho du lieu moi", device)
            return None
    win = df[df["ts"] >= start - pd.Timedelta(seconds=sample_s / 2)]
    edges = pd.concat([pd.Series([start]), win["ts"], pd.Series([end])])
    if edges.diff().dt.total_seconds().max() > MAX_GAP_S:
        log.info("%s: co khoang trong > %d s trong cua so -> bo qua", device, MAX_GAP_S)
        return None

    step = pd.Timedelta(seconds=sample_s)
    anchor = end - step                                        # = ban ghi moi nhat
    slot = anchor + ((win["ts"] - anchor) / step).round().astype(int) * step
    grid = (win.groupby(slot)[SENSOR_FIELDS].mean()
            .reindex(pd.date_range(start, anchor, freq=f"{sample_s}s")))
    missing = grid.isna().sum()
    if (missing > bundle["max_missing_x"]).any():
        log.info("%s: cua so khuyet qua 10%% %s -> bo qua", device, missing.to_dict())
        return None

    t0 = time.perf_counter()
    t_s = np.arange(n) * sample_s
    feats = {}
    for f in bundle["sensors"]:
        s = grid[f].reset_index(drop=True).interpolate(limit_area="inside").bfill().ffill()
        feats.update(channel_features(s, t_s, f))
    y_hat = float(bundle["model"].predict(pd.DataFrame([feats])[bundle["features"]])[0])
    compute_ms = (time.perf_counter() - t0) * 1000

    doc = {
        "ts": end.to_pydatetime(),                      # thoi diem phat du bao = cuoi cua so X
        "meta": {"device_id": device},
        "temperature_forecast_next60s": round(y_hat, 3),
        "input_window_start": start.to_pydatetime(),
        "forecast_window_start": end.to_pydatetime(),
        "forecast_window_end": (end + pd.Timedelta(seconds=window_s)).to_pydatetime(),
        "current_temperature": float(grid["temperature"].dropna().iloc[-1]),
        "model": "Ridge_6",
        "selected_features": bundle["selected"],
        "trained_on": bundle.get("data_path"),
        "computed_at": now.to_pydatetime(),
        "compute_ms": round(compute_ms, 3),
    }
    db[FORECAST].replace_one({"meta.device_id": device, "ts": doc["ts"]}, doc, upsert=True)
    log.info("%s: du bao %.2f C cho [%s, %s) | hien tai %.2f C | %.1f ms", device, y_hat,
             end.strftime("%H:%M:%S"), doc["forecast_window_end"].strftime("%H:%M:%S"),
             doc["current_temperature"], compute_ms)
    return doc


def main() -> None:
    p = argparse.ArgumentParser(description="Du bao nhiet do trung binh 60 s tiep theo (Bai 3)")
    p.add_argument("--device", required=True)
    p.add_argument("--every", type=int, default=30, help="chu ky du bao (giay), bang buoc truot 30 s")
    p.add_argument("--once", action="store_true")
    p.add_argument("--model", default=str(MODEL_PATH))
    args = p.parse_args()

    bundle = load_bundle(Path(args.model))
    log.info("Model Ridge_6, 6 cot: %s (huan luyen tu %s)", bundle["selected"], bundle.get("data_path"))
    db = get_db()
    ensure_indexes(db)
    while True:
        predict_once(db, args.device, bundle)
        if args.once:
            break
        try:
            time.sleep(args.every)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
