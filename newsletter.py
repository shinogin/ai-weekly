"""
AI週報 - 自動配信スクリプト (Kit + Blogger メール投稿版)
- 無料RSS/API + GitHub Trending から AI関連ニュースを収集（追加コストゼロ）
- HTMLレンダリングして Kit V4 API で配信
- Bloggerには「メール投稿」機能経由で同時投稿（SMTPだけで完了、OAuth不要）
"""
import os, datetime, html, re, json, smtplib, ssl
import urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ---------- 設定 ----------
KIT_API_KEY        = os.environ.get("KIT_API_KEY", "")
STRIPE_PRO_URL     = "https://buy.stripe.com/6oUdR9clA9ze9mj34E53000"
LANDING_URL        = "https://shinogin.github.io/ai-weekly"

# Blogger メール投稿（SMTPで送るだけで自動投稿される）
BLOGGER_POST_EMAIL = os.environ.get("BLOGGER_POST_EMAIL", "")  # 例: xxx.yyy.zzz@blogger.com
GMAIL_USER         = os.environ.get("GMAIL_USER", "")          # 送信元 Gmail アドレス
GMAIL_APP_PASS     = os.environ.get("GMAIL_APP_PASSWORD", "")  # Gmail アプリパスワード

# 無料ソース（追加コストゼロ）
SOURCES = [
    ("arXiv cs.AI",         "http://export.arxiv.org/rss/cs.AI",   "arxiv"),
    ("arXiv cs.LG",         "http://export.arxiv.org/rss/cs.LG",   "arxiv"),
    ("Hugging Face Papers", "https://huggingface.co/papers",       "hf"),
    ("Hacker News",
     "https://hnrss.org/frontpage?q=AI+OR+LLM+OR+GPT+OR+Claude+OR+Anthropic+OR+OpenAI",
     "rss"),
]
UA = {"User-Agent": "Mozilla/5.0 (AI Weekly Newsletter Bot)"}

# ---------- 収集 ----------
def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")

def parse_rss(xml_text, source_name, limit=5):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", "", (it.findtext("description") or "").strip())[:280]
        if title and link:
            items.append({"title": title, "link": link, "desc": desc, "source": source_name})
    if not items:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for it in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = (it.findtext("a:title", "", ns) or "").strip()
            link_el = it.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            summary = re.sub(r"<[^>]+>", "", (it.findtext("a:summary", "", ns) or "").strip())[:280]
            if title and link:
                items.append({"title": title, "link": link, "desc": summary, "source": source_name})
    return items[:limit]

def parse_hf_papers(html_text, source_name, limit=5):
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
    items = []
    queries = [
        ("LLM Tools",     "topic:llm stars:>200"),
        ("AI Agents",     "topic:ai-agents stars:>100"),
        ("Generative AI", "topic:generative-ai stars:>200"),
    ]
    last_week = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    for src_name, q in queries:
        try:
            url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(q + ' pushed:>' + last_week)}&sort=stars&order=desc&per_page={limit}"
            data = json.loads(fetch(url))
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
    all_items = []
    for name, url, kind in SOURCES:
        try:
            text = fetch(url)
            items = parse_hf_papers(text, name, 4) if kind == "hf" else parse_rss(text, name, 4)
            all_items.extend(items)
        except Exception as e:
            print(f"[WARN] {name}: {e}")
    all_items.extend(fetch_github_trending(limit=4))
    seen, uniq = set(), []
    for it in all_items:
        key = re.sub(r"\W+", "", it["title"].lower())[:60]
        if key and key not in seen:
            seen.add(key); uniq.append(it)
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
                f'<div style="color:#666;font-size:13px;line-height:1.55">{d}</div></li>')
        sections_html.append(
            f'<h3 style="font-family:Georgia,serif;font-size:16px;color:#c0392b;border-bottom:2px solid #111;padding-bottom:6px;margin:32px 0 16px">{html.escape(src)}</h3>'
            f'<ul style="padding:0;margin:0">{"".join(rows)}</ul>')
    return f"""<!DOCTYPE html>
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
</div></body></html>"""

def render_preview(items):
    return items[0]["title"][:140] if items else "今週のAIニュースをお届け"

# ---------- 配信: Kit ----------
def send_kit(subject, html_body, preview):
    if not KIT_API_KEY:
        print("[SKIP] KIT_API_KEY not set"); return False
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = json.dumps({
        "subject": subject, "content": html_body, "description": subject,
        "public": False, "published_at": now, "preview_text": preview,
        "send_at": now, "subscriber_filter": [{"all": []}]
    }).encode()
    req = urllib.request.Request(
        "https://api.kit.com/v4/broadcasts", data=payload, method="POST",
        headers={"X-Kit-Api-Key": KIT_API_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("[KIT]", r.status, r.read().decode()[:200]); return True
    except urllib.error.HTTPError as e:
        print("[KIT ERROR]", e.code, e.read().decode()[:200]); return False

# ---------- 配信: Blogger (メール投稿) ----------
def post_blogger_via_email(subject, html_body):
    """Blogger の Mail-to-Blogger 機能で投稿。SMTPだけで動く。"""
    if not BLOGGER_POST_EMAIL or not GMAIL_USER or not GMAIL_APP_PASS:
        print("[SKIP] Blogger mail vars not set (BLOGGER_POST_EMAIL/GMAIL_USER/GMAIL_APP_PASSWORD)")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = BLOGGER_POST_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASS)
            smtp.sendmail(GMAIL_USER, [BLOGGER_POST_EMAIL], msg.as_string())
        print("[BLOGGER MAIL] sent to", BLOGGER_POST_EMAIL)
        return True
    except Exception as e:
        print("[BLOGGER MAIL ERROR]", e); return False

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
    post_blogger_via_email(subject, html_body)
    print("[DONE]")

if __name__ == "__main__":
    main()
