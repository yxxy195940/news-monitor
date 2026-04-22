import requests
import time
import json
import re
from datetime import datetime

class SinaSuggestClient:
    """新浪股票搜索接口，用于将名称转换为代码"""
    @staticmethod
    def search(keyword: str):
        url = f"http://suggest3.sinajs.cn/suggest/type=11,12&key={keyword}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            r = requests.get(url, headers=headers, timeout=10)
            r.encoding = 'gbk'
            # var suggestvalue="比亚迪,11,002594,sz002594,比亚迪,,比亚迪,99,1,ESG,,";
            match = re.search(r'var suggestvalue="(.*?)"', r.text)
            if match and match.group(1):
                items = match.group(1).split(';')
                if items:
                    parts = items[0].split(',')
                    if len(parts) >= 4:
                        return {"name": parts[0], "code": parts[2], "full_code": parts[3]}
        except Exception as e:
            print(f"[AnnouncementAPI] Sina suggest error: {e}")
        return None

class AnnouncementAPI:
    """抓取上交所和深交所公告"""
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        }

    def fetch_szse_org_id(self, code: str):
        """巨潮资讯需要 orgId"""
        url = "http://www.cninfo.com.cn/new/information/topSearch/query"
        try:
            r = requests.post(url, headers=self.headers, data={"keyWord": code}, timeout=10)
            data = r.json()
            if data and isinstance(data, list) and len(data) > 0:
                return data[0].get("orgId")
        except Exception as e:
            print(f"[AnnouncementAPI] SZSE orgId fetch error: {e}")
        return None

    def fetch_szse_announcements(self, code: str, page: int = 1, limit: int = 10):
        """深交所（巨潮资讯）"""
        org_id = self.fetch_szse_org_id(code)
        stock_param = f"{code},{org_id}" if org_id else code
        
        url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
        headers = self.headers.copy()
        headers["Referer"] = "http://www.cninfo.com.cn/new/index"
        data = {
            "stock": stock_param,
            "pageNum": page,
            "pageSize": limit,
            "tabName": "fulltext",
            "column": "szse"
        }
        results = []
        try:
            r = requests.post(url, headers=headers, data=data, timeout=10)
            json_data = r.json()
            anns = json_data.get("announcements", [])
            for a in anns:
                if not a:
                    continue
                # adjunctUrl looks like finalpage/2026-04-14/1225098089.PDF
                pdf_url = f"http://static.cninfo.com.cn/{a['adjunctUrl']}" if a.get('adjunctUrl') else ""
                
                # cninfo time is sometimes timestamp in ms
                pub_time = a.get("announcementTime")
                time_str = ""
                if pub_time:
                    time_str = datetime.fromtimestamp(pub_time/1000).strftime("%Y-%m-%d %H:%M:%S")

                results.append({
                    "id": f"szse_{a['announcementId']}",
                    "code": code,
                    "title": a.get("announcementTitle", ""),
                    "time": time_str,
                    "url": pdf_url,
                    "exchange": "SZSE"
                })
        except Exception as e:
            print(f"[AnnouncementAPI] SZSE fetch error: {e}")
        return results

    def fetch_sse_announcements(self, code: str, page: int = 1, limit: int = 10):
        """上交所"""
        # 上交所通常查询需要指定日期范围
        end_date = datetime.now().strftime("%Y-%m-%d")
        url = f"https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do?jsonCallBack=jsonpCallback&isPagination=true&pageHelp.pageSize={limit}&pageHelp.cacheSize=1&START_DATE=2020-01-01&END_DATE={end_date}&SECURITY_CODE={code}&TITLE=&BULLETIN_TYPE=&stockType=&pageHelp.pageNo={page}&pageHelp.beginPage={page}&pageHelp.endPage={page}&_={int(time.time()*1000)}"
        headers = self.headers.copy()
        headers["Referer"] = "https://www.sse.com.cn/"
        results = []
        try:
            r = requests.get(url, headers=headers, timeout=10)
            text = r.text
            match = re.search(r'jsonpCallback\((.*)\)', text)
            if match:
                json_str = match.group(1)
                data = json.loads(json_str)
                # data['pageHelp']['data'] is a list of lists?? or list of dicts?
                # Sometimes it is list of lists
                items = data.get("pageHelp", {}).get("data", [])
                
                # flatten if needed
                flat_items = []
                for x in items:
                    if isinstance(x, list):
                        flat_items.extend(x)
                    else:
                        flat_items.append(x)
                        
                for a in flat_items:
                    # SSE URL is like /disclosure/listedinfo/announcement/c/new/2026-04-10/603986_20260410_13H6.pdf
                    url_path = a.get("URL", "")
                    full_url = f"http://static.sse.com.cn{url_path}" if url_path else ""
                    
                    results.append({
                        "id": f"sse_{a.get('ORG_BULLETIN_ID', '')}",
                        "code": code,
                        "title": a.get("TITLE", ""),
                        "time": a.get("SSEDATE", "") + " 00:00:00", # SSE mostly provides date only
                        "url": full_url,
                        "exchange": "SSE"
                    })
        except Exception as e:
            print(f"[AnnouncementAPI] SSE fetch error: {e}")
        return results

    def fetch_latest_announcements(self, keyword: str, page: int = 1, limit: int = 10):
        """入口方法：自动识别代码/名称并抓取最新公告"""
        # 1. 解析代码
        code = keyword
        exchange = None
        if not re.match(r'^\d{6}$', keyword):
            # 不是纯数字，调用新浪接口搜索
            suggest = SinaSuggestClient.search(keyword)
            if suggest:
                code = suggest['code']
                if suggest['full_code'].startswith('sh'):
                    exchange = 'SSE'
                elif suggest['full_code'].startswith('sz'):
                    exchange = 'SZSE'
        else:
            # 纯数字，通过首字母判断
            if code.startswith('6') or code.startswith('9'): # 9 for some B shares/Kechuang
                exchange = 'SSE'
            else:
                exchange = 'SZSE'
                
        # 如果新浪接口没返回明确的交易所，通过前缀兜底
        if not exchange:
            if code.startswith('6') or code.startswith('9'):
                exchange = 'SSE'
            else:
                exchange = 'SZSE'
                
        print(f"[AnnouncementAPI] 识别目标: {keyword} -> {code} ({exchange})")
        
        # 2. 根据交易所拉取数据
        if exchange == 'SSE':
            return self.fetch_sse_announcements(code, page, limit)
        else:
            return self.fetch_szse_announcements(code, page, limit)

# 测试代码
if __name__ == "__main__":
    api = AnnouncementAPI()
    print("上交所测试 (603986):")
    res = api.fetch_latest_announcements("603986", limit=2)
    for r in res:
        print(r)
    
    print("\n深交所测试 (比亚迪):")
    res = api.fetch_latest_announcements("比亚迪", limit=2)
    for r in res:
        print(r)
