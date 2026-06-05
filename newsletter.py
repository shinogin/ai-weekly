import os, datetime, requests
n    = (datetime.date.today() - datetime.date(2025,9,1)).days // 7 + 1
subj = f"AI Weekly Vol.{n}"
body = f"<h2>{subj}</h2><p>今週のAIニュースをお届けします。生成AIの業務活用が新たな段階に入っています。</p>"
now  = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
h    = {"X-Kit-Api-Key": os.environ["KIT_API_KEY"], "Content-Type": "application/json"}
r    = requests.post("https://api.kit.com/v4/broadcasts", headers=h,
                              json={"broadcast": {"subject": subj, "content": body, "send_at": now}}, timeout=30)
print(r.status_code, r.text)
