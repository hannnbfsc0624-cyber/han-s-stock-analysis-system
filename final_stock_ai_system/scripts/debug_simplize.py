import re, sys, requests
t = sys.argv[1] if len(sys.argv) > 1 else "HPG"
h = requests.get(f"https://simplize.vn/co-phieu/{t}/bao-cao",
                 headers={"User-Agent": "Mozilla/5.0"}, timeout=15).text
print(len(h), "bytes")
print("pdf:", list(set(re.findall(r'https?://[^\s"\'<>\\]+\.pdf', h)))[:10])
print("co __NEXT_DATA__:", "__NEXT_DATA__" in h)
