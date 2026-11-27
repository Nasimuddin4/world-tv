"""Build site/data.json from iptv-org API dumps in raw/ (run: python build.py --fetch to refresh)."""
import json, os, sys, urllib.request

RAW = os.path.join(os.path.dirname(__file__), "raw")
OUT = os.path.join(os.path.dirname(__file__), "site", "data.json")
FILES = ["streams", "channels", "logos", "countries", "categories"]

if "--fetch" in sys.argv:
    for f in FILES:
        urllib.request.urlretrieve(f"https://iptv-org.github.io/api/{f}.json", os.path.join(RAW, f + ".json"))

load = lambda n: json.load(open(os.path.join(RAW, n + ".json"), encoding="utf-8"))
streams, channels, logos, countries, cats = (load(f) for f in FILES)

ch = {c["id"]: c for c in channels}
logo = {}
for l in logos:
    if l["channel"] and l["channel"] not in logo and l["url"].startswith("https"):
        logo[l["channel"]] = l["url"]

qrank = lambda q: int((q or "0p").rstrip("pi") or 0) if (q or "").rstrip("pi").isdigit() else 0
groups = {}
for s in streams:
    u = s["url"]
    if not u.startswith("https://") or s.get("referrer") or s.get("user_agent"):
        continue
    c = ch.get(s["channel"]) if s["channel"] else None
    if c and (c["is_nsfw"] or c["closed"]):
        continue
    key = s["channel"] or s["title"]
    g = groups.setdefault(key, {"c": c, "title": s["title"], "urls": []})
    g["urls"].append((qrank(s["quality"]), s["quality"] or "", u))

cat_ids = [c["id"] for c in cats]
cidx = {c: i for i, c in enumerate(cat_ids)}
items = []
for key, g in groups.items():
    c = g["c"]
    seen, urls = set(), []
    for x in sorted(g["urls"], key=lambda x: -x[0]):
        if x[2] not in seen:
            seen.add(x[2]); urls.append(x)
    is_sport = bool(c and "sports" in c["categories"])
    urls = urls[: 25 if is_sport else 10]
    items.append([
        c["name"] if c else g["title"],
        c["country"] if c else "",
        [cidx[x] for x in (c["categories"] if c else []) if x in cidx],
        logo.get(key, "") if c else "",
        [[q, u] for _, q, u in urls],
    ])
items.sort(key=lambda x: x[0].lower())
used = {i[1] for i in items}
data = {
    "countries": [[c["code"], c["name"], c["flag"]] for c in countries if c["code"] in used],
    "categories": [c["name"] for c in cats],
    "channels": items,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print(len(items), "channels,", len(data["countries"]), "countries,", os.path.getsize(OUT) // 1024, "KB")
