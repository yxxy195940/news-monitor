"""
AnnouncementMonitor — 公司公告后台监控与分析模块

职责：
1. 管理监控股票列表（持久化到 data/ann_watchlist.json）
2. 后台轮询巨潮资讯，自动发现新公告
3. 下载 PDF，优先用 pdfplumber 解析，失败后降级到 Jina Reader
4. 调用大模型（deepseek-reasoner 优先）生成情绪判断与核心摘要
5. 将结果持久化到 data/ann_processed.json（按股票代码分组）
6. 提供 get_digest_by_company() 方法，供 Telegram 指令读取
"""

import os
import sys
import json
import re
import io
import requests
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.config import BASE_DIR

DATA_DIR = os.path.join(BASE_DIR, "data")
WATCHLIST_FILE = os.path.join(DATA_DIR, "ann_watchlist.json")
PROCESSED_FILE = os.path.join(DATA_DIR, "ann_processed.json")


class AnnouncementMonitor:
    """公告监控器：后台轮询 + 解析存储 + 按公司摘要读取"""

    def __init__(self, llm_engine, announcement_api):
        self.llm_engine = llm_engine
        self.announcement_api = announcement_api
        os.makedirs(DATA_DIR, exist_ok=True)
        self._watchlist = self._load_json(WATCHLIST_FILE, {})
        # {code: [{"id":..., "title":..., "time":..., "url":..., "sentiment":..., "ai_summary":..., "analyzed_at":...}]}
        self._processed = self._load_json(PROCESSED_FILE, {})

    # ─────────────────────────────────────────────
    # 工具方法
    # ─────────────────────────────────────────────
    def _load_json(self, path: str, default):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return default

    def _save_json(self, path: str, data):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[公告监控器] 写入失败 {path}: {e}")

    # ─────────────────────────────────────────────
    # 监控列表管理
    # ─────────────────────────────────────────────
    def add_watch(self, keyword: str) -> dict | None:
        """添加监控，返回解析到的股票信息"""
        stock = self.announcement_api.resolve_stock(keyword)
        if not stock:
            return None
        self._watchlist[stock["code"]] = {
            "name": stock["name"],
            "orgId": stock.get("orgId"),
            "exchange": stock.get("exchange"),
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_json(WATCHLIST_FILE, self._watchlist)
        return stock

    def remove_watch(self, keyword: str) -> str | None:
        """移除监控，支持名称或代码，返回被移除的公司名"""
        keyword = keyword.strip()
        target_code = None
        for code, info in self._watchlist.items():
            if code == keyword or info.get("name") == keyword:
                target_code = code
                break
        if target_code:
            name = self._watchlist[target_code].get("name", target_code)
            del self._watchlist[target_code]
            self._save_json(WATCHLIST_FILE, self._watchlist)
            return name
        return None

    def get_watchlist(self) -> dict:
        return self._watchlist

    # ─────────────────────────────────────────────
    # PDF 解析
    # ─────────────────────────────────────────────
    def _parse_pdf_bytes(self, pdf_bytes: bytes) -> str:
        """优先 pdfplumber，失败降级空字符串"""
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                pages_text = []
                for page in pdf.pages[:30]:  # 最多读30页，避免超长
                    text = page.extract_text()
                    if text:
                        pages_text.append(text)
                return "\n".join(pages_text)
        except ImportError:
            print("[公告监控器] pdfplumber 未安装，跳过本地解析")
        except Exception as e:
            print(f"[公告监控器] pdfplumber 解析失败: {e}")
        return ""

    def _fetch_pdf_via_jina(self, url: str) -> str:
        """降级方案：通过 Jina Reader 读取 PDF"""
        if not url:
            return ""
        try:
            print(f"[公告监控器] Jina 降级解析: {url}")
            resp = requests.get(f"https://r.jina.ai/{url}", timeout=45)
            if resp.status_code == 200 and len(resp.text) > 100:
                return resp.text[:20000]
        except Exception as e:
            print(f"[公告监控器] Jina 解析失败: {e}")
        return ""

    def _extract_pdf_text(self, ann: dict) -> str:
        """提取公告 PDF 文本（pdfplumber 优先，Jina 兜底）"""
        url = ann.get("url", "")
        if not url:
            return ""

        # 1. 下载 PDF 字节
        pdf_bytes = self.announcement_api.download_pdf_bytes(url)
        if pdf_bytes:
            text = self._parse_pdf_bytes(pdf_bytes)
            if text and len(text) > 100:
                print(f"[公告监控器] pdfplumber 解析成功，长度={len(text)}")
                return text[:20000]

        # 2. pdfplumber 失败，降级 Jina
        return self._fetch_pdf_via_jina(url)

    # ─────────────────────────────────────────────
    # LLM 分析
    # ─────────────────────────────────────────────
    def _llm_analyze(self, title: str, content: str) -> tuple[str, str]:
        """调用大模型，返回 (ai_summary, sentiment)"""
        if not self.llm_engine or not self.llm_engine.api_ready:
            return "（大模型未连接，无法解读）", "⚪ 中性"

        prompt = f"""你是一位资深的A股证券分析师。请仔细阅读以下上市公司公告内容，并为投资者提供极速解读。

公告标题：{title}
公告正文/节选：
{content[:8000] if content else "（PDF解析失败，请根据标题判断）"}

请按以下严格格式输出：
【情绪判断】：从 (🔴 利好 / 🟢 利空 / ⚪ 中性) 中选择一个，只输出这几个字，不加解释。
【核心要点】：用3条短句列举核心数据或事实。
【潜台词/影响】：用1句话总结该公告对公司基本面或短期股价的真实潜在影响。"""

        try:
            result_text = ""
            if self.llm_engine.provider == "deepseek":
                response = self.llm_engine.deepseek_client.chat.completions.create(
                    model="deepseek-reasoner",
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

            # 过滤 <think> 标签
            result_text = re.sub(r'<think>.*?</think>', '', result_text, flags=re.DOTALL).strip()

            sentiment = "⚪ 中性"
            first_line = result_text.split('\n')[0] if result_text else ""
            if "利好" in first_line:
                sentiment = "🔴 利好"
            elif "利空" in first_line:
                sentiment = "🟢 利空"

            return result_text, sentiment
        except Exception as e:
            print(f"[公告监控器] LLM 分析失败: {e}")
            return "解析失败", "⚪ 中性"

    # ─────────────────────────────────────────────
    # 核心轮询
    # ─────────────────────────────────────────────
    def _get_processed_ids(self, code: str) -> set:
        return {r["id"] for r in self._processed.get(code, [])}

    def _save_result(self, code: str, record: dict):
        if code not in self._processed:
            self._processed[code] = []
        self._processed[code].append(record)
        # 每只股票最多保留 50 条记录
        self._processed[code] = self._processed[code][-50:]
        self._save_json(PROCESSED_FILE, self._processed)

    def poll_all_watched(self) -> int:
        """
        轮询所有监控股票，对新公告执行「下载→解析→LLM分析→存储」。
        返回本次新处理的公告数量。
        """
        total_new = 0
        for code, info in list(self._watchlist.items()):
            stock_info = {
                "code": code,
                "name": info.get("name", code),
                "orgId": info.get("orgId"),
                "exchange": info.get("exchange", "szse"),
            }
            try:
                anns = self.announcement_api.fetch_announcements(stock_info, page=1, limit=10)
            except Exception as e:
                print(f"[公告监控器] 拉取 {code} 失败: {e}")
                continue

            known_ids = self._get_processed_ids(code)
            for ann in anns:
                if ann["id"] in known_ids:
                    continue

                print(f"[公告监控器] 🔔 发现新公告: [{code}] {ann['title']}")
                content = self._extract_pdf_text(ann)
                ai_summary, sentiment = self._llm_analyze(ann["title"], content)

                record = {
                    "id": ann["id"],
                    "title": ann["title"],
                    "time": ann["time"],
                    "url": ann.get("url", ""),
                    "sentiment": sentiment,
                    "ai_summary": ai_summary,
                    "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                self._save_result(code, record)
                total_new += 1

        print(f"[公告监控器] 本轮结束，共新增 {total_new} 条公告分析记录。")
        return total_new

    # ─────────────────────────────────────────────
    # 摘要查询（供 /anndigest 使用）
    # ─────────────────────────────────────────────
    def get_digest_by_company(self) -> dict:
        """
        返回所有监控公司的已分析结果，格式：
        {code: {"name": str, "records": [...]}}
        """
        result = {}
        for code, info in self._watchlist.items():
            records = self._processed.get(code, [])
            result[code] = {
                "name": info.get("name", code),
                "records": records,
            }
        return result

    def clear_digest(self, code: str = None):
        """清除已读摘要，code=None 时清除全部"""
        if code:
            self._processed.pop(code, None)
        else:
            self._processed = {}
        self._save_json(PROCESSED_FILE, self._processed)

    # ─────────────────────────────────────────────
    # 按需解读（保持兼容旧 /ann 命令）
    # ─────────────────────────────────────────────
    def decode_announcement(self, ann: dict) -> dict:
        """处理单条公告，返回含 ai_summary 和 sentiment 的结果（供按需 /ann 命令使用）"""
        print(f"[公告监控器] 按需解读: {ann['title']}")
        content = self._extract_pdf_text(ann)
        ai_summary, sentiment = self._llm_analyze(ann["title"], content)
        ann["ai_summary"] = ai_summary
        ann["sentiment"] = sentiment
        return ann

    # ─────────────────────────────────────────────
    # 向后兼容旧 AnnouncementDecoder 属性名
    # ─────────────────────────────────────────────
    @property
    def announcement_decoder(self):
        return self
