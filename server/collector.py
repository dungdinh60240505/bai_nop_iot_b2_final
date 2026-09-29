"""MQTT collector: subscribe Mosquitto local -> validate -> ghi MongoDB (real-time).

    python collector.py

Chuc nang:
  * Validate JSON (kieu du lieu, truong bat buoc, gioi han vat ly)
  * Loai goi trung lap (device_id, seq, ts) bang cache LRU (nap lai tu DB khi khoi dong)
  * Phat hien mat goi / thiet bi khoi dong lai dua tren so thu tu seq
  * Ghi sensor_raw (time-series); goi loi -> rejected_messages
  * Do tre: net_ms = recv - ts(ESP32), db_ms = ghi xong - recv, e2e_ms = ghi xong - ts
  * MongoDB loi -> dem tam trong RAM, tu ghi bu khi ket noi lai
  * Persistent session QoS 1: collector tat tam thi Mosquitto giu lai message
"""
from __future__ import annotations

import json
import math
import signal
import sys
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt
from pymongo import DESCENDING
from pymongo.database import Database
from pymongo.errors import PyMongoError

from common import (DEVICES, LATENCY, RAW, REJECTED, SENSOR_FIELDS, get_db,
                    settings, setup_logging)

log = setup_logging("collector")

# Gioi han "khong the xay ra" ve vat ly -> loai ngay tai collector.
# (Gia tri bat thuong nhung van co the xay ra se de preprocess.py xu ly.)
HARD_LIMITS = {
    "temperature": (-50.0, 150.0),
    "humidity": (0.0, 100.0),
    "distance_cm": (0.0, 1000.0),
}
MIN_VALID_TS_MS = 1_672_531_200_000  # 2023-01-01, truoc moc nay = ESP32 chua dong bo NTP
MAX_FUTURE_MS = 5 * 60 * 1000         # cho phep dong ho ESP32 nhanh hon toi da 5 phut
LATENCY_WINDOW_MS = (-5_000, 60_000)  # ngoai khoang nay coi la lech dong ho / du lieu backfill


def utc_from_ms(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def validate(topic: str, payload: bytes, recv_ms: float,
             retention_days: int = settings.raw_retention_days) -> tuple[dict | None, list[str]]:
    """Kiem tra goi tin. Tra ve (record de ghi DB, danh sach loi). record=None neu bi tu choi."""
    errors: list[str] = []
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [f"JSON khong hop le: {exc}"]
    if not isinstance(data, dict):
        return None, ["Payload phai la JSON object"]

    device_id = data.get("device_id")
    if not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 64:
        errors.append("device_id thieu hoac khong hop le")

    seq = data.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        errors.append("seq phai la so nguyen >= 0")

    room = data.get("room")
    if room is None:
        room = topic.rsplit("/", 1)[-1]  # iot/sensor/<room>
    elif not isinstance(room, str):
        errors.append("room phai la chuoi")

    values: dict[str, float | None] = {}
    for field in SENSOR_FIELDS:
        v = data.get(field)
        if v is None:
            values[field] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            errors.append(f"{field} phai la so hoac null")
            continue
        lo, hi = HARD_LIMITS[field]
        if not lo <= v <= hi:
            errors.append(f"{field}={v} ngoai gioi han vat ly [{lo}, {hi}]")
            continue
        values[field] = float(v)
    if not errors and all(values.get(f) is None for f in SENSOR_FIELDS):
        errors.append("khong co gia tri cam bien nao")

    alarm = data.get("alarm")
    if alarm is not None and not isinstance(alarm, bool):
        errors.append("alarm phai la boolean")
    rssi = data.get("rssi")
    if rssi is not None and (isinstance(rssi, bool) or not isinstance(rssi, int)):
        errors.append("rssi phai la so nguyen")
    uptime = data.get("uptime_s")
    if uptime is not None and (isinstance(uptime, bool) or not isinstance(uptime, int)):
        errors.append("uptime_s phai la so nguyen")

    ts_ms = data.get("ts")
    ts_source = "device"
    if ts_ms is None:
        ts_source = "server"
    elif isinstance(ts_ms, bool) or not isinstance(ts_ms, int):
        errors.append("ts phai la epoch milliseconds (so nguyen)")
    else:
        oldest = recv_ms - retention_days * 86_400_000
        if ts_ms < max(MIN_VALID_TS_MS, oldest) or ts_ms > recv_ms + MAX_FUTURE_MS:
            ts_source = "server"  # dong ho thiet bi sai -> dung thoi diem nhan

    if errors:
        return None, errors

    record = {
        "ts": utc_from_ms(ts_ms if ts_source == "device" else recv_ms),
        "meta": {"device_id": device_id, "room": room},
        "seq": seq,
        **values,
        "alarm": alarm,
        "rssi": rssi,
        "uptime_s": uptime,
        "ts_source": ts_source,
        "device_ts_ms": ts_ms if isinstance(ts_ms, int) else None,
        "recv_at": utc_from_ms(recv_ms),
        "topic": topic,
    }
    return record, []


class Collector:
    def __init__(self, db: Database, dedup_size: int = 5000, buffer_size: int = 10000):
        self.db = db
        self.dedup_size = dedup_size
        self.seen: OrderedDict[tuple, None] = OrderedDict()
        self.last_seq: dict[str, int] = {}
        self.pending: deque[tuple[dict, float]] = deque(maxlen=buffer_size)
        self.room_status: dict[str, str] = {}
        self.stats = {"received": 0, "stored": 0, "duplicates": 0, "rejected": 0,
                      "lost": 0, "reboots": 0, "buffered": 0}

    # ---------- khoi dong ----------
    def warm_up(self) -> None:
        """Nap cac key gan nhat tu DB de khong ghi trung khi collector khoi dong lai."""
        try:
            cursor = self.db[RAW].find(
                {}, {"meta.device_id": 1, "seq": 1, "device_ts_ms": 1, "uptime_s": 1}
            ).sort("ts", DESCENDING).limit(self.dedup_size)
            docs = list(cursor)
        except PyMongoError as exc:
            log.warning("Khong nap duoc cache chong trung: %s", exc)
            return
        for doc in reversed(docs):
            self._remember(self._key(doc["meta"]["device_id"], doc))
        for dev in self.db[DEVICES].find({}, {"last_seq": 1}):
            if dev.get("last_seq") is not None:
                self.last_seq[dev["_id"]] = dev["last_seq"]
        log.info("Nap %d key chong trung, %d thiet bi", len(self.seen), len(self.last_seq))

    # ---------- chong trung ----------
    @staticmethod
    def _key(device_id: str, rec: dict) -> tuple:
        return (device_id, rec.get("seq"), rec.get("device_ts_ms") or rec.get("uptime_s"))

    def _remember(self, key: tuple) -> None:
        self.seen[key] = None
        if len(self.seen) > self.dedup_size:
            self.seen.popitem(last=False)

    # ---------- xu ly telemetry ----------
    def handle_telemetry(self, topic: str, payload: bytes, recv_s: float | None = None) -> str:
        recv_ms = (recv_s or time.time()) * 1000.0
        self.stats["received"] += 1
        record, errors = validate(topic, payload, recv_ms)

        if record is None:
            self.stats["rejected"] += 1
            log.warning("REJECT %s: %s", topic, "; ".join(errors))
            self._safe(lambda: self.db[REJECTED].insert_one({
                "recv_at": utc_from_ms(recv_ms), "topic": topic,
                "payload": payload.decode("utf-8", errors="replace")[:1000], "errors": errors,
            }))
            self._bump_device(payload, {"rejected": 1})
            return "rejected"
        device_id = record["meta"]["device_id"]
        key = self._key(device_id, record)
        if key in self.seen:
            self.stats["duplicates"] += 1
            log.info("DUPLICATE %s seq=%s -> bo qua", device_id, record["seq"])
            self._safe(lambda: self.db[DEVICES].update_one(
                {"_id": device_id}, {"$inc": {"received": 1, "duplicates": 1}}, upsert=True))
            return "duplicate"
        self._remember(key)

        inc = {"received": 1, "stored": 1}
        inc.update(self._check_sequence(device_id, record["seq"]))

        # Ghi bu cac ban ghi dang cho (neu truoc do MongoDB loi)
        if self.pending:
            self._flush_pending()

        try:
            self.db[RAW].insert_one(record)
        except PyMongoError as exc:
            self.pending.append((record, recv_ms))
            self.stats["buffered"] += 1
            log.error("MongoDB loi, dem tam (%d ban ghi): %s", len(self.pending), exc)
            return "buffered"
        stored_ms = time.time() * 1000.0
        self.stats["stored"] += 1

        self._log_latency(record, recv_ms, stored_ms)
        self._safe(lambda: self._update_device(device_id, record, inc))
        return "stored"

    def _check_sequence(self, device_id: str, seq: int) -> dict[str, int]:
        inc: dict[str, int] = {}
        last = self.last_seq.get(device_id)
        if last is not None:
            if seq > last + 1:
                lost = seq - last - 1
                inc["lost"] = lost
                self.stats["lost"] += lost
                log.warning("MAT GOI %s: seq %d -> %d (mat %d goi)", device_id, last, seq, lost)
            elif seq <= last:
                inc["reboots"] = 1
                self.stats["reboots"] += 1
                log.warning("%s khoi dong lai (seq %d -> %d)", device_id, last, seq)
        self.last_seq[device_id] = seq
        return inc

    def _log_latency(self, record: dict, recv_ms: float, stored_ms: float) -> None:
        if record["ts_source"] != "device":
            return
        dev_ms = record["device_ts_ms"]
        net_ms = recv_ms - dev_ms
        if not LATENCY_WINDOW_MS[0] <= net_ms <= LATENCY_WINDOW_MS[1]:
            return  # du lieu backfill hoac lech dong ho qua lon
        doc = {
            "device_id": record["meta"]["device_id"],
            "seq": record["seq"],
            "device_ts": record["ts"],
            "recv_at": record["recv_at"],
            "net_ms": round(net_ms, 1),
            "db_ms": round(stored_ms - recv_ms, 1),
            "e2e_ms": round(stored_ms - dev_ms, 1),
        }
        self._safe(lambda: self.db[LATENCY].insert_one(doc))

    def _flush_pending(self) -> None:
        batch = [rec for rec, _ in self.pending]
        try:
            self.db[RAW].insert_many(batch, ordered=False)
        except PyMongoError as exc:
            log.error("Ghi bu that bai, giu lai %d ban ghi: %s", len(batch), exc)
            return
        self.pending.clear()
        self.stats["stored"] += len(batch)
        log.info("Da ghi bu %d ban ghi", len(batch))

    # ---------- trang thai thiet bi ----------
    def _update_device(self, device_id: str, record: dict, inc: dict[str, int]) -> None:
        room = record["meta"]["room"]
        update: dict[str, Any] = {
            "$set": {"room": room, "last_seen": record["recv_at"], "last_seq": record["seq"],
                     "last_ts_source": record["ts_source"]},
            "$inc": inc,
            "$setOnInsert": {"first_seen": record["recv_at"]},
        }
        if room in self.room_status:
            update["$set"]["status"] = self.room_status.pop(room)
            update["$set"]["status_at"] = record["recv_at"]
        self.db[DEVICES].update_one({"_id": device_id}, update, upsert=True)

    def _bump_device(self, payload: bytes, inc: dict[str, int]) -> None:
        """Goi bi reject: van tang bo dem va cap nhat seq (neu doc duoc) de khong bi tinh la 'mat goi'."""
        try:
            data = json.loads(payload)
            device_id, seq = data.get("device_id"), data.get("seq")
        except Exception:  # noqa: BLE001 - payload rac, khong doc duoc gi
            return
        if not isinstance(device_id, str) or not device_id:
            return
        if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0:
            inc = {**inc, **self._check_sequence(device_id, seq)}
        self._safe(lambda: self.db[DEVICES].update_one({"_id": device_id}, {"$inc": inc}, upsert=True))

    def handle_status(self, topic: str, payload: bytes) -> None:
        room = topic.rsplit("/", 1)[-1]
        status = payload.decode("utf-8", errors="replace").strip()[:32]
        log.info("STATUS %s = %s", room, status)
        now = datetime.now(timezone.utc)
        try:
            res = self.db[DEVICES].update_many({"room": room},
                                               {"$set": {"status": status, "status_at": now}})
            if res.matched_count == 0:
                self.room_status[room] = status  # ap dung khi nhan telemetry dau tien
        except PyMongoError as exc:
            log.error("Khong cap nhat duoc status: %s", exc)

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except PyMongoError as exc:
            log.error("MongoDB loi: %s", exc)


def build_mqtt_client(collector: Collector) -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=settings.mqtt_client_id,
        clean_session=False,  # persistent session: broker giu message QoS1 khi collector offline
    )
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    def on_connect(cl, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("Ket noi MQTT that bai: %s", reason_code)
            return
        log.info("Da ket noi MQTT %s:%d (session_present=%s)",
                 settings.mqtt_host, settings.mqtt_port, flags.session_present)
        cl.subscribe([(settings.telemetry_topic, 1), (settings.status_topic, 1)])

    def on_disconnect(cl, userdata, flags, reason_code, properties):
        log.warning("Mat ket noi MQTT (%s), dang ket noi lai...", reason_code)

    def on_message(cl, userdata, msg):
        recv_s = time.time()
        try:
            if mqtt.topic_matches_sub(settings.status_topic, msg.topic):
                collector.handle_status(msg.topic, msg.payload)
            else:
                collector.handle_telemetry(msg.topic, msg.payload, recv_s)
        except Exception:  # noqa: BLE001 - khong de 1 goi loi lam chet collector
            log.exception("Loi xu ly message tren %s", msg.topic)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    return client


def main() -> None:
    db = get_db()
    try:
        db.client.admin.command("ping")
    except PyMongoError as exc:
        log.error("Khong ket noi duoc MongoDB (%s). Da chay 'docker compose up -d' chua?", exc)
        sys.exit(1)

    collector = Collector(db)
    collector.warm_up()
    client = build_mqtt_client(collector)

    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())

    try:
        client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=30)
    except OSError as exc:
        log.error("Khong ket noi duoc MQTT %s:%d (%s)", settings.mqtt_host, settings.mqtt_port, exc)
        sys.exit(1)
    client.loop_start()
    log.info("Dang lang nghe %s va %s ... (Ctrl+C de dung)",
             settings.telemetry_topic, settings.status_topic)

    last_report, last_count = time.time(), 0
    while not stop_event.wait(1):
        if time.time() - last_report >= 60:
            rate = (collector.stats["received"] - last_count) / (time.time() - last_report)
            log.info("Thong ke: %s | %.2f msg/s", collector.stats, rate)
            last_report, last_count = time.time(), collector.stats["received"]

    log.info("Dung collector. Thong ke: %s", collector.stats)
    client.disconnect()
    client.loop_stop()


if __name__ == "__main__":
    main()
