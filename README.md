# Stock AI System

Hệ thống phân tích cổ phiếu: quét kỹ thuật đa mã + tổng hợp tin tức từ nhiều
nguồn, tự động phân loại **tích cực/tiêu cực/trung lập**, kèm khuyến nghị tổng
hợp. Lấy cảm hứng từ ANNA-RYO STOCK trong ảnh bạn gửi.

**Đã test chạy được thật (screener, phân tích mã, dashboard, lưu lịch sử DB).**

## 1. Cài đặt

```bash
cd stock_ai_system
python3 -m venv venv && source venv/bin/activate   # khuyến nghị dùng venv
pip install -r requirements.txt
cp .env.example .env
```

## 2. Chạy ngay (dữ liệu MOCK — không cần internet/API key)

```bash
python run.py
```

Mở trình duyệt: **http://localhost:8000**

Giao diện được tổ chức thành nhiều **tiện ích riêng biệt** ở thanh điều hướng bên trái, bấm vào mục nào thì mở ra đúng mục đó (giống ANNA-RYO STOCK):

- **📊 Tổng quan thị trường** — số mã tăng/giảm, tín hiệu mua/bán mạnh, top tăng/giảm, phân bổ sentiment tin tức toàn danh mục.
- **🔍 Bộ lọc cổ phiếu (Screener)** — bảng quét nhiều mã cùng lúc (giá, %thay đổi, khối lượng, xu hướng kỹ thuật, sentiment), sắp xếp theo %thay đổi hoặc sentiment, bấm vào 1 dòng để xem chi tiết.
- **📈 Phân tích chi tiết** — biểu đồ giá kèm SMA20/SMA50/EMA20, biểu đồ RSI và MACD, chi tiết từng tín hiệu kỹ thuật, **kế hoạch đầu tư tham khảo tự động** (vùng vào lệnh / mục tiêu chốt lời / cắt lỗ / tỷ lệ R:R), khuyến nghị tổng hợp và danh sách tin tức kèm nguồn + sentiment.
- **✉️ Tin tức & Sentiment** — dòng tin tổng hợp toàn danh mục, lọc theo nguồn/sentiment, thống kê % tích cực-tiêu cực-trung lập.
- **↺ Lịch sử tín hiệu** — xem lại các lần phân tích đã lưu vào SQLite cho từng mã.
- **☆ Danh mục theo dõi** — tự thêm/xoá mã, lưu trực tiếp vào DB, dùng chung cho toàn bộ hệ thống.
- **⚙️ Cài đặt & nguồn dữ liệu** — xem nhanh provider đang dùng (mock/thật) và hướng dẫn nâng cấp.

Mặc định dùng `MockPriceProvider` / `MockNewsProvider` — sinh dữ liệu giả có
quy luật (không phải số ngẫu nhiên vô nghĩa) để bạn thấy toàn bộ pipeline chạy
đúng logic mà không cần đăng ký API nào cả.

## 3. Cắm dữ liệu THẬT (khi triển khai production)

Hệ thống được thiết kế theo interface (`BasePriceProvider`, `BaseNewsProvider`)
nên đổi nguồn dữ liệu không phải sửa code chỗ khác:

### a. Giá real-time
Sửa trong `.env`:
```
PRICE_PROVIDER=vnstock
PRICE_API_BASE_URL=http://localhost:5000   # service adapter bạn tự deploy
```
Bạn cần deploy 1 service nhỏ bọc quanh SDK thật (vnstock-js / SSI FastConnect /
VPS / Entrade) và expose 2 endpoint: `GET /ohlcv?ticker=&days=` và
`GET /quote?ticker=`. Class `VnstockLikeProvider` trong
`app/data/price_provider.py` sẽ tự gọi sang đó.

*Lý do tách adapter riêng*: các SDK chứng khoán VN thường là Node.js/độc
quyền, không thuần Python — tách thành 1 service REST giúp hệ thống này không
bị khoá cứng vào 1 SDK.

### b. Tin tức thật (CafeF)
```
NEWS_PROVIDER=cafef
```
`CafeFNewsProvider` trong `app/data/news_provider.py` đã viết sẵn scraper cơ
bản bằng `requests` + `BeautifulSoup`. **Lưu ý**: cấu trúc HTML của CafeF có
thể đổi — nếu selector lỗi, cập nhật lại, hoặc chuyển sang dùng Firecrawl API
(ổn định hơn, tự xử lý trang JS-render). Muốn thêm Vietstock/FireAnt: viết
class mới kế thừa `BaseNewsProvider`, tương tự `CafeFNewsProvider`.

### c. Sentiment chính xác hơn
Hiện tại dùng **lexicon tiếng Việt** (`app/analysis/sentiment.py`) — nhanh,
chạy offline, minh bạch, không cần model. Khi cần độ chính xác cao hơn có 2
hướng nâng cấp (đã có sẵn điểm cắm `classify_with_llm()`):
1. Gọi LLM (vd Claude API) để phân loại — chính xác hơn nhưng tốn phí/latency.
2. Gán nhãn tay 1000–2000 tiêu đề rồi fine-tune PhoBERT/DistilBERT đa ngôn ngữ.

### d. Cảnh báo Telegram
```
TELEGRAM_BOT_TOKEN=xxxx
TELEGRAM_CHAT_ID=xxxx
```
Chạy `python scripts/pipeline.py FPT HPG TCB` — script này tự gửi cảnh báo
Telegram khi phát hiện tín hiệu MUA MẠNH/BÁN MẠNH. Có thể đặt cron chạy mỗi
15 phút (xem comment đầu file `scripts/pipeline.py`).

## 4. Cấu trúc thư mục

```
stock_ai_system/
├── run.py                     # Chạy web server
├── scripts/pipeline.py        # Chạy độc lập (cron) — fetch + phân tích + alert
├── app/
│   ├── config.py               # Đọc biến môi trường
│   ├── db.py                   # SQLite — lưu lịch sử tin tức & tín hiệu
│   ├── data/
│   │   ├── price_provider.py   # Nguồn giá: Mock + interface cắm real API
│   │   └── news_provider.py    # Nguồn tin: Mock + CafeF scraper thật
│   ├── analysis/
│   │   ├── indicators.py       # SMA/EMA/RSI/MACD + logic phát hiện tín hiệu
│   │   ├── sentiment.py        # Phân loại tích cực/tiêu cực (lexicon VN)
│   │   └── screener.py         # Gộp kỹ thuật + tin tức -> khuyến nghị
│   └── api/routes.py           # REST API (Flask)
└── frontend/index.html         # Dashboard (thuần HTML/JS, không cần build)
```

## 5. API Endpoints

| Endpoint | Mô tả |
|---|---|
| `GET /api/screener?tickers=FPT,HPG,VCB` | Bảng tổng hợp nhiều mã (mặc định = danh mục theo dõi) |
| `GET /api/market/overview?tickers=...` | Tổng quan thị trường: tăng/giảm, top mover, phân bổ sentiment |
| `GET /api/analyze/<ticker>` | Phân tích đầy đủ 1 mã: kỹ thuật + tin tức + sentiment + OHLCV cho biểu đồ + kế hoạch đầu tư tham khảo |
| `GET /api/news/<ticker>` | Tin tức đã phân loại sentiment, chưa lưu DB |
| `GET /api/news-feed?tickers=...&limit_per_ticker=6` | Dòng tin tổng hợp nhiều mã, sắp theo thời gian |
| `GET /api/quote/<ticker>` | Giá hiện tại |
| `GET /api/history/signals/<ticker>` | Lịch sử tín hiệu đã lưu |
| `GET /api/history/news/<ticker>` | Lịch sử tin tức đã lưu |
| `GET/POST /api/watchlist`, `DELETE /api/watchlist/<ticker>` | Quản lý danh mục theo dõi (lưu SQLite) |
| `GET /api/config` | Thông tin cấu hình hiện tại (provider giá/tin tức, Telegram) |

**Lưu ý về "kế hoạch đầu tư tham khảo" (`investment_plan`)**: đây là logic rule-based minh bạch dựa trên độ biến động giá + tín hiệu kỹ thuật + sentiment, **không phải khuyến nghị đầu tư chính thức** — xem `app/analysis/screener.py::_investment_plan()` để tuỳ chỉnh công thức theo khẩu vị rủi ro của bạn.

## 6. Ghi chú quan trọng

- Trong môi trường sandbox tạo project này **không có internet**, nên phần
  scraper CafeF/API thật CHƯA được test bằng dữ liệu thật — bạn cần chạy trên
  máy/server có mạng và tự kiểm tra selector HTML còn đúng không.
- Toàn bộ pipeline **logic** (chỉ báo kỹ thuật, sentiment, screener, DB, API,
  dashboard) đã chạy thử thành công với dữ liệu mock, đảm bảo kiến trúc đúng.
- Muốn mở rộng thêm: Backtest engine dùng lại bảng `signals` trong SQLite để
  tính Win Rate/Drawdown; Multi-chart dùng thư viện `lightweight-charts` ở
  frontend; AI Copilot Chat thì dựng RAG bằng cách embed dữ liệu
  `news` + `signals` vào 1 vector DB (Chroma) rồi query khi user hỏi.


## 7. Bản fix lag/lỗi (mới nhất) — vì sao trước đây bị "Failed to fetch" / "Có lỗi xảy ra ở server"

**Nguyên nhân chính**: `run.py` chạy Flask ở chế độ **không threaded** (mặc
định của Werkzeug). Trang "Phân tích chi tiết" tự bắn SONG SONG 4-5 lệnh
`fetch()` (`/api/analyze`, `/api/fundamental`, `/api/company`, `/api/ai`...),
mỗi lệnh có thể mất vài giây đến vài chục giây vì phải gọi vnstock thật. Khi
server không threaded, các request này phải XẾP HÀNG xử lý tuần tự từng cái
một — tổng thời gian chờ cộng dồn rất lớn, trình duyệt tự huỷ kết nối trước
khi server kịp trả lời → `Failed to fetch`. Ngoài ra `app/data/company_provider.py`
trước đây hoàn toàn không có cache/giãn cách/giới hạn thời gian chờ, và 3 nơi
gọi vnstock (giá / BCTC / thông tin doanh nghiệp) mỗi nơi tự giãn cách RIÊNG
— tổng tốc độ request thực tế không được kiểm soát tập trung, dễ dính rate
limit của vnstock (lỗi 429/SystemExit) → "Có lỗi xảy ra ở server".

**Đã sửa:**
- `run.py`: thêm `threaded=True` — fix quan trọng nhất.
- `app/utils/net.py` (mới): 1 cổng giãn cách DÙNG CHUNG (`vnstock_call`) cho
  mọi provider gọi vnstock, + `call_with_timeout` tái sử dụng được.
- `app/data/price_provider.py`, `app/data/fundamental_provider.py`: dùng
  chung cổng giãn cách thay vì mỗi nơi 1 lock riêng không phối hợp với nhau.
- `app/data/company_provider.py`: viết lại — có cache 30 phút (chỉ cache khi
  THÀNH CÔNG, để nút "Thử lại" ở frontend thực sự gọi lại chứ không trả lỗi
  cũ), có giãn cách dùng chung, có giới hạn thời gian chờ cho từng lệnh gọi.
- `app/analysis/fundamentals.py`: thêm timeout riêng cho BCTC mã chính và
  từng mã cùng ngành khi so sánh — 1 mã treo không còn kéo treo cả trang.
- `app/__init__.py`: error handler hiển thị đúng thông điệp lỗi tiếng Việt đã
  soạn sẵn (timeout/rate-limit...) thay vì luôn thay bằng câu chung chung.
- Frontend: thêm nút "↺ Thử lại" ở phần phân tích cơ bản & thông tin doanh
  nghiệp khi tải lỗi.
- Thêm **bảng tổng hợp 1 chỗ** (kiểu CafeF) ở đầu mục phân tích cơ bản: mã,
  ngành, giá, vốn hoá ước tính, P/E, P/B, PEG, ROE, ROA, EPS, biên LN ròng,
  tăng trưởng, D/E, giá mục tiêu bình quân CTCK, % chênh lệch, số CTCK định
  giá, điểm so với ngành, luận điểm — gộp mọi chỉ số/so sánh ngành/định giá
  đã có ở các bảng chi tiết bên dưới vào 1 bảng duy nhất để xem nhanh.

Các mục ROE/ROA/PEG, so sánh ngành, luận điểm đầu tư/điểm mạnh-yếu, và bảng
định giá CTCK (STT/Công ty chứng khoán/Giá mục tiêu/Link báo cáo) **đã có sẵn**
trong bản bạn upload (`app/analysis/fundamentals.py`) — vấn đề chỉ là nó
không tải được do lag/lỗi ở trên, không phải do thiếu tính năng.

## 8. Bản nâng cấp (phân tích cơ bản + chống lag)

- Phân tích chi tiết → kéo xuống: luận điểm, ROE/ROA/PEG/P-E/P-B, điểm mạnh-yếu, bảng tổng hợp theo nhóm + so sánh ngành,
  bảng định giá CTCK (đọc `data/broker_reports.csv`), kết quả kinh doanh 4 năm, báo cáo do Gemini viết (nếu USE_GEMINI=true).
- BCTC lấy từ vnstock; nếu tên cột lệch chạy `python scripts/inspect_vnstock.py HPG` rồi sửa `KEYS` trong `app/data/fundamental_provider.py`.
- Chống lag: Gemini chạy nền (không chặn trang), sentiment gộp 1 lần gọi, cache RAM + cache đĩa cho BCTC (`.cache/`),
  sửa lỗi timeout giả trong `screener._call_with_timeout`, tải các khối giao diện song song, `WARM_CACHE=true` nạp sẵn BCTC.
- Giá là nến ngày của vnstock (cache 60s) — gần real-time, không phải tick từng giây.
