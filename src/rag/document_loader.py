import os
import fitz  # PyMuPDF
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.config import CHUNK_SIZE, CHUNK_OVERLAP


class DocumentLoader:
    def __init__(self, directory: str = "mock_books"):
        self.directory = directory
        # 中英文双语感知的分隔符顺序：优先按段落 > 句号 > 换行 > 空格
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            length_function=len,
            separators=[
                "\n\n",      # 段落分隔（最优先）
                "。\n", "！\n", "？\n",  # 中文句末换行
                ".\n", "!\n", "?\n",     # 英文句末换行
                "。", "！", "？",        # 中文句号
                ". ", "! ", "? ",        # 英文句号+空格
                "\n",                    # 普通换行
                "，", "、",              # 中文逗号
                ", ",                    # 英文逗号
                " ",                     # 空格
                "",                      # 最后兜底
            ],
            is_separator_regex=False,
        )

    def extract_text_from_pdf(self, file_path: str) -> str:
        doc = fitz.open(file_path)
        pages = []
        for page in doc:
            text = page.get_text().strip()
            if text:
                pages.append(text)
        return "\n\n".join(pages)

    def extract_text_from_epub(self, file_path: str) -> str:
        try:
            book = epub.read_epub(file_path)
            chapters = []
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    soup = BeautifulSoup(item.get_body_content(), 'html.parser')
                    # 保留段落结构：每个 <p> 标签之间加换行
                    paragraphs = [p.get_text(strip=True) for p in soup.find_all(['p', 'h1', 'h2', 'h3']) if p.get_text(strip=True)]
                    if paragraphs:
                        chapters.append("\n\n".join(paragraphs))
            return "\n\n".join(chapters)
        except Exception as e:
            print(f"Error reading EPUB {file_path}: {e}")
            return ""

    def load_and_chunk(self):
        """加载 mock_books 目录下所有文件，返回 [{source, content}] 格式的切片列表"""
        if not os.path.exists(self.directory):
            print(f"Directory {self.directory} does not exist.")
            return []

        all_chunks = []
        for filename in os.listdir(self.directory):
            file_path = os.path.join(self.directory, filename)

            raw_text = ""
            if filename.endswith(".pdf"):
                print(f"提取 PDF: {filename} ...")
                raw_text = self.extract_text_from_pdf(file_path)
            elif filename.endswith(".epub"):
                print(f"提取 EPUB: {filename} ...")
                raw_text = self.extract_text_from_epub(file_path)
            elif filename.endswith(".txt"):
                print(f"提取 TXT: {filename} ...")
                with open(file_path, "r", encoding="utf-8") as f:
                    raw_text = f.read()
            else:
                continue

            if raw_text and len(raw_text.strip()) > 50:
                chunks = self.text_splitter.split_text(raw_text)
                for chunk in chunks:
                    all_chunks.append({
                        "source": filename,
                        "content": chunk
                    })
                print(f"完成 {filename}，共切割出 {len(chunks)} 个片段。")

        return all_chunks
