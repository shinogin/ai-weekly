import os, datetime, requests
n = (datetime.date.today() - datetime.date(2025,9,1)).days // 7 + 1
h = {"Authorization": "Bearer " + os.environ["BEEHIIV_API_KEY"], "Content-Type": "application/json"}
body = "<h2>AI Weekly Vol." + str(n) + "</h2><p>今週のAIニュースをお届けします。生成AIの業務活用が新たな段階に入っています。</p>"
p = {"title": "AI Weekly Vol." + str(n), "subtitle": "今週のAIニュース", "body_content": body, "status": "draft"}
u = "https://api.beehiiv.com/v2/publications/" + os.environ["BEEHIIV_PUB_ID"] + "/posts"
r = requests.post(u, json=p, headers=h, timeout=30)
print(r.status_code, r.text)
