import json, shutil, re, urllib.request, html
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

path = "/var/www/profile/posts.json"
backup_path = path + ".bak_hourly"
RSS_URL = "https://news.google.com/rss/search?q=artificial+intelligence+when:1h&hl=en-US&gl=US&ceid=US:en"
MAX_TEASER_CHARS = 280

def clean_text(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def fetch_url(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.geturl(), resp.read()

def fetch_rss(url):
    return fetch_url(url)[1]

def parse_rss(xml_bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.iter("item"):
        title = clean_text(item.findtext("title", default=""))
        link = item.findtext("link", default="").strip()
        desc = clean_text(item.findtext("description", default=""))
        source_el = item.find("source")
        source_name = clean_text(source_el.text) if source_el is not None and source_el.text else ""
        if title and link:
            items.append({"title": title, "link": link, "desc": desc, "source": source_name})
    return items

def extract_meta_description(page):
    patterns = [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']'
    ]
    for pattern in patterns:
        match = re.search(pattern, page, flags=re.I | re.S)
        if match:
            text = clean_text(match.group(1))
            if len(text) >= 60:
                return text
    return ""

def extract_one_paragraph(url):
    try:
        final_url, html_bytes = fetch_url(url)
        page = html_bytes.decode("utf-8", errors="ignore")

        meta = extract_meta_description(page)
        if meta:
            return meta[:MAX_TEASER_CHARS].rsplit(" ", 1)[0] + ("…" if len(meta) > MAX_TEASER_CHARS else ""), final_url

        p_match = re.search(r"<p[^>]*>(.*?)</p>", page, flags=re.I | re.S)
        if p_match:
            text = clean_text(p_match.group(1))
            if len(text) >= 40:
                return text[:MAX_TEASER_CHARS].rsplit(" ", 1)[0] + ("…" if len(text) > MAX_TEASER_CHARS else ""), final_url

        return "", final_url
    except Exception as e:
        print(f"Extraction failed: {e}")
        return "", url

def clean_title(title):
    title = clean_text(title)
    title = re.sub(r"\s+[-|–—]\s+[A-Za-z0-9 .&']{2,50}$", "", title)
    return title.strip()

def make_slug(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:80]

def get_domain(url):
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url

def pick_icon(title):
    t = title.lower()
    if any(w in t for w in ["safety", "risk", "hack", "breach", "warning", "attack", "security", "cyber"]):
        return "⚠️"
    if any(w in t for w in ["open", "release", "launch", "unveil", "model"]):
        return "🚀"
    if any(w in t for w in ["fund", "invest", "billion", "ipo", "deal"]):
        return "💰"
    if any(w in t for w in ["regulat", "law", "policy", "govern"]):
        return "⚖️"
    return "🧠"

def pick_tags(title):
    t = title.lower()
    tags = ["AI"]
    if any(w in t for w in ["open", "source", "weight"]):
        tags.append("Open Source")
    if any(w in t for w in ["safety", "risk", "hack", "breach", "security", "cyber"]):
        tags.append("AI Safety")
    if any(w in t for w in ["regulat", "law", "policy", "govern"]):
        tags.append("Policy")
    if any(w in t for w in ["fund", "invest", "billion", "ipo", "deal"]):
        tags.append("Industry")
    if len(tags) == 1:
        tags.append("News")
    return tags[:3]

try:
    xml_bytes = fetch_rss(RSS_URL)
    items = parse_rss(xml_bytes)
except Exception as e:
    print(f"RSS fetch failed: {e}")
    raise SystemExit(1)

if not items:
    print("No AI news items found in the last hour.")
    raise SystemExit(0)

shutil.copy(path, backup_path)
with open(path, "r", encoding="utf-8") as f:
    posts = json.load(f)

existing_slugs = {p.get("slug") for p in posts}

chosen = None
for item in items:
    title = clean_title(item["title"])
    slug = make_slug(title)
    if slug not in existing_slugs:
        chosen = (item, title, slug)
        break

if not chosen:
    print("All recent items already posted. Nothing to add.")
    raise SystemExit(0)

item, title, slug = chosen
print(f"Selected news: {title}")

teaser, source_url = extract_one_paragraph(item["link"])
if not teaser:
    teaser = clean_text(item["desc"])[:MAX_TEASER_CHARS]

if not teaser:
    print("Could not get any summary text. Skipping.")
    raise SystemExit(0)

source_name = item["source"] or get_domain(source_url)

now = datetime.now(timezone.utc)
date_str = now.strftime("%b %d, %Y")

new_post = {
    "slug": slug,
    "icon": pick_icon(title),
    "date": date_str,
    "tagInline": "AI · Hourly Brief",
    "title": title[:120],
    "teaser": teaser,
    "tags": pick_tags(title),
    "sourceUrl": source_url,
    "sourceName": source_name
}

posts.insert(0, new_post)
print(f"New post added: {title[:60]}...  (source: {source_name})")

with open(path, "w", encoding="utf-8") as f:
    json.dump(posts, f, indent=2, ensure_ascii=False)

print(f"Total posts now: {len(posts)}")
