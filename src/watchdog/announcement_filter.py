import os
import sys
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

class AnnouncementDecoder:
    def __init__(self, llm_engine):
        self.llm_engine = llm_engine

    def _fetch_pdf_text_via_jina(self, url: str) -> str:
        if not url:
            return ""
        try:
            print(f"[公告解码器] 正在通过 Jina 解析 PDF: {url}")
            resp = requests.get(f"https://r.jina.ai/{url}", timeout=45)
            if resp.status_code == 200:
                text = resp.text
                if len(text) > 100:
                    return text[:20000] # 截断防止超出大模型上下文
        except Exception as e:
            print(f"[公告解码器] Jina PDF 解析失败: {e}")
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
            print(f"[公告解码器] LLM 分析失败: {e}")
            return "解析失败", "⚪ 中性"

    def decode_announcement(self, ann: dict):
        """处理单条公告，返回解析结果"""
        print(f"[公告解码器] 开始深度解读公告: {ann['title']} ({ann['code']})")
        
        content = ""
        if ann.get("url"):
            content = self._fetch_pdf_text_via_jina(ann["url"])

        summary, sentiment = self._llm_analyze_announcement(ann["title"], content)
        
        ann["ai_summary"] = summary
        ann["sentiment"] = sentiment
        return ann
