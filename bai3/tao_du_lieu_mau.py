"""Tao bo du lieu mau du_lieu_mau_B2.csv cho Bai thuc hanh so 3.

    python tao_du_lieu_mau.py                 # 720 dong = 60 phut, chu ky 5 s
    python tao_du_lieu_mau.py --seed 7 --out du_lieu_mau_B2.csv

Bo mau duoc TAO BANG PYTHON theo schema ke thua tu Bai 1/Bai 2
(temperature, humidity, distance_cm, sequence, uptime_s, rssi ...).
KHONG phai du lieu tai tu ThingsBoard/MongoDB va KHONG phai so do moi truong thuc.

Kich ban mo phong thao tac tren Wokwi: cu 1-2 phut doi nhiet do trong 22-35 C,
do am 40-80 %RH, khoang cach 20-200 cm. Nhiet do va do am tien dan toi gia tri
dat (quan tinh bac 1, tau ~40 s) de chuoi co xu huong; khoang cach doi tuc thoi.
Them vai gia tri thieu / ngoai mien cho do am va khoang cach de kiem tra o 2;
nhiet do giu day du (temperature_observed = 1) de y luon la so do goc.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

STEP_S = 5


def piecewise_targets(rng: np.random.Generator, n: int, lo: float, hi: float) -> np.ndarray:
    """Gia tri dat thay doi moi 12-24 mau (60-120 s)."""
    out = np.empty(n)
    i, level = 0, rng.uniform(lo, hi)
    while i < n:
        length = int(rng.integers(12, 25))
        out[i:i + length] = level
        level = level + rng.uniform(-0.45, 0.45) * (hi - lo)
        if level < lo or level > hi:          # phan xa tai bien de khong dinh o mot dau
            level = 2 * (lo if level < lo else hi) - level
        i += length
    return out


def first_order(target: np.ndarray, tau_s: float, start: float) -> np.ndarray:
    a = 1 - np.exp(-STEP_S / tau_s)
    y = np.empty_like(target)
    cur = start
    for i, tgt in enumerate(target):
        cur = cur + a * (tgt - cur)
        y[i] = cur
    return y


def make(n: int = 720, seed: int = 3, start: str = "2026-10-20T08:00:00+07:00") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t0 = pd.Timestamp(start)
    ts_ms = (t0.value // 1_000_000) + np.arange(n) * STEP_S * 1000

    temp_set = piecewise_targets(rng, n, 22, 35)
    hum_set = piecewise_targets(rng, n, 40, 80)
    dist_set = piecewise_targets(rng, n, 20, 200)

    temperature = first_order(temp_set, 40, temp_set[0]) + rng.normal(0, 0.1, n)
    humidity = first_order(hum_set, 40, hum_set[0]) + rng.normal(0, 0.4, n)
    distance = dist_set + rng.normal(0, 0.8, n)

    df = pd.DataFrame({
        "timestamp": ts_ms.astype("int64"),
        "temperature": temperature.round(2),
        "humidity": humidity.round(2),
        "distance_cm": distance.round(2),
        "device_id": "mau-python-01",
        "session_id": "mau-s1",
        "sequence": np.arange(1, n + 1),
        "uptime_s": np.arange(n) * STEP_S + 12,
        "rssi": rng.normal(-62, 4, n).round().astype(int),
        "data_source": "python_sample",
        "temperature_observed": 1,
    })
    # Loi nho de o 2 co viec kiem tra (khong lam mat cua so nao, toi da 1 mau/kenh/cua so)
    df.loc[[57, 389], "humidity"] = np.nan
    df.loc[[141, 610], "distance_cm"] = np.nan
    df.loc[275, "distance_cm"] = 455.0      # ngoai mien HC-SR04 (2-400 cm) -> danh dau thieu
    df.loc[503, "humidity"] = 104.0         # ngoai mien %RH -> danh dau thieu
    return df


def main() -> None:
    p = argparse.ArgumentParser(description="Tao du_lieu_mau_B2.csv (bo mau Python)")
    p.add_argument("--rows", type=int, default=720)
    p.add_argument("--seed", type=int, default=3)
    p.add_argument("--out", default="du_lieu_mau_B2.csv")
    args = p.parse_args()
    df = make(args.rows, args.seed)
    df.to_csv(args.out, index=False)
    print(f"Da ghi {len(df)} dong -> {args.out}")


if __name__ == "__main__":
    main()
