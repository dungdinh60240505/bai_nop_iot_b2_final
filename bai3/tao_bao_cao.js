// Sinh bao cao Word Bai 3 tu ket qua notebook (ket_qua_B3/).
//   cd bai3 && node tao_bao_cao.js            -> Bao_cao_B3.docx
// Chay lai sau moi lan chay notebook (vd. khi doi sang du_lieu_B2.csv cua nhom):
// moi so lieu, bang va hinh deu doc tu ket_qua_B3, khong go tay.
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun, HeadingLevel,
  AlignmentType, WidthType, ShadingType, BorderStyle, LevelFormat, Footer, PageNumber,
} = require("docx");

const OUT = path.join(__dirname, "ket_qua_B3");
const S = JSON.parse(fs.readFileSync(path.join(OUT, "tom_tat.json"), "utf8"));
const ranking = fs.readFileSync(path.join(OUT, "xep_hang_dac_trung.csv"), "utf8").trim().split("\n").slice(1)
  .map((l) => { const [rank, feature, F, p, sel] = l.split(","); return { rank, feature, F: +F, p: +p, sel: sel === "True" }; });

const vn = (x, d = 3) => Number(x).toFixed(d).replace(".", ",");
const M = S.metrics;
const mae = (k) => M[k]["MAE (°C)"], rmse = (k) => M[k]["RMSE (°C)"];
const isSample = String(S.data_path).includes("mau");

const FONT = "Times New Roman";
const p = (text, opts = {}) => new Paragraph({
  spacing: { after: 80, line: 276 }, alignment: opts.align ?? AlignmentType.JUSTIFIED,
  children: (Array.isArray(text) ? text : [text]).map((t) => (typeof t === "string" ? new TextRun(t) : t)),
});
const b = (t) => new TextRun({ text: t, bold: true });
const it = (t) => new TextRun({ text: t, italics: true });
const bullet = (children) => new Paragraph({
  numbering: { reference: "dots", level: 0 }, spacing: { after: 40 }, alignment: AlignmentType.JUSTIFIED,
  children: (Array.isArray(children) ? children : [children]).map((t) => (typeof t === "string" ? new TextRun(t) : t)),
});
const h1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 160, after: 80 }, children: [new TextRun(t)] });
const caption = (t) => new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 },
  children: [new TextRun({ text: t, italics: true, size: 20 })] });

const border = { style: BorderStyle.SINGLE, size: 4, color: "999999" };
const borders = { top: border, bottom: border, left: border, right: border };
function table(header, rows, widths) {
  const total = widths.reduce((a, c) => a + c, 0);
  const cell = (t, w, head) => new TableCell({
    borders, width: { size: w, type: WidthType.DXA },
    shading: head ? { fill: "DCE6F0", type: ShadingType.CLEAR, color: "auto" } : undefined,
    margins: { top: 40, bottom: 40, left: 80, right: 80 },
    children: [new Paragraph({ children: [new TextRun({ text: String(t), bold: head, size: 20 })] })],
  });
  return new Table({
    width: { size: total, type: WidthType.DXA }, columnWidths: widths,
    rows: [new TableRow({ tableHeader: true, children: header.map((t, i) => cell(t, widths[i], true)) }),
      ...rows.map((r) => new TableRow({ children: r.map((t, i) => cell(t, widths[i], false)) }))],
  });
}
const img = (file, w, h) => new Paragraph({ alignment: AlignmentType.CENTER, children: [
  new ImageRun({ type: "png", data: fs.readFileSync(path.join(OUT, file)), transformation: { width: w, height: h } })] });

// --- nhan xet tu dong theo so lieu
const r6 = mae("Ridge_6"), rp = mae("Persistence"), r21 = mae("Ridge_21");
const beat = r6 < rp;
const pct = Math.abs(rp - r6) / rp * 100;
const weak = ranking.filter((r) => r.sel && r.p > 0.05).map((r) => r.feature);
const strong = ranking.filter((r) => r.sel && r.p <= 0.05).map((r) => r.feature);

function droppedText() {
  const d = (o) => `${o["X_khuyet_qua_10%"]} cặp do X khuyết > 10%, ${o["y_thieu_nhiet_do_goc"]} cặp do y thiếu nhiệt độ gốc`;
  const zero = (o) => Object.values(o).every((v) => v === 0);
  if (zero(S.dropped.train) && zero(S.dropped.test)) return "không cặp nào bị loại vì X khuyết > 10% hay y thiếu nhiệt độ gốc";
  return `loại ở train: ${d(S.dropped.train)}; ở test: ${d(S.dropped.test)}`;
}

const children = [
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 }, children: [
    new TextRun({ text: "BÁO CÁO BÀI THỰC HÀNH SỐ 3", bold: true, size: 30 })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 }, children: [
    new TextRun({ text: "Trích chọn đặc trưng và học từ dữ liệu IoT", bold: true, size: 26 })] }),
  p("Họ và tên: ………………………………  Mã sinh viên: ……………  Lớp/nhóm: ……………  Ngày: ……………", { align: AlignmentType.LEFT }),

  h1("1. Phần kế thừa từ Bài 1 và Bài 2"),
  table(["Thành phần", "Đã thực hiện", "Dùng trong Bài 3"], [
    ["Nút cảm biến", "ESP32 DevKit V1 trên Wokwi; DHT22 DATA = GPIO15", "temperature (°C), humidity (%RH)"],
    ["Khoảng cách", "HC-SR04 TRIG = GPIO5, ECHO = GPIO18; LED = GPIO2 (cảnh báo GPIO4)", "distance_cm (cm), kênh thứ ba"],
    ["Truyền dữ liệu", "Wokwi-GUEST → MQTT broker.emqx.io → bridge → Mosquitto local; chu kỳ 5 s", "Giữ nguyên, không sửa firmware"],
    ["Telemetry", "temperature, humidity, distance_cm, seq, ts, uptime_s, rssi, alarm", "3 kênh tạo X; seq/uptime_s chỉ để ngắt đoạn"],
    ["Lưu trữ", "collector.py → MongoDB time-series sensor_raw; preprocess.py", "export_b2_csv.py → du_lieu_B2.csv (đơn vị vật lý)"],
    ["Hiển thị", "Dashboard Streamlit (dashboard.py)", "Tab mới “Dự báo (Bài 3)”"],
  ], [1700, 4300, 3026]),
  p(""),
  p(["Hệ thống không dùng ThingsBoard: lịch sử được đọc trực tiếp từ MongoDB của Bài 2. Khoá ", it("seq"),
    " của firmware được đổi tên thành ", it("sequence"), " trong CSV bàn giao theo schema của đề."]),

  h1("2. Nguồn và chất lượng dữ liệu"),
  p([b("Nguồn: "), `${S.data_path} — ${S.data_note}`]),
  p(`Thiết bị ${S.device_id}, khoảng ${S.time_start.slice(0, 19).replace("T", " ")} → ${S.time_end.slice(11, 19)}. ` +
    `Số dòng trước/sau kiểm tra: ${S.rows_in}/${S.rows_after_check}; trùng (device_id, timestamp): ${S.duplicates}. ` +
    `Giá trị ngoài miền (−40…80 °C, 0…100 %RH, 2…400 cm) được đánh dấu thiếu: ` +
    Object.entries(S.out_of_range_set_nan).map(([k, v]) => `${k} ${v}`).join(", ") + ". " +
    `Thiếu sau kiểm tra: ` + Object.entries(S.missing_after_check).map(([k, v]) => `${k} ${v}`).join(", ") +
    `. Số đoạn liên tục: ${S.segments} (ngắt khi sequence/uptime_s giảm hoặc khoảng cách > 20 s); mỗi đoạn resample trên lưới 5 s, không điền 0.`),
  ...(isSample ? [p([b("Lưu ý trung thực: "), "các số liệu trong báo cáo này được tính trên bộ mẫu tạo bằng Python, không phải dữ liệu thu từ Wokwi/MongoDB của nhóm. Khi xuất được du_lieu_B2.csv, nhóm chạy lại notebook và lệnh node tao_bao_cao.js để cập nhật toàn bộ số liệu."])] : []),

  h1("3. Cửa sổ và chia tập"),
  p(`Cửa sổ X = [t, t+60 s) gồm N = ${S.N} mẫu (0, 5, …, 55 s); y = trung bình 12 nhiệt độ quan sát gốc của [t+60, t+120 s). ` +
    `Bước trượt 30 s (${S.step} mẫu). Chia theo thời gian trước khi tạo cặp: 70% đầu cho train, 30% cuối cho test, mốc chia ${S.cut.slice(11, 19)}. ` +
    `Cặp được tạo riêng trong từng tập và từng đoạn, nên train["target_end"].max() ≤ cut và test["input_start"].min() ≥ cut (đã assert). ` +
    `Số cặp trước lọc train/test: ${S.pairs_before_filter.train}/${S.pairs_before_filter.test}; hợp lệ: ${S.pairs_valid.train}/${S.pairs_valid.test} (` +
    droppedText() + ")."),
  img("hinh1_ba_chuoi_moc_chia.png", 560, 357),
  caption("Hình 1. Ba chuỗi cảm biến và mốc chia thời gian train/test"),
  img("hinh2_cua_so_X_y.png", 560, 179),
  caption("Hình 2. Cửa sổ dùng trích X (quá khứ) và cửa sổ dùng tạo y (phút tương lai)"),

  h1("4. Trích xuất và lựa chọn đặc trưng"),
  p("Mỗi kênh có 7 đặc trưng: mean, std (ddof = 0), min, max, range (np.ptp), delta = x[−1] − x[0] (đơn vị kênh) và slope = hệ số góc np.polyfit theo giây (đơn vị kênh/s), tổng 21 cột. Kiểm tra tay với x = [1, 2, 3, 4], t = [0, 5, 10, 15] s: mean 2,5; std ≈ 1,1180; range 3; delta 3; slope 0,2/s — khớp assert trong notebook. Khuyết tối đa 1 mẫu/kênh trong X được nội suy tuyến tính trong chính cửa sổ (mép lấy giá trị gần nhất); nhãn y không nội suy."),
  p(`Pipeline StandardScaler → SelectKBest(f_regression, k = ${S.k}) → Ridge(alpha = ${S.alpha}) chỉ fit trên train. Sáu cột được chọn: ${S.selected_features.join(", ")}.`),
  table(["Hạng", "Đặc trưng", "Điểm F", "p-value", "Chọn"],
    ranking.slice(0, 9).map((r) => [r.rank, r.feature, vn(r.F, 2), r.p < 1e-4 ? "< 0,0001" : vn(r.p, 4), r.sel ? "✓" : ""]),
    [900, 3400, 1600, 1800, 1326]),
  p(""),
  p(`Các cột có ý nghĩa thống kê (p ≤ 0,05): ${strong.join(", ") || "không có"} — mô tả mức nhiệt của phút vừa qua, thông tin mạnh nhất cho mức phút sau vì nhiệt độ thay đổi chậm; chúng gần trùng thông tin do bộ lọc đơn biến không loại trùng lặp. ` +
    (weak.length ? `Các cột ${weak.join(", ")} có p > 0,05, chỉ được giữ vì buộc k = 6; delta/slope mô tả xu hướng, đơn biến yếu nhưng có thể có ích khi kết hợp với mức nhiệt trong Ridge. ` : "") +
    "Điểm F không phải bằng chứng nhân quả và không chứng minh sáu cột là tối ưu."),

  h1("5. Kết quả đánh giá"),
  table(["Cấu hình", "Cách dự báo", "MAE (°C)", "RMSE (°C)"], [
    ["Mean_train", "Trung bình y_train", vn(mae("Mean_train")), vn(rmse("Mean_train"))],
    ["Persistence", "temperature_mean cửa sổ hiện tại", vn(mae("Persistence")), vn(rmse("Persistence"))],
    ["Ridge_21", "Ridge trên 21 đặc trưng", vn(mae("Ridge_21")), vn(rmse("Ridge_21"))],
    ["Ridge_6", "SelectKBest k = 6 + Ridge", vn(mae("Ridge_6")), vn(rmse("Ridge_6"))],
  ], [1700, 3726, 1800, 1800]),
  p(""),
  img("hinh3_du_bao_test.png", 580, 211),
  caption("Hình 3. Dự báo nhiệt độ trung bình phút tiếp theo trên phần thời gian test"),
  bullet([b(beat ? "Ridge_6 vượt Persistence: " : "Ridge_6 KHÔNG vượt Persistence: "),
    `MAE ${vn(r6)} so với ${vn(rp)} °C (${beat ? "giảm" : "tăng"} ${vn(pct, 1)}%), RMSE ${vn(rmse("Ridge_6"))} so với ${vn(rmse("Persistence"))} °C.` +
    (beat ? " Persistence luôn trễ khoảng một phút ở các đoạn tăng/giảm; mô hình dùng xu hướng trong cửa sổ để bám sát hơn." : " Ghi nhận đúng kết quả, không chỉnh k/alpha trên test.")]),
  bullet([b("Giảm số cột: "), `Ridge_6 so với Ridge_21: MAE ${vn(r6)} so với ${vn(r21)} °C — ` +
    (r6 <= r21 ? "giảm cột không làm lỗi tăng; các kênh ít liên quan chủ yếu thêm phương sai khi train chỉ có " + S.pairs_valid.train + " cặp."
      : "giảm cột làm lỗi tăng, nghĩa là bộ lọc đơn biến đã bỏ thông tin hữu ích; vẫn giữ k = 6 như đã đặt trước.")]),
  bullet([b("Mốc đối chứng: "), `Mean_train (MAE ${vn(mae("Mean_train"))} °C) kém nhất vì không dùng biến thiên cảm biến.`]),
  bullet([b("Suy luận một cửa sổ: "), `từ cửa sổ ${S.example_forecast.input_window[0].slice(11, 19)}–${S.example_forecast.input_window[1].slice(11, 19)}, ` +
    `temperature_forecast_next60s = ${vn(S.example_forecast.temperature_forecast_next60s, 2)} °C cho phút ${S.example_forecast.forecast_window[0].slice(11, 19)}–${S.example_forecast.forecast_window[1].slice(11, 19)}; ` +
    `thời gian tính ≈ ${vn(S.compute_ms, 1)} ms. Nhãn thật chỉ có sau khi phút đó kết thúc.`]),

  h1("6. Tích hợp vào hệ thống và giới hạn"),
  p("server/forecast_b3.py đọc 60 s vừa hoàn tất từ sensor_raw mỗi 30 s, kiểm tra như notebook (miền hợp lệ, lưới 5 s, khuyết ≤ 1 mẫu/kênh, không vắt qua reset hay khoảng trống > 20 s), dự báo bằng model đã lưu và ghi collection forecast_b3 với khoá temperature_forecast_next60s cùng thời điểm phát. Tab “Dự báo (Bài 3)” trên dashboard hiển thị giá trị này tách biệt với temperature hiện tại và chấm MAE/RMSE khi phút dự báo kết thúc. Kết quả không được gửi dưới tên telemetry gốc."),
  p([b("Giới hạn: "), "dữ liệu là mô phỏng (bộ mẫu Python và/hoặc Wokwi), chỉ có một phép chia thời gian và các cửa sổ test chồng lấn 50% nên không độc lập. Kết quả chưa đủ để khẳng định hiệu năng trên cảm biến thật, thiết bị khác hay cách thay đổi môi trường khác; với DHT22 trên Wokwi (giá trị nhảy tức thời theo thanh trượt) học máy có thể không vượt persistence."]),
  p([b("Bảo mật: "), "notebook, CSV và báo cáo không chứa token MQTT, API key, JWT hay MONGO_URI có mật khẩu."]),
];

const doc = new Document({
  styles: {
    default: { document: { run: { font: FONT, size: 24 } } },
    paragraphStyles: [{ id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { size: 26, bold: true, font: FONT, color: "1F3864" }, paragraph: { outlineLevel: 0 } }],
  },
  numbering: { config: [{ reference: "dots", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•",
    alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1134, bottom: 1134, left: 1440, right: 1134 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ children: [PageNumber.CURRENT], size: 18 })] })] }) },
    children,
  }],
});
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(path.join(__dirname, "Bao_cao_B3.docx"), buf);
  console.log("Da ghi Bao_cao_B3.docx");
});
