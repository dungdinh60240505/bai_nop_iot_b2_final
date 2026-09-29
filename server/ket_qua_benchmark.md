## So sanh Time-series vs Collection thuong (50000 ban ghi)

| loai | documents | insert_one (ms/doc) | insert_many (doc/s) | storage (KB) | index (KB) | find 1h (ms) | rows 1h | aggregate 6h/phut (ms) |
|---|---|---|---|---|---|---|---|---|
| Time-series | 50000 | 0.728 | 59791 | 4.0 | 4.0 | 10.3 | 721 | 41.6 |
| Thuong | 50000 | 0.383 | 73004 | 4.0 | 404.0 | 5.2 | 721 | 75.9 |

Collection thuong ton dung luong gap 1.0 lan time-series.
