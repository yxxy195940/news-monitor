"""
PolicyFilter — 政策新闻去重、行业归因、LLM 情绪分析模块

工作流：
1. 收到来自 PolicyFetcher 的原始标题列表
2. 按 URL ID 去重（持久化到 data/policy_seen.json）
3. 关键词粗筛：命中行业词库 → 标记板块（未命中记为"宏观"）
4. LLM 分析：判断利好/利空，生成一句话摘要（仅命中板块或带有重大关键词时触发）
5. 结果持久化到 data/policy_digest.json（按时间倒排，最多保留 500 条）
6. 重大政策（如降准/降息/印花税）立即标记，由轮询器上层决定是否推送
"""

import os
import sys
import re
import json
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.config import BASE_DIR

DATA_DIR = os.path.join(BASE_DIR, "data")
SEEN_FILE = os.path.join(DATA_DIR, "policy_seen.json")
DIGEST_FILE = os.path.join(DATA_DIR, "policy_digest.json")


# ─── 行业板块关键词词库 ──────────────────────────────────────────────────────

SECTOR_KEYWORDS: dict[str, list[str]] = {
    "新能源": ["光伏", "风电", "储能", "新能源", "充电桩", "绿电", "碳中和", "双碳", "电动车", "氢能"],
    "房地产": ["房地产", "楼市", "限购", "房贷", "公积金", "保障性住房", "白名单", "商品房", "地产", "住建"],
    "银行/金融": ["降准", "降息", "LPR", "存款利率", "社融", "M2", "信贷", "存款准备金", "利率", "流动性"],
    "科技/半导体": ["芯片", "半导体", "集成电路", "人工智能", "大模型", "科创板", "算力", "数据中心", "信创"],
    "消费": ["消费券", "以旧换新", "促消费", "零售", "餐饮", "旅游", "免税", "内需", "家电补贴"],
    "医药/医疗": ["医保", "集采", "药品", "医疗器械", "创新药", "医院", "卫健委", "仿制药", "生物医药"],
    "军工/国防": ["军工", "军费", "国防", "航空发动机", "船舶", "武器", "军事", "国防预算"],
    "大宗商品": ["铁矿石", "铜价", "原油", "煤炭", "钢铁", "有色金属", "黄金", "铝", "镍", "期货"],
    "交通/基建": ["高铁", "公路", "港口", "机场", "基础设施", "特别国债", "专项债", "城投"],
    "农业": ["粮食", "农业", "化肥", "种子", "收储", "农村", "乡村振兴"],
    "资本市场": ["印花税", "融资融券", "IPO", "退市", "股票回购", "减持", "证监会新规", "分红"],
}

# 重大关键词 — 命中时触发立即推送
MAJOR_KEYWORDS = [
    "降准", "降息", "存款准备金", "印花税", "LPR下调",
    "国务院常务会议", "重磅", "重大政策", "特别国债",
    "系统性风险", "暂停", "叫停", "紧急"
]


class PolicyFilter:
    """政策新闻过滤与分析器"""

    def __init__(self, llm_engine):
        self.llm_engine = llm_engine
        os.makedirs(DATA_DIR, exist_ok=True)
        self._seen_ids: set = self._load_json(SEEN_FILE, [])
        if isinstance(self._seen_ids, list):
            self._seen_ids = set(self._seen_ids)
        self._digest: list[dict] = self._load_json(DIGEST_FILE, [])

    # ─── 持久化工具 ─────────────────────────────────────────────────────────

    def _load_json(self, path: str, default):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return default

    def _save_seen(self):
        try:
            with open(SEEN_FILE, "w", encoding="utf-8") as f:
                # 只保留最近 5000 条 ID 防止文件无限增长
                json.dump(list(self._seen_ids)[-5000:], f)
        except Exception as e:
            print(f"[PolicyFilter] 保存去重记录失败: {e}")

    def _save_digest(self):
        try:
            with open(DIGEST_FILE, "w", encoding="utf-8") as f:
                json.dump(self._digest[-500:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PolicyFilter] 保存摘要失败: {e}")

    # ─── 行业归因 ────────────────────────────────────────────────────────────

    def classify_sectors(self, title: str) -> list[str]:
        """返回命中的所有板块标签列表，未命中则返回空列表"""
        sectors = []
        for sector, keywords in SECTOR_KEYWORDS.items():
            if any(kw in title for kw in keywords):
                sectors.append(sector)
        return sectors

    def is_major(self, title: str) -> bool:
        """判断是否为重大政策（触发立即推送）"""
        return any(kw in title for kw in MAJOR_KEYWORDS)

    # ─── LLM 分析 ────────────────────────────────────────────────────────────

    def _llm_analyze(self, title: str, sectors: list[str]) -> tuple[str, str]:
        """
        调用大模型，返回 (sentiment, one_line_summary)。
        sentiment: '🔴 利好' / '🟢 利空' / '⚪ 中性'
        """
        if not self.llm_engine or not self.llm_engine.api_ready:
            return "⚪ 中性", ""

        sector_str = "、".join(sectors) if sectors else "宏观整体"
        prompt = f"""你是A股资深宏观策略分析师。请根据以下政策新闻标题，判断其对中国A股市场（尤其是【{sector_str}】板块）的影响。

政策新闻标题：「{title}」

请按以下格式严格输出，不要输出其他内容：
【情绪】：从 (🔴 利好 / 🟢 利空 / ⚪ 中性) 中选一个
【一句话解读】：用一句话（30字以内）点明该政策对上述板块的核心影响"""

        try:
            result_text = ""
            if self.llm_engine.provider == "deepseek":
                resp = self.llm_engine.deepseek_client.chat.completions.create(
                    model="deepseek-chat",  # 政策快分析用普通模型节省成本
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=150,
                )
                result_text = resp.choices[0].message.content
            elif self.llm_engine.provider == "minimax":
                resp = self.llm_engine.minimax_client.chat.completions.create(
                    model="MiniMax-M2.7-highspeed",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=150,
                )
                result_text = resp.choices[0].message.content
            elif self.llm_engine.provider == "gemini":
                resp = self.llm_engine.gemini_model.generate_content(prompt)
                result_text = resp.text

            # 解析输出
            result_text = re.sub(r"<think>.*?</think>", "", result_text, flags=re.DOTALL).strip()
            sentiment = "⚪ 中性"
            summary = ""

            m_sentiment = re.search(r"【情绪】[：:]\s*([🔴🟢⚪].*?)[\n\r]", result_text)
            if m_sentiment:
                s = m_sentiment.group(1).strip()
                if "利好" in s:
                    sentiment = "🔴 利好"
                elif "利空" in s:
                    sentiment = "🟢 利空"

            m_summary = re.search(r"【一句话解读】[：:]\s*(.+)", result_text)
            if m_summary:
                summary = m_summary.group(1).strip()

            return sentiment, summary

        except Exception as e:
            print(f"[PolicyFilter] LLM 分析失败: {e}")
            return "⚪ 中性", ""

    # ─── 核心处理流程 ────────────────────────────────────────────────────────

    def process_batch(self, raw_items: list[dict]) -> list[dict]:
        """
        处理一批原始新闻，返回本次新增且完成分析的记录列表。
        每条记录格式：
        {id, title, url, source, pub_time, sectors, sentiment, summary, is_major, analyzed_at}
        """
        new_records = []

        for item in raw_items:
            item_id = item.get("id", "")
            title = item.get("title", "")

            # 去重
            if item_id in self._seen_ids:
                continue
            if not title or len(title) < 5:
                continue

            self._seen_ids.add(item_id)

            sectors = self.classify_sectors(title)
            major = self.is_major(title)

            # 只有命中板块词库 或 属于重大关键词，才触发 LLM（节省 Token）
            sentiment = "⚪ 中性"
            summary = ""
            if sectors or major:
                sentiment, summary = self._llm_analyze(title, sectors)

            record = {
                "id": item_id,
                "title": title,
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "pub_time": item.get("pub_time", ""),
                "sectors": sectors,
                "sentiment": sentiment,
                "summary": summary,
                "is_major": major,
                "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._digest.append(record)
            new_records.append(record)

        if new_records:
            self._save_seen()
            self._save_digest()
            print(f"[PolicyFilter] 本批新增 {len(new_records)} 条政策记录（含LLM分析）")

        return new_records

    # ─── 查询接口 ────────────────────────────────────────────────────────────

    def get_latest(self, sector: str = None, limit: int = 20) -> list[dict]:
        """
        返回最新的 N 条记录，可按板块过滤。
        sector=None 时返回全量；sector='新能源' 时只返回该板块记录。
        """
        data = list(reversed(self._digest))  # 最新在前
        if sector:
            data = [r for r in data if sector in r.get("sectors", [])]
        return data[:limit]

    def get_all_sectors(self) -> list[str]:
        return list(SECTOR_KEYWORDS.keys())


if __name__ == "__main__":
    # 简单测试
    from src.ingestion.policy_fetcher import PolicyFetcher
    from src.analyzer.llm_engine import LLMEngine

    llm = LLMEngine()
    fetcher = PolicyFetcher()
    pf = PolicyFilter(llm)

    raw = fetcher.fetch_all(limit_per_source=5)
    new_recs = pf.process_batch(raw)
    for r in new_recs:
        print(f"{r['sentiment']} [{','.join(r['sectors']) or '宏观'}] {r['title']}")
        if r['summary']:
            print(f"  → {r['summary']}")
