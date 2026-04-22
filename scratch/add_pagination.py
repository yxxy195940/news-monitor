import re

def main():
    with open('src/delivery/telegram_ui.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Replace manual_announcement with pagination-aware version
    old_method_regex = r"    async def manual_announcement\(self, update, context\):.*?await wait_msg\.edit_text\(\"\\n\"\.join\(text_lines\), reply_markup=reply_markup, parse_mode='Markdown'\)"
    
    new_methods = """    async def manual_announcement(self, update, context):
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
                await query.message.reply_text(f"❌ 未能找到 [{keyword}] 第 {page} 页的公告。")
            return
            
        text_lines = [f"🏢 **[{keyword}] 最新公告** (第 {page} 页)\\n"]
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
            
        text_lines.append("\\n👉 _点击下方编号，AI 将为您极速解码这份研报的潜台词：_")
        reply_markup = __import__('telegram').InlineKeyboardMarkup(keyboard)
        
        if message_to_edit:
            await message_to_edit.edit_text("\\n".join(text_lines), reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.callback_query.message.edit_text("\\n".join(text_lines), reply_markup=reply_markup, parse_mode='Markdown')"""
            
    content = re.sub(old_method_regex, new_methods, content, flags=re.DOTALL)
    
    # 2. Add callback handler for page_ann
    callback_logic = """        elif action == 'page_ann':
            keyword, page_str = data.split(':', 1)
            await self._fetch_and_render_ann_page(update, context, keyword, int(page_str), message_to_edit=query.message)
            await query.answer()
            
        elif action == 'read_ann':"""
        
    content = content.replace("        elif action == 'read_ann':", callback_logic)
    
    with open('src/delivery/telegram_ui.py', 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    main()
