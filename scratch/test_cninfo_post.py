import requests

def test_cninfo_post():
    url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "http://www.cninfo.com.cn/new/index"
    }
    data = {
        "stock": "002594,gssz0002594", # format: code,orgId. Usually just "002594," works too, or we can fetch orgId if needed.
        "pageNum": 1,
        "pageSize": 5,
        "tabName": "fulltext",
        "column": "szse"
    }
    # Let's try without orgId first
    data["stock"] = "002594"
    r = requests.post(url, headers=headers, data=data)
    print("No orgId status:", r.status_code)
    try:
        print(r.json()['announcements'][0]['secName'])
    except Exception as e:
        print("Failed without orgId:", e)

    # Let's try fetching orgId first if needed. There's an endpoint: http://www.cninfo.com.cn/new/information/topSearch/query?keyWord=002594
    r2 = requests.post("http://www.cninfo.com.cn/new/information/topSearch/query", data={"keyWord": "002594"}, headers=headers)
    try:
        orgId = r2.json()[0]['orgId']
        print("Got orgId:", orgId)
        data["stock"] = f"002594,{orgId}"
        r3 = requests.post(url, headers=headers, data=data)
        print("With orgId status:", r3.status_code)
        anns = r3.json().get('announcements', [])
        for a in anns[:2]:
            print(a['secName'], a['announcementTitle'], a['adjunctUrl'])
    except Exception as e:
        print("Error getting orgId:", e)

if __name__ == "__main__":
    test_cninfo_post()
