# ===== FIX ENCODING UTF-8 CHO WINDOWS =====
import os
import sys
import io

os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

# Ép stdout/stderr sang UTF-8 nếu đang là cp1252
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
    except Exception:
        pass

from app import create_app
from app.config import FLASK_PORT

app = create_app()

if __name__ == "__main__":
    print(f"🚀 Stock AI System chạy tại: http://localhost:{FLASK_PORT}")
    # use_reloader=False để tránh việc file stock_ai.db (được tạo lúc chạy) làm
    # watchdog tự restart server liên tục. Bật debug=True vẫn giữ traceback đẹp.
    #
    # threaded=True LÀ FIX QUAN TRỌNG NHẤT cho tình trạng "lag/load không được":
    # mặc định Werkzeug dev server xử lý TUẦN TỰ từng request một (threaded=False).
    # Trang "Phân tích chi tiết" tự bắn SONG SONG nhiều lệnh fetch() từ trình
    # duyệt (/api/analyze, /api/fundamental, /api/company, /api/ai...), mỗi lệnh
    # có thể mất vài giây tới vài chục giây vì phải gọi vnstock thật. Nếu server
    # không threaded, các request này xếp hàng chờ nhau -> tổng thời gian chờ
    # cộng dồn rất lớn -> trình duyệt tự huỷ kết nối -> "Failed to fetch" / trang
    # treo. Bật threaded=True để mỗi request chạy 1 thread riêng, xử lý thật sự
    # song song (việc giãn cách/rate-limit với vnstock đã được kiểm soát tập
    # trung ở app/utils/net.py nên bật song song ở đây vẫn an toàn).
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=True, use_reloader=False, threaded=True)
