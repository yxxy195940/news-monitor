from src.delivery.telegram_ui import TelegramBotUI
from src.rag.vector_store import VectorStore
from src.config import TELEGRAM_BOT_TOKEN

def initialize_system():
    print("==================================================")
    print("🚀  AI 场景化理财伴学平台 (FinLearn MVP) 正在启动")
    print("==================================================")
    
    # 【启动自检 1】: 强制初始化或验证本地教材向量库
    print("\n[系统自检] 连接本地 RAG 教材向量簇...")
    VectorStore()
    print("[系统自检] 知识底座状态：就绪。\n")

def main():
    # 执行初始化
    initialize_system()
    
    # 【启动自检 2】: 验证通讯通道
    if not TELEGRAM_BOT_TOKEN or "修改为你" in TELEGRAM_BOT_TOKEN:
        print("==================================================")
        print("❌ [严重错误] 服务已被主控进程挂起！")
        print("原因：系统检测到根目录的 .env 文件中缺少通信凭证 TELEGRAM_BOT_TOKEN。")
        print("解决办法：请去 Telegram 的 @BotFather 创建机器人，获取 Token 后填入 .env 中。")
        print("==================================================")
        return
        
    # 启动对外的交互终端 (阻塞式进程)
    bot = TelegramBotUI()
    bot.run()

if __name__ == "__main__":
    main()
