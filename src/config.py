import os
from dotenv import load_dotenv

# 加载本地 .env 文件中的环境变量
load_dotenv()

# API Keys (建议将这些写在根目录的 .env 文件中)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY", "")
# 机主用户的 Telegram User ID（只有该用户才能上传书籍），可在 @userinfobot 查询
OWNER_USER_ID = int(os.getenv("OWNER_USER_ID", "0"))

# RAG 配置
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK_BOOKS_DIR = os.path.join(BASE_DIR, "mock_books")
CHROMA_DB_DIR = os.path.join(BASE_DIR, "chroma_db")

# ===== 嵌入模型配置 =====
# 模式选择：
#   api   — 使用云端 Embedding API（推荐！2G 服务器零内存压力，支持最强跨语言模型）
#   local — 使用本地 sentence-transformers（需要 4GB+ 内存的高配机器）
EMBEDDING_MODE = os.getenv("EMBEDDING_MODE", "api")

# --- API 模式配置 ---
# 推荐使用硅基流动 (SiliconFlow) 免费额度，注册即送 2000万 Token：https://siliconflow.cn
# 也可以用阿里云 DashScope、火山引擎等任何 OpenAI 兼容的 Embedding API
EMBEDDING_API_BASE = os.getenv("EMBEDDING_API_BASE", "https://api.siliconflow.cn/v1")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")

# --- 本地模式配置（仅 EMBEDDING_MODE=local 时生效）---
# 可选值：intfloat/multilingual-e5-small / BAAI/bge-small-zh-v1.5
# LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")

# RAG 文本切片配置——基于语义边界感知优化
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))     # 从 500 提升至 800，提供更广上下文
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))  # 从 50 提升至 120，减少切片断层错误
