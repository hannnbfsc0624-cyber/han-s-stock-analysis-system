import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.data.simplize_provider import fetch_simplize_reports

t = sys.argv[1] if len(sys.argv) > 1 else "HPG"
rows = fetch_simplize_reports(t)
print(len(rows), "báo cáo có giá mục tiêu")
for r in rows:
    loai = "PDF " if r["url"].lower().endswith(".pdf") else "TRANG"
    goc = r.get("target_price_original")
    print(r["date"], r["broker"].ljust(18), str(r["target_price"]).ljust(6), ("gốc " + str(goc)) if goc else "", loai, r["url"])
