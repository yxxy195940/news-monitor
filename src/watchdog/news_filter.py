"""
新闻过滤器：对每条新闻/快讯进行关键字匹配，命中后深度爬取+LLM精炼+持久化。
"""
import os
import sys
import json
import re
import requests
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.config import BASE_DIR

DATA_DIR = os.path.join(BASE_DIR, "data")
MATCHED_NEWS_FILE = os.path.join(DATA_DIR, "matched_news.json")


class NewsFilter:
    def __init__(self, keyword_manager, llm_engine):
        self.keyword_manager = keyword_manager
        self.llm_engine = llm_engine
        self._ensure_data_dir()
        # 防止同一条新闻重复入库的去重池
        self._processed_urls = set()
        self._processed_titles = set()
        # 加载已有的匹配记录来初始化去重池
        self._init_dedup_pool()

    def _ensure_data_dir(self):
        os.makedirs(DATA_DIR, exist_ok=True)

    def _init_dedup_pool(self):
        """从已有存储中恢复去重池"""
        existing = self._load_matched()
        for item in existing:
            if item.get("url"):
                self._processed_urls.add(item["url"])
            if item.get("title"):
                self._processed_titles.add(item["title"])

    def _load_matched(self) -> list:
        """加载已匹配的新闻列表"""
        if not os.path.exists(MATCHED_NEWS_FILE):
            return []
        try:
            with open(MATCHED_NEWS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("matched", [])
        except Exception:
            return []

    def _save_matched(self, matched_list: list):
        """保存匹配新闻列表"""
        try:
            with open(MATCHED_NEWS_FILE, "w", encoding="utf-8") as f:
                json.dump({"matched": matched_list}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[新闻过滤器] 保存匹配新闻失败: {e}")

    def _append_matched(self, item: dict):
        """追加一条匹配新闻"""
        matched_list = self._load_matched()
        matched_list.append(item)
        self._save_matched(matched_list)

    def get_all_matched(self) -> list:
        """获取所有已匹配的新闻"""
        return self._load_matched()

    def clear_matched(self):
        """清空已匹配的新闻（整理发送后调用）"""
        self._save_matched([])
        self._processed_urls.clear()
        self._processed_titles.clear()
        print("[新闻过滤器] 已清空匹配新闻池。")

    def _deep_crawl(self, url: str) -> str:
        """深度爬取新闻全文"""
        if not url or "mock-url" in url:
            return ""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
            }
            resp = requests.get(f"https://r.jina.ai/{url}", headers=headers, timeout=25)
            if resp.status_code == 200 and len(resp.text) > 100:
                return resp.text[:15000]
        except Exception as e:
            print(f"[新闻过滤器] 深度爬取失败: {e}")
        return ""

    def _llm_refine(self, title: str, content: str) -> str:
        """使用大模型精炼新闻摘要"""
        if not self.llm_engine or not self.llm_engine.api_ready:
            return content[:300] if content else title

        prompt = f"""你是一位专业的金融新闻编辑。请阅读以下新闻，提炼出 100-200 字的精华摘要。
要求：
1. 保留核心事件、关键数据、影响判断
2. 语言精炼，不说废话
3. 必须是中文

新闻标题：{title}
新闻内容：{content[:8000]}

请直接输出摘要，不要有任何前缀或标记。"""

        try:
            result_text = ""
            if self.llm_engine.provider == "deepseek":
                response = self.llm_engine.deepseek_client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "user", "content": prompt}],
                )
                result_text = response.choices[0].message.content

            elif self.llm_engine.provider == "minimax":
                response = self.llm_engine.minimax_client.chat.completions.create(
                    model="MiniMax-M2.7-highspeed",
                    messages=[{"role": "user", "content": prompt}],
                )
                result_text = response.choices[0].message.content

            elif self.llm_engine.provider == "gemini":
                response = self.llm_engine.gemini_model.generate_content(prompt)
                result_text = response.text

            # 清洗 think 标签
            result_text = re.sub(r'<think>.*?</think>', '', result_text, flags=re.DOTALL).strip()
            return result_text if result_text else (content[:300] if content else title)

        except Exception as e:
            print(f"[新闻过滤器] LLM 精炼失败: {e}")
            return content[:300] if content else title

    def check_and_process_news(self, news_item: dict) -> dict | None:
        """
        检查一条新闻是否匹配监控关键字。
        如果匹配：深度爬取 → LLM精炼 → 持久化保存 → 返回匹配信息。
        如果不匹配：返回 None。
        
        Args:
            news_item: 新闻字典，需包含 title, content/url/publishedAt 等字段
        Returns:
            匹配信息字典 或 None
        """
        title = news_item.get("title", "")
        content = news_item.get("content", "")
        url = news_item.get("url", "")

        # 去重检查
        if url and url in self._processed_urls:
            return None
        if title and title in self._processed_titles:
            return None

        # 关键字匹配（同时匹配标题和内容摘要）
        match_text = f"{title} {content}"
        watch_name, matched_keyword = self.keyword_manager.find_matching_watch(match_text)

        if not watch_name:
            return None

        print(f"[新闻过滤器] 🎯 命中！关键字组[{watch_name}] 关键字[{matched_keyword}] → {title[:40]}...")

        # 深度爬取全文
        full_content = content
        if url:
            crawled = self._deep_crawl(url)
            if crawled:
                full_content = crawled

        # LLM 精炼
        summary = self._llm_refine(title, full_content)

        # 获取时间
        time_str = news_item.get("publishedAt", "")
        if not time_str:
            ts = news_item.get("timestamp", 0)
            if ts:
                try:
                    from datetime import datetime as dt
                    time_str = dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    time_str = ""

        # 判断来源类型
        source_type = "flash" if news_item.get("id") and not url else "news"

        # 组装匹配记录
        matched_record = {
            "watch_name": watch_name,
            "matched_keyword": matched_keyword,
            "title": title,
            "summary": summary,
            "url": url,
            "source_type": source_type,
            "time": time_str,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        # 持久化
        self._append_matched(matched_record)

        # 加入去重池
        if url:
            self._processed_urls.add(url)
        if title:
            self._processed_titles.add(title)

        return matched_record

    def check_and_process_flash(self, flash_item: dict) -> dict | None:
        """
        检查一条快讯是否匹配监控关键字。
        快讯没有 URL，不需要深度爬取，直接 LLM 精炼。

        Args:
            flash_item: 快讯字典，需包含 id, title, content, timestamp 等字段
        Returns:
            匹配信息字典 或 None
        """
        content = flash_item.get("content", "")
        title = flash_item.get("title", "")
        flash_id = flash_item.get("id", "")

        # 去重
        dedup_key = f"flash_{flash_id}"
        if dedup_key in self._processed_titles:
            return None

        # 关键字匹配
        match_text = f"{title} {content}"
        watch_name, matched_keyword = self.keyword_manager.find_matching_watch(match_text)

        if not watch_name:
            return None

        print(f"[新闻过滤器] ⚡ 快讯命中！关键字组[{watch_name}] 关键字[{matched_keyword}] → {title[:30]}...")

        # 快讯不深度爬取，直接精炼内容
        summary = self._llm_refine(title, content)

        # 获取时间
        ts = flash_item.get("timestamp", 0)
        time_str = ""
        if ts:
            try:
                from datetime import datetime as dt
                time_str = dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        matched_record = {
            "watch_name": watch_name,
            "matched_keyword": matched_keyword,
            "title": title,
            "summary": summary,
            "url": "",
            "source_type": "flash",
            "time": time_str,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        self._append_matched(matched_record)
        self._processed_titles.add(dedup_key)

        return matched_record
