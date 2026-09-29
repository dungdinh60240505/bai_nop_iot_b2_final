"""Tien xu ly du lieu IoT: sensor_raw -> sensor_processed.

Vi du:
  python preprocess.py                               # 60 phut gan nhat, cua so 1 phut, IQR
  python preprocess.py --minutes 180 --window 5min --method zscore
  python preprocess.py --start 2026-09-29T08:00 --end 2026-09-29T10:00 --csv ket_qua.csv
  python preprocess.py --every 300                   # chay dinh ky moi 5 phut (Ctrl+C de dung)

Pipeline cho tung thiet bi:
  1. Doc du lieu tho theo khoang thoi gian
  2. Loai goi trung (device_id, seq, ts), sap xep theo thoi gian
  3. Kiem tra dai do cua cam bien (DHT22, HC-SR04) -> ngoai dai = NaN
  4. Phat hien outlier (IQR hoac Z-score) tren phan du so voi median truot -> NaN
  5. Resampling theo cua so thoi gian (mean) + dem so mau / cua so
  6. Xu ly missing: noi suy theo thoi gian cho khoang trong ngan, bo cua so trong qua dai
  7. Tao dac trung: rolling mean, delta; chuan hoa Z-score (StandardScaler) va Min-Max
  8. Ghi de vao sensor_processed (idempotent) + ghi nhat ky vao preprocess_runs
"""
from __future__ import annotations

import argparse
import time
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from pymongo.database import Database
from sklearn.preprocessing import MinMaxScaler, StandardScaler

from common import PROCESSED, RAW, RUNS, SENSOR_FIELDS, get_db, settings, setup_logging

log = setup_logging("preprocess")

# Dai do theo datasheet: ngoai dai = sai so cam bien
SPEC_RANGE = {"temperature": (-40.0, 80.0), "humidity": (0.0, 100.0), "distance_cm": (2.0, 400.0)}
# Do phan tan toi thieu: tranh truong hop du lieu gan nhu hang so (Wokwi) lam IQR = 0
MIN_SPREAD = {"temperature": 0.3, "humidity": 1.0, "distance_cm": 2.0}
# He so IQR theo truong: khoang cach thay doi dot ngot la su kien that (vat can) -> nguong rong hon
IQR_K = {"temperature": 1.5, "humidity": 1.5, "distance_cm": 3.0}


# ------------------------------------------------------------------ doc du lieu
def load_raw(db: Database, start: datetime, end: datetime, device_id: str | None = None) -> pd.DataFrame:
    query: dict = {"ts": {"$gte": start, "$lt": end}}
    if device_id:
        query["meta.device_id"] = device_id
    projection = {"_id": 0, "ts": 1, "meta": 1, "seq": 1, "device_ts_ms": 1, **{f: 1 for f in SENSOR_FIELDS}}
    rows = list(db[RAW].find(query, projection).sort("ts", 1))
    if not rows:
        return pd.DataFrame(columns=["ts", "device_id", "room", "seq", "device_ts_ms", *SENSOR_FIELDS])
    df = pd.json_normalize(rows).rename(columns={"meta.device_id": "device_id", "meta.room": "room"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for f in SENSOR_FIELDS:
        df[f] = pd.to_numeric(df.get(f), errors="coerce")
    return df


# ------------------------------------------------------------------ lam sach
def detect_outliers(series: pd.Series, field: str, method: str, detrend_window: int,
                    iqr_k: float | None = None, z_thresh: float = 3.0) -> pd.Series:
    """Tra ve mask True cho diem bat thuong.

    Phat hien tren phan du r = x - median_truot(x) de khong nham su thay doi
    xu huong that (vd. troi nong dan) la outlier. detrend_window=0 -> dung median toan cuc.
    """
    x = series.astype(float)
    valid = x.notna()
    if valid.sum() < 5:
        return pd.Series(False, index=x.index)
    if detrend_window and detrend_window > 1:
        baseline = x.rolling(detrend_window, center=True, min_periods=1).median()
    else:
        baseline = pd.Series(x.median(), index=x.index)
    r = x - baseline
    if method == "iqr":
        k = iqr_k if iqr_k is not None else IQR_K[field]
        q1, q3 = r.quantile(0.25), r.quantile(0.75)
        iqr = max(q3 - q1, MIN_SPREAD[field])
        mask = (r < q1 - k * iqr) | (r > q3 + k * iqr)
    elif method == "zscore":
        std = max(r.std(ddof=0), MIN_SPREAD[field])
        mask = ((r - r.mean()) / std).abs() > z_thresh
    else:
        raise ValueError(f"method khong ho tro: {method}")
    return mask & valid


def clean(df: pd.DataFrame, method: str, detrend_window: int) -> tuple[pd.DataFrame, dict]:
    stats: dict = {"rows_raw": int(len(df))}
    key = ["seq", "device_ts_ms"] if "device_ts_ms" in df else ["seq"]
    before = len(df)
    df = df.drop_duplicates(subset=key, keep="first").sort_values("ts").copy()
    stats["duplicates_removed"] = int(before - len(df))

    seq = df["seq"].dropna().astype(int).to_numpy()
    steps = np.diff(seq) if len(seq) > 1 else np.array([], dtype=int)
    stats["seq_gaps_lost"] = int(steps[steps > 1].sum() - (steps > 1).sum()) if len(steps) else 0

    stats["null_values"], stats["out_of_spec"], stats["outliers"] = {}, {}, {}
    for f in SENSOR_FIELDS:
        stats["null_values"][f] = int(df[f].isna().sum())
        lo, hi = SPEC_RANGE[f]
        bad = df[f].notna() & ~df[f].between(lo, hi)
        stats["out_of_spec"][f] = int(bad.sum())
        df.loc[bad, f] = np.nan
        out = detect_outliers(df[f], f, method, detrend_window)
        stats["outliers"][f] = int(out.sum())
        df[f"{f}_is_outlier"] = out | bad  # danh dau ca gia tri ngoai dai do
        df.loc[out, f] = np.nan
    return df, stats


# ------------------------------------------------------------------ resample + features
def resample_and_features(df: pd.DataFrame, window: str, gap_limit: int, roll: int,
                          expected_interval_s: float) -> tuple[pd.DataFrame, dict]:
    s = df.set_index("ts")[SENSOR_FIELDS]
    agg = s.resample(window, label="left", closed="left").mean()
    counts = s.resample(window, label="left", closed="left").size()
    agg["n_samples"] = counts.reindex(agg.index).fillna(0).astype(int)

    stats = {"windows_total": int(len(agg)),
             "windows_empty": int((agg["n_samples"] == 0).sum())}
    missing_before = agg[SENSOR_FIELDS].isna()
    for f in SENSOR_FIELDS:
        agg[f] = agg[f].interpolate(method="time", limit=gap_limit, limit_area="inside")
    filled = missing_before & agg[SENSOR_FIELDS].notna()
    agg["filled"] = filled.any(axis=1)
    stats["values_interpolated"] = int(filled.to_numpy().sum())

    before = len(agg)
    agg = agg.dropna(subset=SENSOR_FIELDS, how="any")
    stats["windows_dropped"] = int(before - len(agg))

    expected = pd.Timedelta(window).total_seconds() / expected_interval_s
    agg["completeness"] = (agg["n_samples"] / expected).clip(upper=1.0).round(3)

    for f in SENSOR_FIELDS:
        agg[f"{f}_roll"] = agg[f].rolling(roll, min_periods=1).mean()
        agg[f"{f}_delta"] = agg[f].diff()
    stats["rows_out"] = int(len(agg))
    return agg, stats


def normalize(agg: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    if agg.empty:
        return agg, {}
    values = agg[SENSOR_FIELDS].to_numpy()
    std = StandardScaler().fit(values)
    mm = MinMaxScaler().fit(values)
    z, norm = std.transform(values), mm.transform(values)
    for i, f in enumerate(SENSOR_FIELDS):
        agg[f"{f}_z"] = z[:, i]
        agg[f"{f}_norm"] = norm[:, i]
    params = {f: {"mean": float(std.mean_[i]), "std": float(std.scale_[i]),
                  "min": float(mm.data_min_[i]), "max": float(mm.data_max_[i])}
              for i, f in enumerate(SENSOR_FIELDS)}
    return agg, params


# ------------------------------------------------------------------ ghi ket qua
def to_documents(agg: pd.DataFrame, device_id: str, room: str, window: str, run_id: str) -> list[dict]:
    docs = []
    agg = agg.astype(object).where(agg.notna(), None)
    for ts, row in agg.iterrows():
        doc = {"ts": ts.to_pydatetime(), "meta": {"device_id": device_id, "room": room, "window": window},
               "run_id": run_id}
        for col, val in row.items():
            if isinstance(val, (np.floating, float)) and val is not None:
                val = round(float(val), 4)
            elif isinstance(val, np.integer):
                val = int(val)
            elif isinstance(val, np.bool_):
                val = bool(val)
            doc[col] = val
        docs.append(doc)
    return docs


def floor_time(dt: datetime, window: str) -> datetime:
    return pd.Timestamp(dt).floor(window).to_pydatetime()


# ------------------------------------------------------------------ chay pipeline
def run(db: Database, start: datetime, end: datetime, window: str = "1min", method: str = "iqr",
        detrend_window: int = 11, gap_limit: int = 3, roll: int = 5, device_id: str | None = None,
        expected_interval_s: float = 5.0, csv_path: str | None = None) -> dict:
    t0 = time.perf_counter()
    start = floor_time(start, window)
    run_id = uuid.uuid4().hex[:12]
    raw = load_raw(db, start, end, device_id)
    summary = {"run_id": run_id, "run_at": datetime.now(timezone.utc), "start": start, "end": end,
               "params": {"window": window, "method": method, "detrend_window": detrend_window,
                          "gap_limit": gap_limit, "roll": roll},
               "devices": {}}
    frames = []
    for dev, part in raw.groupby("device_id"):
        room = str(part["room"].dropna().iloc[0]) if part["room"].notna().any() else ""
        cleaned, st_clean = clean(part, method, detrend_window)
        agg, st_res = resample_and_features(cleaned, window, gap_limit, roll, expected_interval_s)
        agg, scaler = normalize(agg)
        docs = to_documents(agg, dev, room, window, run_id)

        db[PROCESSED].delete_many({"meta.device_id": dev, "meta.window": window,
                                   "ts": {"$gte": start, "$lt": end}})
        if docs:
            db[PROCESSED].insert_many(docs)
        summary["devices"][dev] = {**st_clean, **st_res, "scaler": scaler}
        log.info("%s: %d ban ghi tho -> %d cua so %s | trung=%d, ngoai dai=%s, outlier=%s, noi suy=%d",
                 dev, st_clean["rows_raw"], st_res["rows_out"], window, st_clean["duplicates_removed"],
                 st_clean["out_of_spec"], st_clean["outliers"], st_res["values_interpolated"])
        if csv_path:
            frames.append(agg.assign(device_id=dev))

    summary["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    db[RUNS].insert_one(dict(summary))
    if csv_path and frames:
        pd.concat(frames).to_csv(csv_path)
        log.info("Da xuat CSV: %s", csv_path)
    if raw.empty:
        log.warning("Khong co du lieu tho trong khoang %s -> %s", start, end)
    log.info("Hoan tat run %s trong %.0f ms", run_id, summary["duration_ms"])
    return summary


def parse_time(value: str) -> datetime:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(settings.timezone)  # gio dia phuong
    return ts.tz_convert("UTC").to_pydatetime()


def main() -> None:
    p = argparse.ArgumentParser(description="Tien xu ly du lieu IoT tu MongoDB")
    p.add_argument("--minutes", type=int, default=60, help="xu ly N phut gan nhat (neu khong co --start)")
    p.add_argument("--start", help="thoi diem bat dau, vd 2026-09-29T08:00 (gio dia phuong)")
    p.add_argument("--end", help="thoi diem ket thuc (mac dinh: bay gio)")
    p.add_argument("--window", default="1min", help="cua so resample: 30s, 1min, 5min, 1h...")
    p.add_argument("--method", choices=["iqr", "zscore"], default="iqr")
    p.add_argument("--detrend-window", type=int, default=11,
                   help="so mau cua median truot khi tim outlier (0 = median toan cuc)")
    p.add_argument("--gap-limit", type=int, default=3, help="so cua so trong lien tiep toi da duoc noi suy")
    p.add_argument("--roll", type=int, default=5, help="so cua so cho rolling mean")
    p.add_argument("--device", help="chi xu ly 1 device_id")
    p.add_argument("--interval", type=float, default=5.0, help="chu ky gui cua thiet bi (giay)")
    p.add_argument("--csv", help="xuat ket qua ra file CSV")
    p.add_argument("--every", type=int, default=0, help="chay lap lai moi N giay")
    args = p.parse_args()

    db = get_db()
    while True:
        end = parse_time(args.end) if args.end else datetime.now(timezone.utc)
        start = parse_time(args.start) if args.start else end - timedelta(minutes=args.minutes)
        run(db, start, end, args.window, args.method, args.detrend_window, args.gap_limit,
            args.roll, args.device, args.interval, args.csv)
        if not args.every:
            break
        log.info("Cho %d giay toi lan chay tiep theo...", args.every)
        try:
            time.sleep(args.every)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
