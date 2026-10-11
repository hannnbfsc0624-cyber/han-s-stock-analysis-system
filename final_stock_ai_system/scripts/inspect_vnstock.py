"""Xem tên cột thật mà vnstock trả về, để chỉnh KEYS trong app/data/fundamental_provider.py.
Chạy:  python scripts/inspect_vnstock.py HPG   (cần internet)"""
import sys
sys.path.insert(0, ".")
from app.config import VNSTOCK_SOURCE
from vnstock import Finance, Quote

t = (sys.argv[1] if len(sys.argv) > 1 else "HPG").upper()
print("== GIÁ ==")
try:
    q = Quote(symbol=t, source=VNSTOCK_SOURCE.lower()).history(start="2026-09-01", end="2026-10-02", interval="1D")
    print(q.tail(3))
except Exception as e:
    print("LỖI (giá):", e)
fin = Finance(symbol=t, source=VNSTOCK_SOURCE)
for kind in ("income_statement", "balance_sheet"):
    print(f"\n== {kind}: TOAN BO item_id ==")
    try:
        df = getattr(fin, kind)(period="year", lang="vi")
        with __import__("pandas").option_context("display.max_rows", None, "display.width", 160):
            print(df[["item_id", "item"]].to_string())
    except Exception as e:
        print("LỖI:", e)
