"""
AI週報 - 自動配信スクリプト v5 (Kit + Blogger メール投稿版 / PRO配信対応)
- 無料RSS/API + GitHub Trending から AI関連ニュースを収集（追加コストゼロ）
- HTMLレンダリングして Kit V4 API で配信
- Bloggerには「メール投稿」機能経由で同時投稿（SMTPだけで完了、OAuth不要）
- v4: 冒頭サマリー / 文脈説明文 / キーワードタグ / 今週のまとめ セクション追加
- v5: PRO会員向けに「全記事詳細版」を別配信するロジックを追加
       (KIT_PRO_TAG_ID が設定されている場合のみ有効。無料版とPRO版で内容を分けるが、
        自動生成できない「プロンプト5選」「ツールレビュー」等の創作コンテンツは含めない)
"""
import os, datetime, html, re, json, smtplib, ssl
import urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ---------- 設定 ----------
KIT_API_KEY = os.environ.get("KIT_API_KEY", "")
STRIPE_PRO_URL = "https://buy.stripe.com/00wfZhdpE9zecyveNm53O02"
LANDING_URL = "https://shinogin.github.io/ai-weekly"
BLOG_URL = "https://ai-weekly-jp.blogspot.com/"  # 本文（日本語版）はBloggerに掲載

# PRO会員配信用（Kitで「pro」タグを作成し、そのタグIDを設定すると有効化される）
# 未設定の場合はPRO版配信をスキップする（無料版のみ配信）
KIT_PRO_TAG_ID = os.environ.get("KIT_PRO_TAG_ID", "")

# Blogger メール投稿（SMTPで送るだけで自動投稿される）
BLOGGER_POST_EMAIL = os.environ.get("BLOGGER_POST_EMAIL", "")
GMAIL_USER        = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASS    = os.environ.get("GMAIL_APP_PASSWORD", "")

# 無料ソース（追加コストゼロ）
SOURCES = [
    ("arXiv cs.AI",  "http://export.arxiv.org/rss/cs.AI",  "arxiv"),
    ("arXiv cs.LG",  "http://export.arxiv.org/rss/cs.LG",  "arxiv"),
    ("Hugging Face Papers", "https://huggingface.co/papers", "hf"),
    ("Hacker News",
     "https://hnrss.org/frontpage?q=AI+OR+LLM+OR+GPT+OR+Claude+OR+Anthropic+OR+OpenAI",
     "rss"),
]
UA = {"User-Agent": "Mozilla/5.0 (AI Weekly Newsletter Bot)"}

# キーワードタグ定義
TAG_RULES = [
    ("LLM",      r"llm|large language model|言語モデル"),
    ("エージェント", r"agent|agentic|multi.?agent"),
    ("生成AI",    r"generat|diffusion|image gen|text.to"),
    ("RAG",       r"\brag\b|retrieval|augmented generation"),
    ("ファインチューン", r"fine.?tun|lora|rlhf|instruct"),
    ("マルチモーダル", r"multimodal|vision|image.text|audio"),
    ("推論",      r"reasoning|chain.of.thought|cot|math"),
    ("安全性",    r"safety|alignment|jailbreak|harmful"),
    ("ツール",    r"tool|framework|library|sdk|api"),
    ("研究",      r"arxiv|paper|benchmark|dataset"),
]

def get_tags(item):
    text = (item["title"] + " " + item["desc"]).lower()
    return [tag for tag, pat in TAG_RULES if re.search(pat, text, re.I)]

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
        link  = (it.findtext("link")  or "").strip()
        desc  = re.sub(r"<[^>]+>", "", (it.findtext("description") or "").strip())[:280]
        if title and link:
            items.append({"title": title, "link": link, "desc": desc, "source": source_name})
    if not items:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for it in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title   = (it.findtext("a:title", "", ns) or "").strip()
            link_el = it.find("a:link", ns)
            link    = link_el.get("href") if link_el is not None else ""
            summary = re.sub(r"<[^>]+>", "", (it.findtext("a:summary", "", ns) or "").strip())[:280]
            if title and link:
                items.append({"title": title, "link": link, "desc": summary, "source": source_name})
    return items[:limit]

def parse_hf_papers(html_text, source_name, limit=5):
    items = []
    for m in re.finditer(r'<a[^>]+href="(/papers/[^"]+)"[^>]*>([^<]{10,200})</a>', html_text):
        url   = "https://huggingface.co" + m.group(1)
        title = html.unescape(m.group(2)).strip()
        if title and not any(it["link"] == url for it in items):
            items.append({"title": title, "link": url, "desc": "", "source": source_name})
        if len(items) >= limit:
            break
    return items

def fetch_github_trending(limit=5):
    items = []
    queries = [
        ("LLM Tools",      "topic:llm stars:>200"),
        ("AI Agents",      "topic:ai-agents stars:>100"),
        ("Generative AI",  "topic:generative-ai stars:>200"),
    ]
    last_week = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    for src_name, q in queries:
        try:
            url  = f"https://api.github.com/search/repositories?q={urllib.parse.quote(q + ' pushed:>' + last_week)}&sort=stars&order=desc&per_page={limit}"
            data = json.loads(fetch(url))
            for r in data.get("items", [])[:limit]:
                items.append({
                    "title":  r["full_name"],
                    "link":   r["html_url"],
                    "desc":   (r.get("description") or "")[:240] + f" · ⭐{r.get('stargazers_count',0):,}",
                    "source": "GitHub Trending: " + src_name
                })
        except Exception as e:
            print(f"[WARN] GitHub {src_name}: {e}")
    return items

def collect():
    all_items = []
    for name, url, kind in SOURCES:
        try:
            text  = fetch(url)
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

# ---------- タグ集計・サマリー生成 ----------
def build_summary(items):
    tag_count = {}
    for it in items:
        for t in get_tags(it):
            tag_count[t] = tag_count.get(t, 0) + 1
    top = sorted(tag_count.items(), key=lambda x: -x[1])[:4]
    tag_str = "　".join(f"#{k}({v}件)" for k, v in top) if top else ""
    return f"今週は <strong>{len(items)}件</strong> のAIニュースをお届けします。{('　注目トピック: ' + tag_str) if tag_str else ''}"

def build_weekly_summary(items):
    tag_count = {}
    for it in items:
        for t in get_tags(it):
            tag_count[t] = tag_count.get(t, 0) + 1
    top = sorted(tag_count.items(), key=lambda x: -x[1])[:5]
    lines = [f"<li style='margin:0 0 8px'><strong>#{k}</strong> が今週最も活発（{v}件）</li>" for k, v in top]
    return "".join(lines)

# ---------- レンダリング: 無料版（teaser） ----------
SRC_JA = {
    "arXiv cs.AI": "AI研究の最新論文",
    "arXiv cs.LG": "機械学習の最新論文",
    "Hugging Face Papers": "いま話題の論文（Hugging Face）",
    "Hacker News": "海外で話題のAIニュース",
}

def render_email_teaser(vol, items):
    today = datetime.date.today().strftime("%Y年%m月%d日")
    topics = []
    for it in items:
        src = it["source"]
        label = SRC_JA.get(src, "GitHubで人気のAIツール" if src.startswith("GitHub") else src)
        if label not in topics:
            topics.append(label)
    topic_html = "".join(f'<li style="margin:0 0 8px">{html.escape(t)}</li>' for t in topics[:6])
    return f"""<!DOCTYPE html>
<html><body style="font-family:'Helvetica Neue',Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#111;background:#faf9f6;line-height:1.7">
<div style="border-bottom:2px solid #111;padding-bottom:16px;margin-bottom:24px">
  <div style="font-size:11px;letter-spacing:.15em;color:#c0392b;text-transform:uppercase;margin-bottom:8px">Weekly AI Intelligence</div>
  <h1 style="font-family:Georgia,serif;font-size:26px;margin:0;letter-spacing:-.01em">AI週報 Vol.{vol}</h1>
  <div style="color:#888;font-size:12px;margin-top:6px">{today} 配信</div>
</div>
<p style="font-size:15px;color:#333;margin:0 0 18px">今週もAI週報をお届けします。海外の最新AIニュースや論文を、専門用語をかみくだいて<strong>中高生でもわかる日本語</strong>で解説しました。</p>
<div style="background:#fff8f0;border-left:3px solid #c0392b;padding:14px 18px;margin-bottom:24px">
  <div style="font-size:13px;color:#666;margin-bottom:8px">今週の内容</div>
  <ul style="padding:0 0 0 18px;margin:0;color:#333;font-size:14px">{topic_html}</ul>
</div>
<div style="text-align:center;margin:28px 0">
  <a href="{BLOG_URL}" style="display:inline-block;background:#111;color:#fff;padding:14px 32px;text-decoration:none;font-size:15px;font-weight:500;border-radius:3px">今週号をやさしい日本語で読む →</a>
</div>
<div style="margin:32px 0 24px;padding:20px;background:#111;color:#faf9f6;border-radius:4px">
  <div style="font-size:11px;letter-spacing:.12em;color:#e07060;margin-bottom:8px">PRO版のご案内</div>
  <p style="font-size:13px;color:#bbb;margin:0 0 14px">PRO版（月額300円）では、今週収集した全記事の詳細版（全ソース・全タグ・今週の統計まとめ）をお届けします。</p>
  <a href="{STRIPE_PRO_URL}" style="display:inline-block;background:#c0392b;color:#fff;padding:11px 22px;text-decoration:none;font-size:13px;font-weight:500;border-radius:2px">PRO版にアップグレード →</a>
</div>
<div style="border-top:1px solid #ddd;padding-top:16px;margin-top:32px;color:#888;font-size:11px;text-align:center">
  <p style="margin:0 0 6px">AI週報 · 毎週月曜配信 · <a href="{LANDING_URL}" style="color:#888">公式サイト</a></p>
  <p style="margin:0">配信停止はメール末尾のリンクから</p>
</div></body></html>"""

def render_preview(items):
    return "海外の最新AIニュースを、やさしい日本語で。"

# ---------- レンダリング: PRO版（全記事詳細版） ----------
def render_html(vol, items):
    """PRO会員向け：全記事・全ソース・タグ・今週の統計を含む詳細版"""
    today      = datetime.date.today().strftime("%Y年%m月%d日")
    summary    = build_summary(items)
    weekly_sum = build_weekly_summary(items)

    by_source = {}
    for it in items:
        by_source.setdefault(it["source"], []).append(it)

    sections_html = []
    for src, sec_items in by_source.items():
        rows = []
        for it in sec_items[:6]:
            t    = html.escape(it["title"])
            d    = html.escape(it["desc"]) if it["desc"] else ""
            tags = get_tags(it)
            tag_html = "".join(
                f'<span style="display:inline-block;background:#f0f0f0;color:#555;'
                f'font-size:11px;padding:1px 6px;border-radius:2px;margin:0 3px 0 0">#{tg}</span>'
                for tg in tags
            )
            rows.append(
                f'<li style="margin:0 0 18px;padding:0 0 18px;border-bottom:1px solid #eee;list-style:none">'
                f'<a href="{html.escape(it["link"])}" style="color:#111;text-decoration:none;font-weight:500;font-size:15px;display:block;margin-bottom:4px">{t} →</a>'
                f'<div style="color:#666;font-size:13px;line-height:1.55;margin-bottom:5px">{d}</div>'
                f'<div style="margin-top:4px">{tag_html}</div>'
                f'</li>'
            )
        sections_html.append(
            f'<h3 style="font-family:Georgia,serif;font-size:16px;color:#c0392b;border-bottom:2px solid #111;padding-bottom:6px;margin:32px 0 16px">{html.escape(src)}</h3>'
            f'<ul style="padding:0;margin:0">{"".join(rows)}</ul>'
        )

    return f"""<!DOCTYPE html>
<html><body style="font-family:'Helvetica Neue',Arial,sans-serif;max-width:640px;margin:0 auto;padding:24px;color:#111;background:#faf9f6;line-height:1.7">
<div style="border-bottom:2px solid #111;padding-bottom:16px;margin-bottom:24px">
  <div style="font-size:11px;letter-spacing:.15em;color:#c0392b;text-transform:uppercase;margin-bottom:8px">Weekly AI Intelligence · PRO</div>
  <h1 style="font-family:Georgia,serif;font-size:28px;margin:0;letter-spacing:-.01em">AI週報 Vol.{vol}（PRO版・全記事詳細）</h1>
  <div style="color:#888;font-size:12px;margin-top:6px">{today} 配信 · PRO会員限定</div>
</div>
<div style="background:#fff8f0;border-left:3px solid #c0392b;padding:14px 18px;margin-bottom:28px;font-size:14px;color:#444;line-height:1.7">
  {summary}
</div>
{"".join(sections_html)}
<div style="margin:48px 0 24px;padding:24px;background:#f7f7f5;border:1px solid #e0e0e0;border-radius:4px">
  <h3 style="font-family:Georgia,serif;font-size:17px;margin:0 0 14px;color:#111">📊 今週のまとめ</h3>
  <ul style="padding:0;margin:0;color:#444;font-size:14px">
    {weekly_sum}
  </ul>
</div>
<div style="border-top:1px solid #ddd;padding-top:16px;margin-top:32px;color:#888;font-size:11px;text-align:center">
  <p style="margin:0 0 6px">AI週報 PRO · 毎週月曜配信 · <a href="{LANDING_URL}" style="color:#888">公式サイト</a></p>
  <p style="margin:0">配信停止はメール末尾のリンクから</p>
</div></body></html>"""

# ---------- 配信: Kit ----------
def send_kit(subject, html_body, preview, subscriber_filter=None):
    """Kit Broadcast APIでメール配信する。
    subscriber_filter を渡さない場合は全購読者に配信される。
    PRO限定配信の場合は [{"all": [{"type": "tag", "ids": [KIT_PRO_TAG_ID]}]}] のような形式で渡す。"""
    if not KIT_API_KEY:
        print("[SKIP] KIT_API_KEY not set"); return False
    now     = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    payload_dict = {
        "subject": subject, "content": html_body, "description": subject,
        "public": False, "published_at": now, "preview_text": preview,
        "send_at": now,
        "subscriber_filter": subscriber_filter if subscriber_filter else [{"all": []}]
    }
    payload = json.dumps(payload_dict).encode()
    req = urllib.request.Request(
        "https://api.kit.com/v4/broadcasts", data=payload, method="POST",
        headers={"X-Kit-Api-Key": KIT_API_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("[KIT]", r.status, r.read().decode()[:200]); return True
    except urllib.error.HTTPError as e:
        print("[KIT ERROR]", e.code, e.read().decode()[:200]); return False

def send_kit_pro(vol, items):
    """PRO会員（KIT_PRO_TAG_IDタグ保有者）向けに詳細版を配信する。
    KIT_PRO_TAG_ID が未設定の場合は何もしない（無料版のみの現行運用を維持）。"""
    if not KIT_PRO_TAG_ID:
        print("[SKIP] KIT_PRO_TAG_ID not set — PRO配信は未設定のためスキップ")
        return False
    subject = f"AI週報 Vol.{vol}｜PRO版・全記事詳細"
    body    = render_html(vol, items)
    preview = "PRO会員向け：今週収集した全記事の詳細版です。"
    filt    = [{"all": [{"type": "tag", "ids": [int(KIT_PRO_TAG_ID)]}]}]
    return send_kit(subject, body, preview, subscriber_filter=filt)

# ---------- 配信: Blogger (メール投稿) ----------
def post_blogger_via_email(subject, html_body):
    if not BLOGGER_POST_EMAIL or not GMAIL_USER or not GMAIL_APP_PASS:
        print("[SKIP] Blogger mail vars not set"); return False
    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = BLOGGER_POST_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PASS)
            smtp.sendmail(GMAIL_USER, [BLOGGER_POST_EMAIL], msg.as_string())
        print("[BLOGGER MAIL] sent to", BLOGGER_POST_EMAIL); return True
    except Exception as e:
        print("[BLOGGER MAIL ERROR]", e); return False

# ---------- 号数カウンター（送信回数に対応）----------
# 日付計算をやめ、実際に送信した号数を vol.txt で管理する。
# 配信が成功するたびに +1 し、ワークフローがリポジトリへコミットして永続化する。
VOL_FILE = "vol.txt"

def read_last_vol():
    try:
        with open(VOL_FILE, encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return 0

def write_last_vol(v):
    with open(VOL_FILE, "w", encoding="utf-8") as f:
        f.write(str(v))


# ---------- X投稿用テキスト生成 ----------
def compose_tweet(vol, items):
    """週刊ニュースのハイライトからツイート文を生成（280文字以内）"""
    highlights = []
    seen = set()
    for it in items:
        s = it['source']
        if s not in seen and len(highlights) < 3:
            t = it['title']
            if len(t) > 28: t = t[:26] + '…'
            highlights.append(t)
            seen.add(s)
    lines = [f'📬 AI週報 Vol.{vol} 配信', '']
    for h in highlights:
        lines.append(f'・{h}')
    lines += ['', f'厳選{len(items)}件のAIニュース👇', LANDING_URL, '', '#AI #LLM #AIエージェント']
    tweet = chr(10).join(lines)
    if len(tweet) > 280:
        lines = [f'📬 AI週報 Vol.{vol} 配信', '', f'厳選{len(items)}件のAIニュース👇', LANDING_URL, '', '#AI #LLM #AIエージェント']
        tweet = chr(10).join(lines)
    return tweet

# ---------- 実行 ----------
def main():
    vol = read_last_vol() + 1   # 前回送信号の次の番号 = 今回の号数
    print(f"[START] AI週報 Vol.{vol}")
    items = collect()
    print(f"[COLLECT] {len(items)} items")
    if not items:
        items = [{"title": "今週のAIニュースをお届けします",
                  "link": LANDING_URL, "desc": "詳細はWebサイトで",
                  "source": "AI週報"}]
    subject   = f"AI週報 Vol.{vol}｜今週のAIニュースをやさしい日本語で"
    email_body = render_email_teaser(vol, items)
    preview   = render_preview(items)
    ok = send_kit(subject, email_body, preview)
    # Bloggerへの英語アブストラクト自動投稿は停止（AdSense審査・読者維持に不利なため）。
    # 本文の日本語版は毎週の手動フローでnote/Bloggerに掲載する。
    # post_blogger_via_email(subject, render_html(vol, items))
    if ok:
        write_last_vol(vol)   # 送信成功時のみ号数を確定・永続化
        print(f"[VOL] saved Vol.{vol} to {VOL_FILE}")
        # PRO会員向け詳細版を配信（KIT_PRO_TAG_ID未設定の場合はスキップされる）
        send_kit_pro(vol, items)
    # X投稿用テキスト（コピペ用）をログに出力
    tweet = compose_tweet(vol, items)
    print("[X TWEET - コピペ用] " + "-"*40)
    print(tweet)
    print("-"*50)
    print("[DONE]")

if __name__ == "__main__":
    main()
