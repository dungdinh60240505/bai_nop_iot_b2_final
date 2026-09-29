# Bài thực hành số 2 – Thu thập, lưu trữ và tiền xử lý dữ liệu IoT

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