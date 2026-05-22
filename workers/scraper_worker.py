"""
Scraper Worker — 访问官网，提取 TG 链接 + X 账号 + 邮箱，存入 SQLite
多 lane 并发架构：每个并发通道持有独立 BrowserContext，轮询分发 URL。
每步 Playwright 调用都有 asyncio.wait_for 超时保护。
"""
import asyncio
import json
import re
import time

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
from workers.base_worker import BaseWorker
from workers.browser_stealth import (
    STEALTH_ARGS, IGNORE_DEFAULT_ARGS, STEALTH_INIT_SCRIPT,
    CONTEXT_KWARGS, EXTRA_HTTP_HEADERS,
)

TG_PATTERN = r't\.me/(?:joinchat/[\w\d_-]+|(?!(?:share|s|addstickers|setlanguage)/)[\w\d_]{5,})'
X_PATTERN  = r'(?:twitter\.com|x\.com)/(?!(?:home|explore|notifications|messages|search|tos|privacy|i|settings|hashtag|intent)\b)([\w\d_]{1,15})'
EMAIL_PATTERN = r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'

EXCLUDE_EMAIL_DOMAINS = {
    'example.com', 'test.com', 'sentry.io', 'cloudflare.com',
    'googleapis.com', 'googleusercontent.com', 'gstatic.com',
    'w3.org', 'schema.org', 'wixpress.com', 'squarespace.com',
}

URL_TIMEOUT = 60  # 每个网站的最大处理时间


class ScraperWorker(BaseWorker):
    _key_index = 0

    def __init__(self, log_callback, progress_callback=None, use_llm=False, concurrency=5):
        super().__init__(log_callback, progress_callback)
        self.use_llm = use_llm
        self.concurrency = max(1, concurrency)
        self._llm_fails = 0
        self._llm_disabled = False

    def _safe_log(self, msg):
        try:
            self.log(msg)
        except (UnicodeEncodeError, UnicodeDecodeError, OSError):
            try:
                self.log(msg.encode('ascii', errors='replace').decode('ascii'))
            except Exception:
                pass

    def run(self):
        """主入口：看门狗 + asyncio 爬虫"""
        self._watchdog_alive = True
        self._last_activity = time.time()

        def _watchdog():
            import subprocess as _sp
            while self._watchdog_alive:
                time.sleep(30)
                idle = time.time() - self._last_activity
                if idle > 180 and not self._stop:
                    self._safe_log(f"[!!] 爬虫无响应 {idle:.0f}s，强制清理 Chrome 进程...")
                    self._stop = True
                    time.sleep(10)
                    for proc in ["chrome.exe", "chromium.exe"]:
                        try:
                            _sp.run(["taskkill", "/f", "/im", proc],
                                    capture_output=True, timeout=10)
                        except Exception:
                            pass
                    self._watchdog_alive = False
                    return
                elif self._stop:
                    return

        import threading as _th
        _th.Thread(target=_watchdog, daemon=True).start()

        try:
            self._safe_asyncio_run(self._async_run())
        except Exception as e:
            self._safe_log(f"[错误] {e}")
        finally:
            self._watchdog_alive = False

    async def _async_run(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self._safe_log("请安装 playwright: pip install playwright && playwright install chromium")
            return

        websites = db.get_all_websites()
        total = len(websites)
        if total == 0:
            self._safe_log("数据库中没有官网，请先导入数据")
            return

        self._safe_log(f"共 {total} 个官网待爬取，并发数: {self.concurrency}")
        if self.use_llm:
            self._safe_log("[LLM] DeepSeek 筛选已启用")

        self._safe_log("[浏览器] 正在启动 Chromium...")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=STEALTH_ARGS,
                ignore_default_args=IGNORE_DEFAULT_ARGS,
            )

            # 每个并发通道持有独立的 context，避免并发 new_page() 竞争
            lanes = []
            for _ in range(self.concurrency):
                ctx = await browser.new_context(**CONTEXT_KWARGS)
                await ctx.add_init_script(STEALTH_INIT_SCRIPT)
                await ctx.set_extra_http_headers(EXTRA_HTTP_HEADERS)
                lanes.append({"ctx": ctx, "pages": 0, "lock": asyncio.Lock()})
            self._safe_log(f"[浏览器] 已启动，{len(lanes)} 个 context")

            done_count = 0
            done_lock = asyncio.Lock()
            BROWSER_RESTART_EVERY = 200

            async def restart_lane(lane):
                try:
                    await lane["ctx"].close()
                except Exception:
                    pass
                lane["ctx"] = await browser.new_context(**CONTEXT_KWARGS)
                await lane["ctx"].add_init_script(STEALTH_INIT_SCRIPT)
                await lane["ctx"].set_extra_http_headers(EXTRA_HTTP_HEADERS)
                lane["pages"] = 0

            async def process_one(idx, pid, url, src, lane):
                nonlocal done_count
                if self._stop:
                    return

                self._safe_log(f"[{idx+1}/{total}] 扫描: {url}")

                # 串行化对同一 context 的访问
                async with lane["lock"]:
                    tg, x_links, emails = set(), set(), set()
                    try:
                        tg, x_links, emails = await asyncio.wait_for(
                            self._scan(lane["ctx"], url), timeout=URL_TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        self._safe_log(f"  [!] 超时 ({URL_TIMEOUT}s)，跳过")
                    except Exception as e:
                        emsg = str(e)[:100]
                        self._safe_log(f"  [!] 失败: {emsg}")
                        if any(k in emsg for k in ("EPIPE", "ECONNRESET", "Connection closed", "Target closed")):
                            self._safe_log("  [!] 连接断开，重建 context")
                            try:
                                await restart_lane(lane)
                            except Exception:
                                pass

                    lane["pages"] += 1
                    if lane["pages"] >= BROWSER_RESTART_EVERY:
                        self._safe_log("[浏览器] 例行重建 context")
                        await restart_lane(lane)

                # 写入数据库（不持锁，多协程可并行写入）
                for link in tg:
                    db.insert_tg_link(pid, link, source=src)
                for handle in x_links:
                    db.insert_x_link(pid, handle, source=src)
                for email in emails:
                    db.insert_email(pid, email, source=src)
                db.mark_project_scraped(pid)

                if tg or x_links or emails:
                    self._safe_log(f"  -> TG:{len(tg)} X:{len(x_links)} Email:{len(emails)}")

                async with done_lock:
                    done_count += 1
                    self._last_activity = time.time()
                    self.progress(done_count, total)

            # 按 lane 轮询分发任务：URL 0 → lane 0, URL 1 → lane 1, ...
            tasks = []
            for i, (pid, url, src) in enumerate(websites):
                lane = lanes[i % len(lanes)]
                tasks.append(asyncio.create_task(process_one(i, pid, url, src, lane)))

            await asyncio.gather(*tasks, return_exceptions=True)

            self._safe_log(f"爬取完成! 共处理 {done_count}/{total}")

    async def _scan(self, context, url):
        """扫描一个官网，返回 (tg_links, x_links, emails)"""
        page = None
        tg_links = set()
        x_links  = set()
        emails   = set()

        try:
            page = await asyncio.wait_for(context.new_page(), timeout=15)
        except asyncio.TimeoutError:
            self._safe_log(f"  [!] new_page 超时，跳过 {url}")
            return tg_links, x_links, emails

        try:
            try:
                await asyncio.wait_for(
                    page.goto(url, wait_until="commit", timeout=20000),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                self._safe_log(f"  [!] 页面加载超时，跳过 {url}")
                return tg_links, x_links, emails

            # 滚动
            try:
                await asyncio.wait_for(
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)"),
                    timeout=5,
                )
                await asyncio.sleep(1)
            except Exception:
                pass

            # 提取内容
            try:
                content = await asyncio.wait_for(page.content(), timeout=15)
            except asyncio.TimeoutError:
                self._safe_log(f"  [!] 提取内容超时，跳过 {url}")
                return tg_links, x_links, emails

            for link in re.findall(TG_PATTERN, content):
                full = "https://" + link if not link.startswith("http") else link
                tg_links.add(full)

            for match in re.finditer(X_PATTERN, content):
                x_links.add(f"https://x.com/{match.group(1)}")

            for email in re.findall(EMAIL_PATTERN, content):
                domain = email.split('@')[1].lower()
                if domain not in EXCLUDE_EMAIL_DOMAINS:
                    emails.add(email.lower())

            # LLM 筛选
            need_llm = len(emails) > 1 or (self.use_llm and (len(tg_links) > 1 or len(x_links) > 1))
            if need_llm and not self._llm_disabled:
                self._safe_log(f"  [LLM] 多候选(TG:{len(tg_links)} X:{len(x_links)} Email:{len(emails)})，调用 DeepSeek...")
                try:
                    tg_links, x_links, emails = await asyncio.wait_for(
                        asyncio.to_thread(self._filter_with_llm, url, tg_links, x_links, emails),
                        timeout=35,
                    )
                    self._llm_fails = 0
                except (asyncio.TimeoutError, Exception) as e:
                    self._llm_fails += 1
                    self._safe_log(f"  [LLM] 失败({self._llm_fails}/3)，保留原始结果")
                    if self._llm_fails >= 3:
                        self._llm_disabled = True
                        self._safe_log("  [LLM] 连续失败3次，本轮剩余项目跳过LLM筛选")
        finally:
            if page and not page.is_closed():
                try:
                    await asyncio.wait_for(page.close(), timeout=5)
                except Exception:
                    pass

        return tg_links, x_links, emails

    def _filter_with_llm(self, site_url, tg_links, x_links, emails):
        """调用 DeepSeek API 筛选 TG/X/Email"""
        import requests

        keys_raw = db.get_setting("deepseek_api_key", "")
        keys = [k.strip() for k in keys_raw.replace('\n', ',').split(',') if k.strip()]
        if not keys:
            self._safe_log("  [LLM] 未在设置页填写 DeepSeek API Key，跳过筛选")
            return tg_links, x_links, emails

        api_key = keys[ScraperWorker._key_index % len(keys)]
        ScraperWorker._key_index += 1

        tg_list    = "\n".join(tg_links) if tg_links else "none"
        x_list     = "\n".join(x_links)  if x_links  else "none"
        email_list = "\n".join(emails)   if emails   else "none"

        prompt = (
            f"You are helping filter social media links and email addresses for a Web3 project.\n"
            f"Project website: {site_url}\n\n"
            f"Candidate Telegram links:\n{tg_list}\n\n"
            f"Candidate X/Twitter links:\n{x_list}\n\n"
            f"Candidate email addresses:\n{email_list}\n\n"
            f"Rules:\n"
            f"1. Telegram: keep only the project's official COMMUNITY GROUP (interactive, not broadcast channel). "
            f"Discard third-party, exchange, or media links.\n"
            f"2. X/Twitter: keep only the project's own official account. "
            f"Discard exchanges, media, or unrelated accounts.\n"
            f"3. Email: return a SORTED list (best first) of the company's official emails. Apply these priorities:\n"
            f"   a) Domain must match the project website (e.g. @company.com). Discard emails from gmail.com, outlook.com, "
            f"or domains unrelated to the project.\n"
            f"   b) Among official domain emails, prioritize departments most likely to buy growth/marketing services: "
            f"marketing, bd, partnerships, growth, sales > info, hello, contact > support > personal names > "
            f"tech, engineering, security, hr, legal (lowest priority).\n"
            f"   c) Between a personal-name email (john@company.com) and a low-priority department email "
            f"(dev@company.com), prefer the personal-name email.\n"
            f"   d) If no official domain email exists, return an empty list.\n\n"
            f"Respond with JSON only:\n"
            '{ "tg": ["group url or empty list"], "x": ["official x url or empty list"], '
            '"emails": ["sorted best-first, or empty list"] }'
        )

        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            timeout=(10, 20),
        )
        resp.raise_for_status()
        result = json.loads(resp.json()["choices"][0]["message"]["content"])
        filtered_tg    = set(result.get("tg", []))
        filtered_x     = set(result.get("x",  []))
        filtered_emails = list(result.get("emails", []))
        best_emails = set(filtered_emails[:1]) if filtered_emails else set()
        self._safe_log(f"  [LLM] 筛选后 -> TG:{len(filtered_tg)} X:{len(filtered_x)} Email:{len(best_emails)}")
        return filtered_tg, filtered_x, best_emails
