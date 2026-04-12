import os
import sys

try:
    import chromadb
except ImportError:
    print("尚未安装 chromadb。请运行 pip install -r requirements.txt")
    sys.exit(1)

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.config import (
    CHROMA_DB_DIR, EMBEDDING_MODE, EMBEDDING_MODEL,
    EMBEDDING_API_KEY, EMBEDDING_API_BASE
)


# ============================================================
# 方案 A：云端 API Embedding（零内存占用，支持 bge-m3 旗舰跨语言）
# ============================================================
class APIEmbeddingFunction:
    """
    调用 OpenAI 兼容的 Embedding API（如硅基流动、阿里云 DashScope、火山引擎）。
    完全不依赖 PyTorch，内存占用约 30MB，2G 小鸡轻松运行。
    """
    def __init__(self, model_name: str, api_key: str, base_url: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model_name
        print(f"[Embedding] API 模式已就绪: {model_name}")
        print(f"[Embedding] 端点: {base_url}")

    def __call__(self, texts: list[str]) -> list[list[float]]:
        """批量文档向量化"""
        if not texts:
            return []
        # API 限制单次批量不超过 64 条，做分批处理
        batch_size = 32
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            response = self.client.embeddings.create(
                input=batch,
                model=self.model
            )
            all_embeddings.extend([d.embedding for d in response.data])
        return all_embeddings

    def embed_query(self, query: str) -> list[float]:
        """单条查询向量化"""
        response = self.client.embeddings.create(
            input=[query],
            model=self.model
        )
        return response.data[0].embedding


# ============================================================
# 方案 B：本地模型 Embedding（需要 4GB+ 内存的高配机器）
# ============================================================
class LocalEmbeddingFunction:
    """
    本地 sentence-transformers 模型，支持 E5 前缀对齐。
    仅在 EMBEDDING_MODE=local 时使用，需要足够内存。
    """
    def __init__(self, model_name: str):
        os.environ['OMP_NUM_THREADS'] = '1'
        os.environ['MKL_NUM_THREADS'] = '1'
        self.model_name = model_name
        self._is_e5 = "e5" in model_name.lower()
        print(f"[Embedding] 正在加载本地模型: {model_name}")
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name, model_kwargs={"low_cpu_mem_usage": True})
        print(f"[Embedding] 本地模型加载完毕 ✓")

    def __call__(self, texts: list[str]) -> list[list[float]]:
        if self._is_e5:
            texts = [f"passage: {t}" for t in texts]
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    def embed_query(self, query: str) -> list[float]:
        if self._is_e5:
            query = f"query: {query}"
        embedding = self.model.encode(query, normalize_embeddings=True)
        return embedding.tolist()


# ============================================================
# 统一入口：根据配置自动选择 Embedding 后端
# ============================================================
def create_embedding_function():
    """根据 EMBEDDING_MODE 自动创建对应的 Embedding 函数"""
    if EMBEDDING_MODE == "api":
        if not EMBEDDING_API_KEY:
            print("❌ [致命错误] EMBEDDING_MODE=api 但 EMBEDDING_API_KEY 未配置！")
            print("   请在 .env 中设置 EMBEDDING_API_KEY")
            print("   推荐免费注册硅基流动: https://siliconflow.cn")
            sys.exit(1)
        return APIEmbeddingFunction(EMBEDDING_MODEL, EMBEDDING_API_KEY, EMBEDDING_API_BASE)
    else:
        return LocalEmbeddingFunction(EMBEDDING_MODEL)


# ============================================================
# VectorStore 核心类
# ============================================================
class VectorStore:
    def __init__(self, collection_name: str = None):
        if not os.path.exists(CHROMA_DB_DIR):
            os.makedirs(CHROMA_DB_DIR)

        self.ef = create_embedding_function()

        # 用模型名称的末段作为集合后缀，确保不同模型互相隔离
        model_tag = EMBEDDING_MODEL.split("/")[-1].replace("-", "_").lower()
        if collection_name is None:
            collection_name = f"financial_books_{model_tag}"

        self.client = chromadb.PersistentClient(path=CHROMA_DB_DIR)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"embedding_model": EMBEDDING_MODEL, "hnsw:space": "cosine"}
        )
        print(f"[VectorStore] 已连接集合 '{collection_name}' (模型: {EMBEDDING_MODEL}, 模式: {EMBEDDING_MODE})")

    def _embed_docs(self, texts: list[str]) -> list[list[float]]:
        return self.ef(texts)

    def _embed_query(self, query: str) -> list[float]:
        return self.ef.embed_query(query)

    def upsert_chunks(self, chunks: list[dict]):
        """批量插入/更新知识切片，chunks 格式: [{'source': str, 'content': str}]"""
        if not chunks:
            return
        ids = [f"{c['source']}_chunk_{i}" for i, c in enumerate(chunks)]
        documents = [c["content"] for c in chunks]
        metadatas = [{"source": c["source"]} for c in chunks]
        embeddings = self._embed_docs(documents)
        self.collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

    def search(self, query: str, n_results: int = 4) -> dict:
        """跨语言语义搜索"""
        q_embedding = self._embed_query(query)
        results = self.collection.query(
            query_embeddings=[q_embedding],
            n_results=n_results
        )
        return results

    def search_within_book(self, query: str, source_filename: str, n_results: int = 6) -> dict:
        """限定在单本书籍内的语义检索"""
        q_embedding = self._embed_query(query)
        results = self.collection.query(
            query_embeddings=[q_embedding],
            n_results=n_results,
            where={"source": source_filename}
        )
        return results

    def upsert_single_book(self, filename: str, chunks: list[str]):
        """增量建库接口：为单本书注入向量"""
        ids = [f"{filename}_chunk_{i}" for i in range(len(chunks))]
        embeddings = self._embed_docs(chunks)
        self.collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=[{"source": filename}] * len(chunks)
        )

    def get_unique_sources(self) -> list:
        """提取库中所有不同来源的书籍文件名"""
        try:
            data = self.collection.get(include=["metadatas"])
            if not data or not data.get("metadatas"):
                return []
            sources = set()
            for meta in data["metadatas"]:
                if meta and "source" in meta:
                    sources.add(meta["source"])
            return list(sources)
        except Exception as e:
            print(f"提取书籍名录失败: {e}")
            return []


if __name__ == "__main__":
    vs = VectorStore()
    print("\n========= 跨语言 RAG 检索测试 =========")
    queries = [
        "一只亏损的科技公司，市盈率应该是多少？",
        "What is the P/E ratio of a loss-making tech company?",
    ]
    for q in queries:
        print(f"\n用户提问: {q}")
        res = vs.search(q, n_results=2)
        if res['documents'] and res['documents'][0]:
            for i, doc in enumerate(res['documents'][0]):
                meta = res['metadatas'][0][i]
                score = res['distances'][0][i]
                print(f"  [命中 {i+1} | 来源: {meta['source']} | 距离: {score:.4f}]")
                print(f"  {doc[:80]}...")
        else:
            print("  未检索到相关内容。")
