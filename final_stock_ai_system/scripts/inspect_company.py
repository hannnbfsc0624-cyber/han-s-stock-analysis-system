"""Xem vnstock trả gì cho hồ sơ công ty.  Chạy:  python scripts\\inspect_company.py GAS"""
import sys
from vnstock import Vnstock

t = (sys.argv[1] if len(sys.argv) > 1 else "GAS").upper()
c = Vnstock().stock(symbol=t, source="VCI").company
for name, fn in (("overview", c.overview), ("shareholders", c.shareholders),
                 ("officers", lambda: c.officers(filter_by="working"))):
    print("==", name)
    try:
        df = fn()
        print("cot:", list(df.columns))
        print(df.head(3).to_string())
    except BaseException as e:
        print("LOI:", type(e).__name__, e)
