"""
关键字管理器：负责关键字的增删查、LLM 智能扩展、JSON 持久化。
"""
import os
import sys
import json
import re
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.config import BASE_DIR

# 数据持久化路径
DATA_DIR = os.path.join(BASE_DIR, "data")
KEYWORDS_FILE = os.path.join(DATA_DIR, "watch_keywords.json")


class KeywordManager:
    def __init__(self, llm_engine=None):
        """
        Args:
            llm_engine: 可复用已有的 LLMEngine 实例中的 API 客户端（避免重复初始化）
        """
        self.llm_engine = llm_engine
        self._ensure_data_dir()
        self.watches = self._load()

    def _ensure_data_dir(self):
        os.makedirs(DATA_DIR, exist_ok=True)

    def _load(self) -> list:
        """从 JSON 文件加载关键字列表"""
        if not os.path.exists(KEYWORDS_FILE):
            return []
        try:
            with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("watches", [])
        except Exception as e:
            print(f"[关键字管理器] 加载关键字文件失败: {e}")
            return []

    def _save(self):
        """持久化关键字列表到 JSON 文件"""
        try:
            with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
                json.dump({"watches": self.watches}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[关键字管理器] 保存关键字文件失败: {e}")

    def get_all_watches(self) -> list:
        """获取所有监控组"""
        return self.watches

    def get_all_keywords_flat(self) -> list:
        """获取所有关键字的扁平列表（用于快速匹配）"""
        all_kw = []
        for watch in self.watches:
            all_kw.extend(watch.get("keywords", []))
        return all_kw

    def find_matching_watch(self, text: str) -> tuple:
        """
        检查文本是否命中任何监控关键字。
        Returns: (watch_name, matched_keyword) 或 (None, None)
        """
        text_lower = text.lower()
        for watch in self.watches:
            for kw in watch.get("keywords", []):
                if kw.lower() in text_lower:
                    return watch["name"], kw
        return None, None

    def remove_watch(self, name: str) -> bool:
        """移除一个监控组"""
        name_lower = name.lower()
        for i, watch in enumerate(self.watches):
            if watch["name"].lower() == name_lower:
                self.watches.pop(i)
                self._save()
                return True
        return False

    def add_watch(self, name: str, keywords: list) -> dict:
        """添加一个监控组（如果已存在则合并关键字）"""
        # 检查是否已存在
        for watch in self.watches:
            if watch["name"].lower() == name.lower():
                # 合并关键字（去重）
                existing = set(kw.lower() for kw in watch["keywords"])
                for kw in keywords:
                    if kw.lower() not in existing:
                        watch["keywords"].append(kw)
                        existing.add(kw.lower())
                self._save()
                return watch

        # 新建监控组
        watch = {
            "name": name,
            "keywords": keywords,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.watches.append(watch)
        self._save()
        return watch

    def expand_keywords_via_llm(self, user_input: str) -> list:
        """
        使用大模型从用户输入（可能是股票名称、代码、概念）扩展出一组关联关键字。
        返回关键字列表（包含原始输入）。
        """
        if not self.llm_engine or not self.llm_engine.api_ready:
            # 降级：直接使用原始输入
            return [user_input]

        prompt = f"""你是金融信息检索专家。用户想监控与"{user_input}"相关的新闻。
请提取并扩展与之关联的关键字，用于匹配新闻标题和内容。

规则：
1. 包含原始输入"{user_input}"本身
2. 如果是股票名称，加入：股票代码、英文名、公司简称、核心人物（如CEO）、核心产品/业务
3. 如果是概念/行业，加入：该概念的同义词、缩写、英文、核心相关术语
4. 关键字不要太泛化（如"投资"、"市场"这种匹配一切的词要排除）
5. 总数控制在 3-8 个

你必须严格只返回一个 JSON 数组，不要有任何其他文字：
["关键字1", "关键字2", "关键字3"]
"""
        try:
            result_text = ""
            if self.llm_engine.provider == "deepseek":
                response = self.llm_engine.deepseek_client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"}
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

            # 清洗
            result_text = re.sub(r'<think>.*?</think>', '', result_text, flags=re.DOTALL)
            result_text = result_text.replace("```json", "").replace("```", "").strip()

            # 尝试解析 JSON 数组
            start = result_text.find('[')
            end = result_text.rfind(']')
            if start != -1 and end != -1:
                result_text = result_text[start:end+1]

            keywords = json.loads(result_text)

            # 如果返回的是对象而非数组，尝试提取值
            if isinstance(keywords, dict):
                # 可能形如 {"keywords": [...]}
                for v in keywords.values():
                    if isinstance(v, list):
                        keywords = v
                        break

            if isinstance(keywords, list) and len(keywords) > 0:
                # 确保原始输入在列表中
                if user_input not in keywords:
                    keywords.insert(0, user_input)
                return [str(k) for k in keywords]

        except Exception as e:
            print(f"[关键字管理器] LLM 扩展失败: {e}")

        # 降级
        return [user_input]
