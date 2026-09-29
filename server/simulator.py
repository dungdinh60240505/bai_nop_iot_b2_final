"""Gia lap ESP32 (khi chua chay Wokwi) + tiem loi de kiem thu pipeline.

Vi du:
  python simulator.py                          # gui moi 5 s vao Mosquitto local
  python simulator.py --interval 1 --count 120 # 120 goi, 1 goi/giay
  python simulator.py --backfill-minutes 120   # tao nhanh 2 gio du lieu qua khu roi chay tiep
  python simulator.py --host broker.emqx.io --namespace dungdinh60240505   # di qua bridge nhu ESP32

Loi duoc tiem ngau nhien (co the chinh ty le):
  --p-missing   gia tri null   --p-spike  gai nhieu      --p-duplicate goi trung
  --p-drop      bo qua goi (tao khoang trong seq)        --p-invalid  JSON sai / ngoai gioi han
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time

import paho.mqtt.client as mqtt

from common import settings, setup_logging

log = setup_logging("simulator")


class SensorModel:
    """Tin hieu gan thuc te: nhiet do dao dong hinh sin, do am nguoc pha, khoang cach random walk."""

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.distance = 120.0

    def read(self, t: float) -> dict[str, float]:
        phase = 2 * math.pi * (t % 3600) / 3600  # chu ky 1 gio cho de thay tren dashboard
        temperature = 27 + 2.5 * math.sin(phase) + self.rng.gauss(0, 0.15)
        humidity = 65 - 8 * math.sin(phase) + self.rng.gauss(0, 0.6)
        self.distance += self.rng.gauss(0, 3)
        if self.rng.random() < 0.01:
            self.distance = self.rng.uniform(8, 20)  # vat can den gan
        self.distance = min(max(self.distance, 5), 380)
        return {"temperature": round(temperature, 2), "humidity": round(humidity, 2),
                "distance_cm": round(self.distance, 2)}


def build_payload(model: SensorModel, args, seq: int, t: float) -> dict:
    values = model.read(t)
    rng = model.rng
    for field in values:
        if rng.random() < args.p_missing:
            values[field] = None
    if rng.random() < args.p_spike and values["temperature"] is not None:
        values["temperature"] = round(values["temperature"] + rng.choice([-1, 1]) * rng.uniform(15, 40), 2)
    distance = values["distance_cm"]
    return {
        "device_id": args.device_id,
        "room": args.room,
        "seq": seq,
        "ts": int(t * 1000),
        **values,
        "alarm": distance is not None and distance < 15,
        "rssi": int(rng.gauss(-60, 5)),
        "uptime_s": int(t - args.start_time),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Gia lap cam bien ESP32")
    p.add_argument("--host", default=settings.mqtt_host)
    p.add_argument("--port", type=int, default=settings.mqtt_port)
    p.add_argument("--namespace", default="", help="tien to topic khi gui len public broker")
    p.add_argument("--device-id", default="sim-room2")
    p.add_argument("--room", default="room2")
    p.add_argument("--interval", type=float, default=5.0, help="giay giua 2 goi")
    p.add_argument("--count", type=int, default=0, help="so goi real-time (0 = chay mai)")
    p.add_argument("--backfill-minutes", type=int, default=0, help="tao truoc N phut du lieu qua khu")
    p.add_argument("--p-missing", type=float, default=0.03)
    p.add_argument("--p-spike", type=float, default=0.02)
    p.add_argument("--p-duplicate", type=float, default=0.02)
    p.add_argument("--p-drop", type=float, default=0.02)
    p.add_argument("--p-invalid", type=float, default=0.01)
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()
    # Backfill ket thuc truoc hien tai 2 phut de khong lam ban log do tre
    args.start_time = time.time() - args.backfill_minutes * 60 - (120 if args.backfill_minutes else 0)

    prefix = f"{args.namespace.rstrip('/')}/" if args.namespace else ""
    topic = f"{prefix}iot/sensor/{args.room}"
    status_topic = f"{prefix}iot/status/{args.room}"

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"{args.device_id}-{random.randint(0, 99999)}")
    client.will_set(status_topic, "offline", qos=1, retain=True)
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()
    client.publish(status_topic, "online", qos=1, retain=True)
    log.info("Publish -> %s:%d topic=%s", args.host, args.port, topic)

    model = SensorModel(args.seed)
    rng = model.rng
    seq = 0
    counts = {"sent": 0, "duplicate": 0, "dropped": 0, "invalid": 0}

    def send(t: float) -> None:
        nonlocal seq
        seq += 1
        if rng.random() < args.p_drop:
            counts["dropped"] += 1  # "mat" goi tren duong truyen
            return
        payload = build_payload(model, args, seq, t)
        if rng.random() < args.p_invalid:
            counts["invalid"] += 1
            bad = rng.choice(["{temperature: 25", json.dumps({**payload, "humidity": 180}),
                              json.dumps({**payload, "seq": "abc"})])
            client.publish(topic, bad, qos=1)
            return
        body = json.dumps(payload)
        client.publish(topic, body, qos=1)
        counts["sent"] += 1
        if rng.random() < args.p_duplicate:
            client.publish(topic, body, qos=1)
            counts["duplicate"] += 1

    try:
        if args.backfill_minutes:
            n = int(args.backfill_minutes * 60 / 5)
            log.info("Backfill %d goi (%d phut, moi 5 s)...", n, args.backfill_minutes)
            for i in range(n):
                send(args.start_time + i * 5)
                if i % 200 == 0:
                    time.sleep(0.05)  # tranh don ep broker
            log.info("Backfill xong: %s", counts)

        i = 0
        while args.count == 0 or i < args.count:
            send(time.time())
            i += 1
            if i % 12 == 0:
                log.info("Da gui %d goi real-time | %s", i, counts)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        time.sleep(0.5)
        client.publish(status_topic, "offline", qos=1, retain=True).wait_for_publish(2)
        client.loop_stop()
        client.disconnect()
        log.info("Ket thuc: %s", counts)


if __name__ == "__main__":
    main()
