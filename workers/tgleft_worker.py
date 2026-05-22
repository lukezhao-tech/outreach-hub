"""
TGLeft Worker — 扫描小群离群用户，存入 SQLite
基于 tgleft.py
"""
import asyncio

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import config
from workers.base_worker import BaseWorker


class TGLeftWorker(BaseWorker):
    def __init__(self, log_callback, progress_callback=None,
                 max_members=None, max_messages=None):
        super().__init__(log_callback, progress_callback)
        self.max_members  = max_members  or config.TGLEFT_MAX_MEMBERS
        self.max_messages = max_messages or config.TGLEFT_MAX_MESSAGES

    def run(self):
        try:
            self._safe_asyncio_run(self._async_run())
        except Exception as e:
            self.safe_log(f"[错误] {e}")

    async def _async_run(self):
        try:
            from telethon import TelegramClient, errors
            from telethon.tl.functions.channels import GetFullChannelRequest
            from telethon.tl.functions.users import GetFullUserRequest
            from telethon.tl.types import User
        except ImportError:
            self.safe_log("请安装 telethon：pip install telethon")
            return

        api_id, api_hash, session = db.get_tg_credentials("left")
        if not api_id or not api_hash:
            self.safe_log("❌ 未配置 Telegram API（left）。请到「⚙️ 设置」→「TG 账号凭证」填入 api_id / api_hash")
            self.safe_log("   申请地址：https://my.telegram.org → API development tools")
            return
        self.safe_log(f"使用账号 API_ID={api_id}，session={session}")
        client = TelegramClient(session, api_id, api_hash)
        from workers.telethon_auth import async_start_client
        if not await async_start_client(client, log_callback=self.safe_log):
            self.safe_log("登录失败或已取消")
            return
        me = await client.get_me()
        self.safe_log(f"已登录：{me.first_name}")

        total_found = 0
        group_idx = 0

        async for dialog in client.iter_dialogs():
            if self._stop:
                self.safe_log("已停止")
                break
            if not dialog.is_group:
                continue

            group_idx += 1
            entity     = dialog.entity
            group_name = dialog.name

            # 获取最新人数
            try:
                full_info     = await client(GetFullChannelRequest(entity))
                current_count = full_info.full_chat.participants_count
            except Exception:
                current_count = getattr(entity, 'participants_count', 0)

            self.safe_log(f"检查群：{group_name}（{current_count} 人）")

            if current_count > self.max_members:
                self.safe_log(f"  超过 {self.max_members}，跳过")
                continue

            # 当前成员（只记有 username 的，无 username 的也无法发 DM）
            present = set()
            try:
                async for user in client.iter_participants(entity):
                    if user.username:
                        present.add(user.username.lower())
            except Exception as e:
                self.safe_log(f"  ⚠ 无法读取成员：{e}")
                continue

            # 扫描历史消息
            left_users = {}
            try:
                async for msg in client.iter_messages(entity, limit=self.max_messages):
                    if self._stop:
                        break
                    if not msg.sender_id or msg.sender_id == me.id:
                        continue
                    sender = msg.sender
                    if not isinstance(sender, User):
                        continue
                    if sender.username:
                        uname = sender.username.lower()
                        if uname not in present and uname not in left_users:
                            bio = ""
                            try:
                                await asyncio.sleep(0.3)
                                full_u = await client(GetFullUserRequest(sender))
                                bio    = full_u.full_user.about or ""
                            except errors.FloodWaitError as fw:
                                self.safe_log(f"  ⚠ 触发限流，等待 {fw.seconds} 秒...")
                                await asyncio.sleep(fw.seconds + 1)
                            except Exception:
                                pass
                            left_users[uname] = {
                                "username":     sender.username,
                                "display_name": sender.first_name or "",
                                "bio":          bio.replace('\n', ' '),
                                "group_name":   group_name,
                            }
                            self.safe_log(f"  发现离群：@{sender.username}")
            except Exception as e:
                self.safe_log(f"  ⚠ 扫描消息失败：{e}")

            # 不管 _stop 还是正常结束，当前群已找到的都入库
            for u in left_users.values():
                db.insert_tg_left_user(
                    username=u["username"],
                    display_name=u["display_name"],
                    bio=u["bio"],
                    group_name=u["group_name"]
                )
            total_found += len(left_users)

        await client.disconnect()
        self.safe_log(f"完成！共发现 {total_found} 个离群用户")
