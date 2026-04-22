import os
import sys
import time
import hashlib
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.config import TELEGRAM_BOT_TOKEN, MOCK_BOOKS_DIR, OWNER_USER_ID
from src.ingestion.news_api import NewsFetcher
from src.analyzer.llm_engine import LLMEngine
from src.rag.rag_engine import RAGEngine
from src.rag.document_loader import DocumentLoader
from src.models import ProcessedNews
from src.watchdog.keyword_manager import KeywordManager
from src.watchdog.news_filter import NewsFilter
from src.watchdog.digest_builder import DigestBuilder
from src.ingestion.announcement_api import AnnouncementAPI
from src.watchdog.announcement_filter import AnnouncementDecoder

class TelegramBotUI:
    def __init__(self):
        self.news_fetcher = NewsFetcher()
        self.llm_engine = LLMEngine()
        self.rag_engine = RAGEngine()
        
        self.user_context = {}  # user_id -> ProcessedNews (用于回答新闻相关的提问)
        self.subscribers = set()
        self.flash_muted = set()  # user_id -> 标记已静音快讯推送的用户
        
        # 缓存系统: user_id -> {"query": str, "timestamp": float, "news": list}
        self.user_search_cache = {}
        self.PAGE_SIZE = 10  # 改为按 10 条一页下发
        
        # 万能快讯/新闻短效池 id -> dict（不管是快讯点播还是后台推送的新闻组合点播，统一在此做ID映射）
        self.flash_cache = {}
        
        self.book_map = {} # 制止长书名爆雷： book_id -> book_name
        
        # ===== 关键字监控引擎 =====
        self.keyword_manager = KeywordManager(llm_engine=self.llm_engine)
        self.news_filter = NewsFilter(self.keyword_manager, self.llm_engine)
        self.digest_builder = DigestBuilder(self.news_filter, self.llm_engine)
        
        # ===== 公司公告监控引擎 =====
        self.announcement_api = AnnouncementAPI()
        self.announcement_decoder = AnnouncementDecoder(self.llm_engine)
        self.announcement_cache = {}

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        self.subscribers.add(user_id)
        welcome_text = (
            "👋 欢迎！金融新闻检索引擎已激活。\n\n"
            "📡 机器人会随时通过超大内存缓存为您极速提供商业大事件。\n\n"
            "🔍 **强大的指令中枢：**\n"
            "`/news` - 从 2500 条底座中取最新 10 大商业头条\n"
            "`/flash` - 获取最新全网 10 条实时快讯，随时翻页直连网络\n"
            "`/news 特斯拉 降价` - 在大图谱中检索同时包含这几个关键字的报道\n"
            "`/books` - 探查本地 Chroma 库挂载并存活的绝密投资宝典\n\n"
            "📚 **知识库管理（机主专属）：**\n"
            "直接发送 `.epub` 或 `.pdf` 文件 → 自动下载并完成增量建库\n\n"
            "🎯 **关键字监控（自动盯盘）：**\n"
            "`/watch 特斯拉` - 添加关键字/股票监控，AI 自动扩展关联词\n"
            "`/unwatch 特斯拉` - 移除一组监控关键字\n"
            "`/watchlist` - 查看当前所有监控关键字\n"
            "`/digest` - 立刻按关键字生成并发送近期监控新闻整理\n\n"
            "🏢 **公司内部事件透视：**\n"
            "`/ann 603986` 或 `/ann 比亚迪` - 即刻调取并深度解读该公司的最新重大公告\n\n"
            "💬 **随时提问：**\n"
            "`/flash_off` - 关闭每30秒一次的高频快讯自动推送\n"
            "`/flash_on` - 重新开启快讯自动推送\n"
        )
        await update.message.reply_text(welcome_text, parse_mode='Markdown')



    async def flash_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        self.subscribers.add(user_id)
        if user_id in self.flash_muted:
            self.flash_muted.remove(user_id)
        await update.message.reply_text("🔔 已为您 **开启** 每 30 秒一次的华尔街全球快讯高频接收。")

    async def flash_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        self.flash_muted.add(user_id)
        await update.message.reply_text("🔕 已为您 **关闭** 快讯高频推送。您依然可以随时发送 `/flash` 主动查阅。")

    async def manual_books(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        
        sources = self.rag_engine.vector_store.get_unique_sources()
        if not sources:
            await update.message.reply_text("📚 当前知识库中未挂载任何书籍，请先执行 `python build_index.py`。")
            return
            
        text_lines = [f"📚 **您的专属本地经典藏书阁** (共计确收 {len(sources)} 本实体读物)\n"]
        for i, src in enumerate(sources):
            text_lines.append(f"{i+1}. 📖 `{src}`")
            
        text_lines.append("\n👉 点击下方名录触发 AI 对该书进行底层灵魂提取与金句验证：")
        
        keyboard = []
        for i, src in enumerate(sources):
            # 将几十个字的恶心文件名压缩为轻量映射
            book_id = f"b{i}"
            self.book_map[book_id] = src
            
            # 按钮长度如果太长会导致 Telegram 报错，这里可缩略书名，提取前15个字
            btn_text = src[:15] + "..." if len(src) > 15 else src
            keyboard.append([InlineKeyboardButton(text=f"🧠 提取与评价: {btn_text}", callback_data=f"eval_book:{book_id}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg_text = "\n".join(text_lines)
        
        await update.message.reply_text(msg_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def manual_news(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        self.subscribers.add(user_id)
        
        # 解析指令
        text = update.message.text.strip()
        parts = text.split(maxsplit=1)
        args_str = parts[1] if len(parts) > 1 else ""
            
        query = args_str if args_str else None
        cache_key = query if query else "GLOBAL_TOP"
        
        wait_msg = await update.message.reply_text(f"⏳ 正在千万级本地图谱中瞬时比对 [{query or '全球商业'}] (支持多关键字 AND 组合)...")
        
        # 本地纯内存过滤，速度极快（一次性最多挑出50条符合的）
        results = self.news_fetcher.fetch_search_list(query=query, limit=50)
        
        if not results:
            await wait_msg.edit_text("😢 搜索完缓存大盘，没有找到相关的长篇头条（你可以尝试缩短关键字，或者它不存在于近 2500 条新闻中）。")
            return
            
        # 写入个人的阅读切片缓存内存区
        self.user_search_cache[user_id] = {
            "query": cache_key,
            "timestamp": time.time(),
            "news": results
        }
        
        await wait_msg.delete()
        # 渲染第 0 页
        await self._render_news_list(update, context, user_id, 0)

    async def _render_news_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, page: int, message_to_edit=None):
        """长篇新闻头条核心列表渲染与分页按键调度"""
        cache = self.user_search_cache.get(user_id)
        if not cache:
            return
            
        news_list = cache["news"]
        total = len(news_list)
        
        start_idx = page * self.PAGE_SIZE
        end_idx = min(start_idx + self.PAGE_SIZE, total)
        current_page_news = news_list[start_idx:end_idx]
        
        text_lines = [f"📰 **{'热门检索' if cache['query'] == 'GLOBAL_TOP' else '关键字检索: ' + cache['query']}** (本地共 {total} 篇符合)\n"]
        
        for i, article in enumerate(current_page_news):
            global_idx = start_idx + i + 1
            pub_date = article.get("publishedAt", "")[:10]
            date_str = f" ({pub_date})" if pub_date else ""
            text_lines.append(f"{global_idx}. {article['title']}{date_str}\n")
            
        text_lines.append("\n👉 _点击下方对应的新闻编号，大模型将发来带核心观点的深度精要：_")
        message_text = "\n".join(text_lines)
        
        keyboard = []
        num_row = []
        for i in range(len(current_page_news)):
            global_idx = start_idx + i + 1
            btn = InlineKeyboardButton(text=f"[ {global_idx} ]", callback_data=f"read_index:{start_idx + i}")
            num_row.append(btn)
            if len(num_row) == 5:
                keyboard.append(num_row)
                num_row = []
        if num_row:
            keyboard.append(num_row)
            
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text="🔼 上一页", callback_data=f"page_list:{page - 1}"))
        else:
            nav_row.append(InlineKeyboardButton(text="🚫 已是首页", callback_data="alert:已经是第一页了！"))
            
        if end_idx < total:
            nav_row.append(InlineKeyboardButton(text="🔽 下一页", callback_data=f"page_list:{page + 1}"))
        else:
            nav_row.append(InlineKeyboardButton(text="🚫 已是末页", callback_data="alert:已达到本次检索的结果末尾！"))
            
        if nav_row:
            keyboard.append(nav_row)
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if message_to_edit:
            await message_to_edit.edit_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await context.bot.send_message(chat_id=user_id, text=message_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def manual_flash(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        self.subscribers.add(user_id)
        
        wait_msg = await update.message.reply_text("⏳ 正在跨网直连抓取新浪大盘第一页快讯...")
        await self._fetch_and_render_flash_page(update, context, user_id, 1, wait_msg)

    async def _fetch_and_render_flash_page(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, page: int, message_to_edit=None):
        """直插华尔街见闻底层网络的实时翻页器，不采用缓存"""
        results = self.news_fetcher.fetch_flash_list(page=page, limit=10)
        
        if not results:
            if message_to_edit:
                await message_to_edit.edit_text("😢 拉取快讯失败或网络超时，请检查节点。")
            return
            
        # 短期续命快闪池供后续按键精读使用
        for item in results:
            self.flash_cache[item["id"]] = item
            
        text_lines = [f"⚡ **全球最新大盘快讯直连** (第 {page} 页)\n"]
        
        for i, article in enumerate(results):
            global_idx = (page - 1) * 10 + i + 1
            ts = article.get("timestamp", 0)
            time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""
            
            content_preview = article['content'].replace('\n', '')[:65] + "..." if len(article['content']) > 65 else article['content']
            
            text_lines.append(f"**{global_idx}. [{time_str}]** {article['title']}")
            text_lines.append(f"_{content_preview}_\n")
            
        text_lines.append("\n👉 _点击编号深潜，透视快讯潜台词：_")
        message_text = "\n".join(text_lines)
        
        keyboard = []
        num_row = []
        for i, article in enumerate(results):
            global_idx = (page - 1) * 10 + i + 1
            # 回看时走的统一是 read_flash，它能从 flash_cache 根据 id 取得完整内容去问 LLM
            btn = InlineKeyboardButton(text=f"[ {global_idx} ]", callback_data=f"read_flash:{article['id']}")
            num_row.append(btn)
            if len(num_row) == 5:
                keyboard.append(num_row)
                num_row = []
        if num_row:
            keyboard.append(num_row)
            
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton(text="🔼 上一页", callback_data=f"page_flash:{page - 1}"))
        else:
            nav_row.append(InlineKeyboardButton(text="🚫 已是最新", callback_data="alert:已经是最新发出的第一页快讯！"))
            
        if len(results) == 10:
            nav_row.append(InlineKeyboardButton(text="🔽 早些时候", callback_data=f"page_flash:{page + 1}"))
        else:
            nav_row.append(InlineKeyboardButton(text="🚫 到底了", callback_data="alert:服务器已无更多历史快讯！"))
            
        if nav_row:
            keyboard.append(nav_row)
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if message_to_edit:
            await message_to_edit.edit_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await context.bot.send_message(chat_id=user_id, text=message_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def background_poll_flash(self, context: ContextTypes.DEFAULT_TYPE):
        if not self.subscribers:
            return
        # 华尔街见闻 30 秒高帧率队列
        fresh_flashes = self.news_fetcher.fetch_flash_lives()
        for flash in fresh_flashes:
            self.flash_cache[flash["id"]] = flash
            
            # ===== 关键字监控过滤 =====
            matched = self.news_filter.check_and_process_flash(flash)
            if matched:
                # 命中的快讯通知用户已捕获
                for chat_id in self.subscribers:
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"🎯 <b>关键字命中</b>：[{matched['watch_name']}] 捕获快讯\n\n"
                                 f"⚡ {flash['content'][:100]}...\n\n"
                                 f"<i>📥 已自动精炼保存，发送 /digest 查看整理报告</i>",
                            parse_mode='HTML',
                            disable_notification=False  # 关键字命中 → 正常响铃
                        )
                    except Exception:
                        pass
            
            for chat_id in self.subscribers:
                if chat_id not in self.flash_muted:
                    await self._dispatch_flash_ui(context, chat_id, flash)

        # 触发容量检查
        await self._check_and_auto_digest(context)

    async def _dispatch_flash_ui(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, flash: dict):
        ts = flash.get("timestamp", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else datetime.now().strftime("%H:%M:%S")
        
        msg = (
            f"⚡ <b>最新快讯</b> — <i>{time_str}</i>\n\n"
            f"{flash['content']}"
        )
        
        keyboard = [
            [InlineKeyboardButton(text="🧠 让 AI 深度解读潜台词", callback_data=f"read_flash:{flash['id']}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='HTML', reply_markup=reply_markup, disable_notification=True)  # 常规快讯 → 静默

    async def background_poll_news(self, context: ContextTypes.DEFAULT_TYPE):
        if not self.subscribers:
            return
        print("[哨兵进程] 后台半小时长篇拉取判定，更新大盘...")
        fresh_top_news = self.news_fetcher.fetch_background_news() # 收到刚刚诞生的全新 10 条
        if not fresh_top_news:
            return
        
        # ===== 关键字监控过滤（在推送前先扫描所有新闻） =====
        watch_hit_count = 0
        for article in fresh_top_news:
            matched = self.news_filter.check_and_process_news(article)
            if matched:
                watch_hit_count += 1
        
        if watch_hit_count > 0:
            for chat_id in self.subscribers:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"🎯 <b>关键字监控</b>：本轮新闻扫描命中 {watch_hit_count} 条\n"
                             f"<i>📥 已自动深度爬取并精炼保存，发送 /digest 查看整理报告</i>",
                        parse_mode='HTML',
                        disable_notification=False  # 关键字命中 → 正常响铃
                    )
                except Exception:
                    pass
        
        text_lines = [f"📰 **半小时侦测：海外宏观最新合辑 ({len(fresh_top_news)}条)**\n"]
        keyboard = []
        num_row = []
        
        for i, article in enumerate(fresh_top_news):
            global_idx = i + 1
            # 后台传来的新闻只有 url，我们把它 hash 为唯一伪快讯 ID 装进通用池子
            news_id = "news_" + hashlib.md5(article.get("url", "").encode()).hexdigest()[:8]
            self.flash_cache[news_id] = article
            
            pub_date = article.get("publishedAt", "")[:10]
            date_str = f" ({pub_date})" if pub_date else ""
            text_lines.append(f"{global_idx}. {article['title']}{date_str}\n")
            
            btn = InlineKeyboardButton(text=f"[ {global_idx} ]", callback_data=f"read_flash:{news_id}")
            num_row.append(btn)
            if len(num_row) == 5:
                keyboard.append(num_row)
                num_row = []
                
        if num_row:
            keyboard.append(num_row)
            
        text_lines.append("\n👉 _按下编号，耗费 Token 让大模型一网打尽！_")
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg_text = "\n".join(text_lines)
        
        for chat_id in self.subscribers:
            await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode='Markdown', reply_markup=reply_markup, disable_notification=True)  # 常规新闻 → 静默

        # 触发容量检查
        await self._check_and_auto_digest(context)

    async def _check_and_auto_digest(self, context: ContextTypes.DEFAULT_TYPE):
        """检查是否有关键字组的缓存内容超过阈值，并自动整理发送"""
        if not self.subscribers:
            return
            
        digest_meta = self.digest_builder.get_digest_metadata()
        if digest_meta.get("empty"):
            return
            
        for watch_name, items in digest_meta["groups"].items():
            # 计算该组新闻的字符总长 (标题 + 摘要)
            total_len = sum(len(item.get("title", "")) + len(item.get("summary", "")) for item in items)
            
            # 以 4000 字符作为强制触发阈值（留出 header 等余量，确保总长度不超过 TG 4096 限制）
            if total_len >= 4000:
                print(f"[哨兵进程] 关键字 [{watch_name}] 缓存达到阈值({total_len}字)，触发自动整理。")
                for chat_id in self.subscribers:
                    try:
                        header_msg = (
                            f"🔔 **[系统防超载提醒]** 关键字【{watch_name}】累积新闻已接近单条消息上限，系统已自动为您提前整理推送：\n\n"
                            f"🏷️ **【{watch_name}】** ({len(items)} 条)\n{'─' * 20}\n\n"
                        )
                        gen = self.digest_builder.stream_group(watch_name, items)
                        await self._stream_to_message(context, chat_id, gen, header=header_msg)
                    except Exception as e:
                        print(f"[Telegram UI] 自动整理推送失败: {e}")
                        
                # 仅清理这一个超标的关键字新闻池
                self.news_filter.clear_matched_by_watch(watch_name)

    async def background_poll_announcements(self, context: ContextTypes.DEFAULT_TYPE):
        """后台轮询股票公告（每5分钟）"""
        if not self.subscribers:
            return
            
        watches = self.announcement_filter.get_all_watches()
        if not watches:
            return
            
        print("[哨兵进程] 正在轮询 A股公司公告...")
        for code, info in watches.items():
            name = info['name']
            anns = self.announcement_api.fetch_latest_announcements(code, limit=3)
            for ann in anns:
                ann['watch_name'] = name
                processed = self.announcement_filter.process_new_announcement(ann)
                if processed:
                    # 发现新公告并解析成功，直接向用户推送
                    for chat_id in self.subscribers:
                        try:
                            msg = (
                                f"🏢 **公司公告速递** | **{name}** (`{code}`)\n"
                                f"━━━━━━━━━━━━━━━━\n"
                                f"📑 **标题**：{processed['title']}\n"
                                f"🕒 **时间**：{processed['time']}\n"
                                f"📊 **AI 情感判断**：{processed['sentiment']}\n\n"
                                f"🧠 **核心解读**：\n{processed['ai_summary']}\n\n"
                            )
                            if processed.get("url"):
                                msg += f"🔗 [查看原公告PDF]({processed['url']})"
                            
                            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
                        except Exception as e:
                            print(f"[Telegram UI] 公告推送失败: {e}")

    async def _dispatch_news_ui(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, processed_news: ProcessedNews, raw_news: dict):
        self.user_context[chat_id] = processed_news
        
        image_html = f"<a href='{raw_news.get('image', '')}'>&#8205;</a>" if raw_news.get("image") else ""
        date_str = raw_news.get('publishedAt', '')[:10]
        
        msg = (
            f"{image_html}📰 <b>{processed_news.title}</b>\n"
            f"🕒 <i>{date_str}</i>\n\n"
            f"📝 <b>原文速览：</b>\n<i>{raw_news.get('description', '')}</i>\n\n"
            f"🎯 <b>核心价值解析：</b>\n{processed_news.ai_one_sentence_summary}\n\n"
            f"📊 <b>宏观定调：</b>{processed_news.sentiment}\n\n"
            f"🔗 <a href='{raw_news.get('url', '')}'>点击阅读原始网页全文</a>\n\n"
            f"💡 <i>可敲键盘提问或点击底部知识点深度补充：</i>"
        )
        
        # 如果爬虫失败，在消息底部显示警告
        if processed_news.fetch_warning:
            msg += f"\n\n⚠️ <b>提示：</b><i>{processed_news.fetch_warning}</i>"
        
        keyboard = []
        if processed_news.key_financial_terms:
            for term in processed_news.key_financial_terms:
                btn = InlineKeyboardButton(text=f"📚 搞不懂『{term}』是什么", callback_data=f"ask:{term}")
                keyboard.append([btn])
        
        # 如果爬虫失败，追加一个重试按钮（原文链接直达）
        if processed_news.fetch_warning and raw_news.get("url"):
            keyboard.append([InlineKeyboardButton(text="🔄 我想重试—直接打开原文", url=raw_news.get("url"))])
                
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='HTML', reply_markup=reply_markup)


    async def handle_button_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        chat_id = update.effective_user.id
        callback_data = query.data
        
        # 1. 翻页请求
        if callback_data.startswith("page_list:"):
            target_page = int(callback_data.split(":")[1])
            await self._render_news_list(update, context, chat_id, target_page, message_to_edit=query.message)
            
        elif callback_data.startswith("page_flash:"):
            target_page = int(callback_data.split(":")[1])
            await self._fetch_and_render_flash_page(update, context, chat_id, target_page, message_to_edit=query.message)
            
        # 2. 从列表中点播具体的新闻要求大模型解析
        elif callback_data.startswith("read_index:"):
            news_idx = int(callback_data.split(":")[1])
            cache = self.user_search_cache.get(chat_id)
            if not cache or news_idx >= len(cache["news"]):
                await context.bot.send_message(chat_id=chat_id, text="⚠ 本地阅读切片已过期失效，请重新发送 /news 指令查阅。")
                return
                
            target_news = cache["news"][news_idx]
            await query.message.reply_text(f"🧠 正在提取原文精华，精读第 {news_idx+1} 篇: 『{target_news['title'][:15]}...』", disable_notification=True)
            
            processed = self.llm_engine.process_news(target_news["title"], target_news["content"], target_news.get("url"))
            await self._dispatch_news_ui(context, chat_id, processed, target_news)
            
        # 3. 具体新闻底部的一键提问交互
        elif callback_data.startswith("ask:"):
            term = callback_data.replace("ask:", "")
            user_question = f"请教一下老师，新闻里的提到的 {term} 是什么意思？对我有啥影响？"
            await context.bot.send_message(chat_id=chat_id, text=f"👤 您提问了：{user_question}")
            await self._run_rag_and_reply(context, chat_id, user_question)

        # 4. 快讯 / 哨兵新闻 点播引擎
        elif callback_data.startswith("read_flash:"):
            flash_id = callback_data.replace("read_flash:", "")
            flash_data = self.flash_cache.get(flash_id)
            if not flash_data:
                await query.answer("过期游离链接：原始数据可能已从缓冲池被清除。", show_alert=True)
                return
                
            await query.message.reply_text(f"🧠 大模型已被您唤醒，正在解构它的潜台词: 『{flash_data['title'][:15]}...』...", disable_notification=True)
            
            processed = self.llm_engine.process_news(flash_data["title"], flash_data["content"], flash_data.get("url"))
            
            raw_mock = {
                "description": flash_data.get("description", flash_data["content"]),
                "url": flash_data.get("url", ""),
                "image": flash_data.get("image", ""),
                "publishedAt": flash_data.get("publishedAt", "")
            }
            await self._dispatch_news_ui(context, chat_id, processed, raw_mock)

        # 5. 错误弹窗交互
        elif callback_data.startswith("alert:"):
            alert_msg = callback_data.replace("alert:", "")
            await query.answer(alert_msg, show_alert=True)
            
        # 6. 藏经阁书籍原生精练
        elif callback_data.startswith("eval_book:"):
            book_id = callback_data.replace("eval_book:", "")
            book_name = self.book_map.get(book_id)
            if not book_name:
                await query.answer("此书单已过期或被洗出内存，请重新发送 /books 唤起最新书单！", show_alert=True)
                return
                
            await query.message.reply_text(f"\u2694\ufe0f 正在调用宏观交易员模块底层的 Chroma 检索库强制剥离并评价《{book_name[:40]}》的灵魂金句...", disable_notification=True)
            
            # 截断书名防止超长文件名触发 Telegram HTTP 层 UnicodeEncodeError
            safe_name = book_name.encode('utf-8', errors='replace').decode('utf-8')
            if len(safe_name) > 50:
                safe_name = safe_name[:50] + "..."
            gen = self.rag_engine.evaluate_classic_book(book_name)
            header = f"📖 **深度客观评析与金句溯源**：`{safe_name}`\n\n"
            await self._stream_to_message(context, chat_id, gen, header=header)

        # 7. 公告翻页
        elif callback_data.startswith("page_ann:"):
            _, keyword, page_str = callback_data.split(':', 2)
            await self._fetch_and_render_ann_page(update, context, keyword, int(page_str), message_to_edit=query.message)
            await query.answer()
            
        # 8. 公告阅读
        elif callback_data.startswith("read_ann:"):
            ann_id = callback_data.replace("read_ann:", "")
            ann = self.announcement_cache.get(ann_id)
            if not ann:
                await query.answer("公告缓存已过期，请重新查询。", show_alert=True)
                return
                
            await query.answer("🧠 大模型正在逐字研读公告...", show_alert=False)
            wait_msg = await query.message.reply_text(f"⏳ 正在跨网直连底层 PDF 档案并调用 AI 分析：\n{ann['title']}")
            
            # 同步调用大模型
            processed = self.announcement_decoder.decode_announcement(ann)
            
            msg = (
                f"🏢 **公司公告解码** | **{ann['code']}**\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📑 **标题**：{processed['title']}\n"
                f"🕒 **时间**：{processed['time']}\n"
                f"📊 **AI 情感判断**：{processed['sentiment']}\n\n"
                f"🧠 **核心解读**：\n{processed['ai_summary']}\n\n"
            )
            if processed.get("url"):
                msg += f"🔗 [查看原公告PDF]({processed['url']})"
            
            await wait_msg.edit_text(msg, parse_mode='Markdown', disable_web_page_preview=True)


    async def handle_book_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理用户上传的 EPUB/PDF 电子书，即时增量建库"""
        user_id = update.effective_user.id
        
        # 验权：只有机主才能上传书籍
        if OWNER_USER_ID != 0 and user_id != OWNER_USER_ID:
            await update.message.reply_text("🚫 该功能仅限机主账号操作。")
            return
            
        doc = update.message.document
        if not doc:
            return
            
        filename = doc.file_name or ""
        ext = os.path.splitext(filename)[1].lower()
        
        if ext not in (".epub", ".pdf"):
            await update.message.reply_text(
                f"⚠️ 不支持该文件格式（{ext or '无后缀'}），目前只接纳 <b>.epub</b> 和 <b>.pdf</b>。",
                parse_mode='HTML'
            )
            return

        # Telegram 免费档单文件最大 20MB
        MAX_SIZE_MB = 20
        if doc.file_size and doc.file_size > MAX_SIZE_MB * 1024 * 1024:
            await update.message.reply_text(
                f"⚠️ 文件过大（{doc.file_size // 1024 // 1024}MB），Telegram API 免费档单上限为 20MB。"
            )
            return

        progress_msg = await update.message.reply_text(
            f"📥 正在接收：<b>{filename}</b>，下载中...",
            parse_mode='HTML'
        )
        
        try:
            # 下载文件到 mock_books 目录
            os.makedirs(MOCK_BOOKS_DIR, exist_ok=True)
            save_path = os.path.join(MOCK_BOOKS_DIR, filename)
            
            tg_file = await context.bot.get_file(doc.file_id)
            await tg_file.download_to_drive(save_path)
            
            await progress_msg.edit_text(
                f"✅ <b>{filename}</b> 下载完成！\n"
                f"🔪 正在进行内容剥离与切片...",
                parse_mode='HTML'
            )
            
            # 增量建库：只对这一本书进行向量化
            loader = DocumentLoader(MOCK_BOOKS_DIR)
            
            # 只提取这一本书的内容
            if ext == ".pdf":
                raw_text = loader.extract_text_from_pdf(save_path)
            else:
                raw_text = loader.extract_text_from_epub(save_path)
                
            if not raw_text or len(raw_text.strip()) < 100:
                await progress_msg.edit_text(
                    f"⚠️ 文件 <b>{filename}</b> 内容为空或无法解析（可能是加密或扫描版 PDF）。",
                    parse_mode='HTML'
                )
                return
                
            chunks = loader.text_splitter.split_text(raw_text)
            
            await progress_msg.edit_text(
                f"🧠 <b>{filename}</b> 切割完成，共 {len(chunks)} 个知识片段。\n"
                f"🔥 正在焰烧 CPU 映射向量空间...（根据文件大小需要 1-3 分钒）",
                parse_mode='HTML'
            )
            
            # 注入 Chroma——使用统一的 upsert_single_book 接口，自动带 E5 passage 前缀向量
            vs = self.rag_engine.vector_store
            vs.upsert_single_book(filename, chunks)
                
            await progress_msg.edit_text(
                f"🎉 <b>建库完成！</b>\n\n"
                f"📖 书籍：{filename}\n"
                f"📊 共建立 {len(chunks)} 个知识切片\n\n"
                f"👉 发送 /books 可立即见到新书，并进行 AI 提炼验证。",
                parse_mode='HTML'
            )
            print(f"[上传建库] 新书 '{filename}' 建库完成，{len(chunks)} 切片已注入。")

            
        except Exception as e:
            await progress_msg.edit_text(
                f"❌ 建库失败: <code>{e}</code>",
                parse_mode='HTML'
            )
            print(f"[上传建库异常] {e}")

    async def handle_user_question(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_user.id
        user_question = update.message.text
        await self._run_rag_and_reply(context, chat_id, user_question)

    async def _stream_to_message(self, context, chat_id: int, async_gen, header: str = "") -> str:
        """通用流式渲染助手：先发占位消息，逐块积累 token 并定期覆盖编辑。返回最终完整文本。"""
        import asyncio
        CHUNK_THRESHOLD = 60   # 每积累多少字就刷新一次
        THROTTLE_SECS  = 0.8   # 至少 0.8 秒间隔（防TG限流）

        placeholder = await context.bot.send_message(
            chat_id=chat_id,
            text=(header + "\u23f3 正在思考...") if header else "\u23f3 正在思考..."
        )

        accumulated = header
        last_edit_len = len(accumulated)
        last_edit_time = asyncio.get_event_loop().time()

        async for token in async_gen:
            accumulated += token
            now = asyncio.get_event_loop().time()
            chars_since = len(accumulated) - last_edit_len
            if chars_since >= CHUNK_THRESHOLD and (now - last_edit_time) >= THROTTLE_SECS:
                try:
                    await placeholder.edit_text(accumulated + "\u258c")
                    last_edit_len = len(accumulated)
                    last_edit_time = now
                except Exception:
                    pass

        try:
            await placeholder.edit_text(accumulated)
        except Exception:
            pass

        return accumulated

    async def _run_rag_and_reply(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, question: str):
        related_news = self.user_context.get(chat_id)
        if not related_news:
            await context.bot.send_message(chat_id, "\ud83e\udd14 我们目前还没有正在讨论的话题哦。请先点击一篇新闻进行深度解读。")
            return

        gen = self.rag_engine.generate_tutor_response(question, related_news)
        await self._stream_to_message(context, chat_id, gen)

    # ===== 关键字监控命令 =====

    async def watch_keyword(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /watch <关键字/股票名称/代码> 命令"""
        text = update.message.text.strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await update.message.reply_text(
                "⚠️ 请指定要监控的关键字或股票名称/代码。\n\n"
                "用法示例：\n"
                "`/watch 特斯拉`\n"
                "`/watch AAPL`\n"
                "`/watch 比特币`",
                parse_mode='Markdown'
            )
            return

        user_input = parts[1].strip()
        wait_msg = await update.message.reply_text(f"🧠 正在使用 AI 分析并扩展 [{user_input}] 的关联关键字...")

        # LLM 扩展关键字
        keywords = self.keyword_manager.expand_keywords_via_llm(user_input)

        # 保存
        watch = self.keyword_manager.add_watch(user_input, keywords)

        # 格式化回复
        kw_list = "\n".join([f"  • `{kw}`" for kw in watch['keywords']])
        await wait_msg.edit_text(
            f"✅ **监控已设置**：`{watch['name']}`\n\n"
            f"🔍 系统将使用以下关键字自动过滤新闻/快讯：\n{kw_list}\n\n"
            f"📥 命中的内容会自动深度爬取、AI精炼后保存。\n"
            f"📤 发送 `/digest` 随时获取整理报告。",
            parse_mode='Markdown'
        )

    async def unwatch_keyword(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /unwatch <关键字> 命令"""
        text = update.message.text.strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await update.message.reply_text(
                "⚠️ 请指定要移除的监控名称。\n"
                "用法：`/unwatch 特斯拉`\n\n"
                "发送 `/watchlist` 查看当前所有监控。",
                parse_mode='Markdown'
            )
            return

        name = parts[1].strip()
        removed = self.keyword_manager.remove_watch(name)
        if removed:
            await update.message.reply_text(f"✅ 已移除监控：`{name}`", parse_mode='Markdown')
        else:
            await update.message.reply_text(
                f"❌ 未找到名为 `{name}` 的监控。\n发送 `/watchlist` 查看当前所有监控。",
                parse_mode='Markdown'
            )

    async def show_watchlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /watchlist 命令"""
        watches = self.keyword_manager.get_all_watches()
        if not watches:
            await update.message.reply_text(
                "📭 当前没有设置任何监控关键字。\n\n"
                "使用 `/watch <关键字>` 开始监控，例如：\n"
                "`/watch 特斯拉`\n"
                "`/watch 比特币`",
                parse_mode='Markdown'
            )
            return

        # 同时显示当前待整理的新闻条数
        matched_count = len(self.news_filter.get_all_matched())

        text_lines = [f"🎯 **关键字监控面板** (共 {len(watches)} 组)\n"]
        for i, watch in enumerate(watches):
            kw_display = ", ".join([f"`{kw}`" for kw in watch['keywords']])
            text_lines.append(f"{i+1}. **{watch['name']}**")
            text_lines.append(f"   关键字：{kw_display}")
            text_lines.append(f"   创建时间：{watch.get('created_at', 'N/A')}\n")

        if matched_count > 0:
            text_lines.append(f"\n📥 当前已积累 **{matched_count}** 条命中新闻，发送 `/digest` 获取整理报告。")
        else:
            text_lines.append("\n📭 暂无命中新闻，系统正在后台持续监控中...")

        await update.message.reply_text("\n".join(text_lines), parse_mode='Markdown')

    async def send_digest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /digest 命令：按关键字分多条发送已匹配的新闻"""
        chat_id = update.effective_user.id
        
        digest_meta = self.digest_builder.get_digest_metadata()
        if digest_meta.get("empty"):
            await context.bot.send_message(
                chat_id, 
                "📭 当前没有命中关键字的新闻待整理。\n\n"
                "💡 提示：请先使用 `/watch <关键字>` 设置监控，系统会自动在后台过滤并积累相关新闻。",
                parse_mode='Markdown'
            )
            return

        wait_msg = await update.message.reply_text("📊 正在分批整理已捕获的新闻，请稍候...")

        # 1. 发送头部总览
        header_text = (
            "📊 **新闻整理报告**\n"
            "━━━━━━━━━━━━━━━━\n"
            f"共 {digest_meta['total_count']} 条命中新闻，涉及 {digest_meta['group_count']} 个监控主题\n"
        )
        await context.bot.send_message(chat_id, text=header_text, parse_mode='Markdown')

        # 2. 针对每个监控关键字分别发送一条消息
        for watch_name, items in digest_meta["groups"].items():
            group_header = f"🏷️ **【{watch_name}】** ({len(items)} 条)\n{'─' * 20}\n\n"
            gen = self.digest_builder.stream_group(watch_name, items)
            # 使用流式渲染单条关键字新闻
            await self._stream_to_message(context, chat_id, gen, header=group_header)

        # 3. 发送底部总结并清理
        footer_text = f"✅ 整理完毕，共 {digest_meta['total_count']} 条新闻已分发。新闻池已清空。"
        await context.bot.send_message(chat_id, text=footer_text)
        
        self.news_filter.clear_matched()

        try:
            await wait_msg.delete()
        except Exception:
            pass

    # ==========================
    # 公司公告监控命令处理
    # ==========================

    # ==========================
    # 公司公告按需查询
    # ==========================
    async def manual_announcement(self, update, context):
        text = update.message.text.strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await update.message.reply_text("⚠️ 请提供股票代码或名称。例如：`/ann 603986` 或 `/ann 比亚迪`", parse_mode='Markdown')
            return
            
        keyword = parts[1].strip()
        wait_msg = await update.message.reply_text(f"🔍 正在检索 [{keyword}] 的最新公告...")
        await self._fetch_and_render_ann_page(update, context, keyword, 1, wait_msg)

    async def _fetch_and_render_ann_page(self, update, context, keyword, page, message_to_edit=None):
        anns = self.announcement_api.fetch_latest_announcements(keyword, page=page, limit=10)
        if not anns:
            if message_to_edit:
                await message_to_edit.edit_text(f"❌ 未能找到 [{keyword}] 第 {page} 页的公告，可能已达末尾或输入有误。")
            else:
                if update.callback_query:
                    await update.callback_query.message.reply_text(f"❌ 未能找到 [{keyword}] 第 {page} 页的公告。")
                else:
                    await update.message.reply_text(f"❌ 未能找到 [{keyword}] 第 {page} 页的公告。")
            return
            
        text_lines = [f"🏢 **[{keyword}] 最新公告** (第 {page} 页)\n"]
        keyboard = []
        num_row = []
        
        for i, ann in enumerate(anns):
            global_idx = (page - 1) * 10 + i + 1
            self.announcement_cache[ann['id']] = ann
            title = ann['title']
            time_str = ann['time'][:10]
            text_lines.append(f"{global_idx}. {title} ({time_str})")
            
            btn = __import__('telegram').InlineKeyboardButton(text=f"[ {global_idx} ]", callback_data=f"read_ann:{ann['id']}")
            num_row.append(btn)
            if len(num_row) == 5:
                keyboard.append(num_row)
                num_row = []
                
        if num_row:
            keyboard.append(num_row)
            
        nav_row = []
        if page > 1:
            nav_row.append(__import__('telegram').InlineKeyboardButton("🔼 上一页", callback_data=f"page_ann:{keyword}:{page-1}"))
        else:
            nav_row.append(__import__('telegram').InlineKeyboardButton("🚫 已是首页", callback_data="alert:已经是第一页了！"))
            
        # We assume if we got exactly 10, there might be a next page
        if len(anns) == 10:
            nav_row.append(__import__('telegram').InlineKeyboardButton("🔽 下一页", callback_data=f"page_ann:{keyword}:{page+1}"))
        else:
            nav_row.append(__import__('telegram').InlineKeyboardButton("🚫 已是末页", callback_data="alert:已到达结果末尾！"))
            
        if nav_row:
            keyboard.append(nav_row)
            
        text_lines.append("\n👉 _点击下方编号，AI 将为您极速解码这份研报的潜台词：_")
        reply_markup = __import__('telegram').InlineKeyboardMarkup(keyboard)
        
        if message_to_edit:
            await message_to_edit.edit_text("\n".join(text_lines), reply_markup=reply_markup, parse_mode='Markdown')
        else:
            if update.callback_query:
                await update.callback_query.message.edit_text("\n".join(text_lines), reply_markup=reply_markup, parse_mode='Markdown')
            else:
                await update.message.reply_text("\n".join(text_lines), reply_markup=reply_markup, parse_mode='Markdown')

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        error_msg = str(context.error)
        if "NetworkError" in error_msg or "ReadError" in error_msg or "ConnectError" in error_msg:
            print(f"🌍 [Telegram 防线] 代理波动拦截: {error_msg}")
        else:
            print(f"⚠️ [Telegram 未知异常] {context.error}")

    def run(self):
        if not TELEGRAM_BOT_TOKEN or "修改为你" in TELEGRAM_BOT_TOKEN:
            print("[错误] 未配置有效的 TELEGRAM_BOT_TOKEN！服务离线。(TOKEN:", TELEGRAM_BOT_TOKEN, ")")
            return
            
        # 挂载海量缓存重构环节（重要）
        self.news_fetcher.initialize_global_news()
            
        print("🤖 Telegram 智能体调度中心正在启动...")
        app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
        
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("news", self.manual_news))
        app.add_handler(CommandHandler("flash", self.manual_flash))
        app.add_handler(CommandHandler("flash_on", self.flash_on))
        app.add_handler(CommandHandler("flash_off", self.flash_off))
        app.add_handler(CommandHandler("books", self.manual_books))
        # 关键字监控命令
        app.add_handler(CommandHandler("watch", self.watch_keyword))
        app.add_handler(CommandHandler("unwatch", self.unwatch_keyword))
        app.add_handler(CommandHandler("watchlist", self.show_watchlist))
        app.add_handler(CommandHandler("digest", self.send_digest))
        # 股票公告监控命令
        app.add_handler(CommandHandler("ann", self.manual_announcement))
        
        app.add_handler(CallbackQueryHandler(self.handle_button_click))
        # 书籍上传：优先于文字消息处理，必须放在 TEXT handler 之前
        app.add_handler(MessageHandler(filters.Document.ALL, self.handle_book_upload))
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.handle_user_question))
        
        # 挂载全局异常拦截器
        app.add_error_handler(self._error_handler)
        
        # 定时挂载区：宏观事件哨兵与高频快讯雷达
        app.job_queue.run_repeating(self.background_poll_news, interval=1800, first=60) # 延迟60秒免得和系统初始化冲突
        app.job_queue.run_repeating(self.background_poll_flash, interval=30, first=80)
        print("✅ 伴学智能体巨兽已苏醒！即刻前往 Telegram 对话。")
        print(f"📡 关键字监控已挂载，当前 {len(self.keyword_manager.get_all_watches())} 组监控活跃。")
        app.run_polling()
