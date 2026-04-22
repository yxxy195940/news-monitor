import sys

def refactor():
    with open('src/delivery/telegram_ui.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Imports
    content = content.replace("from src.watchdog.announcement_filter import AnnouncementFilter", "from src.watchdog.announcement_filter import AnnouncementDecoder")

    # 2. Init
    content = content.replace("self.announcement_filter = AnnouncementFilter(self.llm_engine)", "self.announcement_decoder = AnnouncementDecoder(self.llm_engine)\n        self.announcement_cache = {}")

    # 3. /start
    old_start = """            "🏢 **公司内部事件监控（看家本领）：**\\n"\n            "`/watchstock 603986` - 监控指定股票的公司公告（支持名称或代码）\\n"\n            "`/unwatchstock 比亚迪` - 取消个股公告监控\\n"\n            "`/stocklist` - 查看关注的股票池\\n\\n\""""
    new_start = """            "🏢 **公司内部事件透视：**\\n"\n            "`/ann 603986` 或 `/ann 比亚迪` - 即刻调取并深度解读该公司的最新重大公告\\n\\n\""""
    content = content.replace(old_start, new_start)

    # 4. Remove old methods
    # We will use regex to remove methods
    import re
    
    # Remove watch_stock, unwatch_stock, stocklist
    # Notice: def watch_stock... to next def
    content = re.sub(r'    async def watch_stock.*?    async def _error_handler', '    async def _error_handler', content, flags=re.DOTALL)
    
    # Wait, in the previous fix I removed watch_stock but the file still has some.
    # Actually let's just insert manual_announcement right above _error_handler
    
    manual_ann = """
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
        
        anns = self.announcement_api.fetch_latest_announcements(keyword, limit=10)
        if not anns:
            await wait_msg.edit_text(f"❌ 未能找到 [{keyword}] 的近期公告，请检查代码或名称是否正确。")
            return
            
        text_lines = [f"🏢 **[{keyword}] 最新公告**\\n"]
        keyboard = []
        num_row = []
        
        for i, ann in enumerate(anns):
            global_idx = i + 1
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
            
        text_lines.append("\\n👉 _点击下方编号，AI 将为您极速解码这份研报的潜台词：_")
        reply_markup = __import__('telegram').InlineKeyboardMarkup(keyboard)
        await wait_msg.edit_text("\\n".join(text_lines), reply_markup=reply_markup, parse_mode='Markdown')

    async def _error_handler"""
    
    content = content.replace("    async def _error_handler", manual_ann)
    
    # Add callback logic
    callback_logic = """
        elif action == 'eval_book':
            book_id = data
            book_name = self.book_map.get(book_id)
            if not book_name:
                await query.answer("此书单已过期或被洗出内存，请重新发送 /books 唤起最新书单！", show_alert=True)
                return
                
            await query.message.reply_text(f"⚔️ 正在调用宏观交易员模块底层的 Chroma 检索库强制剥离并评价《{book_name[:40]}》的灵魂金句...", disable_notification=True)
            
            # 截断书名防止超长文件名触发 Telegram HTTP 层 UnicodeEncodeError
            safe_name = book_name.encode('utf-8', errors='replace').decode('utf-8')
            if len(safe_name) > 50:
                safe_name = safe_name[:50] + "..."
            gen = self.rag_engine.evaluate_classic_book(book_name)
            header = f"📖 **深度客观评析与金句溯源**：`{safe_name}`\\n\\n"
            await self._stream_to_message(context, chat_id, gen, header=header)
            
        elif action == 'read_ann':
            ann_id = data
            ann = self.announcement_cache.get(ann_id)
            if not ann:
                await query.answer("公告缓存已过期，请重新查询。", show_alert=True)
                return
                
            await query.answer("🧠 大模型正在逐字研读公告...", show_alert=False)
            wait_msg = await query.message.reply_text(f"⏳ 正在跨网直连巨潮/上交所底层 PDF 档案并调用 AI 分析：\\n{ann['title']}")
            
            # 同步调用大模型（公告处理目前未做成流式）
            processed = self.announcement_decoder.decode_announcement(ann)
            
            msg = (
                f"🏢 **公司公告解码** | **{ann['code']}**\\n"
                f"━━━━━━━━━━━━━━━━\\n"
                f"📑 **标题**：{processed['title']}\\n"
                f"🕒 **时间**：{processed['time']}\\n"
                f"📊 **AI 情感判断**：{processed['sentiment']}\\n\\n"
                f"🧠 **核心解读**：\\n{processed['ai_summary']}\\n\\n"
            )
            if processed.get("url"):
                msg += f"🔗 [查看原公告PDF]({processed['url']})"
            
            await wait_msg.edit_text(msg, parse_mode='Markdown', disable_web_page_preview=True)
"""
    # Replace eval_book block to inject read_ann block
    content = re.sub(r"\s*elif action == 'eval_book':.*?await self._stream_to_message\(context, chat_id, gen, header=header\)", callback_logic, content, flags=re.DOTALL)
    
    # Clean up old handlers
    content = re.sub(r'        app.add_handler\(CommandHandler\("watchstock", self.watch_stock\)\)\s*app.add_handler\(CommandHandler\("unwatchstock", self.unwatch_stock\)\)\s*app.add_handler\(CommandHandler\("stocklist", self.stocklist\)\)', '        app.add_handler(CommandHandler("ann", self.manual_announcement))', content)
    
    # Remove background_poll_announcements job
    content = re.sub(r'        app.job_queue.run_repeating\(self.background_poll_announcements, interval=300, first=45\) # 5分钟轮询一次公告\s*', '', content)
    
    # Remove watchstock text from init print
    content = re.sub(r'        print\(f"🏢 股票公告监控挂载，当前 \{len\(self.announcement_filter.get_all_watches\(\)\)\} 只股票活跃。"\)\s*', '', content)

    with open('src/delivery/telegram_ui.py', 'w', encoding='utf-8') as f:
        f.write(content)
        
if __name__ == '__main__':
    refactor()
    print("Refactor complete")
