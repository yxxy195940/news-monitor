from pydantic import BaseModel
from typing import List, Optional

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
