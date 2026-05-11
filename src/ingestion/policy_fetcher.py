"""
PolicyFetcher — 宏观政策新闻采集模块

数据源（按风险由低到高）：
1. 三大证券报（中证网/证券时报/上海证券报）- HTML/RSS，风险最低
2. 国务院 gov.cn                           - RSS，风险低
3. 证监会 CSRC                            - HTML 新闻列表，风险中
4. 央行 PBOC                              - HTML 新闻列表，风险中
5. 财政部 MOF                             - HTML 新闻列表，风险中
6. 发改委 NDRC                            - HTML 新闻列表，风险中
7. 金融监管总局 NFRA                      - HTML 新闻列表，风险中

反爬策略：
- 随机 User-Agent
- 每个站点最小间隔 60 秒（轮询层控制）
- 只抓列表页标题+链接，不爬正文
- 出错时静默跳过，不中断整体流程
"""

import re
import time
import random
import hashlib
import requests
from datetime import datetime
from xml.etree import ElementTree


# ─── User-Agent 池 ──────────────────────────────────────────────────────────

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


def _rand_headers(referer: str = "") -> dict:
    h = {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        h["Referer"] = referer
    return h


def _get(url: str, referer: str = "", timeout: int = 12) -> requests.Response | None:
    try:
        r = requests.get(url, headers=_rand_headers(referer), timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"[PolicyFetcher] 请求失败 {url}: {e}")
        return None


def _item(title: str, url: str, source: str, pub_time: str = "") -> dict:
    """标准化单条新闻对象"""
    return {
        "id": hashlib.md5((url or title).encode()).hexdigest()[:12],
        "title": title.strip(),
        "url": url.strip(),
        "source": source,
        "pub_time": pub_time or datetime.now().strftime("%Y-%m-%d"),
    }


# ─── RSS 通用解析器 ──────────────────────────────────────────────────────────

def _parse_rss(url: str, source: str, limit: int = 20) -> list[dict]:
    r = _get(url)
    if not r:
        return []
    try:
        root = ElementTree.fromstring(r.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = []

        # Standard RSS 2.0
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "")[:10]
            if title and link:
                items.append(_item(title, link, source, pub))
                if len(items) >= limit:
                    break

        # Atom feed
        if not items:
            for entry in root.findall("atom:entry", ns) or root.findall("{http://www.w3.org/2005/Atom}entry"):
                title_el = entry.find("{http://www.w3.org/2005/Atom}title")
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                pub_el = entry.find("{http://www.w3.org/2005/Atom}updated")
                title = (title_el.text if title_el is not None else "").strip()
                link = (link_el.get("href", "") if link_el is not None else "").strip()
                pub = (pub_el.text[:10] if pub_el is not None else "")
                if title and link:
                    items.append(_item(title, link, source, pub))
                    if len(items) >= limit:
                        break

        print(f"[PolicyFetcher] {source} RSS 获取 {len(items)} 条")
        return items
    except Exception as e:
        print(f"[PolicyFetcher] {source} RSS 解析失败: {e}")
        return []


# ─── HTML 正则解析工具 ──────────────────────────────────────────────────────

def _extract_links(html: str, pattern: str, base_url: str, source: str, limit: int = 20) -> list[dict]:
    """通用 HTML 链接提取，pattern 需包含两个分组：(url, title)"""
    items = []
    for m in re.finditer(pattern, html, re.DOTALL):
        try:
            raw_url = m.group(1).strip()
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if not title:
                continue
            full_url = raw_url if raw_url.startswith("http") else base_url + raw_url
            items.append(_item(title, full_url, source))
            if len(items) >= limit:
                break
        except Exception:
            continue
    print(f"[PolicyFetcher] {source} HTML 获取 {len(items)} 条")
    return items


# ────────────────────────────────────────────────────────────────────────────
# 各数据源采集函数
# ────────────────────────────────────────────────────────────────────────────

def fetch_gov_cn(limit: int = 20) -> list[dict]:
    """国务院 gov.cn — 最新政策 RSS"""
    # gov.cn 提供 RSS：http://www.gov.cn/zhengce/rss.htm
    return _parse_rss("http://www.gov.cn/zhengce/rss.htm", "国务院 gov.cn", limit)


def fetch_csrc(limit: int = 20) -> list[dict]:
    """证监会 CSRC — 新闻发布页"""
    url = "https://www.csrc.gov.cn/csrc/c100028/zfxxgk_zdgk.shtml"
    r = _get(url, referer="https://www.csrc.gov.cn/")
    if not r:
        return []
    pattern = r'href="(/csrc/c\d+/[^"]+\.shtml)"[^>]*>\s*<[^>]+>([^<]{5,})</[^>]+>'
    return _extract_links(r.text, pattern, "https://www.csrc.gov.cn", "证监会 CSRC", limit)


def fetch_pboc(limit: int = 20) -> list[dict]:
    """央行 PBOC — 政策及新闻页"""
    url = "http://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html"
    r = _get(url, referer="http://www.pbc.gov.cn/")
    if not r:
        return []
    r.encoding = "utf-8"
    pattern = r'href="(/goutongjiaoliu/[^"]+\.html)"[^>]*>([^<]{5,})</a>'
    return _extract_links(r.text, pattern, "http://www.pbc.gov.cn", "央行 PBOC", limit)


def fetch_mof(limit: int = 20) -> list[dict]:
    """财政部 MOF — 新闻资讯页"""
    url = "https://www.mof.gov.cn/zhengwuxinxi/xinwenlianbo/"
    r = _get(url, referer="https://www.mof.gov.cn/")
    if not r:
        return []
    r.encoding = "utf-8"
    pattern = r'href="(/zhengwuxinxi/xinwenlianbo/[^"]+\.htm)"[^>]*>([^<]{5,})</a>'
    return _extract_links(r.text, pattern, "https://www.mof.gov.cn", "财政部 MOF", limit)


def fetch_ndrc(limit: int = 20) -> list[dict]:
    """发改委 NDRC — 新闻动态页"""
    url = "https://www.ndrc.gov.cn/xwdt/xwfb/"
    r = _get(url, referer="https://www.ndrc.gov.cn/")
    if not r:
        return []
    r.encoding = "utf-8"
    pattern = r'href="(/xwdt/xwfb/\d{6}/[^"]+\.html)"[^>]*>\s*([^<]{5,})\s*</a>'
    return _extract_links(r.text, pattern, "https://www.ndrc.gov.cn", "发改委 NDRC", limit)


def fetch_nfra(limit: int = 20) -> list[dict]:
    """金融监管总局 NFRA — 新闻发布页"""
    url = "https://www.nfra.gov.cn/nfra/xinwenfabu/index.html"
    r = _get(url, referer="https://www.nfra.gov.cn/")
    if not r:
        return []
    r.encoding = "utf-8"
    pattern = r'href="(/nfra/xinwenfabu/[^"]+\.html)"[^>]*>([^<]{5,})</a>'
    return _extract_links(r.text, pattern, "https://www.nfra.gov.cn", "金融监管总局 NFRA", limit)


def fetch_cs_news(limit: int = 20) -> list[dict]:
    """中证网 cs.com.cn — 证券头条 RSS（备用：抓新闻列表页）"""
    # 中证网提供 RSS
    items = _parse_rss("https://www.cs.com.cn/xwzx/rss/ssgs.xml", "中证网 CS", limit)
    if items:
        return items
    # 降级：抓 HTML 列表
    r = _get("https://www.cs.com.cn/xwzx/hsyw/", referer="https://www.cs.com.cn/")
    if not r:
        return []
    r.encoding = "gb2312"
    pattern = r'href="(https://www\.cs\.com\.cn/xwzx/[^"]+\.html)"[^>]*>([^<]{5,})</a>'
    return _extract_links(r.text, pattern, "https://www.cs.com.cn", "中证网 CS", limit)


def fetch_stcn(limit: int = 20) -> list[dict]:
    """证券时报 stcn.com — 要闻 RSS"""
    items = _parse_rss("https://www.stcn.com/rss/index.html", "证券时报 STCN", limit)
    if items:
        return items
    # 降级：抓快讯列表
    r = _get("https://www.stcn.com/kuaixun/", referer="https://www.stcn.com/")
    if not r:
        return []
    r.encoding = "utf-8"
    pattern = r'href="(https://www\.stcn\.com/[^"]+\.html)"[^>]*>\s*<[^>]*>([^<]{8,})</[^>]+'
    return _extract_links(r.text, pattern, "", "证券时报 STCN", limit)


def fetch_cnstock(limit: int = 20) -> list[dict]:
    """上海证券报 cnstock.com — 新闻列表页"""
    url = "https://www.cnstock.com/v_news/sns_bkd/"
    r = _get(url, referer="https://www.cnstock.com/")
    if not r:
        return []
    r.encoding = "utf-8"
    pattern = r'href="(https://www\.cnstock\.com/[^"]+\.html)"[^>]*>([^<]{5,})</a>'
    return _extract_links(r.text, pattern, "", "上海证券报 CNSTOCK", limit)


# ─── 汇总入口 ────────────────────────────────────────────────────────────────

SOURCES = [
    ("gov_cn",   fetch_gov_cn),
    ("cs_news",  fetch_cs_news),
    ("stcn",     fetch_stcn),
    ("cnstock",  fetch_cnstock),
    ("csrc",     fetch_csrc),
    ("pboc",     fetch_pboc),
    ("mof",      fetch_mof),
    ("ndrc",     fetch_ndrc),
    ("nfra",     fetch_nfra),
]


class PolicyFetcher:
    """统一调度所有政策信息源，带间隔控制"""

    def fetch_all(self, limit_per_source: int = 20) -> list[dict]:
        """
        采集所有数据源，每个来源之间随机间隔 1~3 秒。
        返回去标记后的合并列表，字段：id, title, url, source, pub_time
        """
        all_items = []
        for name, func in SOURCES:
            try:
                items = func(limit=limit_per_source)
                all_items.extend(items)
            except Exception as e:
                print(f"[PolicyFetcher] {name} 采集异常: {e}")
            # 每个数据源之间短暂休眠，避免被WAF识别为爬虫
            time.sleep(random.uniform(1.0, 2.5))
        print(f"[PolicyFetcher] 本轮共采集 {len(all_items)} 条原始政策新闻")
        return all_items


# ─── 测试入口 ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    fetcher = PolicyFetcher()
    results = fetcher.fetch_all(limit_per_source=5)
    for item in results:
        print(f"[{item['source']}] {item['title']} → {item['url'][:60]}")
