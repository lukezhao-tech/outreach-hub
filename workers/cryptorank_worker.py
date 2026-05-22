"""
CryptoRank Funding Rounds Worker
从 cryptorank.io/funding-rounds 逐页爬取项目，点进详情页提取官网 + X 链接。

流程：
1. Playwright 有头浏览器打开列表页
2. 用户手动登录后点「已就绪」
3. 从当前 URL 读取起始页码
4. 逐个点击项目 → 详情页 → Overview tab → 正则提取官网 / X
5. 返回列表页，完成本页后修改 URL 翻页
"""
import asyncio
import re
import random
import json

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
from workers.base_worker import BaseWorker
from workers.browser_stealth import (
    STEALTH_ARGS, IGNORE_DEFAULT_ARGS, STEALTH_INIT_SCRIPT,
    CONTEXT_KWARGS, EXTRA_HTTP_HEADERS,
)

X_PATTERN = re.compile(r'https?://(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})')
# 排除的 X 账号（CryptoRank 自身 + 通用无效）
EXCLUDE_X = {
    "cryptorank_io", "cryptorank_vcs", "cryptorankio",
    "crypto", "home", "share", "intent", "search", "hashtag", "i",
    "settings", "explore", "notifications", "messages", "login",
}

# CryptoRank 自身的 TG，排除
EXCLUDE_TG = {
    "cryptoranknews", "cryptorank_fundraising", "cryptoranken",
    "cryptorank_io_sup", "cryptorank_io",
}

# 社交 / 非官网域名，用于排除
SOCIAL_DOMAINS = [
    'twitter.com', 'x.com', 't.me', 'telegram.', 'discord.',
    'github.com', 'medium.com', 'linkedin.com', 'facebook.com',
    'youtube.com', 'instagram.com', 'reddit.com',
    'cryptorank.io', 'coingecko.com', 'coinmarketcap.com',
    'defillama.com', 'crunchbase.com', 'rootdata.com',
    'apple.com', 'play.google.com', 'apps.apple.com',
    'chrome.google.com', 'addons.mozilla.org',
]

TG_PATTERN = re.compile(r'https?://t\.me/(?:joinchat/[\w\d_-]+|(?!(?:share|s|addstickers|setlanguage)/)[\w\d_]{5,})')


class CryptoRankWorker(BaseWorker):
    def __init__(self, source_tag, log_callback, progress_callback=None,
                 start_url="https://cryptorank.io/funding-rounds?page=1&rows=20",
                 max_pages=999):
        super().__init__(log_callback, progress_callback)
        if not source_tag or source_tag == "tg_left":
            raise ValueError("source_tag 必填，且不能为保留值 'tg_left'")
        self.source_tag = source_tag
        self.start_url = start_url
        self.max_pages = max_pages

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

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=STEALTH_ARGS,
                ignore_default_args=IGNORE_DEFAULT_ARGS,
            )
            context = await browser.new_context(**CONTEXT_KWARGS)
            await context.add_init_script(STEALTH_INIT_SCRIPT)
            await context.set_extra_http_headers(EXTRA_HTTP_HEADERS)
            page = await context.new_page()

            self.log(f"打开：{self.start_url}")
            await page.goto(self.start_url, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            self.log("浏览器已打开，如需登录请先完成，然后点击「已就绪」按钮。")

            # 等待就绪信号
            while not getattr(self, '_ready', False):
                if self._stop:
                    await browser.close()
                    return
                await asyncio.sleep(0.5)

            # 从当前 URL 读取页码和 rows
            current_url = page.url
            current_page = self._parse_page(current_url)
            rows_per_page = self._parse_rows(current_url)
            self.log(f"当前第 {current_page} 页，每页 {rows_per_page} 行")

            total_inserted = 0
            total_skipped = 0
            pages_done = 0

            # 拦截列表页的 API 响应（翻页时触发）
            self._api_rounds = None

            async def _on_response(response):
                url = response.url
                if 'funding-rounds' in url and 'api.cryptorank.io' in url:
                    try:
                        self._api_rounds = await response.json()
                        self.log(f"  [拦截] {url[:100]}")
                    except Exception:
                        pass

            page.on('response', _on_response)

            while not self._stop and pages_done < self.max_pages:
                page_num = current_page + pages_done
                self.log(f"\n── 第 {page_num} 页 ──")

                if pages_done == 0:
                    # 第一页：从已加载的 __NEXT_DATA__ 读取
                    project_links = await self._get_project_links_from_json(page)
                else:
                    # 后续页：在列表页点击下一页，拦截 API 响应
                    self._api_rounds = None
                    clicked = await self._click_next_page(page)
                    if not clicked:
                        self.log("  已到最后一页")
                        break
                    # 等 API 响应
                    for _ in range(30):
                        if self._api_rounds is not None:
                            break
                        await asyncio.sleep(0.5)
                    if self._api_rounds:
                        project_links = self._parse_api_rounds(self._api_rounds)
                    else:
                        self.log("  ⚠ 未拦截到翻页 API 响应")
                        project_links = []

                if not project_links:
                    self.log("  本页无项目，停止")
                    break

                self.log(f"  本页 {len(project_links)} 个项目")

                # 逐个在新 tab 中打开详情页（列表页 tab 保持不动）
                for idx, (name, href) in enumerate(project_links):
                    if self._stop:
                        break

                    self.log(f"  [{idx+1}/{len(project_links)}] {name}")
                    ins, skp = await self._process_project_in_new_tab(context, name, href)
                    total_inserted += ins
                    total_skipped += skp
                    self.progress(total_inserted + total_skipped, 0)
                    await asyncio.sleep(random.uniform(2, 4))

                pages_done += 1

                if self._stop:
                    break

            self.log(f"\n完成！新增 {total_inserted} 条，跳过 {total_skipped} 条")
            self.log(f"数据库 projects 表总数：{db.count_projects()}")
            await browser.close()

    def set_ready(self):
        self._ready = True

    def _parse_page(self, url):
        m = re.search(r'[?&]page=(\d+)', url)
        return int(m.group(1)) if m else 1

    def _parse_rows(self, url):
        m = re.search(r'[?&]rows=(\d+)', url)
        return int(m.group(1)) if m else 20

    async def _click_next_page(self, page):
        """在列表页点击下一页按钮"""
        try:
            # CryptoRank 分页器的下一页按钮
            selectors = [
                'button[aria-label="Next"]',
                'button.next',
                'a[aria-label="Next"]',
                'button:has(> svg[data-icon="chevron-right"])',
            ]
            for sel in selectors:
                btn = page.locator(f'{sel}:not([disabled])')
                if await btn.count() > 0:
                    await btn.first.click()
                    await asyncio.sleep(3)
                    return True

            # 兜底：找分页区域最后一个非 disabled 按钮
            pagination_btns = page.locator('[class*="pagination"] button:not([disabled]), [class*="Pagination"] button:not([disabled])')
            count = await pagination_btns.count()
            if count > 0:
                await pagination_btns.last.click()
                await asyncio.sleep(3)
                return True

            return False
        except Exception as e:
            self.log(f"  ⚠ 翻页失败：{str(e)[:60]}")
            return False

    def _parse_api_rounds(self, api_data):
        """从拦截到的 API 响应中解析项目列表"""
        if not api_data:
            self.log("  ⚠ 未拦截到 API 响应")
            return []
        # 打印结构帮助调试
        if isinstance(api_data, dict):
            self.log(f"  API keys: {list(api_data.keys())}")
            rounds = api_data.get('data', [])
        elif isinstance(api_data, list):
            rounds = api_data
        else:
            return []

        result = []
        seen = set()
        for item in rounds:
            if not isinstance(item, dict):
                continue
            key = item.get('key', '')
            name = item.get('name', '') or key
            if not key or key in seen:
                continue
            seen.add(key)
            href = f"https://cryptorank.io/ico/{key}"
            result.append((name, href))
        return result

    async def _get_project_links_from_json(self, page):
        """第一页：从 __NEXT_DATA__ 提取融资项目列表"""
        try:
            json_text = await page.evaluate(
                "() => document.querySelector('script#__NEXT_DATA__').textContent"
            )
            data = json.loads(json_text)
            rounds = (data.get('props', {}).get('pageProps', {})
                      .get('fallbackRounds', {}).get('data', []))
        except Exception as e:
            self.log(f"  ⚠ 解析列表失败：{str(e)[:80]}")
            return []

        result = []
        seen = set()
        for item in rounds:
            key = item.get('key', '')
            name = item.get('name', '') or key
            if not key or key in seen:
                continue
            seen.add(key)
            href = f"https://cryptorank.io/ico/{key}"
            result.append((name, href))
        self.log(f"  提取到 {len(result)} 个项目")
        return result

    async def _process_project_in_new_tab(self, context, name, href):
        """在新 tab 打开详情页，提取官网/X/TG，完成后关闭 tab"""
        detail_page = await context.new_page()
        inserted = skipped = 0
        try:
            await detail_page.goto(href, timeout=45000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2, 3))

            website, x_handles, tg_links = await self._extract_detail(detail_page)

            # 未找到官网，刷新重试一次
            if not website:
                self.log(f"    未找到官网，刷新重试…")
                await detail_page.reload(timeout=45000, wait_until="networkidle")
                await asyncio.sleep(5)
                website, x_handles, tg_links = await self._extract_detail(detail_page)

            self.log(f"    官网:{website or '无'}  X:{len(x_handles)}  TG:{len(tg_links)}")

            if website:
                project_id = db.upsert_project(name, website, "", source=self.source_tag)
                if project_id:
                    db.mark_project_scraped(project_id)
                    for tg in tg_links:
                        db.insert_tg_link(project_id, tg, source=self.source_tag)
                    for x in x_handles:
                        db.insert_x_link(project_id, x, source=self.source_tag)
                    inserted = 1
                else:
                    skipped = 1
            else:
                skipped = 1
                self.log(f"    ⚠ 重试后仍未找到官网，跳过")

        except Exception as e:
            self.log(f"    ⚠ 出错：{str(e)[:80]}")
            skipped = 1
        finally:
            await detail_page.close()

        return inserted, skipped

    async def _extract_detail(self, page):
        """从详情页提取官网/X/TG，先试 JSON 再试 HTML"""
        # 尝试点击 Overview tab（如果有）
        try:
            overview_tab = page.locator('a:has-text("Overview"), button:has-text("Overview")').first
            if await overview_tab.count() > 0:
                await overview_tab.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        html = await page.content()
        website, x_handles, tg_links = self._extract_from_json(html)
        if not website and not x_handles:
            website, x_handles, tg_links = self._extract_from_html(html)
        return website, x_handles, tg_links

    def _extract_from_json(self, html):
        """从 Next.js __NEXT_DATA__ JSON 中提取链接"""
        website = None
        x_handles = set()
        tg_links = set()

        m = re.search(r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not m:
            return website, x_handles, tg_links

        try:
            data = json.loads(m.group(1))
            page_props = data.get('props', {}).get('pageProps', {})
            # CryptoRank 用 coin 或 ico 存项目数据
            coin = page_props.get('coin') or page_props.get('ico') or {}
            links = coin.get('links', [])

            for link in links:
                val = link.get('value', '').strip()
                typ = link.get('type', '')

                if typ == 'web' and not website:
                    # 排除社交/非官网域名
                    if not any(d in val.lower() for d in SOCIAL_DOMAINS):
                        website = val.rstrip('/')

                elif typ == 'twitter':
                    xm = X_PATTERN.search(val)
                    if xm:
                        handle = xm.group(1).lower()
                        if handle not in EXCLUDE_X:
                            x_handles.add(f"https://x.com/{handle}")

                elif typ == 'telegram':
                    tm = TG_PATTERN.search(val)
                    if tm:
                        slug = val.rstrip('/').split('/')[-1].lower()
                        if slug not in EXCLUDE_TG:
                            tg_links.add(val.split('?')[0])

        except (json.JSONDecodeError, KeyError):
            pass

        return website, x_handles, tg_links

    def _extract_from_html(self, html):
        """正则兜底：从页面 HTML 中提取链接（仅限项目信息区域）"""
        website = None
        x_handles = set()
        tg_links = set()

        # 尝试缩小范围到项目信息区域，排除 header/footer/nav
        # CryptoRank 详情页项目信息一般在 <main> 或特定 class 里
        # 去掉 <header>...</header>、<footer>...</footer>、<nav>...</nav>
        cleaned = re.sub(r'<header[\s>].*?</header>', '', html, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<footer[\s>].*?</footer>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r'<nav[\s>].*?</nav>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)

        # 提取所有 href
        hrefs = re.findall(r'href="(https?://[^"]+)"', cleaned)

        for href in hrefs:
            href_lower = href.lower()

            # X / Twitter
            xm = X_PATTERN.search(href)
            if xm:
                handle = xm.group(1).lower()
                if handle not in EXCLUDE_X:
                    x_handles.add(f"https://x.com/{handle}")
                continue

            # Telegram
            tm = TG_PATTERN.search(href)
            if tm:
                slug = href.rstrip('/').split('/')[-1].lower()
                if slug not in EXCLUDE_TG:
                    tg_links.add(href.split('?')[0])
                continue

            # 官网候选：非社交域名的 https 链接
            if (website is None
                    and href.startswith('https://')
                    and not any(d in href_lower for d in SOCIAL_DOMAINS)):
                website = href.rstrip('/')

        return website, x_handles, tg_links
