import os
import sys
import json
import requests
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.config import BASE_DIR

DATA_DIR = os.path.join(BASE_DIR, "data")
PROCESSED_ANNOUNCEMENTS_FILE = os.path.join(DATA_DIR, "processed_announcements.json")
WATCHED_STOCKS_FILE = os.path.join(DATA_DIR, "watched_stocks.json")

class AnnouncementFilter:
    def __init__(self, llm_engine):
        self.llm_engine = llm_engine
        self._ensure_data_dir()
        self._processed_ids = set()
        self._init_dedup_pool()
        self.watched_stocks = self._load_watched_stocks()

    def _ensure_data_dir(self):
        os.makedirs(DATA_DIR, exist_ok=True)

    def _init_dedup_pool(self):
        """恢复去重池"""
        if os.path.exists(PROCESSED_ANNOUNCEMENTS_FILE):
            try:
                with open(PROCESSED_ANNOUNCEMENTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        self._processed_ids.add(item.get("id"))
            except Exception:
                pass

    def _save_processed_id(self, ann_item: dict, summary: str, sentiment: str):
        """保存已处理的公告"""
        data = []
        if os.path.exists(PROCESSED_ANNOUNCEMENTS_FILE):
            try:
                with open(PROCESSED_ANNOUNCEMENTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
        
        record = {
            "id": ann_item["id"],
            "code": ann_item["code"],
            "title": ann_item["title"],
            "time": ann_item["time"],
            "url": ann_item["url"],
            "summary": summary,
            "sentiment": sentiment,
            "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        data.append(record)
        try:
            # 只保留最近 500 条记录防止文件过大
            with open(PROCESSED_ANNOUNCEMENTS_FILE, "w", encoding="utf-8") as f:
                json.dump(data[-500:], f, ensure_ascii=False, indent=2)
            self._processed_ids.add(ann_item["id"])
        except Exception as e:
            print(f"[公告过滤器] 保存记录失败: {e}")

    # ===== 监控列表管理 =====
    def _load_watched_stocks(self):
        if not os.path.exists(WATCHED_STOCKS_FILE):
            return {}
        try:
            with open(WATCHED_STOCKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_watched_stocks(self):
        try:
            with open(WATCHED_STOCKS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.watched_stocks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[公告过滤器] 保存监控列表失败: {e}")

    def add_watch(self, code: str, name: str):
        self.watched_stocks[code] = {
            "name": name,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self._save_watched_stocks()

    def remove_watch(self, keyword: str) -> bool:
        """支持通过代码或名称移除"""
        to_remove = None
        for code, info in self.watched_stocks.items():
            if code == keyword or info["name"] == keyword:
                to_remove = code
                break
        if to_remove:
            del self.watched_stocks[to_remove]
            self._save_watched_stocks()
            return True
        return False

    def get_all_watches(self):
        return self.watched_stocks

    # ===== 处理核心逻辑 =====
    def _fetch_pdf_text_via_jina(self, url: str) -> str:
        if not url:
            return ""
        try:
            print(f"[公告过滤器] 正在通过 Jina 解析 PDF: {url}")
            headers = {"Accept": "application/json"} # Using JSON might be cleaner or just get text
            # Actually Jina Reader directly returns Markdown for GET requests
            resp = requests.get(f"https://r.jina.ai/{url}", timeout=45)
            if resp.status_code == 200:
                text = resp.text
                if len(text) > 100:
                    return text[:20000] # 截断防止超出大模型上下文
        except Exception as e:
            print(f"[公告过滤器] Jina PDF 解析失败: {e}")
        return ""

    def _llm_analyze_announcement(self, title: str, content: str):
        """让大模型提取摘要并判断情绪"""
        if not self.llm_engine or not self.llm_engine.api_ready:
            return "（大模型未连接，无法解读）", "⚪ 中性"

        prompt = f"""你是一位资深的A股证券分析师。请仔细阅读以下上市公司公告内容，并为投资者提供极速解读。

公告标题：{title}
公告正文/节选：
{content[:8000]}

请按以下严格格式输出：
【情绪判断】：从 (🔴 利好 / 🟢 利空 / ⚪ 中性) 中选择一个，只输出这几个字，不加解释。
【核心要点】：用3条短句列举核心数据或事实。
【潜台词/影响】：用1句话总结该公告对公司基本面或短期股价的真实潜在影响。"""

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

            # 提取情绪和正文
            import re
            result_text = re.sub(r'<think>.*?</think>', '', result_text, flags=re.DOTALL).strip()
            
            sentiment = "⚪ 中性"
            if "利好" in result_text.split('\n')[0]:
                sentiment = "🔴 利好"
            elif "利空" in result_text.split('\n')[0]:
                sentiment = "🟢 利空"
                
            return result_text, sentiment
        except Exception as e:
            print(f"[公告过滤器] LLM 分析失败: {e}")
            return "解析失败", "⚪ 中性"

    def process_new_announcement(self, ann: dict):
        """处理单条新公告，如果是新的则返回解析结果，否则返回 None"""
        if ann["id"] in self._processed_ids:
            return None

        print(f"[公告过滤器] 发现新公告: {ann['title']} ({ann['code']})")
        
        content = ""
        if ann["url"]:
            content = self._fetch_pdf_text_via_jina(ann["url"])

        summary, sentiment = self._llm_analyze_announcement(ann["title"], content)
        
        self._save_processed_id(ann, summary, sentiment)
        
        ann["ai_summary"] = summary
        ann["sentiment"] = sentiment
        return ann
