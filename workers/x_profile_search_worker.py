"""
X Profile Search Worker — People 搜索找关键人

流程：
  1. 对每个项目 handle 搜索 x.com/search?q=@{handle}&f=user
  2. 遍历搜索结果用户卡片
  3. 提取卡片全文，同时满足以下两个条件才收录：
       a. 卡片文本（bio/display_name）中包含该项目的 @handle
       b. 卡片文本中包含角色关键词（CEO / CMO / Growth / Founder）
  4. 写入 x_contacts 表

为什么用 People 搜索而非 following 列表：
  following 列表会引入大量无关账号（营销机构、KOL 合作等）；
  People 搜索 + bio 里有项目 @handle 才是真正的自我认同成员。

为什么用全文搜索而非逐行解析：
  X 搜索结果卡片的 innerText 结构因客户端版本变化频繁，
  全文匹配更稳定，且 X 在 People 搜索里会把 bio 中匹配到的内容展示出来。
"""
import asyncio
import re
import random
import urllib.parse

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
from workers.base_worker import BaseWorker

# ── 角色关键词 ────────────────────────────────────────────────────────────────
ROLE_KEYWORDS = {
    "ceo": [
        "ceo", "chief executive", "c.e.o",
    ],
    "cmo": [
        "cmo", "chief marketing", "c.m.o",
        "head of marketing", "marketing lead", "marketing director",
        "vp of marketing", "vp marketing",
    ],
    "growth": [
        "head of growth", "vp of growth", "vp growth",
        "growth lead", "growth hacker", "growth manager",
        "director of growth",
        # 单独的 "growth" 太宽泛（营销机构也会有），只匹配带定语的形式
    ],
    "founder": [
        "founder", "co-founder", "cofounder", "co founder",
        "founding", "创始人", "联合创始人",
    ],
}

SEARCH_URL = "https://x.com/search?q={query}&f=user&src=typed_query"
SEL_USER_CELL = "[data-testid='UserCell']"

# 触发限速时等待的秒数（等完自动继续）
RATE_LIMIT_WAIT = 90

# X 限速/错误页面的特征文本
RATE_LIMIT_SIGNALS = [
    "rate limit exceeded",
    "something went wrong",
    "try again",
    "请稍后重试",
    "too many requests",
    "/error",
]


def _detect_role(text: str) -> str | None:
    """在文本中检测角色关键词，ceo 优先级最高。"""
    t = text.lower()
    for role in ("ceo", "cmo", "growth", "founder"):
        for kw in ROLE_KEYWORDS[role]:
            if kw in t:
                return role
    return None


def _contains_project_handle(text: str, project_username: str) -> bool:
    """
    判断文本中是否明确提到了项目的 X handle。
    匹配形式：@handle、x.com/handle、twitter.com/handle（均忽略大小写）。
    """
    t = text.lower()
    u = project_username.lower()
    return (
        f"@{u}" in t
        or f"x.com/{u}" in t
        or f"twitter.com/{u}" in t
    )


class XProfileSearchWorker(BaseWorker):
    """People 搜索 → bio 含项目 handle + 角色关键词 → 写入 x_contacts"""

    def __init__(self, log_callback, progress_callback=None):
        super().__init__(log_callback, progress_callback)

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
            self.log("请安装 playwright：pip install playwright && playwright install")
            return

        x_links = db.get_x_links_for_profile_search()
        if not x_links:
            self.log("没有待搜索的 X 项目 handle（已全部完成）")
            return

        self.log(f"共 {len(x_links)} 个项目 handle 待搜索关键人...")

        async with async_playwright() as p:
            page, context, need_close = await self._launch_browser(p)
            if page is None:
                return
            try:
                self.log("浏览器已打开，请确认已登录 X，然后点击「已登录就绪」按钮。")
                self._ready = False
                while not getattr(self, '_ready', False):
                    if self._stop:
                        self.log("已停止")
                        return
                    await asyncio.sleep(0.5)

                await self._search_loop(page, x_links)
            finally:
                if need_close:
                    await context.close()

    async def _launch_browser(self, p):
        """
        只连接 start_chrome_cdp.sh 启动的 CDP Chrome。CDP 失败不再 fallback
        到 launch_persistent_context — 那条路径用的是独立的会话目录，弹出
        来跟同事的登录态完全无关，徒增空白浏览器困惑。
        """
        last_err = None
        for attempt in range(1, 4):
            try:
                self.log(f"[浏览器] 连接 Chrome CDP（端口 9222），尝试 {attempt}/3...")
                browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = context.pages[0] if context.pages else await context.new_page()
                await self._maybe_apply_legacy_stealth(context, page)
                await page.bring_to_front()
                self.log("[浏览器] 已连接（CDP 模式，使用真实 Chrome 指纹）✓")
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
        self.log("  3. 回到本程序，重新点「▶ 开始搜索」")
        self.log("─" * 50)
        return None, None, False

    async def _maybe_apply_legacy_stealth(self, context, page):
        """
        X 关键人搜索也连真实 Chrome。默认不再注入固定 UA/Client Hints，
        避免真实 Chrome 版本和伪装指纹不一致；设置 OUTREACH_X_STEALTH=1
        可临时恢复旧逻辑。
        """
        if os.getenv("OUTREACH_X_STEALTH") != "1":
            return
        try:
            from workers.browser_stealth import STEALTH_INIT_SCRIPT, EXTRA_HTTP_HEADERS
            await context.add_init_script(STEALTH_INIT_SCRIPT)
            await context.set_extra_http_headers(EXTRA_HTTP_HEADERS)
            await page.evaluate(STEALTH_INIT_SCRIPT)
            self.log("[浏览器] 已启用旧版 stealth 注入（OUTREACH_X_STEALTH=1）")
        except Exception as e:
            self.log(f"[浏览器] stealth 注入失败，继续使用真实指纹：{str(e)[:60]}")

    # ── 主循环 ────────────────────────────────────────────────────────────────

    async def _search_loop(self, page, x_links):
        total = len(x_links)
        found_total = 0

        for idx, (x_link_id, handle, project_id) in enumerate(x_links):
            if self._stop:
                self.log("已停止")
                break

            await self._wait_if_paused()
            if self._stop:
                self.log("已停止")
                break

            username = self._extract_project_username(handle)
            if not username:
                self.log(f"[{idx+1}/{total}] 无法解析 handle：{handle}，跳过")
                db.mark_x_link_profile_searched(x_link_id)
                continue

            self.log(f"[{idx+1}/{total}] @{username}")
            result = await self._search_one(page, x_link_id, project_id, username, handle)

            if result == "rate_limited":
                # 不标记 done，下次重跑会继续
                self.log(f"  ⚠ 触发限速，暂停 {RATE_LIMIT_WAIT}s 后继续...")
                for remaining in range(RATE_LIMIT_WAIT, 0, -10):
                    if self._stop:
                        break
                    self.log(f"    等待 {remaining}s...")
                    await asyncio.sleep(min(10, remaining))
                continue  # 不推进进度，重试同一个 handle 会在下轮循环处理（未标记 done）

            found_total += result
            db.mark_x_link_profile_searched(x_link_id)
            if result > 0:
                self.log(f"  → +{result}，累计 {found_total}")
            self.progress(idx + 1, total)

            # 有结果时稍等，无结果时几乎不等
            if idx < total - 1 and not self._stop:
                wait = random.uniform(1.5, 3.0) if result > 0 else random.uniform(0.3, 0.8)
                await asyncio.sleep(wait)
                await self._wait_if_paused()

        self.log(f"\n搜索完成！共找到 {found_total} 个关键人写入 x_contacts")

    # ── 单项目搜索 ────────────────────────────────────────────────────────────

    async def _search_one(self, page, x_link_id, project_id, username, raw_handle):
        """返回新增关键人数量（int），或字符串 'rate_limited'。"""
        query = urllib.parse.quote(f"@{username}")
        url = SEARCH_URL.format(query=query)

        try:
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        except Exception as e:
            self.log(f"  导航失败：{str(e)[:60]}")
            return 0

        # 检测限速 / 错误页
        if await self._is_rate_limited(page):
            return "rate_limited"

        # 等卡片出现（最多 6s），出现即继续
        try:
            await page.wait_for_selector(SEL_USER_CELL, timeout=6000)
        except Exception:
            # 再检查一次是否限速（有时错误页延迟加载）
            if await self._is_rate_limited(page):
                return "rate_limited"
            return 0

        # 一次轻量滚动
        await page.evaluate("window.scrollBy(0, 600)")
        await asyncio.sleep(0.6)

        cells = page.locator(SEL_USER_CELL)
        total_cells = await cells.count()
        if total_cells > 3:
            self.log(f"  {total_cells} 个结果，筛选中...")

        found = 0
        for i in range(total_cells):
            if self._stop:
                break
            cell = cells.nth(i)
            try:
                person = await self._extract_cell(cell, username)
            except Exception:
                continue

            if person is None:
                continue

            self.log(
                f"  ✓ @{person['username']}  [{person['role'].upper()}]  "
                f"{person['display_name'][:25]}  |  {person['bio'][:60]}"
            )
            is_new = db.insert_x_contact(
                x_link_id=x_link_id,
                project_id=project_id,
                username=person["username"],
                display_name=person["display_name"],
                bio=person["bio"],
                role=person["role"],
                project_handle=raw_handle,
            )
            if is_new:
                found += 1

        return found

    # ── 限速检测 ─────────────────────────────────────────────────────────────

    async def _is_rate_limited(self, page) -> bool:
        """检测当前页面是否是 X 的限速/错误页。"""
        try:
            # URL 跳转到错误页
            if any(s in page.url for s in ("/error", "login")):
                return True
            body = (await page.inner_text("body")).lower()
            return any(s in body for s in RATE_LIMIT_SIGNALS)
        except Exception:
            return False

    # ── 卡片解析 ─────────────────────────────────────────────────────────────

    async def _extract_cell(self, cell, project_username: str) -> dict | None:
        """
        解析一个 UserCell。
        返回 dict 当且仅当：
          1. 卡片文本包含 @{project_username}（bio 里明确提到项目）
          2. 卡片文本包含角色关键词
        否则返回 None。
        """
        # ── 提取 username（从 href）──────────────────────────────────────────
        cell_username = ""
        links = cell.locator("a[href^='/']")
        for j in range(await links.count()):
            href = await links.nth(j).get_attribute("href") or ""
            if href.startswith("/") and not href.startswith("/i/") and href.count("/") == 1:
                cell_username = href.lstrip("/").lower()
                break

        if not cell_username:
            return None

        # 跳过项目自身账号出现在结果里的情况
        if cell_username == project_username.lower():
            return None

        # ── 取卡片全文 ──────────────────────────────────────────────────────
        full_text = (await cell.inner_text()).strip()

        # ── 过滤条件 1：bio 里必须明确提到项目 handle ───────────────────────
        if not _contains_project_handle(full_text, project_username):
            return None

        # ── 过滤条件 2：必须有角色关键词 ────────────────────────────────────
        role = _detect_role(full_text)
        if not role:
            return None

        # ── 解析 display_name 和 bio ─────────────────────────────────────────
        lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
        display_name = lines[0] if lines else cell_username

        # bio = @handle 行之后、Follow 行之前的内容
        bio_lines: list[str] = []
        state = "seek_handle"
        for ln in lines[1:]:
            if state == "seek_handle":
                if ln.lower().startswith("@") or ln.lower() == cell_username:
                    state = "in_bio"
            elif state == "in_bio":
                low = ln.lower()
                if low in ("follow", "following", "follows you", "pending"):
                    break
                # 跳过纯数字（关注数/粉丝数）
                if re.match(r'^[\d,\.]+\s*(following|followers|k|m)?$', low):
                    continue
                bio_lines.append(ln)

        bio = " ".join(bio_lines).strip()

        # 如果解析出的 bio 为空，把整个卡片文本当 bio 存（至少有内容可查）
        if not bio:
            bio = full_text[:200]

        return {
            "username": cell_username,
            "display_name": display_name,
            "bio": bio,
            "role": role,
        }

    @staticmethod
    def _extract_project_username(handle: str) -> str:
        handle = handle.strip()
        if handle.startswith("http"):
            m = re.search(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})", handle)
            return m.group(1).lower() if m else ""
        return handle.lstrip("@").lower()
