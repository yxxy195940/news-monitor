import os
import sys
import shutil

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.rag.document_loader import DocumentLoader
from src.rag.vector_store import VectorStore
from src.config import CHROMA_DB_DIR, MOCK_BOOKS_DIR, EMBEDDING_MODEL, CHUNK_SIZE, CHUNK_OVERLAP


def main():
    print("=" * 60)
    print("🚀  RAG 向量知识库构建引擎 — 跨语言最佳实践版")
    print("=" * 60)
    print(f"📌 向量模型 : {EMBEDDING_MODEL}")
    print(f"📌 切片大小 : {CHUNK_SIZE} 字符 | 重叠: {CHUNK_OVERLAP} 字符")
    print(f"📌 数据目录 : {MOCK_BOOKS_DIR}")
    print(f"📌 存储目录 : {CHROMA_DB_DIR}")
    print()

    # ---- 检测旧模型的集合冲突，提示清理 ----
    if os.path.exists(CHROMA_DB_DIR):
        # 判断是否存在旧版 bge-small 的集合（通过集合名称识别）
        import chromadb
        try:
            client_check = chromadb.PersistentClient(path=CHROMA_DB_DIR)
            existing = [c.name for c in client_check.list_collections()]
            old_names = [n for n in existing if n == "financial_books"]  # 旧版集合名
            if old_names:
                print(f"⚠️  检测到旧版集合 {old_names}（使用了 bge-small-zh 模型）")
                print("   新模型将使用独立集合，旧集合保留不删除（可手动 python build_index.py --clean 清理）")
        except Exception:
            pass

    # ---- 加载文件并切片 ----
    print("📚 正在从 mock_books 提取并语义切片 PDF / EPUB / TXT...")
    loader = DocumentLoader(MOCK_BOOKS_DIR)
    chunks = loader.load_and_chunk()

    if not chunks:
        print("❌ 未在 mock_books 目录下切分出任何有效片段，请检查目录是否有文件。")
        return

    print(f"\n🔪 切片完成，共提纯出 {len(chunks)} 块高质量语义片段。")
    print(f"🧠 正在加载 {EMBEDDING_MODEL} 并进行向量空间映射...(首次运行会下载模型，请耐心等待)")
    print()

    # ---- 向量化并写入 ChromaDB ----
    vs = VectorStore()

    batch_size = 200  # E5/bge-m3 模型参数量更大，批次调小防 OOM
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        vs.upsert_chunks(batch)
        done = min(i + batch_size, len(chunks))
        print(f"  ↳ 降维注入进度: {done} / {len(chunks)} 块 ({done * 100 // len(chunks)}%)")

    print()
    print(f"🎉 建库竣工！RAG 知识图谱已锁死在 [{CHROMA_DB_DIR}]")
    print(f"📊 集合统计: {vs.collection.count()} 条向量记录")
    print("💡 现在可以启动主程序: python main.py")

    # ---- 快速自检：向中英文各投一个测试查询 ----
    print("\n🔍 快速自检（中英文跨语言检索测试）...")
    test_queries = ["投资风险管理", "stock market trend analysis"]
    for q in test_queries:
        res = vs.search(q, n_results=1)
        if res['documents'] and res['documents'][0]:
            doc = res['documents'][0][0]
            src = res['metadatas'][0][0]['source']
            dist = res['distances'][0][0]
            print(f"  ✓ '{q}' → 命中《{src}》 | 余弦距离: {dist:.4f}")
            print(f"    片段: {doc[:60]}...")
        else:
            print(f"  ✗ '{q}' → 未命中（库可能为空）")

    print("\n✅ 全部完成！")


if __name__ == "__main__":
    # 支持 --clean 参数清空旧库重建
    if "--clean" in sys.argv:
        if os.path.exists(CHROMA_DB_DIR):
            shutil.rmtree(CHROMA_DB_DIR)
            print(f"🗑️  已清空旧向量库: {CHROMA_DB_DIR}")
    main()
