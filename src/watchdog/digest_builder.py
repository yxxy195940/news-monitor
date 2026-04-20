"""
摘要整理器：按关键字分组，调用 LLM 统一整理，生成结构化推送内容。
"""
import os
import sys
import re
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class DigestBuilder:
    def __init__(self, news_filter, llm_engine):
        self.news_filter = news_filter
        self.llm_engine = llm_engine

    def get_digest_metadata(self):
        """
        返回整理需要的元数据和分组列表
        """
        matched_list = self.news_filter.get_all_matched()
        
        if not matched_list:
            return {"empty": True}

        # 按 watch_name 分组
        groups = defaultdict(list)
        for item in matched_list:
            groups[item["watch_name"]].append(item)

        return {
            "empty": False,
            "total_count": len(matched_list),
            "group_count": len(groups),
            "groups": groups
        }

    async def stream_group(self, watch_name, items):
        """
        对单个分组进行 LLM 整理的生成器
        """
        # 构建这一组的所有新闻摘要提交给 LLM
        news_for_llm = []
        for i, item in enumerate(items):
            entry = f"[{i+1}] 时间：{item.get('time', '未知')}\n标题：{item['title']}\n摘要：{item['summary']}"
            if item.get("url"):
                entry += f"\n链接：{item['url']}"
            entry += f"\n来源类型：{'📰 新闻' if item['source_type'] == 'news' else '⚡ 快讯'}"
            news_for_llm.append(entry)

        all_news_text = "\n\n".join(news_for_llm)

        # 如果只有 1-2 条，直接格式化输出，不需要浪费 LLM 额度
        if len(items) <= 2:
            for item in items:
                source_icon = "📰" if item["source_type"] == "news" else "⚡"
                time_str = item.get("time", "")
                time_display = f"🕒 {time_str}" if time_str else ""
                
                yield f"{source_icon} **{item['title']}**\n"
                if time_display:
                    yield f"{time_display}\n"
                yield f"{item['summary']}\n"
                if item.get("url"):
                    yield f"🔗 [原文链接]({item['url']})\n"
                yield "\n"
            return

        # 多条新闻时，调用 LLM 统一整理
        prompt = f"""你是专业金融新闻编辑。请将以下关于"{watch_name}"的 {len(items)} 条新闻/快讯进行统一整理。

要求：
1. 按时间线串联事件脉络，提炼出核心动态
2. 每条新闻保留：时间、核心要点（1-2句话）
3. 如果是新闻（有链接的），在末尾注明原文链接
4. 如果多条新闻讲的是同一件事的不同阶段，请合并为一个条目
5. 结尾给出一句话总结当前该主题的整体态势
6. 使用中文，格式清晰

原始新闻数据：
{all_news_text}

请直接输出整理结果，不要有任何前缀说明。"""

        if not self.llm_engine or not self.llm_engine.api_ready:
            # 降级：直接格式化输出
            for item in items:
                source_icon = "📰" if item["source_type"] == "news" else "⚡"
                time_str = item.get("time", "")
                time_display = f"🕒 {time_str}" if time_str else ""
                
                yield f"{source_icon} **{item['title']}**\n"
                if time_display:
                    yield f"{time_display}\n"
                yield f"{item['summary']}\n"
                if item.get("url"):
                    yield f"🔗 [原文链接]({item['url']})\n"
                yield "\n"
            return

        try:
            if self.llm_engine.provider == "deepseek":
                stream = self.llm_engine.deepseek_client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "user", "content": prompt}],
                    stream=True
                )
                in_think = False
                think_buf = ""
                accumulated = ""
                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    for ch in delta:
                        if not in_think:
                            accumulated += ch
                            if accumulated.endswith("<think>"):
                                in_think = True
                                accumulated = accumulated[:-7]
                            else:
                                yield ch
                        else:
                            think_buf += ch
                            if think_buf.endswith("</think>"):
                                in_think = False
                                think_buf = ""

            elif self.llm_engine.provider == "minimax":
                stream = self.llm_engine.minimax_client.chat.completions.create(
                    model="MiniMax-M2.7-highspeed",
                    messages=[{"role": "user", "content": prompt}],
                    stream=True
                )
                in_think = False
                think_buf = ""
                accumulated = ""
                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    for ch in delta:
                        if not in_think:
                            accumulated += ch
                            if accumulated.endswith("<think>"):
                                in_think = True
                                accumulated = accumulated[:-7]
                            else:
                                yield ch
                        else:
                            think_buf += ch
                            if think_buf.endswith("</think>"):
                                in_think = False
                                think_buf = ""

            elif self.llm_engine.provider == "gemini":
                response = self.llm_engine.gemini_model.generate_content(prompt)
                full = re.sub(r'<think>.*?</think>', '', response.text, flags=re.DOTALL).strip()
                for i in range(0, len(full), 50):
                    yield full[i:i+50]

        except Exception as e:
            yield f"\n⚠️ LLM 整理失败: {e}\n"
            # 降级输出
            for item in items:
                source_icon = "📰" if item["source_type"] == "news" else "⚡"
                yield f"{source_icon} {item.get('time', '')} | {item['title']}\n"
