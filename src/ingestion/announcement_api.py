import requests
import time
import json
import re
import io
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
    """
    统一使用巨潮资讯（cninfo.com.cn）作为数据源，
    同时支持上交所（sse）和深交所（szse）上市公司。
    """
    CNINFO_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
    CNINFO_SEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "http://www.cninfo.com.cn/new/index",
        }
        # 缓存：code -> {name, orgId, exchange}
        self._resolve_cache = {}

    def resolve_stock(self, keyword: str) -> dict | None:
        """
        将关键字（名称或代码）解析为完整的股票信息。
        返回 {code, name, orgId, exchange} 或 None。
        """
        keyword = keyword.strip()
        if keyword in self._resolve_cache:
            return self._resolve_cache[keyword]

        code = keyword
        name = keyword
        exchange = None

        # 如果不是纯6位数字，先通过新浪接口反查代码
        if not re.match(r'^\d{6}$', keyword):
            suggest = SinaSuggestClient.search(keyword)
            if suggest:
                code = suggest['code']
                name = suggest['name']
                full_code = suggest['full_code']
                if full_code.startswith('sh'):
                    exchange = 'sse'
                elif full_code.startswith('sz'):
                    exchange = 'szse'
            else:
                print(f"[AnnouncementAPI] 未能识别关键字: {keyword}")
                return None

        # 纯数字代码，通过前缀判断交易所
        if not exchange:
            if code.startswith('6') or code.startswith('9'):
                exchange = 'sse'
            else:
                exchange = 'szse'

        # 查询 orgId（巨潮查询必需）
        org_id = self._fetch_org_id(code)

        result = {
            "code": code,
            "name": name,
            "orgId": org_id,
            "exchange": exchange,
        }
        # 缓存结果（同时缓存原始关键字和代码）
        self._resolve_cache[keyword] = result
        self._resolve_cache[code] = result
        print(f"[AnnouncementAPI] 解析成功: {keyword} -> {code} ({exchange}) orgId={org_id}")
        return result

    def _fetch_org_id(self, code: str) -> str | None:
        """从巨潮查询股票的 orgId"""
        try:
            r = requests.post(
                self.CNINFO_SEARCH_URL,
                headers=self.headers,
                data={"keyWord": code},
                timeout=10
            )
            data = r.json()
            if data and isinstance(data, list) and len(data) > 0:
                return data[0].get("orgId")
        except Exception as e:
            print(f"[AnnouncementAPI] orgId fetch error for {code}: {e}")
        return None

    def fetch_announcements(self, stock_info: dict, page: int = 1, limit: int = 10) -> list[dict]:
        """
        从巨潮拉取指定股票的公告列表。
        stock_info 为 resolve_stock() 的返回值。
        """
        code = stock_info["code"]
        org_id = stock_info.get("orgId")
        exchange = stock_info.get("exchange", "szse")
        stock_param = f"{code},{org_id}" if org_id else code

        data = {
            "stock": stock_param,
            "pageNum": page,
            "pageSize": limit,
            "tabName": "fulltext",
            "column": exchange,  # "sse" 或 "szse"
        }

        results = []
        try:
            r = requests.post(self.CNINFO_QUERY_URL, headers=self.headers, data=data, timeout=15)
            json_data = r.json()
            anns = json_data.get("announcements", [])
            for a in anns:
                if not a:
                    continue
                pdf_url = (
                    f"http://static.cninfo.com.cn/{a['adjunctUrl']}"
                    if a.get('adjunctUrl') else ""
                )
                pub_time = a.get("announcementTime")
                time_str = ""
                if pub_time:
                    time_str = datetime.fromtimestamp(pub_time / 1000).strftime("%Y-%m-%d %H:%M:%S")

                results.append({
                    "id": f"cninfo_{a['announcementId']}",
                    "code": code,
                    "name": stock_info.get("name", code),
                    "title": a.get("announcementTitle", ""),
                    "time": time_str,
                    "url": pdf_url,
                    "exchange": exchange.upper(),
                })
        except Exception as e:
            print(f"[AnnouncementAPI] 公告列表拉取失败 ({code}): {e}")
        return results

    def download_pdf_bytes(self, url: str) -> bytes | None:
        """下载公告 PDF，返回原始字节流"""
        if not url:
            return None
        try:
            r = requests.get(url, headers=self.headers, timeout=30)
            if r.status_code == 200 and len(r.content) > 1000:
                return r.content
        except Exception as e:
            print(f"[AnnouncementAPI] PDF下载失败 ({url}): {e}")
        return None

    def fetch_latest_announcements(self, keyword: str, page: int = 1, limit: int = 10) -> list[dict]:
        """
        对外统一入口：支持 /ann 命令按名称/代码临时查询。
        """
        stock_info = self.resolve_stock(keyword)
        if not stock_info:
            return []
        return self.fetch_announcements(stock_info, page, limit)


# 测试代码
if __name__ == "__main__":
    api = AnnouncementAPI()
    print("=== 深交所测试（比亚迪）===")
    res = api.fetch_latest_announcements("比亚迪", limit=3)
    for r in res:
        print(r)

    print("\n=== 上交所测试（603986）===")
    res = api.fetch_latest_announcements("603986", limit=3)
    for r in res:
        print(r)
