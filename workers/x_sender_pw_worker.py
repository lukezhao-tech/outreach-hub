"""
X (Twitter) Sender Worker — 通过 CDP 连接 start_chrome_cdp.sh 启动的真实
Chrome，自动化发送 DM。登录态/cookies/历史都在用户启动的 Chrome 里。
"""
import asyncio
import random
import re
import time

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
from workers.base_worker import BaseWorker
from workers.browser_stealth import STEALTH_INIT_SCRIPT, EXTRA_HTTP_HEADERS

# ── 选择器（基于 Twitter DM 页面实际结构）───────────────────────────────────
DM_URL = "https://x.com/i/chat"

# New chat 按钮
SEL_NEW_CHAT = "xpath=//*[@id='dm-main-container']/div[2]/div/button"

# 搜索框（稳定 data-testid）
SEL_SEARCH_INPUT = "[data-testid='new-dm-search-input']"

# DM 消息输入框
SEL_DM_TEXTAREA = "xpath=//*[@id='dm-main-container']//form//textarea"

# 发送按钮
SEL_SEND_BTN = "xpath=//*[@id='dm-main-container']//form//button"


class XSenderPWWorker(BaseWorker):
    """Playwright 版 Twitter DM 发送 Worker（macOS）

    mode:
      'x_links'    — 发送给项目官号（x_links 表，默认）
      'x_contacts' — 发送给关键人（x_contacts 表，按 role 过滤）
    """

    def __init__(self, log_callback, progress_callback=None,
                 message_name="", message_content="", source=None,
                 mode="x_links", role=None):
        super().__init__(log_callback, progress_callback)
        self.message_name    = message_name
        self.message_content = message_content
        self.source          = source  # x_links 模式下的 source 过滤
        self.mode            = mode    # 'x_links' | 'x_contacts'
        self.role            = role    # x_contacts 模式下的 role 过滤

    def set_ready(self):
        """由 GUI / Web API 调用，通知 worker 可以开始发送"""
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
            self.log("请安装 playwright：pip install playwright && playwright install")
            return

        if not self.message_content:
            self.log("消息内容为空，请先在「文案」页配置激活文案")
            return

        async with async_playwright() as p:
            page, context, need_close = await self._launch_browser(p)
            if page is None:
                return

            try:
                await self._send_loop(page)
            finally:
                if need_close:
                    await context.close()

    async def _launch_browser(self, p):
        """
        连接 start_chrome_cdp.sh 启动的真实 Chrome（CDP 模式，端口 9222）。
        失败时不再 fallback 启动 Playwright 自带 Chromium —— 那条路径用的是
        独立的 data/twitter_pw_session/，与同事在 CDP Chrome 里的登录态完全
        无关，弹出来必然是空白页，徒增困惑。
        """
        last_err = None
        for attempt in range(1, 4):
            try:
                self.log(f"[浏览器] 连接 Chrome CDP（端口 9222），尝试 {attempt}/3...")
                browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                try:
                    await context.add_init_script(STEALTH_INIT_SCRIPT)
                except Exception:
                    pass
                try:
                    await context.set_extra_http_headers(EXTRA_HTTP_HEADERS)
                except Exception:
                    pass
                page = context.pages[0] if context.pages else await context.new_page()
                try:
                    await page.evaluate(STEALTH_INIT_SCRIPT)
                except Exception:
                    pass
                await page.bring_to_front()
                self.log("[浏览器] 已连接（CDP 模式）✓")
                return page, context, False
            except Exception as e:
                last_err = e
                if attempt < 3:
                    await asyncio.sleep(1)

        self.log("─" * 50)
        self.log(f"❌  连接 Chrome CDP 失败：{str(last_err)[:80]}")
        self.log("")
        self.log("常见原因：")
        self.log("  • CDP Chrome 未启动 / 已退出（窗口被误关）")
        self.log("  • 9222 端口被占用")
        self.log("")
        self.log("修复步骤：")
        self.log("  1. 终端运行：./scripts/start_chrome_cdp.sh --system")
        self.log("  2. 弹出 Chrome 后确认 x.com 已登录")
        self.log("  3. 回到本程序，重新点「▶ 开始发送」")
        self.log("─" * 50)
        return None, None, False

    async def _send_loop(self, page):
        """主发送循环"""
        # 导航到 DM 页
        self.log(f"打开 DM 页面：{DM_URL}")
        try:
            await page.goto(DM_URL, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(3)
        except Exception as e:
            self.log(f"[错误] 无法打开 DM 页面：{str(e)[:80]}")
            return

        # 等待用户确认登录
        self.log("浏览器已打开，请确认已登录 Twitter，然后点击「已登录就绪」按钮。")
        self._ready = False
        while not getattr(self, '_ready', False):
            if self._stop:
                self.log("已停止")
                return
            await asyncio.sleep(0.5)

        # 获取待发列表
        if self.mode == "x_contacts":
            handles = db.get_unsent_x_contacts(role=self.role)
            role_label = self.role or "全部角色"
            if not handles:
                self.log(f"没有待发送的 X 关键人（{role_label}）")
                return
            self.log(f"待发 {len(handles)} 个关键人（{role_label}）")
        else:
            handles = db.get_unsent_x_handles(source=self.source)
            if not handles:
                src_label = {"cb_excel": "仓库导入", "cb_discover": "融资项目（CB Discover）",
                             "rootdata": "融资项目（RootData）", "chainscope": "链上变化",
                             "tokenfinder": "低交易量", "campaign": "活动举办",
                             "cryptorank": "融资项目（CryptoRank）"}.get(self.source, "全部")
                self.log(f"没有待发送的 X 用户（{src_label}）")
                return
            self.log(f"待发 {len(handles)} 个 X 用户")

        sent_count = 0
        skip_count = 0

        for idx, raw_handle in enumerate(handles):
            if self._stop:
                self.log("已停止")
                break

            await self._wait_if_paused()
            if self._stop:
                self.log("已停止")
                break

            handle = self._normalize_handle(raw_handle)
            self.log(f"[{idx+1}/{len(handles)}] {handle}")

            try:
                success = await self._send_dm(page, handle)
            except Exception as e:
                self.log(f"  发送异常：{str(e)[:80]}")
                success = False

            if success:
                src_tag = (
                    f"x_contacts:{self.role or 'all'}"
                    if self.mode == "x_contacts"
                    else (self.source or "x_link")
                )
                db.log_send(raw_handle, "twitter", src_tag, self.message_name)
                sent_count += 1
                self.log(f"  已发送")
            else:
                skip_count += 1
                self.log(f"  跳过")

            self.progress(idx + 1, len(handles))

            # 随机延迟（最后一个不发）
            if idx < len(handles) - 1 and not self._stop:
                wait = random.uniform(5, 15)
                self.log(f"  等待 {wait:.1f}s...")
                await asyncio.sleep(wait)
                await self._wait_if_paused()

        self.log(f"\nX 发送完成！成功 {sent_count}，跳过 {skip_count}")

    async def _send_dm(self, page, handle):
        """
        发送 DM 完整流程：
        New chat → 搜索用户 → 精确匹配 → 输入消息 → 点击发送
        返回 True=发送成功，False=跳过
        """
        try:
            # 1. 每次都回到 DM 主页（发送完上条后在对话页，New chat 按钮不在）
            await page.goto(DM_URL, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            # 2. 点击 New chat 按钮
            new_btn = page.locator(SEL_NEW_CHAT)
            if await new_btn.count() == 0:
                self.log("  New chat 按钮未找到")
                return False
            await new_btn.click()
            await asyncio.sleep(2)

            # 3. 在搜索框输入 handle
            search_input = page.locator(SEL_SEARCH_INPUT).first
            if await search_input.count() == 0:
                self.log("  搜索框未找到")
                await page.keyboard.press("Escape")
                return False

            await search_input.fill(handle)
            self.log(f"  搜索：{handle}")
            await asyncio.sleep(3)

            # 4. 在结果列表中精确匹配
            handle_lower = handle.lower().lstrip('@')
            matched = await self._find_and_click_result(page, handle_lower)
            if not matched:
                self.log(f"  未找到匹配：@{handle_lower}")
                await page.keyboard.press("Escape")
                return False

            self.log(f"  匹配：@{handle_lower}")
            await asyncio.sleep(1)

            # 5. 点击 Next 按钮
            next_btn = page.get_by_role("button", name="Next")
            if await next_btn.count() > 0:
                await next_btn.click()
                await asyncio.sleep(2)

            # 6. 在 textarea 输入 DM 消息
            textarea = page.locator(SEL_DM_TEXTAREA).first
            if await textarea.count() == 0:
                self.log("  消息输入框未找到")
                await page.keyboard.press("Escape")
                return False

            await textarea.click()
            await asyncio.sleep(0.3)
            await textarea.fill(self.message_content)
            await asyncio.sleep(0.5 + random.uniform(0.2, 0.8))

            # 7. 点击发送按钮
            send_btn = page.locator(SEL_SEND_BTN).last  # form 内最后一个 button
            if await send_btn.count() > 0:
                await send_btn.click()
                self.log(f"  已发送")
                await asyncio.sleep(2)
                return True

            # 兜底：按 Enter 发送
            await page.keyboard.press("Enter")
            self.log(f"  已发送（Enter）")
            await asyncio.sleep(2)
            return True

        except Exception as e:
            self.log(f"  发送异常：{str(e)[:80]}")
        finally:
            # 确保关闭任何残留弹窗，回到 DM 主页面
            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
            except Exception:
                pass
            return False

    async def _find_and_click_result(self, page, handle_lower):
        """
        在搜索结果中精确匹配 @handle 的用户并点击。
        返回：True=点击成功，False=未找到或被封禁
        """
        result_locators = [
            "[role='listbox'] [role='option']",
            "[role='list'] [role='listitem']",
            "#dm-main-container [role='option']",
            "#dm-main-container [role='listitem']",
        ]

        for sel in result_locators:
            try:
                items = page.locator(sel)
                count = await items.count()
                if count == 0:
                    continue

                for i in range(min(count, 10)):
                    item = items.nth(i)
                    text = (await item.inner_text()).strip().lower()

                    # 精确匹配 @handle
                    if f"@{handle_lower}" in text:
                        # 先检查是否可点击（被封禁/未开通 DM 的用户元素被遮罩）
                        if not await item.is_enabled():
                            self.log(f"  用户不可点击（封禁/未开通DM）：@{handle_lower}")
                            return False
                        try:
                            await item.click(timeout=3000)
                            return True
                        except Exception:
                            self.log(f"  用户点击失败（封禁/未开通DM）：@{handle_lower}")
                            return False
            except Exception:
                continue

        # 兜底：文本查找
        try:
            el = page.get_by_text(f"@{handle_lower}", exact=False).first
            if await el.count() > 0:
                await el.click()
                return True
        except Exception:
            pass

        return False

    # ── 工具方法 ────────────────────────────────────────────────────────
    @staticmethod
    def _normalize_handle(handle):
        """统一 handle 格式：去掉 @ 和 URL 前缀，返回纯用户名"""
        handle = handle.strip()
        if handle.startswith('http'):
            # 从 URL 提取：https://x.com/username
            m = re.search(r'(?:twitter\.com|x\.com)/([\w\d_]{1,15})', handle)
            if m:
                return m.group(1)
        return handle.lstrip('@')
