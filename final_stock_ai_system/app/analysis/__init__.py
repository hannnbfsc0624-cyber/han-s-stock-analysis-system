from flask import Flask, send_from_directory, jsonify
import os
import logging

from app.config import PRICE_PROVIDER, PRICE_API_BASE_URL, NEWS_PROVIDER
from app.data.price_provider import get_price_provider
from app.data.news_provider import get_news_provider
from app.db import init_db

logger = logging.getLogger(__name__)


def create_app():
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
    app = Flask(__name__, static_folder=frontend_dir, static_url_path="")

    app.config["PRICE_PROVIDER"] = get_price_provider(PRICE_PROVIDER, PRICE_API_BASE_URL or None)
    app.config["NEWS_PROVIDER"] = get_news_provider(NEWS_PROVIDER)

    init_db()

    from app.api.routes import api_bp
    app.register_blueprint(api_bp)

    @app.get("/")
    def index():
        return send_from_directory(frontend_dir, "index.html")

    @app.errorhandler(Exception)
    def handle_any_error(err):
        # Bắt mọi lỗi chưa lường trước (vd: provider thật bị timeout/404, Gemini
        # lỗi lọt ra ngoài...) và trả JSON thay vì trang lỗi HTML mặc định của
        # Flask, để frontend hiện thông báo rõ ràng thay vì "Failed to fetch".
        logger.exception("Unhandled error on %s", getattr(err, "name", err))
        code = getattr(err, "code", 500) or 500
        message = str(err) if code != 500 else "Có lỗi xảy ra ở server, vui lòng thử lại."
        return jsonify(error=message), code

    return app