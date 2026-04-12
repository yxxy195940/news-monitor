import os
import sys
import re
import google.generativeai as genai
from openai import OpenAI

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.config import GEMINI_API_KEY, MINIMAX_API_KEY, DEEPSEEK_API_KEY, LLM_PROVIDER
from src.rag.vector_store import VectorStore
from src.models import ProcessedNews

class RAGEngine:
    def __init__(self):
        self.provider = LLM_PROVIDER
        self.api_ready = False
        
        if self.provider == "gemini":
            if GEMINI_API_KEY and "修改为你" not in GEMINI_API_KEY:
                genai.configure(api_key=GEMINI_API_KEY)
                self.gemini_model = genai.GenerativeModel('gemini-1.5-flash')
                self.api_ready = True
        elif self.provider == "minimax":
            if MINIMAX_API_KEY and "修改为你" not in MINIMAX_API_KEY:
                self.minimax_client = OpenAI(
                    api_key=MINIMAX_API_KEY,
                    base_url="https://api.minimaxi.com/v1"
                )
                self.api_ready = True
        elif self.provider == "deepseek":
            if DEEPSEEK_API_KEY and "修改为你" not in DEEPSEEK_API_KEY:
                self.deepseek_client = OpenAI(
                    api_key=DEEPSEEK_API_KEY,
                    base_url="https://api.deepseek.com"
                )
                self.api_ready = True
                
        if not self.api_ready:
            print(f"[警告] {self.provider} 的 API Key 缺失，RAG 将运行在沙盒模拟模式。")
            
        self.vector_store = VectorStore()

    async def generate_tutor_response(self, user_question: str, related_news: ProcessedNews):
        """async generator: 逐块 yield token，支持 Telegram 流式打字动画"""
        
        # 1. 向量混合检索（提问 + 抓取到的经济学热词）
        search_query = f"{user_question} {' '.join(related_news.key_financial_terms)}"
        print(f"[RAG] 正在向底层 ChromaDB 检索相关教材知识点: {search_query}")
        
        results = self.vector_store.search(search_query, n_results=4)
        
        retrieved_contexts = []
        sources = []
        if results and results.get('documents') and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                retrieved_contexts.append(doc)
                sources.append(results['metadatas'][0][i]['source'])
        
        context_text = "\n\n---\n\n".join(retrieved_contexts)
        unique_sources = ", ".join(list(set(sources))) if sources else "基础教材库"

        # 2. 组装最核心的神级 Prompt
        prompt = f"""
        你是一位幽默、有同理心、深悟人性的底层理财辅导教师。你的目标不是喊单，而是授人以渔。
        
        【当前新闻背景】:
        标题：{related_news.title}
        摘要：{related_news.original_summary}
        
        【用户请求】：
        用户是一个刚刚入市的小白，他在看了上面的新闻后，提出了以下疑问：
        "{user_question}"
        
        【防幻觉强制约束 —— 这是你的武器库】：
        为了防止你给出危险的建议，你必须**严格基于以下检索到的我们独家的《金融经典教材库》片段来回答他的底层逻辑**：
        >>>
        {context_text}
        <<<
        
        【输出格式要求】：
        1. 像朋友聊微信一样，稍微活泼一些。
        2. 一定要把由于新闻引起的疑惑，和刚刚给你的【武器库里的理论】紧密连接起来解释。
        3. 在文本的最后，另起一行，以引用的格式输出这句话："📚 课代表小贴士：今天咱们讲的底层逻辑，出自你的枕边书《{unique_sources}》哦！"
        """

        # 3. 流式生成
        if not self.api_ready:
            yield f"(模拟模式)\n嗨！你这个问题说得极对。结合新闻大暴跌，就像咱们教案里说的一样...\n\n📚 课代表小贴士：今天咱们讲的底层逻辑，出自你的枕边书《{unique_sources}》哦！"
            return
            
        try:
            print(f"[RAG] 知识整合完毕，正在请求 {self.provider} 模型流式生成答复...")

            if self.provider == "gemini":
                # Gemini 模拟流式：按50字分段
                response = self.gemini_model.generate_content(prompt)
                full = re.sub(r'<think>.*?</think>', '', response.text, flags=re.DOTALL).strip()
                for i in range(0, len(full), 50):
                    yield full[i:i+50]
                return

            # DeepSeek / MiniMax 原生 stream
            if self.provider == "deepseek":
                model_name, client = "deepseek-chat", self.deepseek_client
            else:
                model_name, client = "MiniMax-M2.7-highspeed", self.minimax_client

            stream = client.chat.completions.create(
                model=model_name,
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
                        # 检测 <think> 起始
                        if accumulated.endswith("<think>"):
                            in_think = True
                            # 回退：把 <think> 从已 yield 的内容中去除（直接截掉缓冲即可，前面已 yield 出去了）
                            accumulated = accumulated[:-7]
                        else:
                            yield ch
                    else:
                        think_buf += ch
                        if think_buf.endswith("</think>"):
                            in_think = False
                            think_buf = ""

        except Exception as e:
            yield f"抱歉，你的专属金融导师系统打盹了，API 网络发生异常: {e}"


    async def evaluate_classic_book(self, source_filename: str):
        """异步 generator: 逐块 yield token，支持 Telegram 流式打字"""
        print(f"[RAG] 正在向底层提取《{source_filename}》的内核...")
        
        results = self.vector_store.search_within_book("核心思想 投资理念 方法论 总结 交易体系 实战原则", source_filename, n_results=6)
        
        retrieved_contexts = []
        if results and results.get('documents') and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                retrieved_contexts.append(f"【实体切片 {i+1}】\n{doc}")
                
        if not retrieved_contexts:
            yield f"未能在库中靶向提取到《{source_filename}》的核心特征区块。可能该书体量太小未能命中关键字。"
            return
            
        context_text = "\n\n---\n\n".join(retrieved_contexts)
        
        prompt = f"""
        你是一位顶尖的宏观交易员与金融导师。
        
        【目标】：对本地外挂库中提取到的实体扫描资料《{source_filename}》进行核心价值评判和浓缩总结。
        
        【防幻觉强制证明要求！！！】：
        为了证明你确确实实读取了下方我给你截取的"本地物理切片碎片"，你 **必须** 严格采用 markdown blockquote `> ` 的格式，从下方的文本中，**一字不落地摘抄 1~2 句**最具代表性的金句，原封不动地展示给用户作为证明。
        
        【实体碎片化截取原文】：
        >>>
        {context_text}
        <<<
        
        【撰写格式】：
        1. 🌟 **该书灵魂内核** (提炼上面片段中流露出的最核心的思想，2-3句话)
        2. 📖 **物理切片金句指缝提取** (这里是你必须从碎片中一字不漏复制的名言金句原话证明)
        3. ⚔️ **大模型客观评价** (如果不考虑名人光环，基于近期的宏观环境，上述体系思想放到今天是否还适用？给你自由发挥的评价空间)
        """
        
        if not self.api_ready:
            yield "模拟模式下，大模型拒绝进行深层评价。"
            return
            
        try:
            print(f"[RAG] 开始启动 {self.provider} 对《{source_filename}》流式提炼评价...")

            if self.provider == "gemini":
                response = self.gemini_model.generate_content(prompt)
                full = re.sub(r'<think>.*?</think>', '', response.text, flags=re.DOTALL).strip()
                for i in range(0, len(full), 50):
                    yield full[i:i+50]
                return

            if self.provider == "deepseek":
                model_name, client = "deepseek-chat", self.deepseek_client
            else:
                model_name, client = "MiniMax-M2.7-highspeed", self.minimax_client

            stream = client.chat.completions.create(
                model=model_name,
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
        except Exception as e:
            yield f"大模型底层评读引擎失联: {e}"


if __name__ == "__main__":
    engine = RAGEngine()
    mock_news = ProcessedNews(
        title="Tech stocks plunge as inflation data comes in hotter than expected.",
        original_summary="The CPI inflation data spooked markets...",
        ai_one_sentence_summary="通胀这下真下不来了，科技股被吓坏了。",
        key_financial_terms=["通货膨胀", "科技股"],
        sentiment="消极"
    )
    user_q = "科技股大跌是因为通胀吗？"
    import asyncio
    async def test():
        async for tok in engine.generate_tutor_response(user_q, mock_news):
            print(tok, end="", flush=True)
    asyncio.run(test())
