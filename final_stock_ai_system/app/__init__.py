from flask import Flask, send_from_directory, jsonify
import os
import logging

from app.config import (PRICE_PROVIDER, PRICE_API_BASE_URL, NEWS_PROVIDER, FUNDAMENTAL_PROVIDER,
                        VNSTOCK_SOURCE, VNSTOCK_API_KEY, REPORTS_CSV, WARM_CACHE, DEFAULT_WATCHLIST)
from app.data.fundamental_provider import get_fundamental_provider
from app.data.price_provider import get_price_provider
from app.data.news_provider import get_news_provider
from app.db import init_db

logger = logging.getLogger(__name__)


def create_app():
    if VNSTOCK_API_KEY:
        try:
            from vnstock.core import setup_api_key   # đúng cho vnstock 4.0.8
            setup_api_key(VNSTOCK_API_KEY)
            logger.info("Đã áp dụng VNSTOCK_API_KEY.")
        except Exception as e:
            logger.warning("Không áp dụng được VNSTOCK_API_KEY: %s — vẫn chạy ở gói Khách.", e)

    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
    app = Flask(__name__, static_folder=frontend_dir, static_url_path="")

    app.config["PRICE_PROVIDER"] = get_price_provider(PRICE_PROVIDER, PRICE_API_BASE_URL or None)
    app.config["NEWS_PROVIDER"] = get_news_provider(NEWS_PROVIDER)

    app.config["FUNDAMENTAL_PROVIDER"] = get_fundamental_provider(
        FUNDAMENTAL_PROVIDER, app.config["PRICE_PROVIDER"], VNSTOCK_SOURCE, REPORTS_CSV)
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600

    init_db()

    from app.api.routes import api_bp
    app.register_blueprint(api_bp)

    @app.get("/")
    def index():
        return send_from_directory(frontend_dir, "index.html")

    @app.errorhandler(Exception)
    def handle_any_error(err):
        # Bắt mọi lỗi chưa lường trước (vd: provider thật bị timeout/404) và
        # trả JSON thay vì trang lỗi HTML mặc định của Flask (hoặc để request
        # treo không trả gì), để frontend luôn nhận được phản hồi rõ ràng thay
        # vì "Failed to fetch" mù mờ.
        logger.exception("Unhandled error on %s", getattr(err, "name", err))
        code = getattr(err, "code", 500) or 500
        # Các lỗi do chính hệ thống này chủ động ném ra (timeout có giới hạn,
        # rate-limit vnstock, thiếu dữ liệu BCTC...) đã có thông điệp tiếng Việt
        # rõ ràng sẵn — hiển thị thẳng cho người dùng thay vì che bằng thông
        # điệp chung chung, vẫn giữ HTTP 500 để frontend biết là lỗi.
        known_safe = (TimeoutError, ValueError, RuntimeError, KeyError)
        if code != 500:
            message = str(err)
        elif isinstance(err, known_safe):
            message = str(err)
        else:
            message = "Có lỗi xảy ra ở server, vui lòng thử lại."
        return jsonify(error=message), code

    if WARM_CACHE:
        _start_warm_cache(app)

    return app


def _start_warm_cache(app):
    """Nạp sẵn BCTC của danh mục theo dõi ở luồng nền (tuần tự, giãn cách) để lúc mở trang đã có cache."""
    import threading
    from app import db as dbmod

    def run():
        from app.analysis.fundamentals import analyze_fundamental
        try:
            dbmod.seed_watchlist_if_empty(DEFAULT_WATCHLIST)
            for t in dbmod.get_watchlist():
                try:
                    analyze_fundamental(t, app.config["FUNDAMENTAL_PROVIDER"])
                    logger.info("Đã nạp sẵn BCTC %s", t)
                except Exception as e:
                    logger.warning("Nạp sẵn %s lỗi: %s", t, e)
        except Exception:
            logger.exception("warm cache lỗi")

    threading.Thread(target=run, daemon=True).start()
