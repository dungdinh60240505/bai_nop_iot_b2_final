# Bài thực hành số 2 – Thu thập, lưu trữ và tiền xử lý dữ liệu IoT

ESP32 (Wokwi) đọc DHT22 + HC-SR04 → JSON qua MQTT → Mosquitto (bridge) → Python collector → MongoDB Time-Series → tiền xử lý → dashboard Streamlit.

```
ESP32 (Wokwi) ──MQTT──▶ broker.emqx.io ──bridge──▶ Mosquitto local ──▶ collector.py ──▶ MongoDB
 topic: <ns>/iot/sensor/room1                        iot/sensor/room1                   sensor_raw
                                                                                          │
                                            dashboard.py (Streamlit) ◀── preprocess.py ◀──┘
                                                                         sensor_processed
```

> Wokwi không truy cập được `localhost` trên máy bạn, nên ESP32 gửi lên broker công khai `broker.emqx.io`, còn Mosquitto trên máy kéo dữ liệu về bằng **bridge**. Namespace `dungdinh60240505/` giúp tách topic của bạn khỏi người khác trên broker công khai.

## Cấu trúc thư mục

| Đường dẫn | Nội dung |
|---|---|
| `src/main.cpp` | Firmware ESP32: DHT22, HC-SR04, NTP timestamp, JSON, MQTT, Last Will, lệnh tiêm lỗi qua Serial |
| `platformio.ini`, `wokwi.toml`, `diagram.json` | Build PlatformIO + mô phỏng Wokwi |
| `server/docker-compose.yml` | Mosquitto 2 + MongoDB 7 (+ mongo-express tuỳ chọn) |
| `server/mosquitto/config/mosquitto.conf` | Listener 1883 + bridge tới broker.emqx.io |
| `server/.env.example` | Cấu hình (MQTT, MongoDB, retention) |
| `server/init_db.py` | Tạo schema: time-series collections, TTL, index |
| `server/collector.py` | Subscribe MQTT → validate → chống trùng → ghi MongoDB, log độ trễ |
| `server/simulator.py` | Giả lập ESP32 + tiêm lỗi (null, spike, trùng, mất gói, JSON sai) |
| `server/preprocess.py` | Làm sạch, outlier IQR/Z-score, resample, nội suy, rolling mean, delta, chuẩn hoá |
| `server/dashboard.py` | Dashboard/App Streamlit: real-time, đã xử lý, độ trễ, lưu trữ |
| `server/evaluate.py` | Xuất bảng số liệu độ trễ / chất lượng / dung lượng cho báo cáo |
| `server/benchmark_storage.py` | So sánh Time-series vs collection thường |

## Yêu cầu

- Docker Desktop, Python 3.10+
- VS Code + PlatformIO + Wokwi extension (hoặc board ESP32 thật)
- Đồng hồ máy tính đồng bộ Internet (Windows: `w32tm /resync`) để đo độ trễ chính xác

## Chạy nhanh

```bash
# 1. Hạ tầng
cd server
cp .env.example .env            # Windows: copy .env.example .env
docker compose up -d
docker compose logs mosquitto   # thấy "Connecting bridge ... broker.emqx.io"

# 2. Môi trường Python
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Schema MongoDB
python init_db.py

# 4. Collector (để chạy liên tục, terminal riêng)
python collector.py

# 5. Nguồn dữ liệu: build + chạy Wokwi (PlatformIO: Build, rồi "Wokwi: Start Simulator")
#    hoặc giả lập:  python simulator.py --backfill-minutes 120

# 6. Tiền xử lý định kỳ (terminal riêng)
python preprocess.py --every 300

# 7. Dashboard
streamlit run dashboard.py      # http://localhost:8501

# 8. Số liệu cho báo cáo
python evaluate.py --minutes 120
python benchmark_storage.py
```

Kiểm tra nhanh luồng MQTT: `docker exec -it iot-mosquitto mosquitto_sub -t "iot/#" -v`

## Định dạng dữ liệu

Topic: `<namespace>/iot/sensor/<room>` (trên broker công khai) → `iot/sensor/<room>` (local). Trạng thái: `iot/status/<room>` = `online` / `offline` (retained, Last Will).

```json
{"device_id":"esp32-room1","room":"room1","seq":42,"ts":1790661439722,
 "temperature":24.0,"humidity":null,"distance_cm":123.46,"alarm":false,"rssi":-60,"uptime_s":3600}
```

`ts` là epoch milliseconds (UTC) lấy từ NTP. Giá trị cảm biến lỗi được gửi là `null`.

## Schema MongoDB (database `iot_lab2`)

| Collection | Loại | Khoá / trường chính | Retention |
|---|---|---|---|
| `sensor_raw` | Time-series (`timeField=ts`, `metaField=meta`, granularity seconds) | `meta{device_id, room}`, `seq`, `temperature`, `humidity`, `distance_cm`, `alarm`, `rssi`, `uptime_s`, `ts_source`, `device_ts_ms`, `recv_at` | 30 ngày |
| `sensor_processed` | Time-series | `meta{device_id, room, window}`, giá trị trung bình, `n_samples`, `completeness`, `filled`, `*_roll`, `*_delta`, `*_z`, `*_norm`, `run_id` | 180 ngày |
| `latency_log` | Thường + TTL | `device_id`, `seq`, `net_ms`, `db_ms`, `e2e_ms`, `recv_at` | 30 ngày |
| `rejected_messages` | Thường + TTL | `payload`, `errors`, `recv_at` | 7 ngày |
| `device_status` | Thường | `_id=device_id`, `status`, `last_seen`, `last_seq`, `received`, `stored`, `duplicates`, `lost`, `rejected`, `reboots` | — |
| `preprocess_runs` | Thường | tham số + thống kê từng lần tiền xử lý | — |

## Tiêm lỗi để demo tiền xử lý

- Trên Wokwi: gõ vào Serial Monitor `n` (null), `s` (spike +60 °C), `d` (gói trùng); kéo thanh trượt DHT22/HC-SR04 để đổi giá trị; dừng mô phỏng vài phút để tạo khoảng trống.
- Với giả lập: `python simulator.py --p-spike 0.05 --p-missing 0.05 --p-drop 0.05`.

## Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| Collector không nhận gì | Kiểm tra `TOPIC_NAMESPACE` trong `main.cpp` trùng prefix trong `mosquitto.conf`; xem `docker compose logs mosquitto` |
| Bridge bị ngắt liên tục | `remote_clientid` trùng với người khác → đổi tên khác |
| Không có mẫu độ trễ | ESP32 chưa đồng bộ NTP (bản ghi có `ts_source: "server"`), hoặc lệch đồng hồ > 60 s |
| Độ trễ âm / tăng dần | Đồng hồ máy tính chưa đồng bộ, hoặc Wokwi chạy chậm hơn thời gian thực (firmware tự đồng bộ lại NTP mỗi 10 phút) |
| `Không kết nối được MongoDB` | `docker compose ps`, đợi healthcheck `healthy`, kiểm tra `MONGO_URI` trong `.env` |
