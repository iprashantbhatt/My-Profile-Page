import json, shutil, re, urllib.request, html
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

# ---- CONFIG ----
path = "/var/www/profile/posts.json"
backup_path = path + ".bak_hourly"
RSS_URL = "https://news.google.com/rss/search?q=artificial+intelligence+when:1h&hl=en-US&gl=US&ceid=US:en"
# ----------------


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_article = False
        self.in_main = False
        self.in_p = False
        self.skip_depth = 0
        self.paragraphs = []
        self.current = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag in ["script", "style", "noscript", "nav", "footer", "header", "aside"]:
            self.skip_depth += 1
            return

        if self.skip_depth:
            return

        attrs_dict = dict(attrs)
        classes = (attrs_dict.get("class") or "").lower()
        ident = (attrs_dict.get("id") or "").lower()

        if tag == "article":
            self.in_article = True

        if tag == "main":
            self.in_main = True

        if tag == "p":
            self.in_p = True
            self.current = []

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in ["script", "style", "noscript", "nav", "footer", "header", "aside"]:
            if self.skip_depth:
                self.skip_depth -= 1
            return

        if self.skip_depth:
            return

        if tag == "p" and self.in_p:
            text = clean_text(" ".join(self.current))
            if len(text) >= 40:
                self.paragraphs.append(text)
            self.current = []
            self.in_p = False

        if tag == "article":
            self.in_article = False

        if tag == "main":
            self.in_main = False

    def handle_data(self, data):
        if self.skip_depth:
            return

        data = data.strip()

        if data and self.in_p:
            self.current.append(data)


def clean_text(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_url(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "Chrome/131.0 Safari/537.36"
            )
        }
    )

    with urllib.request.urlopen(req, timeout=20) as resp:
        final_url = resp.geturl()
        data = resp.read()

    return final_url, data


def fetch_rss(url):
    return fetch_url(url)[1]


def parse_rss(xml_bytes):
    root = ET.fromstring(xml_bytes)
    items = []

    for item in root.iter("item"):
        title = clean_text(item.findtext("title", default=""))
        link = item.findtext("link", default="").strip()
        desc = clean_text(item.findtext("description", default=""))
        pub = item.findtext("pubDate", default="").strip()

        if title and link:
            items.append({
                "title": title,
                "link": link,
                "desc": desc,
                "pub": pub
            })

    return items


def extract_article(url):
    try:
        final_url, html_bytes = fetch_url(url)

        charset = "utf-8"

        match = re.search(
            rb'<meta[^>]+charset=["\']?([^"\'> ]+)',
            html_bytes[:10000],
            re.I
        )

        if match:
            try:
                charset = match.group(1).decode("ascii", errors="ignore")
            except Exception:
                pass

        text = html_bytes.decode(charset, errors="ignore")

        parser = ArticleParser()
        parser.feed(text)

        paragraphs = parser.paragraphs

        # Remove duplicate paragraphs
        cleaned = []
        seen = set()

        for p in paragraphs:
            p = clean_text(p)

            if len(p) < 40:
                continue

            key = p.lower()

            if key in seen:
                continue

            seen.add(key)
            cleaned.append(p)

        # Keep only meaningful article paragraphs
        cleaned = cleaned[:12]

        return cleaned, final_url

    except Exception as e:
        print(f"Article extraction failed: {e}")
        return [], url


def make_slug(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:80]


def clean_title(title):
    title = clean_text(title)

    # Remove common Google News publisher suffixes
    title = re.sub(
        r"\s+[-|–—]\s+[A-Za-z0-9 .&']{2,40}$",
        "",
        title
    )

    return title.strip()


def make_paragraphs(title, rss_desc, article_paragraphs):
    if len(article_paragraphs) >= 3:
        return article_paragraphs[:5]

    desc = clean_text(rss_desc)

    if desc:
        return [
            desc,
            "The report highlights another example of how increasingly capable AI systems are moving beyond simple text generation and into real-world tasks and decision-making.",
            "As AI agents gain access to tools, networks and external systems, reliable testing, clear boundaries and human oversight become increasingly important."
        ]

    return [
        f"{title}.",
        "The story is developing and additional details are expected as more information becomes available.",
        "Further reporting will help clarify the broader implications of the development."
    ]


def pick_icon(title):
    t = title.lower()

    if any(w in t for w in [
        "safety", "risk", "hack", "breach", "warning",
        "attack", "security", "cyber"
    ]):
        return "⚠️"

    if any(w in t for w in [
        "open", "release", "launch", "unveil", "model"
    ]):
        return "🚀"

    if any(w in t for w in [
        "fund", "invest", "billion", "ipo", "deal"
    ]):
        return "💰"

    if any(w in t for w in [
        "regulat", "law", "policy", "govern"
    ]):
        return "⚖️"

    return "🧠"


def pick_tags(title):
    t = title.lower()
    tags = ["AI"]

    if any(w in t for w in [
        "open", "source", "weight"
    ]):
        tags.append("Open Source")

    if any(w in t for w in [
        "safety", "risk", "hack", "breach", "security", "cyber"
    ]):
        tags.append("AI Safety")

    if any(w in t for w in [
        "regulat", "law", "policy", "govern"
    ]):
        tags.append("Policy")

    if any(w in t for w in [
        "fund", "invest", "billion", "ipo", "deal"
    ]):
        tags.append("Industry")

    if len(tags) == 1:
        tags.append("News")

    return tags[:3]


# ---- FETCH RSS ----

try:
    xml_bytes = fetch_rss(RSS_URL)
    items = parse_rss(xml_bytes)

except Exception as e:
    print(f"RSS fetch failed: {e}")
    raise SystemExit(1)


if not items:
    print("No AI news items found in the last hour.")
    raise SystemExit(0)


# ---- PICK TOP ITEM ----

item = items[0]

rss_title = clean_title(item["title"])
rss_desc = clean_text(item["desc"])
article_url = item["link"]

print(f"Selected news: {rss_title}")
print(f"Fetching article: {article_url}")


# ---- EXTRACT ACTUAL ARTICLE ----

article_paragraphs, final_url = extract_article(article_url)

if article_paragraphs:
    print(f"Article paragraphs extracted: {len(article_paragraphs)}")
else:
    print("Full article text could not be extracted. Using RSS description.")


title = rss_title
slug = make_slug(title)

now = datetime.now(timezone.utc)
date_str = now.strftime("%b %d, %Y")

paragraphs = make_paragraphs(
    title,
    rss_desc,
    article_paragraphs
)


# ---- LOAD ----

shutil.copy(path, backup_path)

with open(path, "r", encoding="utf-8") as f:
    posts = json.load(f)


# ---- DUPLICATE / REPAIR CHECK ----

existing_index = None

for i, p in enumerate(posts):
    if p.get("slug") == slug:
        existing_index = i
        break


new_post = {
    "slug": slug,
    "icon": pick_icon(title),
    "date": date_str,
    "tagInline": "AI · Hourly Brief",
    "title": title[:120],
    "teaser": (
        paragraphs[0][:200]
        if paragraphs
        else title[:200]
    ),
    "tags": pick_tags(title),
    "paragraphs": paragraphs
}


if existing_index is not None:

    existing = posts[existing_index]

    existing_paragraphs = existing.get("paragraphs", [])

    existing_text = " ".join(
        str(x) for x in existing_paragraphs
    )

    # Repair old malformed/short posts
    if len(existing_text) < 500 or len(existing_paragraphs) < 3:

        posts[existing_index] = new_post

        print(f"Existing short post repaired: {slug}")

    else:

        print(f"Post already exists and looks complete: {slug}")
        raise SystemExit(0)

else:

    posts.insert(0, new_post)

    print(f"New post added: {title[:60]}...")


# ---- SAVE ----

with open(path, "w", encoding="utf-8") as f:
    json.dump(
        posts,
        f,
        indent=2,
        ensure_ascii=False
    )


print(f"Total posts now: {len(posts)}")
print(f"Paragraphs saved: {len(paragraphs)}")
