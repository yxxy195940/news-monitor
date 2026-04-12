import os
import sys
import json
import re
import requests
import google.generativeai as genai
from openai import OpenAI

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.config import GEMINI_API_KEY, LLM_PROVIDER, MINIMAX_API_KEY, DEEPSEEK_API_KEY
from src.models import ProcessedNews

class LLMEngine:
    def __init__(self):
        self.provider = LLM_PROVIDER
        self.api_ready = False
        
        if self.provider == "gemini":
            if not GEMINI_API_KEY or "修改为你" in GEMINI_API_KEY:
                print("[警告] 当前配置为 gemini 但未提供有效的 GEMINI_API_KEY。")
            else:
                genai.configure(api_key=GEMINI_API_KEY)
                self.gemini_model = genai.GenerativeModel(
                    'gemini-1.5-flash', 
                    generation_config={"response_mime_type": "application/json"}
                )
                self.api_ready = True
                print("[大脑初始化] 已挂载 Google Gemini 引擎。")
                
        elif self.provider == "minimax":
            if not MINIMAX_API_KEY or "修改为你" in MINIMAX_API_KEY:
                print("[警告] 当前配置为 minimax 但未提供有效的 MINIMAX_API_KEY。")
            else:
                self.minimax_client = OpenAI(
                    api_key=MINIMAX_API_KEY,
                    base_url="https://api.minimaxi.com/v1"
                )
                self.api_ready = True
                print("[大脑初始化] 已挂载 MiniMax (abab) 引擎。")
                
        elif self.provider == "deepseek":
            if not DEEPSEEK_API_KEY or "修改为你" in DEEPSEEK_API_KEY:
                print("[警告] 当前配置为 deepseek 但未提供有效的 DEEPSEEK_API_KEY。")
            else:
                # DeepSeek 同样完美兼容 OpenAI SDK，且模型性价比极高，编程推理极强
                self.deepseek_client = OpenAI(
                    api_key=DEEPSEEK_API_KEY,
                    base_url="https://api.deepseek.com" # v1 后缀在较新 SDK 中自动处理或直接写 url 根
                )
                self.api_ready = True
                print("[大脑初始化] 已挂载 DeepSeek 引擎。")

    def process_news(self, raw_title: str, raw_content: str, url: str = None) -> ProcessedNews:
        """大模型智能处理引擎：将生涩冗长的新闻提取为带锚点的中文化结构卡片"""
        
        # 深潜抓取增强 (Web Scraping via LLM Reader)
        full_content = raw_content
        fetch_warning = None
        if url and "mock-url" not in url:
            print(f"[大模型调度] 正在试图深潜阅读原文全文: {url}")
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
                }
                # 借助专为 LLM 清洗排版的公有阅读器接口获取纯净 markdown，放宽超时限制至25秒
                resp = requests.get(f"https://r.jina.ai/{url}", headers=headers, timeout=25)
                if resp.status_code == 200 and len(resp.text) > 100:
                    # 获取网页完整正文，截断至 1.5万 字以内，防止 Token 爆炸
                    full_content = f"【由爬虫提取的新闻完整正文】\n{resp.text[:15000]}"
                    print("[大模型调度] ✓ 全文提取成功！")
                else:
                    # 状态码异常也识别为框被
                    fetch_warning = f"全文网络请求异常 (HTTP {resp.status_code})，已自动降级为简介模式。"
                    print(f"[大模型调度] ✗ {fetch_warning}")
            except Exception as e:
                err_type = type(e).__name__
                fetch_warning = f"远程全文爬虫失联 ({err_type})，已退化回简介模式。如需全文解读，可点击重试按钒。"
                print(f"[大模型调度] ✗ 全文提取超时或被拦截，优雅降级回简介模式 ({e})。")
                pass
        
        prompt = f"""
        你是一个在华尔街实战多年，且深谙如何教导金融小白的顶级分析师。
        请阅读以下可能是英文或生涩中文的新闻全文/片段，并执行以下核心任务：
        1. 将其提炼为一段通俗易懂的【中文深度摘要】（必须包含原出处的关键数据指标或核心事件脉络，让小白也能听懂这则长新闻到底发生了什么）。
        2. 像雷达一样，提取出这则新闻中涉及到的、**有较高门槛和教学价值的【单个金融/经济学术语】**（比如：降息、通货膨胀、市盈率、市值、缩表等）。最多提取3个核心词，不提取无关紧要的人名。
        3. 判断这则新闻对宏观市场情绪（积极、消极、中性）。

        新闻标题: {raw_title}
        新闻具体内容: {full_content}

        你必须严格输出一个 JSON 字典，绝对不要包含任何 markdown 符号或其它废话，必须符合以下键值对要求：
        {{
            "title": "{raw_title}",
            "original_summary": "{raw_content}",
            "ai_one_sentence_summary": "你提炼的中文深度总结（字数在 150-300 字左右，必须客观总结核心事件脉络、因果关联，以及对金融市场的深层影响，不要说废话）",
            "key_financial_terms": ["专业名词1", "专业名词2"],
            "sentiment": "积极" 或 "消极" 或 "中性"
        }}
        """
        
        if not self.api_ready:
            return ProcessedNews(
                title=raw_title,
                original_summary=raw_content,
                ai_one_sentence_summary=f"无法提炼，系统处于兜底测试模式（原因：{self.provider} API 未配置）。",
                key_financial_terms=[],
                sentiment="中性",
                fetch_warning=fetch_warning
            )
        
        try:
            result_text = ""
            if self.provider == "gemini":
                response = self.gemini_model.generate_content(prompt)
                result_text = response.text
                
            elif self.provider == "minimax":
                response = self.minimax_client.chat.completions.create(
                    model="MiniMax-M2.7-highspeed",
                    messages=[{"role": "user", "content": prompt}],
                )
                result_text = response.choices[0].message.content
                
            elif self.provider == "deepseek":
                response = self.deepseek_client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"} # 强制要求 DeepSeek 返回 JSON
                )
                result_text = response.choices[0].message.content
                
            # 彻底清洗：先把最恶心的 <think> 过程直接砍掉（不管里面有没有包含大括号）
            result_text = re.sub(r'<think>.*?</think>', '', result_text, flags=re.DOTALL)
            
            # 去除大模型可能吐出的 ```json 标志前缀
            result_text = result_text.replace("```json", "").replace("```", "").strip()
            
            # 安全截取：只保留第一个 { 到最后一个 } 之间的完美内容
            start_idx = result_text.find('{')
            end_idx = result_text.rfind('}')
            if start_idx != -1 and end_idx != -1:
                result_text = result_text[start_idx:end_idx+1]
                
            result_dict = json.loads(result_text)
            
            # Pydantic 过滤
            result = ProcessedNews(**result_dict)
            result.fetch_warning = fetch_warning  # 注入爬虫警告
            return result
            
        except Exception as e:
            print(f"[LLM 处理异常] {e}")
            return ProcessedNews(
                title=raw_title,
                original_summary=raw_content,
                ai_one_sentence_summary=f"调用 {self.provider} 模型提炼失败: {e}",
                key_financial_terms=[],
                sentiment="中性",
                fetch_warning=fetch_warning
            )

if __name__ == "__main__":
    engine = LLMEngine()
    test_title = "Nvidia briefly surpasses Microsoft as most valuable company driven by AI hype."
    test_content = "Nvidia's market cap reached new highs crossing 3 Trillion dollars. Many retail investors are wondering if its current high P/E ratio is justified."
    
    print("\n[测试] 正在请求提取实体与情感...")
    res = engine.process_news(test_title, test_content)
    print("========= 大模型输出结果 =========")
    print(res.model_dump_json(indent=4))
