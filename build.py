"""Build the World TV static site from iptv-org API dumps.

  python build.py            build from raw/*.json
  python build.py --fetch    download fresh iptv-org data first
Env WTV_BASE overrides the public base URL (canonical/sitemap).
"""
import datetime, html, json, os, re, shutil, sys, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW, SITE = os.path.join(ROOT, "raw"), os.path.join(ROOT, "site")
BASE = os.environ.get("WTV_BASE", "https://world-tv-ebon.vercel.app").rstrip("/")
FILES = ["streams", "channels", "logos", "countries", "categories"]
# Free/official cricket channels present in the iptv-org list (exact channel names).
FREETV_URL = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"  # official free channels only
CRICKET = {"DD Sports", "Willow", "Willow Sports", "PTV Sports", "T Sports", "Cricket Gold"}

os.makedirs(RAW, exist_ok=True)
if "--fetch" in sys.argv:
    for f in FILES:
        urllib.request.urlretrieve(f"https://iptv-org.github.io/api/{f}.json", os.path.join(RAW, f + ".json"))
    urllib.request.urlretrieve(FREETV_URL, os.path.join(RAW, "freetv.m3u8"))

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

# ---------- Free-TV/IPTV (github.com/Free-TV/IPTV): only officially free channels ----------
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130 Safari/537.36", "Cookie": "CONSENT=YES+1; SOCS=CAI"}


def yt_resolve(u):
    """Return an official embed URL for a YouTube/Dailymotion live link, or None."""
    m = re.search(r"dailymotion\.com/video/([a-zA-Z0-9]+)", u)
    if m:
        return f"https://geo.dailymotion.com/player.html?video={m.group(1)}"
    m = re.search(r"youtube\.com/watch\?v=([\w-]{11})", u) or re.search(r"youtu\.be/([\w-]{11})", u)
    if m:
        return f"https://www.youtube-nocookie.com/embed/{m.group(1)}?autoplay=1"
    m = re.search(r"youtube\.com/channel/(UC[\w-]{22})", u)
    if not m:
        try:
            page = urllib.request.urlopen(urllib.request.Request(re.sub(r"/live/?$", "", u), headers=UA), timeout=15).read().decode("utf-8", "ignore")
            m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', page) or re.search(r'"externalId":"(UC[\w-]{22})"', page)
        except Exception:
            m = None
    return f"https://www.youtube-nocookie.com/embed/live_stream?channel={m.group(1)}&autoplay=1" if m else None


# Resolved embed URLs are cached in a committed file so a CI run where YouTube blocks lookups keeps them.
YT_CACHE = os.path.join(ROOT, "embed_cache.json")
yt_cache = json.load(open(YT_CACHE, encoding="utf-8")) if os.path.exists(YT_CACHE) else {}


def yt_cached(u):
    r = yt_resolve(u)
    return r or yt_cache.get(u)


ftv_path = os.path.join(RAW, "freetv.m3u8")
ftv_added = ftv_new = 0
if os.path.exists(ftv_path):
    import concurrent.futures as cf
    lines = open(ftv_path, encoding="utf-8").read().splitlines()
    ents, inf = [], None
    for l in lines:
        if l.startswith("#EXTINF"):
            inf = l
        elif inf and l.strip() and not l.startswith("#"):
            ents.append((inf, l.strip())); inf = None
    attr = lambda inf, k: (re.search(k + r'="([^"]*)"', inf) or [None, ""])[1]
    ents = [e for e in ents if e[1].startswith("https://") and not attr(e[0], "group-title").startswith("VOD")]
    embeds = [e[1] for e in ents if re.search(r"youtu|dailymotion", e[1])]
    with cf.ThreadPoolExecutor(16) as ex:
        emb = dict(zip(embeds, ex.map(yt_cached, embeds)))
    json.dump({k: v for k, v in sorted(emb.items()) if v}, open(YT_CACHE, "w", encoding="utf-8"), indent=0)
    code_ok = {c["code"] for c in countries}
    by_name = {}
    for k, g in groups.items():
        nm = (g["c"]["name"] if g["c"] else g["title"]).lower()
        by_name.setdefault((nm, g["c"]["country"] if g["c"] else ""), k)
    known_urls = {x[2] for g in groups.values() for x in g["urls"]}
    for inf, u in ents:
        raw_name = inf.rsplit(",", 1)[-1]
        geo, sd = "Ⓖ" in raw_name, "Ⓢ" in raw_name
        name = re.sub(r"[①-⓿]", "", re.sub(r"\s*\[[^\]]*\]", "", raw_name)).strip()
        if u in emb:
            u, q, rank = emb[u], ("Dailymotion" if "dailymotion" in u else "YouTube"), -1
            if not u:
                continue
        elif ".m3u8" in u:
            q, rank = ("SD" if sd else ""), 0
        else:
            continue
        if geo:
            q = (q + " geo").strip()
        if u in known_urls:
            continue
        tid = attr(inf, "tvg-id")
        cc = attr(inf, "tvg-country").split(";")[0].upper()
        cc = cc if cc in code_ok else ""
        if tid in ch:
            if ch[tid]["is_nsfw"] or ch[tid]["closed"]:
                continue
            key = tid
            groups.setdefault(key, {"c": ch[tid], "title": name, "urls": []})
        else:
            key = by_name.get((name.lower(), cc))
            if not key:
                grp = attr(inf, "group-title").lower()
                key = f"ftv:{cc}:{name}"
                by_name[(name.lower(), cc)] = key
                groups[key] = {"c": {"name": name, "country": cc, "categories": [grp] if grp in {c["id"] for c in cats} else [],
                                     "is_nsfw": False, "closed": False}, "title": name, "urls": []}
                logo_url = attr(inf, "tvg-logo")
                if logo_url.startswith("https"):
                    logo[key] = logo_url
                ftv_new += 1
        groups[key]["urls"].append((rank, q, u))
        known_urls.add(u)
        ftv_added += 1

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
country_list = [[c["code"], c["name"], c["flag"]] for c in countries if c["code"] in used]
cat_names = [c["name"] for c in cats]
data = {
    "countries": country_list,
    "categories": cat_names,
    "cricket": sorted({i[0] for i in items if i[0] in CRICKET}),
    "channels": items,
}

# ---------- write site ----------
for d in ("country", "category", "cricket"):
    shutil.rmtree(os.path.join(SITE, d), ignore_errors=True)
os.makedirs(SITE, exist_ok=True)
json.dump(data, open(os.path.join(SITE, "data.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

TPL = open(os.path.join(ROOT, "template.html"), encoding="utf-8").read()
E = lambda s: html.escape(s, quote=True)
slug = lambda s: re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", s.lower()))
cname = {c[0]: (c[1], c[2]) for c in country_list}
today = datetime.date.today().isoformat()
ccount = {}
for i in items:
    ccount[i[1]] = ccount.get(i[1], 0) + 1
catcount = {}
for i in items:
    for x in i[2]:
        catcount[x] = catcount.get(x, 0) + 1
live_cats = [(i, n) for i, n in enumerate(cat_names) if catcount.get(i) and n != "XXX"]

cat_links = "".join(f'<li><a href="/category/{slug(n)}/">{E(n)} TV</a></li>' for i, n in live_cats)
cat_links += '<li><a href="/cricket/">Cricket TV</a></li>'
country_links = "".join(
    f'<li><a href="/country/{code.lower()}/">{flag} {E(name)}</a></li>'
    for code, name, flag in sorted(country_list, key=lambda c: c[1]))


def card(c):
    co = cname.get(c[1])
    meta = (f"{co[1]} {E(co[0])}" if co else "🌐") + (" · " + E(", ".join(cat_names[x] for x in c[2])) if c[2] else "")
    lg = f'<img loading="lazy" src="{E(c[3])}" alt="{E(c[0])} logo">' if c[3] else f'<div class="ph">{E(c[0][:1] or "?")}</div>'
    return f'<div class="card"><div class="lg">{lg}</div><h3 class="n">{E(c[0])}</h3><div class="m">{meta}</div></div>'


def page(path, title, desc, h1, intro, chans, preset):
    url = BASE + path
    ld = [{"@context": "https://schema.org", "@type": "WebSite", "name": "World TV", "url": BASE + "/",
           "potentialAction": {"@type": "SearchAction", "target": BASE + "/?q={search_term_string}",
                               "query-input": "required name=search_term_string"}}]
    if path != "/":
        ld.append({"@context": "https://schema.org", "@type": "CollectionPage", "name": h1, "url": url, "description": desc,
                   "breadcrumb": {"@type": "BreadcrumbList", "itemListElement": [
                       {"@type": "ListItem", "position": 1, "name": "World TV", "item": BASE + "/"},
                       {"@type": "ListItem", "position": 2, "name": h1, "item": url}]}})
    preset = dict(preset, path=path, title=title)
    out = TPL
    for k, v in {
        "TITLE": E(title), "DESC": E(desc), "CANON": url, "BASE": BASE, "H1": E(h1), "INTRO": E(intro),
        "COUNT": f"{len(chans):,} channels", "CARDS": "".join(card(c) for c in chans[:120]),
        "CATLINKS": cat_links, "COUNTRYLINKS": country_links, "UPDATED": today,
        "JSONLD": json.dumps(ld, ensure_ascii=False).replace("</", "<\\/"),
        "PRESET": json.dumps(preset, ensure_ascii=False).replace("</", "<\\/"),
    }.items():
        out = out.replace("{{" + k + "}}", v)
    fp = os.path.join(SITE, path.strip("/"), "index.html")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    open(fp, "w", encoding="utf-8").write(out)
    return path


def names(chans, n=5):
    return ", ".join(c[0] for c in chans[:n])


by_src = lambda L: sorted(L, key=lambda c: (-len(c[4]), c[0].lower()))
paths = []
paths.append(page("/", f"World TV – Watch {len(items):,} Free Live TV Channels Online",
                  f"Watch {len(items):,} free live TV channels from {len(country_list)} countries online: news, sports, cricket, movies, kids, music and more. No sign-up.",
                  "Free Live TV Channels from Around the World",
                  f"Stream {len(items):,} free, publicly available live TV channels from {len(country_list)} countries in your browser. Search by name or browse by country and category.",
                  items, {}))
for code, name, flag in country_list:
    L = [i for i in items if i[1] == code]
    paths.append(page(f"/country/{code.lower()}/", f"{name} Live TV Channels – {len(L):,} Free Channels | World TV",
                      f"Watch {len(L):,} free live TV channels from {name} online: {names(L)} and more. Free streaming, no sign-up.",
                      f"{name} Live TV Channels",
                      f"Watch {len(L):,} free {name} TV channels live online, including {names(L)}.",
                      L, {"country": code}))
for i, n in live_cats:
    L = [c for c in items if i in c[2]]
    if n == "Sports":
        L = by_src(L)
    paths.append(page(f"/category/{slug(n)}/", f"{n} Live TV Channels – {len(L):,} Free Channels | World TV",
                      f"Watch {len(L):,} free live {n.lower()} TV channels online from around the world: {names(L)} and more.",
                      f"{n} Live TV Channels",
                      f"Free live {n.lower()} TV from around the world, including {names(L)}.",
                      L, {"cat": i}))
L = by_src([c for c in items if c[0] in CRICKET])
paths.append(page("/cricket/", f"Live Cricket TV Channels – Watch Free Cricket TV Online | World TV",
                  f"Watch free live cricket TV channels online: {names(L, 6)}. Free, official streams in your browser.",
                  "Live Cricket TV Channels",
                  f"Free and official live cricket channels: {names(L, 6)}. Live match coverage depends on each broadcaster's rights in your country.",
                  L, {"cricket": 1}))

sm = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
for p in paths:
    pr = "1.0" if p == "/" else "0.8" if p.startswith(("/category", "/cricket")) else "0.6"
    sm.append(f"<url><loc>{BASE}{p}</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>{pr}</priority></url>")
sm.append("</urlset>")
open(os.path.join(SITE, "sitemap.xml"), "w", encoding="utf-8").write("\n".join(sm))
open(os.path.join(SITE, "robots.txt"), "w", encoding="utf-8").write(f"User-agent: *\nAllow: /\n\nSitemap: {BASE}/sitemap.xml\n")
open(os.path.join(SITE, "favicon.svg"), "w", encoding="utf-8").write(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect x="4" y="12" width="56" height="38" rx="8" fill="#141821" stroke="#4f8cff" stroke-width="4"/>'
    '<path d="M24 58h16M32 50v8" stroke="#4f8cff" stroke-width="4" stroke-linecap="round"/><circle cx="32" cy="31" r="7" fill="#ff4d5e"/></svg>')

og = os.path.join(SITE, "og.png")
if not os.path.exists(og):
    try:
        from PIL import Image, ImageDraw, ImageFont
        im = Image.new("RGB", (1200, 630), "#0b0d12")
        dr = ImageDraw.Draw(im)
        try:
            big, sm_f = ImageFont.truetype("arialbd.ttf", 120), ImageFont.truetype("arial.ttf", 46)
        except OSError:
            big = sm_f = ImageFont.load_default()
        dr.ellipse((110, 225, 170, 285), fill="#ff4d5e")
        dr.text((200, 185), "World TV", font=big, fill="#e8ebf2")
        dr.text((112, 360), "Free live TV channels from around the world", font=sm_f, fill="#8b93a7")
        dr.text((112, 430), "News · Sports · Cricket · Movies · Kids · Music", font=sm_f, fill="#4f8cff")
        im.save(og, optimize=True)
    except ImportError:
        pass

print("Free-TV: +%d sources, %d new channels" % (ftv_added, ftv_new))
print(len(items), "channels,", len(country_list), "countries,", len(paths), "pages,",
      os.path.getsize(os.path.join(SITE, "data.json")) // 1024, "KB data")
