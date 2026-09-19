import json, shutil, re, urllib.request, html
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin


# ---- CONFIG ----
path = "/var/www/profile/posts.json"
backup_path = path + ".bak_hourly"
RSS_URL = "https://news.google.com/rss/search?q=artificial+intelligence+when:1h&hl=en-US&gl=US&ceid=US:en"
# ----------------


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip_depth = 0
        self.in_p = False
        self.current = []
        self.paragraphs = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag in ["script", "style", "noscript", "nav", "footer", "header", "aside"]:
            self.skip_depth += 1
            return

        if self.skip_depth:
            return

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

            if len(text) >= 50:
                self.paragraphs.append(text)

            self.current = []
            self.in_p = False

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
        pub = item.findtext("pubDate", default="").strip()

        if title and link:
            items.append({
                "title": title,
                "link": link,
                "desc": desc,
                "pub": pub
            })

    return items


def extract_jsonld_article_body(page):
    bodies = []

    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        flags=re.I | re.S
    )

    for raw in scripts:
        raw = html.unescape(raw).strip()

        try:
            data = json.loads(raw)
        except Exception:
            continue

        objects = []

        if isinstance(data, dict):
            objects.append(data)

            if isinstance(data.get("@graph"), list):
                objects.extend(data["@graph"])

        elif isinstance(data, list):
            objects.extend(data)

        for obj in objects:
            if not isinstance(obj, dict):
                continue

            body = obj.get("articleBody")

            if isinstance(body, str) and len(body.strip()) >= 300:
                bodies.append(clean_text(body))

    return bodies


def extract_meta_description(page):
    patterns = [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']'
    ]

    for pattern in patterns:
        match = re.search(pattern, page, flags=re.I | re.S)

        if match:
            text = clean_text(match.group(1))

            if len(text) >= 100:
                return text

    return ""


def extract_article(url):
    try:
        final_url, html_bytes = fetch_url(url)

        page = html_bytes.decode("utf-8", errors="ignore")

        print(f"Resolved URL: {final_url}")

        # ---- METHOD 1: JSON-LD articleBody ----

        jsonld_bodies = extract_jsonld_article_body(page)

        if jsonld_bodies:
            body = max(jsonld_bodies, key=len)

            sentences = re.split(r"(?<=[.!?])\s+", body)

            paragraphs = []
            current = []

            for sentence in sentences:
                sentence = sentence.strip()

                if not sentence:
                    continue

                current.append(sentence)

                if len(" ".join(current)) >= 450:
                    paragraphs.append(" ".join(current))
                    current = []

                if len(paragraphs) >= 5:
                    break

            if current and len(" ".join(current)) >= 100:
                paragraphs.append(" ".join(current))

            if len(paragraphs) >= 2:
                return paragraphs[:5], final_url

        # ---- METHOD 2: Normal HTML paragraphs ----

        parser = ArticleParser()
        parser.feed(page)

        paragraphs = []
        seen = set()

        for p in parser.paragraphs:
            p = clean_text(p)

            if len(p) < 50:
                continue

            key = p.lower()

            if key in seen:
                continue

            seen.add(key)
            paragraphs.append(p)

        if len(paragraphs) >= 3:
            return paragraphs[:5], final_url

        # ---- METHOD 3: Meta description ----

        meta = extract_meta_description(page)

        if meta:
            return [meta], final_url

        return [], final_url

    except Exception as e:
        print(f"Article extraction failed: {e}")
        return [], url


def clean_title(title):
    title = clean_text(title)

    title = re.sub(
        r"\s+[-|–—]\s+[A-Za-z0-9 .&']{2,50}$",
        "",
        title
    )

    return title.strip()


def make_slug(title):
    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        title.lower()
    ).strip("-")

    return slug[:80]


def pick_icon(title):
    t = title.lower()

    if any(w in t for w in [
        "safety", "risk", "hack", "breach",
        "warning", "attack", "security", "cyber"
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

    if any(w in t for w in ["open", "source", "weight"]):
        tags.append("Open Source")

    if any(w in t for w in [
        "safety", "risk", "hack", "breach",
        "security", "cyber"
    ]):
        tags.append("AI Safety")

    if any(w in t in ["regulat", "law", "policy", "govern"]):
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


# ---- LOAD EXISTING POSTS ----

shutil.copy(path, backup_path)

with open(path, "r", encoding="utf-8") as f:
    posts = json.load(f)


# ---- REMOVE OUR TWO TEST POSTS ----

test_slugs = {
    "opinion-this-well-meaning-ideology-feeling-ai-panic-has-a-dark-side",
    "a-world-of-difference-in-10-days-the-changing-course-of-ai"
}

before = len(posts)

posts = [
    p for p in posts
    if p.get("slug") not in test_slugs
]

removed = before - len(posts)

if removed:
    print(f"Removed {removed} test post(s).")


# ---- PICK TOP NEWS ----

item = items[0]

title = clean_title(item["title"])
rss_desc = clean_text(item["desc"])
article_url = item["link"]

print(f"Selected news: {title}")
print(f"Fetching article: {article_url}")


# ---- EXTRACT ACTUAL ARTICLE ----

paragraphs, final_url = extract_article(article_url)


if len(paragraphs) < 2:
    print("Full article could not be extracted.")
    print("Post skipped. No weak RSS-only post will be created.")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            posts,
            f,
            indent=2,
            ensure_ascii=False
        )

    raise SystemExit(0)


print(f"Article paragraphs extracted: {len(paragraphs)}")


# ---- CREATE POST ----

slug = make_slug(title)

now = datetime.now(timezone.utc)
date_str = now.strftime("%b %d, %Y")


new_post = {
    "slug": slug,
    "icon": pick_icon(title),
    "date": date_str,
    "tagInline": "AI · Hourly Brief",
    "title": title[:120],
    "teaser": paragraphs[0][:200],
    "tags": pick_tags(title),
    "paragraphs": paragraphs[:5]
}


# ---- DUPLICATE CHECK ----

existing_index = None

for i, p in enumerate(posts):
    if p.get("slug") == slug:
        existing_index = i
        break


if existing_index is not None:

    existing = posts[existing_index]

    existing_paragraphs = existing.get("paragraphs", [])

    existing_text = " ".join(
        str(x) for x in existing_paragraphs
    )

    if len(existing_text) < 500 or len(existing_paragraphs) < 3:

        posts[existing_index] = new_post

        print(f"Existing short post repaired: {slug}")

    else:

        print(f"Post already exists and looks complete: {slug}")

        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                posts,
                f,
                indent=2,
                ensure_ascii=False
            )

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
print(f"Paragraphs saved: {len(new_post['paragraphs'])}")
