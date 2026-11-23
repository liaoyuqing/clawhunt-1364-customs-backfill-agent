import urllib.request, sys
urls = [
    "https://raw.githubusercontent.com/liaoyuqing/clawhunt-1364-customs-backfill-agent/main/README.md",
    "https://raw.githubusercontent.com/liaoyuqing/clawhunt-1364-customs-backfill-agent/main/reports/metrics.json",
    "https://raw.githubusercontent.com/liaoyuqing/clawhunt-1364-customs-backfill-agent/main/rules/unit.yaml",
]
for u in urls:
    try:
        req = urllib.request.Request(u, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            print(resp.status, len(data), u.rsplit("/", 1)[-1])
    except Exception as exc:
        print("FAIL", type(exc).__name__, str(exc)[:90], u.rsplit("/", 1)[-1])