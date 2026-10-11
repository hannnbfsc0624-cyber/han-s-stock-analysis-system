import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv chưa cài cũng không sao, dùng os.environ trực tiếp


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


PRICE_PROVIDER = os.getenv("PRICE_PROVIDER", "mock")
PRICE_API_BASE_URL = os.getenv("PRICE_API_BASE_URL", "")
NEWS_PROVIDER = os.getenv("NEWS_PROVIDER", "mock")

# ===== Gemini =====
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
USE_GEMINI = _bool("USE_GEMINI")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Model dự phòng (cách nhau dấu phẩy) — tự thử lần lượt khi model chính báo 404/không tồn tại.
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-flash-latest,gemini-2.5-flash,gemini-2.0-flash").split(",") if m.strip()]
# false (mặc định): Gemini CHỈ chạy khi bấm nút -> không đốt quota 20 lượt/ngày của gói free.
# true: tự gọi Gemini cho sentiment tin tức + khuyến nghị ở bảng Screener (rất tốn quota).
GEMINI_AUTO = _bool("GEMINI_AUTO")

# ===== Cache TTL (giây) =====
CACHE_TTL_PRICE = int(os.getenv("CACHE_TTL_PRICE", "60"))
CACHE_TTL_NEWS = int(os.getenv("CACHE_TTL_NEWS", "300"))
CACHE_TTL_SENTIMENT = int(os.getenv("CACHE_TTL_SENTIMENT", "3600"))
CACHE_TTL_GEMINI = int(os.getenv("CACHE_TTL_GEMINI", "600"))

# ===== Phân tích cơ bản =====
FUNDAMENTAL_PROVIDER = os.getenv("FUNDAMENTAL_PROVIDER", "vnstock" if PRICE_PROVIDER == "vnstock_real" else "mock")
VNSTOCK_SOURCE = os.getenv("VNSTOCK_SOURCE", "VCI")
VNSTOCK_API_KEY = os.getenv("VNSTOCK_API_KEY", "")
REPORTS_CSV = os.getenv("REPORTS_CSV", "data/broker_reports.csv")
WARM_CACHE = _bool("WARM_CACHE")          # true: tự nạp sẵn BCTC danh mục khi bật server

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

FLASK_PORT = int(os.getenv("FLASK_PORT", "8000"))
DB_PATH = os.getenv("DB_PATH", "stock_ai.db")

DEFAULT_WATCHLIST = ["FPT", "HPG", "DGC", "GAS", "VNM", "MWG", "VHM", "POW"]
