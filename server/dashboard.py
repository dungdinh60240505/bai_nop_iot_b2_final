"""Dashboard giam sat IoT (Streamlit) - cung la "App ung dung" cua bai thuc hanh.

    streamlit run dashboard.py

Tab:
  1. Real-time        : gia tri moi nhat, trang thai thiet bi, bieu do du lieu tho (tu lam moi)
  2. Da tien xu ly    : tho vs da xu ly, outlier, rolling mean, delta, du lieu chuan hoa
  3. Do tre           : p50/p95/p99 cua net/db/e2e, histogram, theo thoi gian
  4. Luu tru & chat luong: kich thuoc collection, mat/trung/loi goi, nhat ky tien xu ly
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import preprocess
from common import (DEVICES, LATENCY, PROCESSED, RAW, REJECTED, RUNS, SENSOR_FIELDS,
                    get_db, settings)

LABELS = {"temperature": "Nhiệt độ (°C)", "humidity": "Độ ẩm (%)", "distance_cm": "Khoảng cách (cm)"}
RANGES = {"15 phút": 15, "1 giờ": 60, "6 giờ": 360, "24 giờ": 1440, "7 ngày": 10080}
ONLINE_AFTER_S = 30  # khong nhan du lieu qua 30 s -> coi la offline

st.set_page_config(page_title="IoT Lab 2 – Giám sát cảm biến", page_icon="📡", layout="wide")


@st.cache_resource
def db():
    return get_db()


def local(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True).dt.tz_convert(settings.timezone)


# ------------------------------------------------------------------ truy van
def query_raw(device: str, start: datetime, limit: int = 20000) -> pd.DataFrame:
    rows = list(db()[RAW].find({"meta.device_id": device, "ts": {"$gte": start}},
                               {"_id": 0, "ts": 1, "seq": 1, "device_ts_ms": 1,
                                "alarm": 1, "rssi": 1, "recv_at": 1, **{f: 1 for f in SENSOR_FIELDS}})
                .sort("ts", -1).limit(limit))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for f in SENSOR_FIELDS:
        df[f] = pd.to_numeric(df.get(f), errors="coerce")
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.sort_values("ts")


def query_processed(device: str, start: datetime, window: str) -> pd.DataFrame:
    rows = list(db()[PROCESSED].find({"meta.device_id": device, "meta.window": window, "ts": {"$gte": start}},
                                     {"_id": 0, "meta": 0}).sort("ts", 1))
    df = pd.DataFrame(rows)
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def query_latency(device: str, start: datetime) -> pd.DataFrame:
    rows = list(db()[LATENCY].find({"device_id": device, "recv_at": {"$gte": start}}, {"_id": 0})
                .sort("recv_at", 1))
    df = pd.DataFrame(rows)
    if not df.empty:
        df["recv_at"] = pd.to_datetime(df["recv_at"], utc=True)
    return df


def device_list() -> list[str]:
    ids = [d["_id"] for d in db()[DEVICES].find({}, {"_id": 1}).sort("last_seen", -1)]
    return ids or sorted(db()[RAW].distinct("meta.device_id"))


def coll_stats(name: str) -> dict:
    try:
        s = db().command("collStats", name)
    except Exception:  # noqa: BLE001 - collection chua ton tai
        return {"collection": name}
    return {"collection": name,
            "documents": db()[name].estimated_document_count() if name not in (RAW, PROCESSED)
            else db()[name].count_documents({}),
            "size (KB)": round(s.get("size", 0) / 1024, 1),
            "storageSize (KB)": round(s.get("storageSize", 0) / 1024, 1),
            "indexes": s.get("nindexes", 0),
            "indexSize (KB)": round(s.get("totalIndexSize", 0) / 1024, 1)}


# ------------------------------------------------------------------ giao dien
st.title("📡 Giám sát dữ liệu IoT – Bài thực hành số 2")

with st.sidebar:
    st.header("Tuỳ chọn")
    try:
        devices = device_list()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Không kết nối được MongoDB: {exc}")
        st.stop()
    if not devices:
        st.info("Chưa có dữ liệu. Hãy chạy collector.py và ESP32 (hoặc simulator.py).")
        st.stop()
    device = st.selectbox("Thiết bị", devices)
    range_label = st.radio("Khoảng thời gian", list(RANGES), index=1, horizontal=True)
    refresh = st.slider("Tự làm mới (giây)", 2, 60, 5)
    window = st.selectbox("Cửa sổ dữ liệu đã xử lý", ["1min", "5min", "30s", "15min", "1h"])
    st.caption(f"Múi giờ hiển thị: {settings.timezone}")

start = datetime.now(timezone.utc) - timedelta(minutes=RANGES[range_label])
tab_rt, tab_proc, tab_lat, tab_store = st.tabs(
    ["⚡ Real-time", "🧹 Đã tiền xử lý", "⏱️ Độ trễ", "💾 Lưu trữ & chất lượng"])


@st.fragment(run_every=f"{refresh}s")
def realtime_panel() -> None:
    dev = db()[DEVICES].find_one({"_id": device}) or {}
    df = query_raw(device, start)
    now = datetime.now(timezone.utc)
    last_seen = dev.get("last_seen")
    online = bool(last_seen and (now - last_seen).total_seconds() < ONLINE_AFTER_S)

    cols = st.columns(5)
    cols[0].metric("Trạng thái", "🟢 Online" if online else "🔴 Offline",
                   f"LWT: {dev.get('status', '—')}", delta_color="off")
    if not df.empty:
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        for col, f in zip(cols[1:4], SENSOR_FIELDS):
            val = last[f]
            delta = None if pd.isna(val) or pd.isna(prev[f]) else round(val - prev[f], 2)
            col.metric(LABELS[f], "null" if pd.isna(val) else f"{val:.2f}", delta)
        recent = (df["ts"] >= now - timedelta(minutes=5)).sum()
        cols[4].metric("Gói tin / 5 phút", int(recent), f"seq {int(last['seq'])}", delta_color="off")
    if df.empty:
        st.info("Không có dữ liệu thô trong khoảng thời gian đã chọn.")
        return

    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        subplot_titles=[LABELS[f] for f in SENSOR_FIELDS])
    x = local(df["ts"])
    for i, f in enumerate(SENSOR_FIELDS, start=1):
        fig.add_trace(go.Scatter(x=x, y=df[f], mode="lines+markers", marker={"size": 3},
                                 name=LABELS[f], connectgaps=False), row=i, col=1)
    if "alarm" in df and df["alarm"].fillna(False).astype(bool).any():
        a = df[df["alarm"].fillna(False).astype(bool)]
        fig.add_trace(go.Scatter(x=local(a["ts"]), y=a["distance_cm"], mode="markers", name="Cảnh báo < 15 cm",
                                 marker={"color": "#d62728", "size": 8, "symbol": "x"}), row=3, col=1)
    fig.update_layout(height=620, margin={"l": 10, "r": 10, "t": 40, "b": 10}, showlegend=False)
    st.plotly_chart(fig, width="stretch")

    show = df.tail(10).iloc[::-1].copy()
    show["ts"] = local(show["ts"]).dt.strftime("%H:%M:%S")
    st.caption("10 bản ghi mới nhất (null = cảm biến lỗi / thiếu giá trị)")
    st.dataframe(show[["ts", "seq", *SENSOR_FIELDS, "alarm", "rssi"]], hide_index=True,
                 width="stretch")


def processed_panel() -> None:
    c1, c2, c3 = st.columns([2, 2, 1])
    field = c1.selectbox("Đại lượng", SENSOR_FIELDS, format_func=LABELS.get)
    method = c2.radio("Phương pháp outlier (để hiển thị)", ["iqr", "zscore"], horizontal=True)
    if c3.button("▶ Chạy tiền xử lý ngay", width="stretch"):
        with st.spinner("Đang tiền xử lý..."):
            summary = preprocess.run(db(), start, datetime.now(timezone.utc), window=window, method=method,
                                     device_id=device)
        st.success(f"Xong run {summary['run_id']} trong {summary['duration_ms']} ms")

    raw = query_raw(device, start)
    proc = query_processed(device, start, window)
    if raw.empty:
        st.info("Không có dữ liệu thô.")
        return
    cleaned, _ = preprocess.clean(raw.assign(room=""), method, 11)
    outliers = raw.loc[cleaned.index[cleaned[f"{field}_is_outlier"]]]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=local(raw["ts"]), y=raw[field], mode="markers", name="Dữ liệu thô",
                             marker={"size": 4, "color": "rgba(128,128,128,0.45)"}))
    if not outliers.empty:
        fig.add_trace(go.Scatter(x=local(outliers["ts"]), y=outliers[field], mode="markers",
                                 name=f"Outlier ({len(outliers)})",
                                 marker={"size": 9, "color": "#d62728", "symbol": "x"}))
    if not proc.empty:
        fig.add_trace(go.Scatter(x=local(proc["ts"]), y=proc[field], mode="lines", name=f"Trung bình {window}",
                                 line={"width": 2.5, "color": "#1f77b4"}))
        fig.add_trace(go.Scatter(x=local(proc["ts"]), y=proc[f"{field}_roll"], mode="lines",
                                 name="Rolling mean", line={"width": 2, "dash": "dash", "color": "#ff7f0e"}))
        filled = proc[proc["filled"] == True]  # noqa: E712
        if not filled.empty:
            fig.add_trace(go.Scatter(x=local(filled["ts"]), y=filled[field], mode="markers",
                                     name="Giá trị nội suy", marker={"size": 9, "symbol": "diamond-open",
                                                                    "color": "#2ca02c"}))
    fig.update_layout(height=430, title=f"{LABELS[field]}: thô vs đã xử lý",
                      margin={"l": 10, "r": 10, "t": 50, "b": 10}, legend={"orientation": "h", "y": -0.15})
    st.plotly_chart(fig, width="stretch")

    if proc.empty:
        st.warning("Chưa có dữ liệu đã xử lý cho cửa sổ này – bấm “Chạy tiền xử lý ngay” "
                   "hoặc chạy `python preprocess.py --every 300`.")
        return
    c1, c2 = st.columns(2)
    zfig = go.Figure([go.Scatter(x=local(proc["ts"]), y=proc[f"{f}_z"], mode="lines", name=LABELS[f])
                      for f in SENSOR_FIELDS])
    zfig.update_layout(height=320, title="Dữ liệu chuẩn hoá Z-score", margin={"l": 10, "r": 10, "t": 50, "b": 10})
    c1.plotly_chart(zfig, width="stretch")
    dfig = go.Figure([go.Bar(x=local(proc["ts"]), y=proc[f"{field}_delta"], name="Delta")])
    dfig.update_layout(height=320, title=f"Delta {LABELS[field]} giữa 2 cửa sổ",
                       margin={"l": 10, "r": 10, "t": 50, "b": 10})
    c2.plotly_chart(dfig, width="stretch")

    show = proc.copy()
    show["ts"] = local(show["ts"]).dt.strftime("%Y-%m-%d %H:%M")
    cols = ["ts", *SENSOR_FIELDS, "n_samples", "completeness", "filled",
            f"{field}_roll", f"{field}_delta", f"{field}_z", f"{field}_norm"]
    st.dataframe(show[cols].iloc[::-1], hide_index=True, width="stretch")


def latency_panel() -> None:
    lat = query_latency(device, start)
    if lat.empty:
        st.info("Chưa có mẫu độ trễ (cần ESP32 đồng bộ NTP để gửi trường ts).")
        return
    q = lat[["net_ms", "db_ms", "e2e_ms"]].describe(percentiles=[0.5, 0.95, 0.99]).T
    q = q.rename(columns={"50%": "p50", "95%": "p95", "99%": "p99"})[["count", "mean", "min", "p50", "p95", "p99", "max"]]
    cols = st.columns(4)
    cols[0].metric("E2E p50", f"{q.loc['e2e_ms', 'p50']:.0f} ms")
    cols[1].metric("E2E p95", f"{q.loc['e2e_ms', 'p95']:.0f} ms")
    cols[2].metric("E2E p99", f"{q.loc['e2e_ms', 'p99']:.0f} ms")
    cols[3].metric("Ghi DB p50", f"{q.loc['db_ms', 'p50']:.1f} ms")
    st.caption("net = ESP32 → broker công khai → bridge → Mosquitto → collector; "
               "db = collector nhận → ghi xong MongoDB; e2e = net + db.")
    st.dataframe(q.round(1), width="stretch")

    c1, c2 = st.columns(2)
    h = go.Figure([go.Histogram(x=lat["e2e_ms"], nbinsx=40, name="E2E")])
    h.update_layout(height=340, title="Phân bố độ trễ E2E (ms)", margin={"l": 10, "r": 10, "t": 50, "b": 10})
    c1.plotly_chart(h, width="stretch")
    t = go.Figure([go.Scatter(x=local(lat["recv_at"]), y=lat[c], mode="lines", name=c)
                   for c in ["e2e_ms", "net_ms", "db_ms"]])
    t.update_layout(height=340, title="Độ trễ theo thời gian (ms)", margin={"l": 10, "r": 10, "t": 50, "b": 10})
    c2.plotly_chart(t, width="stretch")


def storage_panel() -> None:
    st.subheader("Dung lượng lưu trữ")
    stats = pd.DataFrame([coll_stats(n) for n in [RAW, PROCESSED, LATENCY, REJECTED, RUNS, DEVICES]])
    st.dataframe(stats, hide_index=True, width="stretch")

    st.subheader("Chất lượng đường truyền theo thiết bị")
    devs = pd.DataFrame(list(db()[DEVICES].find({})))
    if not devs.empty:
        for c in ["received", "stored", "duplicates", "lost", "rejected", "reboots"]:
            devs[c] = devs.get(c, 0)
            devs[c] = devs[c].fillna(0).astype(int)
        expected = devs["stored"] + devs["lost"] + devs["rejected"]
        devs["tỉ lệ mất (%)"] = (100 * devs["lost"] / expected.where(expected > 0)).round(2)
        devs["last_seen"] = local(devs["last_seen"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        st.dataframe(devs[["_id", "room", "status", "last_seen", "received", "stored", "duplicates",
                           "lost", "rejected", "reboots", "tỉ lệ mất (%)"]].rename(columns={"_id": "device_id"}),
                     hide_index=True, width="stretch")

    st.subheader("Nhật ký tiền xử lý gần đây")
    runs = list(db()[RUNS].find({}, {"_id": 0}).sort("run_at", -1).limit(10))
    rows = []
    for r in runs:
        for dev, s in r.get("devices", {}).items():
            rows.append({"run_at": r["run_at"], "run_id": r["run_id"], "device": dev,
                         "method": r["params"]["method"], "window": r["params"]["window"],
                         "raw": s["rows_raw"], "trùng": s["duplicates_removed"], "mất (seq)": s["seq_gaps_lost"],
                         "null": sum(s["null_values"].values()), "ngoài dải": sum(s["out_of_spec"].values()),
                         "outlier": sum(s["outliers"].values()), "nội suy": s["values_interpolated"],
                         "cửa sổ ra": s["rows_out"], "thời gian (ms)": r.get("duration_ms")})
    if rows:
        runs_df = pd.DataFrame(rows)
        runs_df["run_at"] = local(runs_df["run_at"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        st.dataframe(runs_df, hide_index=True, width="stretch")
    else:
        st.info("Chưa chạy tiền xử lý lần nào.")

    st.subheader("Gói tin bị từ chối gần đây")
    rej = pd.DataFrame(list(db()[REJECTED].find({}, {"_id": 0}).sort("recv_at", -1).limit(20)))
    if rej.empty:
        st.info("Không có gói tin lỗi.")
    else:
        rej["recv_at"] = local(rej["recv_at"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        rej["errors"] = rej["errors"].apply("; ".join)
        st.dataframe(rej, hide_index=True, width="stretch")


with tab_rt:
    realtime_panel()
with tab_proc:
    processed_panel()
with tab_lat:
    latency_panel()
with tab_store:
    storage_panel()
