# Ket qua danh gia he thong

Khoang thoi gian: 10 phut, den 2026-09-29 23:11:29 (Asia/Ho_Chi_Minh)

## 1. Do tre end-to-end

_(chua co mau)_

## 2. Chat luong du lieu (luy ke tu khi chay collector)

| device | received | stored | duplicates | lost | rejected | reboots | loss_% | dup_% |
|---|---|---|---|---|---|---|---|---|
| esp32-room1 | 23 | 23 | 0 | 0 | 0 | 0 | 0.0 | 0.0 |

Trong khoang danh gia: 0 ban ghi tho, 0 ban ghi co gia tri null, 0 ban ghi phai dung thoi gian server (ESP32 chua dong bo NTP), 0 goi bi tu choi.

### Lan tien xu ly gan nhat (c8845ab99687, method=iqr, window=1min, 29.1 ms)

| device | raw | trung | mat_seq | null_temperature | null_humidity | null_distance_cm | outlier_temperature | outlier_humidity | outlier_distance_cm | cua_so | noi_suy | bo | ra |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| esp32-room1 | 23 | 0 | 0 | 0 | 0 | 0 | 6 | 4 | 0 | 3 | 0 | 1 | 2 |

## 3. Dung luong luu tru

| collection | documents | size_KB | storage_KB | index_KB | bytes/doc (luu tru) |
|---|---|---|---|---|---|
| sensor_raw | 23 | 1.3 | 32.0 | 64.0 | 1424.7 |
| sensor_processed | 2 | 3.2 | 36.0 | 72.0 | 18432.0 |
| latency_log | 6 | 0.8 | 32.0 | 96.0 | 5461.3 |
| rejected_messages | 0 | 0.0 | 4.0 | 8.0 |  |
| preprocess_runs | 242 | 191.0 | 88.0 | 72.0 | 372.4 |
| device_status | 1 | 0.2 | 32.0 | 20.0 | 32768.0 |
