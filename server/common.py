"""Cau hinh va ham dung chung cho cac script Python cua Bai thuc hanh so 2."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.database import Database

BASE_DIR = Path(__file__).resolve().parent
# .env nam canh file nay (server/.env); neu chua co thi dung .env.example
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / ".env.example")

# Ten collection
RAW = "sensor_raw"              # time-series: du lieu tho tu MQTT
PROCESSED = "sensor_processed"  # time-series: du lieu da tien xu ly
LATENCY = "latency_log"         # log do tre end-to-end (TTL)
REJECTED = "rejected_messages"  # goi tin khong hop le (TTL)
DEVICES = "device_status"       # trang thai + bo dem chat luong theo thiet bi
RUNS = "preprocess_runs"        # nhat ky moi lan chay tien xu ly

SENSOR_FIELDS = ["temperature", "humidity", "distance_cm"]


@dataclass(frozen=True)
class Settings:
    mqtt_host: str = os.getenv("MQTT_HOST", "localhost")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "1883"))
    telemetry_topic: str = os.getenv("MQTT_TELEMETRY_TOPIC", "iot/sensor/#")
    status_topic: str = os.getenv("MQTT_STATUS_TOPIC", "iot/status/#")
    mqtt_client_id: str = os.getenv("MQTT_CLIENT_ID", "collector-lab2")
    mongo_uri: str = os.getenv("MONGO_URI", "mongodb://iot:iot123@localhost:27017/?authSource=admin")
    mongo_db: str = os.getenv("MONGO_DB", "iot_lab2")
    raw_retention_days: int = int(os.getenv("RAW_RETENTION_DAYS", "30"))
    processed_retention_days: int = int(os.getenv("PROCESSED_RETENTION_DAYS", "180"))
    latency_retention_days: int = int(os.getenv("LATENCY_RETENTION_DAYS", "30"))
    rejected_retention_days: int = int(os.getenv("REJECTED_RETENTION_DAYS", "7"))
    timezone: str = os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh")


settings = Settings()


def get_db(uri: str | None = None, db_name: str | None = None) -> Database:
    """Tra ve Database; tz_aware=True de moi datetime doc ra deu la UTC co mui gio."""
    client = MongoClient(uri or settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    return client[db_name or settings.mongo_db]


def setup_logging(name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    return logging.getLogger(name)
