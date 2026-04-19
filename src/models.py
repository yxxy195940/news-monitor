from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class ProcessedNews(BaseModel):
    """
    通过 Pydantic 定义核心数据结构。
    确保大模型返回给我们的信息是高度结构化的，绝对不能是一段随意聊天的长文本，
    因为这将被存储到关系型数据库中，并传导给 UI 层。
    """
    title: str
    original_summary: str
    ai_one_sentence_summary: str
    key_financial_terms: List[str]  # 被大模型提取出来的专业词汇锚点
    sentiment: str                  # 情绪标签: 积极, 消极, 中性
    fetch_warning: Optional[str] = None  # 可选：爬虫报警，如果存在则显示在 UI 提示用户重试


class MatchedNews(BaseModel):
    """
    关键字监控命中的新闻/快讯，经大模型精炼后持久化保存。
    """
    watch_name: str           # 所属的监控组名称（用户设置的关键字/股票名称）
    matched_keyword: str      # 实际命中的关键字
    title: str                # 新闻/快讯标题
    summary: str              # LLM 精炼后的摘要
    url: Optional[str] = ""   # 原文链接（快讯可能没有）
    source_type: str = "news" # 来源类型: news / flash
    time: str = ""            # 新闻/快讯的原始发布时间
    saved_at: str = ""        # 保存时间戳

