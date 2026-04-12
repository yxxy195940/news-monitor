import os
import sys

try:
    import chromadb
except ImportError:
    print("尚未安装 chromadb。请运行 pip install -r requirements.txt")
    sys.exit(1)

# 确保脚本可以在任何位置运行
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.config import CHROMA_DB_DIR, EMBEDDING_MODEL
from src.rag.document_loader import DocumentLoader


class MultilingualEmbeddingFunction:
    """
    自定义 Embedding 函数，为 multilingual-e5 系列模型自动添加 query/passage 前缀。
    intfloat/multilingual-e5-* 模型在有前缀时效果比无前缀高 10-15%。
    BAAI/bge-m3 不需要前缀，直接透传。
    """
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._is_e5 = "e5" in model_name.lower()
        print(f"[Embedding] 正在加载向量模型: {model_name}")
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)
        print(f"[Embedding] 模型加载完毕 ✓ (跨语言能力: {'E5-多语言前缀模式' if self._is_e5 else 'BGE-直接编码模式'})")

    def __call__(self, input: list[str]) -> list[list[float]]:
        """ChromaDB 调用此接口进行文档/查询向量化"""
        if self._is_e5:
            # E5 系列：文档端加 "passage: " 前缀
            # ChromaDB 无法区分 query 和 document 调用，
            # 统一用 "passage: " 以保证文档入库的语义准确性；
            # 查询端重写在 search() 方法里完成
            texts = [f"passage: {t}" for t in input]
        else:
            texts = input
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    def embed_query(self, query: str) -> list[float]:
        """查询时单独调用，加 'query: ' 前缀以激活检索语义对齐"""
        if self._is_e5:
            text = f"query: {query}"
        else:
            text = query
        embedding = self.model.encode(text, normalize_embeddings=True)
        return embedding.tolist()


class VectorStore:
    def __init__(self, collection_name: str = None):
        """
        初始化 ChromaDB。
        集合名称与模型名称绑定，防止不同模型的向量混用导致检索错乱。
        """
        if not os.path.exists(CHROMA_DB_DIR):
            os.makedirs(CHROMA_DB_DIR)

        self.ef = MultilingualEmbeddingFunction(EMBEDDING_MODEL)

        # 用模型名称的末段作为集合后缀，确保不同模型互相隔离
        model_tag = EMBEDDING_MODEL.split("/")[-1].replace("-", "_").lower()
        if collection_name is None:
            collection_name = f"financial_books_{model_tag}"

        self.client = chromadb.PersistentClient(path=CHROMA_DB_DIR)

        # 用 get_or_create + metadata 记录模型名（不在 ChromaDB 里传 ef，避免冲突检测）
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"embedding_model": EMBEDDING_MODEL, "hnsw:space": "cosine"}
        )
        print(f"[VectorStore] 已连接集合 '{collection_name}' (模型: {EMBEDDING_MODEL})")

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
        """跨语言语义搜索，查询向量使用 query: 前缀"""
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
        """增量建库接口：为单本书注入向量，供 Telegram 上传时使用"""
        docs_to_upsert = [{"source": filename, "content": c} for c in chunks]
        # 生成稳定的 ID，防止重复上传同一本书造成重复切片
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
        "如何在熊市中保持仓位稳定"
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
