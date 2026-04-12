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

# 嵌入模型选型（修改后必须删除 chroma_db 并重新 python build_index.py）
# 选项：
#   intfloat/multilingual-e5-base   — 推荐，2C2G 服务器最优，占用 ~800MB，100+语言
#   intfloat/multilingual-e5-large  — 更高精度，需 ~1.5GB
#   BAAI/bge-m3                     — 旗舰级，需 ~2.3GB，适合高配内存工作机
#   BAAI/bge-small-zh-v1.5          — 旧版(断弃)，仅支持中文单语言
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-base")

# RAG 文本切片配置——基于语义边界感知优化
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))     # 从 500 提升至 800，提供更广上下文
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))  # 从 50 提升至 120，减少切片断层错误
