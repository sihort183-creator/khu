"""Small read-only connectivity probe for the same runner used by collection."""
import json
import re
import time
import urllib.error
import urllib.request

URLS = [
    'http://sports.khu.ac.kr/bbs/board.php?bo_table=05_01&page=1',
    'http://gradsport.khu.ac.kr/bbs/board.php?bo_table=07_01&page=1',
    'https://sports.khu.ac.kr/bbs/board.php?bo_table=05_01&page=1',
    'https://gradsport.khu.ac.kr/bbs/board.php?bo_table=07_01&page=1',
    'https://me.khu.ac.kr/me_kor/user/bbs/BMSR00040/list.do?menuNo=17300015',
    'https://id.khu.ac.kr/id_kor/user/bbs/BMSR00040/list.do?menuNo=19700005',
]

for url in URLS:
    start = time.monotonic()
    row = {'url': url}
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'khu-notice-bot/0.1', 'Accept-Language': 'ko'})
        with urllib.request.urlopen(req, timeout=12) as response:
            body = response.read(1_000_000)
            row.update(status=response.status, bytes=len(body), final_url=response.url,
                       article_links=len(re.findall(rb'wr_id=|javascript:view\(', body)))
    except Exception as exc:
        row['error'] = str(exc)
    row['seconds'] = round(time.monotonic() - start, 2)
    print(json.dumps(row, ensure_ascii=False), flush=True)
    time.sleep(1)
