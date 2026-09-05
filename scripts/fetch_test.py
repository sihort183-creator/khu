"""GitHub Actions 실행 서버(해외 IP)에서 경희대 게시판이 열리는지 확인하는 1회성 테스트."""
import json, re, sys, time, urllib.request, urllib.error

URLS = [
    ("본부 공지", "https://www.khu.ac.kr/kor/user/bbs/BMSR00040/list.do?menuNo=200072"),
    ("건축공학과", "https://ce.khu.ac.kr/ce25/user/bbs/BMSR00040/list.do?menuNo=21600019"),
    ("컴퓨터공학부", "https://cs.khu.ac.kr/cs/user/bbs/BMSR00040/list.do?menuNo=12200006"),
]
UA = "Mozilla/5.0 (X11; Linux x86_64) khu-notice-fetch-test/0.1"

def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, body, round(time.time() - t, 2), r.geturl(), None
    except urllib.error.HTTPError as e:
        return e.code, e.read(), round(time.time() - t, 2), url, None
    except Exception as e:
        return 0, b"", round(time.time() - t, 2), url, repr(e)

try:
    ip = urllib.request.urlopen("https://api.ipify.org", timeout=10).read().decode()
except Exception as e:
    ip = f"unknown ({e})"
print(f"runner public ip: {ip}")

ok_all = True
for name, url in URLS:
    status, body, sec, final, err = get(url)
    text = body.decode("utf-8", "replace")
    links = re.findall(r'href="([^"]*view\.do\?[^"]*)"', text)
    rows = len(re.findall(r"<tr", text))
    blocked = bool(re.search(r"접근이 차단|access denied|forbidden|해외", text, re.I))
    ok = status == 200 and len(links) >= 5 and not blocked
    ok_all &= ok
    print(json.dumps({"name": name, "status": status, "bytes": len(body), "sec": sec,
                      "view_links": len(links), "tr_rows": rows, "blocked_text": blocked,
                      "final_url": final, "error": err, "ok": ok}, ensure_ascii=False))
    # 상세 1건도 열어 본다
    if links:
        href = links[0]
        if href.startswith("/"):
            base = re.match(r"https?://[^/]+", final).group(0)
            href = base + href
        elif not href.startswith("http"):
            href = final.rsplit("/", 1)[0] + "/" + href
        s2, b2, sec2, _, err2 = get(href)
        print(json.dumps({"name": name + " 상세", "status": s2, "bytes": len(b2), "sec": sec2,
                          "url": href, "error": err2}, ensure_ascii=False))
    time.sleep(2)

print("RESULT:", "PASS" if ok_all else "FAIL")
sys.exit(0 if ok_all else 1)
