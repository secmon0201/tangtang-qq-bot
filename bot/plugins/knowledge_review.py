from __future__ import annotations

from nonebot import get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.params import CommandArg

from bot.services.knowledge_db import KnowledgeDb
from bot.services.knowledge_review import (
    export_pending_doc,
    export_rejected_doc,
    process_review_files,
)
from bot.services.roles import is_super_admin


knowledge_db = KnowledgeDb()
driver = get_driver()


@driver.on_startup
async def _process_knowledge_review_files() -> None:
    try:
        summary = process_review_files(knowledge_db)
        export_pending_doc(knowledge_db)
        export_rejected_doc(knowledge_db)
        if summary["approved"] or summary["rejected"] or summary["invalid"]:
            logger.info(
                "Knowledge review files processed: approved={} rejected={} invalid={}",
                summary["approved"],
                summary["rejected"],
                summary["invalid"],
            )
    except Exception:
        logger.exception("Knowledge review file processing failed")


review_pending = on_command("收录待审", aliases={"收录待审列表"}, priority=5, block=True)


@review_pending.handle()
async def _handle_pending(event: MessageEvent):
    if not is_super_admin(int(event.user_id)):
        await review_pending.finish("只有超级管理员可以查看收录审核队列。")
    rows = knowledge_db.pending_entries()
    if not rows:
        await review_pending.finish("当前没有待审核的收录条目。")
    lines = [f"待审核 {len(rows)} 条（完整文档：data/knowledge/收录待审.md）："]
    for row in rows:
        lines.append(
            f"#{row['id']} [{row['domain']}] {row['entry_id']}｜{row['title']}\n"
            f"  摘要：{(row['summary'] or '')[:80]}\n"
            f"  来源：{row['source_name']} {row['source_url']}"
        )
        if row.get("conflict_note"):
            lines.append(f"  冲突说明：{row['conflict_note'][:80]}")
    await review_pending.finish("\n".join(lines))


review_approve = on_command("收录确认", priority=5, block=True)


@review_approve.handle()
async def _handle_approve(event: MessageEvent, args=CommandArg()):
    if not is_super_admin(int(event.user_id)):
        await review_approve.finish("只有超级管理员可以确认收录。")
    raw = args.extract_plain_text().strip()
    if not raw:
        await review_approve.finish("用法：#收录确认 <待审编号>")
    try:
        entry = knowledge_db.approve_entry(int(raw))
    except (ValueError, TypeError) as exc:
        await review_approve.finish(f"确认失败：{exc}")
    export_pending_doc(knowledge_db)
    await review_approve.finish(
        f"已收录 #{entry['id']}「{entry['title']}」，"
        "现在可被糖糖本地检索使用；待审文档已同步更新。"
    )


review_reject = on_command("收录拒绝", priority=5, block=True)


@review_reject.handle()
async def _handle_reject(event: MessageEvent, args=CommandArg()):
    if not is_super_admin(int(event.user_id)):
        await review_reject.finish("只有超级管理员可以拒绝收录。")
    raw = args.extract_plain_text().strip()
    if not raw:
        await review_reject.finish("用法：#收录拒绝 <待审编号>")
    try:
        knowledge_db.reject_entry(int(raw))
    except (ValueError, TypeError) as exc:
        await review_reject.finish(f"拒绝失败：{exc}")
    export_pending_doc(knowledge_db)
    export_rejected_doc(knowledge_db)
    await review_reject.finish(
        f"已拒绝待审条目 #{raw} 并归档；后续出现相同内容会被自动拒绝。"
        "归档可见 data/knowledge/收录拒绝归档.md。"
    )


review_revive = on_command("收录恢复待审", priority=5, block=True)


@review_revive.handle()
async def _handle_revive(event: MessageEvent, args=CommandArg()):
    if not is_super_admin(int(event.user_id)):
        await review_revive.finish("只有超级管理员可以恢复待审。")
    raw = args.extract_plain_text().strip()
    if not raw:
        await review_revive.finish("用法：#收录恢复待审 <归档编号>")
    try:
        entry = knowledge_db.revive_entry(int(raw))
    except (ValueError, TypeError) as exc:
        await review_revive.finish(f"恢复失败：{exc}")
    export_pending_doc(knowledge_db)
    export_rejected_doc(knowledge_db)
    await review_revive.finish(
        f"已把 #{entry['id']}「{entry['title']}」恢复到收录待审，"
        "可重新确认或拒绝。"
    )
