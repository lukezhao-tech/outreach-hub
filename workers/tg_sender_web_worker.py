"""
TG Sender Worker (macOS) — Playwright 控制 Telegram Web 发送 DM
无需辅助功能/屏幕录制权限。
"""
import asyncio
import random
import re
from datetime import datetime, timedelta

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import config
from workers.base_worker import BaseWorker
from workers.browser_stealth import (
    STEALTH_ARGS, IGNORE_DEFAULT_ARGS, STEALTH_INIT_SCRIPT,
    CONTEXT_KWARGS, EXTRA_HTTP_HEADERS,
)

TG_WEB_URL  = "https://web.telegram.org/k/"
SESSION_DIR = "data/tg_web_session"

SEL_SEARCH       = "input.input-search-input"
SEL_RESULT_ITEMS = "a.chatlist-chat[data-peer-id]"
SEL_MSG_INPUT    = "div.input-message-input[contenteditable='true']:not(.input-field-input-fake)"
SEL_SEND_BTN     = "button.btn-send"


class TGSenderWebWorker(BaseWorker):
    def __init__(self, log_callback, progress_callback=None,
                 source="all", max_per_hour=None,
                 message_name="", message_content=""):
        super().__init__(log_callback, progress_callback)
        self.source          = source or "all"
        self.max_per_hour    = max_per_hour or config.TG_MAX_PER_HOUR
        self.message_name    = message_name
        self.message_content = message_content

    def set_ready(self):
        self._ready = True

    def run(self):
        try:
            self._safe_asyncio_run(self._async_run())
        except Exception as e:
            self.log(f"[错误] {e}")

    async def _async_run(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.log("请安装 playwright：pip install playwright && playwright install chromium")
            return

        if not self.message_content:
            self.log("消息内容为空，请先在「文案」页配置激活文案")
            return

        async with async_playwright() as p:
            page, context = await self._launch_browser(p)
            if page is None:
                return
            try:
                await self._send_loop(page)
            finally:
                await context.close()

    async def _launch_browser(self, p):
        os.makedirs(SESSION_DIR, exist_ok=True)
        try:
            self.log("[浏览器] 启动 Chrome（persistent context）...")
            context = await p.chromium.launch_persistent_context(
                SESSION_DIR,
                headless=False,
                channel="chrome",
                args=STEALTH_ARGS,
                ignore_default_args=IGNORE_DEFAULT_ARGS,
                **CONTEXT_KWARGS,
            )
            await context.add_init_script(STEALTH_INIT_SCRIPT)
            await context.set_extra_http_headers(EXTRA_HTTP_HEADERS)
            page = context.pages[0] if context.pages else await context.new_page()
            return page, context
        except Exception as e:
            self.log(f"[浏览器] 启动失败：{str(e)[:80]}")
            return None, None

    async def _send_loop(self, page):
        self.log(f"打开 Telegram Web：{TG_WEB_URL}")
        await page.goto(TG_WEB_URL, timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        self.log("请确认浏览器已登录 Telegram，然后点击「已登录就绪」按钮。")
        self._ready = False
        while not getattr(self, "_ready", False):
            if self._stop:
                self.log("已停止")
                return
            await asyncio.sleep(0.5)

        handles = db.get_unsent_tg_by_source(self.source)
        if not handles:
            self.log("没有待发送的用户")
            return

        self.log(f"待发 {len(handles)} 个用户，每小时限额 {self.max_per_hour}")

        sent_this_hour = 0
        hour_start     = datetime.now()
        sent_count     = 0
        skip_count     = 0

        for idx, username in enumerate(handles):
            if self._stop:
                self.log("已停止")
                break

            # 小时限额
            now = datetime.now()
            if now - hour_start > timedelta(hours=1):
                sent_this_hour = 0
                hour_start     = now
                self.log("新的一小时，重置计数器")

            if sent_this_hour >= self.max_per_hour:
                next_hour = hour_start + timedelta(hours=1)
                self.log(f"达到限额，等待至 {next_hour.strftime('%H:%M:%S')}...")
                while datetime.now() < next_hour:
                    if self._stop:
                        return
                    await asyncio.sleep(1)
                sent_this_hour = 0
                hour_start     = datetime.now()

            handle = username.lstrip("@")
            self.log(f"[{idx+1}/{len(handles)}] {handle}")

            try:
                success = await self._send_dm(page, handle)
            except Exception as e:
                self.log(f"  发送异常：{str(e)[:80]}")
                success = False

            if success:
                db.log_send(username, "telegram", self.source, self.message_name)
                sent_this_hour += 1
                sent_count     += 1
                self.log(f"  已发送（本小时 {sent_this_hour}/{self.max_per_hour}）")
            else:
                skip_count += 1
                self.log(f"  跳过")

            self.progress(idx + 1, len(handles))

            if idx < len(handles) - 1 and not self._stop:
                wait = random.uniform(8, 18)
                self.log(f"  等待 {wait:.1f}s...")
                await asyncio.sleep(wait)

        self.log(f"\nTG 发送完成！成功 {sent_count}，跳过 {skip_count}")

    async def _send_dm(self, page, handle):
        """
        完整发送流程：
        搜索用户 → 精确匹配结果 → 进入对话 → 输入消息 → 发送
        返回 True=成功，False=跳过
        """
        handle_lower = handle.lower()

        try:
            # 1. 清空搜索框并输入 handle
            search = page.locator(SEL_SEARCH).first
            await search.click()
            await asyncio.sleep(0.3)
            await search.fill("")
            await search.type(handle, delay=50)
            self.log(f"  搜索：{handle}")
            await asyncio.sleep(3)

            # 2. 在结果中精确匹配 @handle
            matched = await self._find_and_click_result(page, handle_lower)
            if not matched:
                self.log(f"  未找到 @{handle_lower}")
                # 清空搜索框，避免影响下一次
                await search.fill("")
                await page.keyboard.press("Escape")
                return False

            await asyncio.sleep(2)

            # 3. 找消息输入框
            msg_input = page.locator(SEL_MSG_INPUT).first
            if await msg_input.count() == 0:
                self.log("  消息输入框未找到")
                return False

            await msg_input.click()
            await asyncio.sleep(0.3)
            await msg_input.fill(self.message_content)
            await asyncio.sleep(0.3 + random.uniform(0.2, 0.5))

            # 4. 点击发送按钮（兜底用 Enter）
            send_btn = page.locator(SEL_SEND_BTN).first
            if await send_btn.count() > 0:
                await send_btn.click()
            else:
                await page.keyboard.press("Enter")

            await asyncio.sleep(2)
            return True

        except Exception as e:
            self.log(f"  异常：{str(e)[:80]}")
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return False

    async def _find_and_click_result(self, page, handle_lower):
        """
        在搜索结果中精确匹配 @handle 并点击。
        结果条目：a.chatlist-chat[data-peer-id]
        匹配依据：条目内 .row-subtitle 文字包含 @handle（不区分大小写）
        """
        items = page.locator(SEL_RESULT_ITEMS)
        count = await items.count()
        if count == 0:
            return False

        for i in range(min(count, 15)):
            item = items.nth(i)
            try:
                text = (await item.inner_text()).lower()
                if f"@{handle_lower}" in text:
                    await item.click(timeout=3000)
                    return True
            except Exception:
                continue

        return False
