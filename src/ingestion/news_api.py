import os
import sys
import requests
import datetime
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.config import GNEWS_API_KEY

class NewsFetcher:
    def __init__(self):
        self.api_key = GNEWS_API_KEY
        self.base_url = "https://gnews.io/api/v4"
        
        # 本地级内存数据库 2500条滚动大盘长新闻缓存
        self.global_news_cache = []
        self.global_news_urls = set()
        
        # 专门用于系统后台定时推送的防御性防重复池（针对长新闻）
        self.seen_background_urls = set()
        
        # 专门用于 30s 快讯频道防重复机制（依赖快讯 ID）
        self.seen_flash_ids = set()

    def initialize_global_news(self):
        """[启动灌库专供] 从新浪滚动新闻拉取最近的 50 页巨量新闻组装到本地缓存"""
        print("🌍 [NewsAPI] 正在爆破抓取全球滚动新闻底座数据 (最高50页，请耐心等待)...")
        items_fetched = 0
        for page in range(1, 51):
            url = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=50&page={page}"
            try:
                r = requests.get(url, timeout=10)
                if r.status_code != 200:
                    continue
                    
                data = r.json()
                items = data.get("result", {}).get("data", [])
                if not items:
                    break
                    
                for item in items:
                    article_url = item.get("url", "")
                    if article_url and article_url not in self.global_news_urls:
                        ts = float(item.get("ctime", 0))
                        try:
                            date_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
                            
                        news_obj = {
                            "title": item.get("title", ""),
                            "content": item.get("intro", ""),
                            "url": article_url,
                            "image": "",
                            "publishedAt": date_str,
                            "timestamp": ts
                        }
                        
                        self.global_news_cache.append(news_obj)
                        self.global_news_urls.add(article_url)
                        self.seen_background_urls.add(article_url) # 初始化抓取的默认为不推送
                        items_fetched += 1
                        
            except Exception as e:
                print(f"[NewsAPI] 第 {page} 页抓取遭遇异常跳过: {e}")
                continue
                
        # 确保按时间降序排序（最新的在前面）
        self.global_news_cache.sort(key=lambda x: x["timestamp"], reverse=True)
        print(f"✅ [NewsAPI] 灌库完毕！成功在内存中重建 {len(self.global_news_cache)} 条全球滚动序列。")


    def fetch_background_news(self) -> list:
        """[专供后台定时任务] 从新浪滚动新闻拉取最新 第1页，将增量写入缓存库，并返回增量的前10条"""
        url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=50&page=1"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                print(f"[NewsAPI] 后台请求新浪新闻中心失败: {r.status_code}")
                return []
                
            data = r.json()
            items = data.get("result", {}).get("data", [])
            new_arrivals = []
            
            for item in items:
                article_url = item.get("url", "")
                if article_url and article_url not in self.global_news_urls:
                    ts = float(item.get("ctime", 0))
                    try:
                        date_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
                        
                    news_obj = {
                        "title": item.get("title", ""),
                        "content": item.get("intro", ""),
                        "url": article_url,
                        "image": "",
                        "publishedAt": date_str,
                        "timestamp": ts
                    }
                    
                    new_arrivals.append(news_obj)
                    self.global_news_urls.add(article_url)
            
            if new_arrivals:
                # 倒叙排保证最前面的绝对是最新的
                new_arrivals.sort(key=lambda x: x["timestamp"], reverse=True)
                # 注入头部的缓存库
                self.global_news_cache = new_arrivals + self.global_news_cache
                
            # 我们只把没推送过的新鲜货挑出来（最多选前10条给广播），避免半小时后如果没有新事态也瞎推送
            broadcast_news = []
            for item in new_arrivals:
                url = item['url']
                if url not in self.seen_background_urls:
                    broadcast_news.append(item)
                    self.seen_background_urls.add(url)
                    if len(broadcast_news) >= 10:
                        break
                        
            return broadcast_news
        except Exception as e:
            print(f"[NewsAPI] 后台网络拦截: {e}")
            return []

    def fetch_flash_lives(self) -> list:
        """[高频快讯专供] 每 30 秒一次从新浪财经全网通道拉取最新的 Flash Content"""
        url = "https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=10&zhibo_id=152"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        try:
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code != 200:
                return []
                
            data = r.json()
            items = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
            fresh_flashes = []
            
            for item in reversed(items):
                item_id = str(item.get("id"))
                if item_id and item_id not in self.seen_flash_ids:
                    content = item.get("rich_text", "")
                    title = content[:30] + "..." if len(content) > 30 else content
                    
                    time_str = item.get("create_time", "")
                    try:
                        ts = time.mktime(time.strptime(time_str, "%Y-%m-%d %H:%M:%S"))
                    except Exception:
                        ts = 0
                        
                    fresh_flashes.append({
                        "id": item_id,
                        "title": title,
                        "content": content,
                        "timestamp": ts 
                    })
                    self.seen_flash_ids.add(item_id)
            return fresh_flashes
        except Exception:
            return []

    def fetch_flash_list(self, page: int = 1, limit: int = 10) -> list:
        """[用户主动查询专供] 绕过缓存，实打实从网络进行动态分页获取快讯列表"""
        url = f"https://zhibo.sina.com.cn/api/zhibo/feed?page={page}&page_size={limit}&zhibo_id=152"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        try:
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code != 200:
                return []
            data = r.json()
            items = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
            
            results = []
            for item in items:
                item_id = str(item.get("id"))
                content = item.get("rich_text", "")
                title = content[:30] + "..." if len(content) > 30 else content
                
                time_str = item.get("create_time", "")
                try:
                    ts = time.mktime(time.strptime(time_str, "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    ts = 0
                    
                results.append({
                    "id": item_id,
                    "title": title,
                    "content": content,
                    "timestamp": ts 
                })
            return results
        except Exception as e:
            print(f"[NewsAPI 快讯抓取错误] {e}")
            return []

    def fetch_search_list(self, query: str = None, limit: int = 10) -> list:
        """[专供用户主动查询] 彻底断网，纯基于内存检索的毫秒级长篇新闻引擎"""
        results = []
        for item in self.global_news_cache:
            if query:
                # 不区分大小写的搜索
                q_lower = query.lower()
                title_lower = item.get("title", "").lower()
                content_lower = item.get("content", "").lower()
                
                if q_lower not in title_lower and q_lower not in content_lower:
                    continue
            
            results.append(item)
            if len(results) >= limit:
                break
                
        return results

    def _get_mock_news(self, background: bool = False) -> list:
        """兜底数据"""
        mock_data = [
            {
                "title": "Nvidia briefly surpasses Microsoft as most valuable company driven by AI hype.",
                "description": "Nvidia has taken the top spot in market capitalization...",
                "publishedAt": "2026-04-09T10:00:00Z",
                "content": "Nvidia's market cap reached new highs crossing 3 Trillion dollars...",
                "url": "https://example.com/mock-url-1",
                "image": "https://images.unsplash.com/photo-1611162617474-5b21e879e113"
            },
            {
                "title": "Federal Reserve holds interest rates steady, sees just one cut this year .",
                "description": "The Fed did exactly what markets feared.",
                "publishedAt": "2026-04-08T15:30:00Z",
                "content": "The Federal Reserve met expectations by leaving its benchmark...",
                "url": "https://example.com/mock-url-2",
                "image": "https://images.unsplash.com/photo-1611162617474-5b21e879e113"
            }
        ]
        
        if not background:
            return mock_data
            
        for mock in mock_data:
            if mock["url"] not in self.seen_background_urls:
                self.seen_background_urls.add(mock["url"])
                return [mock]
        return []
