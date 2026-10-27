# Bài thực hành số 3 – Trích chọn đặc trưng và học từ dữ liệu IoT

Dự báo **nhiệt độ trung bình của 60 s tiếp theo** từ cửa sổ 60 s quá khứ của ba kênh
`temperature`, `humidity`, `distance_cm` (ESP32 + DHT22 + HC-SR04 của Bài 1, lưu MongoDB ở Bài 2).

## Tệp trong thư mục

| Tệp | Nội dung |
|---|---|
| `Bai3_Sinh_vien.ipynb` | Notebook ô 1–10 (TODO 1–5 đã hoàn thành, có đầu ra), phiếu kết quả và trả lời câu hỏi bắt buộc |
| `du_lieu_mau_B2.csv` | Bộ mẫu 720 dòng / 60 phút / 5 s **tạo bằng Python** (`tao_du_lieu_mau.py`, seed 3) — không phải dữ liệu ThingsBoard/MongoDB |
| `du_lieu_B2.csv` | (nhóm tự xuất) dữ liệu lịch sử thật từ MongoDB, xem bước 1 bên dưới |
| `ket_qua_B3/` | `tom_tat.json`, `dac_trung_train.csv`, `dac_trung_test.csv`, `xep_hang_dac_trung.csv`, `bang_loi.csv`, `du_bao_test.csv`, 3 hình, `model_ridge6.joblib` |
| `Bao_cao_B3.docx` | Báo cáo Word 4 trang, sinh tự động từ `ket_qua_B3` bằng `tao_bao_cao.js` |

Liên quan ở thư mục `server/`: `export_b2_csv.py` (MongoDB → CSV), `forecast_b3.py` (dự báo trực tuyến),
tab **🔮 Dự báo (Bài 3)** trong `dashboard.py`.

> Kết quả hiện có trong notebook/báo cáo được chạy trên **bộ mẫu Python**. Sau khi xuất `du_lieu_B2.csv`
> của nhóm, chạy lại các bước 2–3 để thay toàn bộ số liệu.

## Quy trình

```bash
# 0. Hạ tầng Bài 2 đang chạy (docker compose up -d, collector.py) và Wokwi gửi dữ liệu >= 20-60 phút.
#    Trong Wokwi đổi nhiệt độ 22-35 °C, độ ẩm 40-80 %RH, khoảng cách 20-200 cm mỗi 1-2 phút; ghi lại thao tác.

# 1. Xuất dữ liệu lịch sử (còn đơn vị vật lý, chưa chuẩn hoá/làm mượt)
cd server
python export_b2_csv.py --device esp32-room1 --minutes 60          # -> ../bai3/du_lieu_B2.csv (+ .meta.json)

# 2. Notebook: mở Bai3_Sinh_vien.ipynb (Colab: File → Upload notebook, tải CSV vào thanh Files)
#    Ô 1: DATA_PATH = "du_lieu_B2.csv", sửa DATA_NOTE; Run all.
#    Hoặc chạy cục bộ: pip install jupyter matplotlib && jupyter nbconvert --to notebook --execute --inplace Bai3_Sinh_vien.ipynb

# 3. Cập nhật báo cáo Word từ kết quả mới (cần Node.js + `npm install docx`)
cd ../bai3 && node tao_bao_cao.js

# 4. (Mở rộng) Dự báo trực tuyến lên dashboard
cd ../server
python forecast_b3.py --device esp32-room1      # mỗi 30 s ghi forecast_b3.temperature_forecast_next60s
streamlit run dashboard.py                      # tab "🔮 Dự báo (Bài 3)"
```

`model_ridge6.joblib` được lưu bằng scikit-learn của môi trường chạy notebook; nếu `forecast_b3.py` chạy
trong venv có phiên bản sklearn khác, chạy lại notebook trong chính venv đó để tạo lại model.

## Tóm tắt phương pháp

- **Kiểm tra (ô 2–3):** đủ cột, timestamp ms/ISO, một thiết bị, trùng `(device_id, timestamp)`, ngoài miền
  (−40…80 °C, 0…100 %RH, 2…400 cm) → thiếu; ngắt đoạn khi `sequence`/`uptime_s` giảm hoặc khoảng cách > 20 s;
  resample từng đoạn trên lưới 5 s (không điền 0).
- **Cửa sổ (ô 4–5):** N = 12 mẫu, bước 6 mẫu (30 s); chia 70/30 theo thời gian **trước** khi tạo cặp;
  `assert train.target_end.max() <= cut`, `assert test.input_start.min() >= cut`. Bộ mẫu: 81/33 cặp.
- **Đặc trưng (ô 6–7):** mean, std (ddof=0), min, max, range, delta, slope × 3 kênh = 21 cột; X khuyết > 1 mẫu/kênh
  → loại; y cần đủ 12 nhiệt độ quan sát gốc, không nội suy nhãn.
- **Học (ô 8):** `StandardScaler → SelectKBest(f_regression, k=6) → Ridge(alpha=1)` chỉ fit trên train.
- **Đánh giá (ô 9):** MAE/RMSE của Mean_train, Persistence, Ridge_21, Ridge_6.

| Bộ mẫu Python | MAE (°C) | RMSE (°C) |
|---|---|---|
| Mean_train | 2,162 | 2,520 |
| Persistence | 1,467 | 1,681 |
| Ridge_21 | 0,889 | 1,133 |
| Ridge_6 | 0,782 | 0,977 |

Không đưa token MQTT, API key, JWT hay `MONGO_URI` có mật khẩu vào notebook/CSV nộp.
