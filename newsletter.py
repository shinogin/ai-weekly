"""
AI週報 - 自動配信スクリプト (Kit + Blogger クロスポスト版)
- 無料RSS/APIからAI関連ニュースを収集（追加コストゼロ）
- HTMLレンダリングして Kit V4 API で配信
- Blogger API でブログにも同時投稿（AdSense収益化）
"""
import os, datetime, html, re, json, xml.etree.ElementTree as ET
import urllib.request, urllib.parse, urllib.error

# ---------- 設定 ----------
KIT_API_KEY      = os.environ.get("KIT_API_KEY", "")
BLOGGER_TOKEN    = os.environ.get("BLOGGER_OAUTH_TOKEN", "")  # 任意。なければBloggerスキップ
BLOGGER_BLOG_ID  = os.environ.get("BLOGGER_BLOG_ID", "")  # AI週報用Blogger ID（マネーナビとは別ブログ。空の場合はBlogger投稿を完全スキップ）STRIPE_PRO_URL   = "https://buy.stripe.com/6oUdR9clA9ze9mj34E53000"
LANDING_URL      = "https://shinogin.github.io/ai-weekly"
STRIPE_PRO_URL   = "https://buy.stripe.com/6oUdR9clA9ze9mj34E53000"

# 無料ソース（追加コストゼロ）
SOURCES = [
    # arXiv: cs.AI (最新AI論文)
    ("arXiv cs.AI", "http://export.arxiv.org/rss/cs.AI", "arxiv"),
    ("arXiv cs.LG", "http://export.arxiv.org/rss/cs.LG", "arxiv"),
    # Hugging Face Papers (日次トレンド)
    ("Hugging Face Papers", "https://huggingface.co/papers", "hf"),
    # Hacker News (AI関連を抽出)
    ("Hacker News", "https://hnrss.org/frontpage?q=AI+OR+LLM+OR+GPT+OR+Claude+OR+Anthropic+OR+OpenAI", "rss"),
]

UA = {"User-Agent": "Mozilla/5.0 (AI Weekly Newsletter Bot; +https://shinogin.github.io/ai-weekly)"}

# ---------- 収集 ----------
def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")

def parse_rss(xml_text, source_name, limit=5):
    """汎用RSS/Atomパーサー"""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    # RSS 2.0
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = (it.findtext("description") or "").strip()
        desc = re.sub(r"<[^>]+>", "", desc)[:280]
        if title and link:
            items.append({"title": title, "link": link, "desc": desc, "source": source_name})
    # Atom
    if not items:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for it in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = (it.findtext("a:title", "", ns) or "").strip()
            link_el = it.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            summary = (it.findtext("a:summary", "", ns) or "").strip()
            summary = re.sub(r"<[^>]+>", "", summary)[:280]
            if title and link:
                items.append({"title": title, "link": link, "desc": summary, "source": source_name})
    return items[:limit]

def parse_hf_papers(html_text, source_name, limit=5):
    """Hugging Face Papersの簡易HTMLパース"""
    items = []
    for m in re.finditer(r'<a[^>]+href="(/papers/[^"]+)"[^>]*>([^<]{10,200})</a>', html_text):
        url = "https://huggingface.co" + m.group(1)
        title = html.unescape(m.group(2)).strip()
        if title and not any(it["link"] == url for it in items):
            items.append({"title": title, "link": url, "desc": "", "source": source_name})
        if len(items) >= limit:
            break
    return items

def fetch_github_trending(limit=5):
    """GitHub APIで最近更新されたAI関連リポジトリを取得。これは高い確実性で成功する。"""
    items = []
    queries = [
        ("LLM Tools", "topic:llm stars:>200"),
        ("AI Agents", "topic:ai-agents stars:>100"),
        ("Generative AI", "topic:generative-ai stars:>200"),
    ]
    last_week = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    for src_name, q in queries:
        try:
            url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(q + ' pushed:>' + last_week)}&sort=stars&order=desc&per_page={limit}"
            text = fetch(url)
            data = json.loads(text)
            for r in data.get("items", [])[:limit]:
                items.append({
                    "title": r["full_name"],
                    "link": r["html_url"],
                    "desc": (r.get("description") or "")[:240] + f" · ⭐{r.get('stargazers_count',0):,}",
                    "source": "GitHub Trending: " + src_name
                })
        except Exception as e:
            print(f"[WARN] GitHub {src_name}: {e}")
    return items

def collect():
    """全ソースから記事を集める。失敗してもスキップ。"""
    all_items = []
    for name, url, kind in SOURCES:
        try:
            text = fetch(url)
            if kind == "hf":
                items = parse_hf_papers(text, name, limit=4)
            else:
                items = parse_rss(text, name, limit=4)
            all_items.extend(items)
        except Exception as e:
            print(f"[WARN] {name}: {e}")
    all_items.extend(fetch_github_trending(limit=4))
    seen = set()
    uniq = []
    for it in all_items:
        key = re.sub(r"\W+", "", it["title"].lower())[:60]
        if key and key not in seen:
            seen.add(key)
            uniq.append(it)
    return uniq

# ---------- レンダリング ----------
def render_html(vol, items):
    today = datetime.date.today().strftime("%Y年%m月%d日")
    by_source = {}
    for it in items:
        by_source.setdefault(it["source"], []).append(it)
    sections_html = []
    for src, sec_items in by_source.items():
        rows = []
        for it in sec_items[:6]:
            t = html.escape(it["title"])
            d = html.escape(it["desc"]) if it["desc"] else ""
            rows.append(
                f'<li style="margin:0 0 14px;padding:0 0 14px;border-bottom:1px solid #eee;list-style:none">'
                f'<a href="{html.escape(it["link"])}" style="color:#111;text-decoration:none;font-weight:500;font-size:15px;display:block;margin-bottom:4px">{t} →</a>'
                f'<div style="color:#666;font-size:13px;line-height:1.55">{d}</div>'
                f'</li>'
            )
        sections_html.append(
            f'<h3 style="font-family:Georgia,serif;font-size:16px;color:#c0392b;border-bottom:2px solid #111;padding-bottom:6px;margin:32px 0 16px">{html.escape(src)}</h3>'
            f'<ul style="padding:0;margin:0">{"".join(rows)}</ul>'
        )
    body = f"""<!DOCTYPE html>
<html><body style="font-family:'Helvetica Neue',Arial,sans-serif;max-width:640px;margin:0 auto;padding:24px;color:#111;background:#faf9f6;line-height:1.7">
  <div style="border-bottom:2px solid #111;padding-bottom:16px;margin-bottom:24px">
    <div style="font-size:11px;letter-spacing:.15em;color:#c0392b;text-transform:uppercase;margin-bottom:8px">Weekly AI Intelligence</div>
    <h1 style="font-family:Georgia,serif;font-size:28px;margin:0;letter-spacing:-.01em">AI週報 Vol.{vol}</h1>
    <div style="color:#888;font-size:12px;margin-top:6px">{today} 配信 · 厳選AIニュース</div>
  </div>
  <p style="font-size:14px;color:#444">今週のAI業界の動き、論文、注目ツールを厳選してお届けします。読む時間10分、得られる情報は圧倒的。</p>
  {"".join(sections_html)}
  <div style="margin:48px 0 24px;padding:24px;background:#111;color:#faf9f6;border-radius:4px">
    <div style="font-size:11px;letter-spacing:.12em;color:#e07060;margin-bottom:8px">PRO版のご案内</div>
    <h2 style="font-family:Georgia,serif;font-size:20px;margin:0 0 10px">もっと深く、もっと使えるAI情報を</h2>
    <p style="font-size:13px;color:#bbb;margin:0 0 16px">PRO版（月額¥980）では、全記事の詳細解説 / 週次プロンプトテンプレ5選 / AIツール詳細レビュー / バックナンバー全アクセスが追加で読めます。</p>
    <a href="{STRIPE_PRO_URL}" style="display:inline-block;background:#c0392b;color:#fff;padding:12px 24px;text-decoration:none;font-size:13px;font-weight:500;border-radius:2px">PRO版にアップグレード →</a>
  </div>
  <div style="border-top:1px solid #ddd;padding-top:16px;margin-top:32px;color:#888;font-size:11px;text-align:center">
    <p style="margin:0 0 6px">AI週報 · 毎週月曜配信 · <a href="{LANDING_URL}" style="color:#888">公式サイト</a></p>
    <p style="margin:0">配信停止はメール末尾のリンクから</p>
  </div>
</body></html>"""
    return body

def render_preview(items):
    if not items:
        return "今週のAIニュースをお届け"
    return items[0]["title"][:140]

# ---------- 配信 ----------
def send_kit(subject, html_body, preview):
    if not KIT_API_KEY:
        print("[SKIP] KIT_API_KEY not set")
        return False
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = json.dumps({
        "subject": subject, "content": html_body, "description": subject,
        "public": False, "published_at": now, "preview_text": preview,
        "send_at": now, "subscriber_filter": [{"all": []}]
    }).encode()
    req = urllib.request.Request(
        "https://api.kit.com/v4/broadcasts",
        data=payload, method="POST",
        headers={"X-Kit-Api-Key": KIT_API_KEY, "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("[KIT]", r.status, r.read().decode()[:200])
            return True
    except urllib.error.HTTPError as e:
        print("[KIT ERROR]", e.code, e.read().decode()[:200])
        return False

def post_blogger(subject, html_body):
    """Bloggerに同じ内容をクロスポストしAdSense露出を作る。OAuthトークンがある時のみ動作。"""
    if not BLOGGER_TOKEN or not BLOGGER_BLOG_ID:
        print("[SKIP] BLOGGER_OAUTH_TOKEN or BLOGGER_BLOG_ID not set")
        return False
    payload = json.dumps({
        "kind": "blogger#post",
        "title": subject,
        "content": html_body,
        "labels": ["AI週報", "AIニュース", "AI Weekly"]
    }).encode()
    req = urllib.request.Request(
        f"https://www.googleapis.com/blogger/v3/blogs/{BLOGGER_BLOG_ID}/posts/",
        data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {BLOGGER_TOKEN}",
            "Content-Type": "application/json"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("[BLOGGER]", r.status, r.read().decode()[:200])
            return True
    except urllib.error.HTTPError as e:
        print("[BLOGGER ERROR]", e.code, e.read().decode()[:300])
        return False

# ---------- 実行 ----------
def main():
    vol = (datetime.date.today() - datetime.date(2025, 9, 1)).days // 7 + 1
    print(f"[START] AI週報 Vol.{vol}")
    items = collect()
    print(f"[COLLECT] {len(items)} items")
    if not items:
        items = [{"title": "今週のAIニュースをお届けします",
                  "link": LANDING_URL, "desc": "詳細はWebサイトで",
                  "source": "AI週報"}]
    subject = f"AI週報 Vol.{vol} - {items[0]['title'][:50]}"
    html_body = render_html(vol, items)
    preview = render_preview(items)
    send_kit(subject, html_body, preview)
    post_blogger(subject, html_body)
    print("[DONE]")

if __name__ == "__main__":
    main()
