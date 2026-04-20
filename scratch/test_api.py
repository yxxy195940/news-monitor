import requests
import time

def test_cninfo():
    print("Testing cninfo API...")
    url = f"https://irm.cninfo.com.cn/newircs/index/search?_t={int(time.time()*1000)}"
    # Let's try sending a generic payload, normally cninfo has POST endpoints for search
    # But the URL provided is just a GET maybe? Or POST? Let's try GET first.
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers)
        print("Status:", r.status_code)
        print("Text preview:", r.text[:500])
    except Exception as e:
        print("Error:", e)

def test_sse():
    print("\nTesting SSE API...")
    url = "https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do?jsonCallBack=jsonpCallback71520742&isPagination=true&pageHelp.pageSize=5&pageHelp.cacheSize=1&START_DATE=2023-04-20&END_DATE=2026-04-20&SECURITY_CODE=603986&TITLE=&BULLETIN_TYPE=&stockType=&pageHelp.pageNo=1&pageHelp.beginPage=1&pageHelp.endPage=1&_=1776657619952"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.sse.com.cn/"
    }
    try:
        r = requests.get(url, headers=headers)
        import json
        text = r.text
        json_str = text[text.index("(")+1 : text.rindex(")")]
        data = json.loads(json_str)
        for a in data['pageHelp']['data'][:2]:
            print(a['SECURITY_NAME'], a['TITLE'], a['URL'])
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    test_sse()
