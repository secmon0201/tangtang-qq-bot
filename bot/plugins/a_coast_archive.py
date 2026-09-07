from __future__ import annotations

import asyncio
import time
from typing import Any

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.params import CommandArg

from bot.config import settings
from bot.application.command_helpers import text_arg
from bot.services.a_coast_archive import ACoastArchiveService, bounded_evidence
from bot.services.a_coast_archive_render import ACoastArchiveImageRenderer
from bot.services.avatars import AvatarService
from bot.services.media import local_image_segment
from bot.services.runtime import database, group_domains
from bot.services.tangtang_lines import profile_already_running, profile_started
from bot.services.tangtang_profile import ProfileRunGate, TangtangProfileService


db = database()
domains = group_domains()
service = ACoastArchiveService(db)
archive_renderer = ACoastArchiveImageRenderer(
    settings.report_dir, settings.report_font_path, timezone_name=settings.timezone
)
archive_avatar_service = AvatarService(
    settings.avatar_cache_dir,
    settings.avatar_base_url,
    settings.avatar_timeout,
    settings.avatar_cache_ttl,
    refresh_interval=settings.avatar_refresh_interval,
    refresh_cooldown=settings.avatar_refresh_cooldown,
    max_refresh_per_call=settings.avatar_refresh_max_per_call,
    concurrency=settings.avatar_refresh_concurrency,
)
profile_service = TangtangProfileService()
profile_gate = ProfileRunGate()
PROFILE_MINIMUM_MESSAGE_COUNT = 101

PROFILE_EVIDENCE_SYSTEM_PROMPT = (
    "你是发言档案证据整理器。只提炼提供的新增发言中可验证的交流话题、表达习惯、"
    "活跃规律与复读情况，不写人物画像，不使用戏剧化口吻。不得推断身份、健康、"
    "政治、宗教、性取向等敏感属性；不要逐条复述原文或编造事实。"
)

PROFILE_EVIDENCE_SYSTEM_PROMPT += (
    "请明确区分稳定高频特征、零星线索与不足以判断的维度，避免把一次偶然发言当成习惯。"
    "优先保留持续出现的话题、对话题的喜欢或不满、反复表达的观点和可验证的聊天气质；"
    "表达节奏、活跃时间与复读只作辅助线索。"
)

TANGTANG_PROFILE_SYSTEM_PROMPT = """你是糖糖，会认真翻群聊记录的嘉心糖观察员。你喜欢嘉然、乃琳、贝拉、心宜、思诺，爱看枝江娱乐企划相关内容，也会玩《鸣潮》。你的第一人称只用“糖糖”。你不是任何已有角色的扮演或复刻。

【证据边界】
只基于给出的本地统计、既有画像和发言证据判断。先区分稳定高频特征、偶发线索与证据不足；不猜测现实身份、健康、政治、宗教、性取向或其他敏感属性，不编造群内事件，不逐条复述原文。你写的是聊天里的样子，不是现实人格诊断。

【取材重心】
正文不是四等分的统计报告。长图已展示活跃时间、表达和复读数据；除非它们与核心主题紧密相关，不重复解释图表。优先选 1 至 3 个、最多 5 个最突出的主题、兴趣、态度或反复观点，判断用户是在热切关注、真心喜欢、保留观察、认真讨论、习惯性吐槽，还是只偶尔路过。要说出糖糖读到的态度，而不是重念关键词或词频。

【糖糖的声音】
用自然、口语、有画面又不浮夸的中文。糖糖会有一点俏皮、松弛直说和轻微侧评，但幽默只针对可观察的聊天模式，不嘲讽人。可以偶尔说“糖糖觉得”“这一点有点藏不住”“想低调都不太容易”，但一次画像不要堆口头禅、表情、感叹号或网络梗。避免“该用户”“呈现出”“综合来看”“显著”等报告腔。

【同好立场】
记录确实涉及枝江娱乐、A-SOUL、嘉然、乃琳、贝拉、心宜、思诺或《鸣潮》时，可以自然露出同好式肯定。面对缺少根据的一味否定，可以温和提出不同看法；面对具体、真诚且有内容的批评，应承认其观察，不强行护短。评价的是话题、观点和聊天里的倾向，不攻击用户，也不把不同立场写成敌意。没有相关证据时不要硬塞这些内容。

【严格输出结构】
只输出 3 至 5 个自然段，不要标题、列表、免责声明、提问或解释你的方法。
第一段必须以“糖糖开篇：”开头，只写一句，概括最鲜明的聊天气质或兴趣重心。
中间写 1 至 3 段重点观察，以话题、兴趣和有依据的点评为主；每段只讲一个重点。表达、互动、复读、时间只能作为必要旁证，最多一两句。
最后一段必须以“糖糖总评：”开头，用 1 至 2 句、最多 3 句回扣开篇并收束全文；不得在最后总评后继续新增内容。
"""


def parse_user_id(value: str) -> int | None:
    return int(value) if value.isdigit() and 5 <= len(value) <= 12 else None


def has_enough_profile_messages(message_count: int) -> bool:
    return int(message_count) >= PROFILE_MINIMUM_MESSAGE_COUNT


def profile_target_from_args(args: Message) -> int | None:
    """Accept one QQ number or one OneBot @ target, with no extra content."""
    mentions = []
    text_parts: list[str] = []
    for segment in args:
        if segment.type == "at":
            mentions.append(segment)
        elif segment.type == "text":
            text_parts.append(str(segment.data.get("text", "")))
        else:
            return None

    if mentions:
        if len(mentions) != 1 or any(part.strip() for part in text_parts):
            return None
        return parse_user_id(str(mentions[0].data.get("qq", "")))

    return parse_user_id("".join(text_parts).strip())


def can_request_profile(event: MessageEvent) -> bool:
    return isinstance(event, GroupMessageEvent) and domains.domain_for_group(
        int(event.group_id)
    ) is not None


def archive_scope(event: MessageEvent) -> tuple[int, ...]:
    if not isinstance(event, GroupMessageEvent):
        return ()
    return (int(event.group_id),)


def profile_scope(event: MessageEvent) -> tuple[tuple[int, ...], str]:
    if not isinstance(event, GroupMessageEvent):
        return (), ""
    domain = domains.domain_for_group(int(event.group_id))
    if domain is None:
        return (), ""
    scope = domains.domain_groups(domain.domain_id)
    if domain.mode == "solo":
        return scope, f"group:{int(event.group_id)}"
    return scope, domain.domain_key


def group_label(group_id: int, group_name: str) -> str:
    return group_name or str(group_id)


def message_rows_text(rows: list[Any], page: int, keyword: str = "") -> str:
    if not rows:
        return "没有找到符合条件的已存档纯文本发言。"
    heading = f"发言记录 第 {page} 页"
    if keyword:
        heading += f"｜关键词：{keyword}"
    lines = [heading]
    for row in rows:
        content = " ".join(str(row["content"] or "").split())
        if len(content) > 220:
            content = content[:217] + "..."
        lines.append(
            f"[{row['occurred_at']}] {group_label(int(row['group_id']), str(row['group_name'] or ''))}\n{content}"
        )
    return "\n\n".join(lines)


async def finish_message_rows(
    matcher: Any,
    user_id: int,
    rows: list[Any],
    page: int,
    scope: tuple[int, ...],
    keyword: str = "",
) -> None:
    """Return archive list results as an image, retaining text as an outage fallback."""
    try:
        avatar_paths = await archive_avatar_service.prefetch([dict(row) for row in rows])
        total_pages = service.total_pages(user_id, scope, keyword)
        card = archive_renderer.render(user_id, rows, page, keyword, avatar_paths, total_pages)
    except Exception:
        logger.exception("A海岸发言档案图片渲染失败")
        await matcher.finish(message_rows_text(rows, page, keyword))
    await matcher.finish(local_image_segment(card))


async def finish_profile_image(
    matcher: Any,
    user_id: int,
    profile_text: str,
    scope: tuple[int, ...],
    scope_key: str,
    *,
    include_ai_profile: bool,
) -> None:
    """Render the persistent A Coast profile as an adaptive long image."""
    try:
        rows = service.all_records(user_id, scope)
        user = db.user_profiles((user_id,)).get(int(user_id), {})
        nickname = str(user.get("nickname") or "") or "群成员"
        avatar_url = str(user.get("avatar_url") or "")
        avatar_paths = await archive_avatar_service.prefetch(
            [{"user_id": user_id, "avatar_url": avatar_url}]
        )
        card = archive_renderer.render_profile(
            user_id,
            nickname,
            db.a_coast_profile_updated_at(user_id, scope_key),
            profile_text,
            rows,
            avatar_paths.get(user_id),
            include_ai_profile=include_ai_profile,
        )
    except Exception:
        logger.exception("A海岸发言画像长图渲染失败")
        await matcher.finish(profile_text)
    await matcher.finish(local_image_segment(card))


def summary_text(user_id: int, scope: tuple[int, ...]) -> str:
    summary = service.summary(user_id, scope)
    if not summary["message_count"]:
        return "该用户暂无功能上线后采集到的合规纯文本发言。"
    groups = "、".join(
        f"{group_label(int(row['group_id']), str(row['group_name'] or ''))} {int(row['message_count'])}条"
        for row in summary["groups"]
    )
    hours = "、".join(
        f"{int(row['hour']):02d}时 {int(row['message_count'])}条" for row in summary["hours"][:5]
    )
    repeats = "\n".join(
        f"- {int(row['message_count'])} 次：{str(row['content'])[:120]}"
        for row in summary["repeats"][:8]
    ) or "- 暂无重复文本"
    return (
        f"用户 {user_id} 的本地发言画像（来自糖糖聊天历史库）\n"
        f"合规纯文本发言：{int(summary['message_count'])} 条\n"
        f"群分布：{groups}\n"
        f"高频时段：{hours}\n"
        f"高频复读：\n{repeats}"
    )


def profile_chunks(rows: list[Any], maximum_chars: int = 12000) -> list[str]:
    chunks: list[str] = []
    lines: list[str] = []
    size = 0
    for row in rows:
        line = (
            f"[{row['occurred_at']}] {group_label(int(row['group_id']), str(row['group_name'] or ''))}: "
            f"{row['content']}"
        )
        if lines and size + len(line) + 1 > maximum_chars:
            chunks.append("\n".join(lines))
            lines, size = [], 0
        lines.append(line[:maximum_chars])
        size += len(lines[-1]) + 1
    if lines:
        chunks.append("\n".join(lines))
    return chunks


async def incremental_ai_profile_text(
    user_id: int, scope: tuple[int, ...], scope_key: str
) -> str:
    config = profile_service.config()
    if not config.enabled:
        return "AI 画像服务未启用。"
    previous = db.a_coast_profile_state(user_id, scope_key)
    if len(scope) > 1 and not previous:
        group_states = db.a_coast_group_profile_states(user_id, scope)
        inherited = "\n".join(str(row["profile_text"] or "") for row in group_states)
        if inherited:
            previous = (previous + "\n" + inherited).strip()
    records = service.unconsumed(user_id, scope, limit=config.max_records_per_run)
    if not records:
        return previous or "该用户暂无未分析的已存档纯文本发言。"
    started = time.monotonic()
    chunks = profile_chunks(records, config.chunk_chars)
    logger.info(
        "A海岸画像分块完成 user_id={} 未消费={} 共{}块 已耗时{:.0f}s",
        user_id,
        len(records),
        len(chunks),
        time.monotonic() - started,
    )
    semaphore = asyncio.Semaphore(config.evidence_concurrency)

    async def _extract(index: int, chunk: str) -> str:
        async with semaphore:
            logger.info(
                "A海岸画像证据提取开始 user_id={} 第{}/{}块 输入{}字 已耗时{:.0f}s",
                user_id,
                index,
                len(chunks),
                len(chunk),
                time.monotonic() - started,
            )
            result = await profile_service.generate(
                PROFILE_EVIDENCE_SYSTEM_PROMPT,
                f"用户 {user_id} 的新增发言第 {index} 批。"
                f"请提炼可用于更新画像的紧凑证据，不超过 {config.evidence_max_chars} 字：\n{chunk}",
                max_output_tokens=config.evidence_max_tokens,
                max_response_chars=config.evidence_response_chars,
                reasoning_effort=config.evidence_reasoning_effort,
            )
            logger.info(
                "A海岸画像证据提取完成 user_id={} 第{}/{}块 已耗时{:.0f}s",
                user_id,
                index,
                len(chunks),
                time.monotonic() - started,
            )
            return result[: config.evidence_max_chars]

    evidence = bounded_evidence(
        list(
            await asyncio.gather(
                *(_extract(index, chunk) for index, chunk in enumerate(chunks, 1))
            )
        ),
        config.evidence_max_chars,
    )
    if not evidence:
        evidence = ["（本次新增发言未提炼出有效证据。）"]
    merge_round = 0
    while len(evidence) > 1:
        merge_round += 1
        logger.info(
            "A海岸画像证据合并开始 user_id={} 第{}轮 待合并{}条 已耗时{:.0f}s",
            user_id,
            merge_round,
            len(evidence),
            time.monotonic() - started,
        )
        merge_chunks = profile_chunks(
            [
                {"occurred_at": "阶段摘要", "group_id": 0, "group_name": "", "content": value}
                for value in evidence
            ],
            config.merge_chunk_chars,
        )
        merge_semaphore = asyncio.Semaphore(config.evidence_concurrency)

        async def _merge_chunk(index: int, chunk: str) -> str:
            async with merge_semaphore:
                logger.info(
                    "A海岸画像证据合并调用开始 user_id={} 第{}轮 第{}/{}块 输入{}字 已耗时{:.0f}s",
                    user_id,
                    merge_round,
                    index,
                    len(merge_chunks),
                    len(chunk),
                    time.monotonic() - started,
                )
                result = await profile_service.generate(
                    PROFILE_EVIDENCE_SYSTEM_PROMPT,
                    f"合并这些新增发言摘要，只保留关键变化和稳定特征，"
                    f"不超过 {config.evidence_max_chars} 字：\n{chunk}",
                    max_output_tokens=config.evidence_max_tokens,
                    max_response_chars=config.evidence_response_chars,
                    reasoning_effort=config.evidence_reasoning_effort,
                )
                logger.info(
                    "A海岸画像证据合并调用完成 user_id={} 第{}轮 第{}/{}块 输出{}字 已耗时{:.0f}s",
                    user_id,
                    merge_round,
                    index,
                    len(merge_chunks),
                    len(result),
                    time.monotonic() - started,
                )
                return result[: config.evidence_max_chars]

        evidence = bounded_evidence(
            list(
                await asyncio.gather(
                    *(_merge_chunk(index, chunk) for index, chunk in enumerate(merge_chunks, 1))
                )
            ),
            config.evidence_max_chars,
        )
        if not evidence:
            evidence = ["（本次合并未提炼出有效证据。）"]
        logger.info(
            "A海岸画像证据合并完成 user_id={} 第{}轮 剩余{}条 已耗时{:.0f}s",
            user_id,
            merge_round,
            len(evidence),
            time.monotonic() - started,
        )
    prompt = (
        f"用户 {user_id} 的既有画像：\n{previous or '无，首次建立画像。'}\n\n"
        f"全部已存档发言的本地统计：\n{summary_text(user_id, scope)}\n\n"
        f"新增发言证据：\n{evidence[0]}\n\n"
        f"请以糖糖的人格输出更新后的完整发言画像，不超过 {config.final_max_chars} 字。"
    )
    logger.info(
        "A海岸画像最终生成开始 user_id={} 已耗时{:.0f}s",
        user_id,
        time.monotonic() - started,
    )
    profile = await profile_service.generate(
        TANGTANG_PROFILE_SYSTEM_PROMPT,
        prompt,
        max_output_tokens=config.final_max_tokens,
        max_response_chars=config.final_response_chars,
    )
    logger.info(
        "A海岸画像最终生成完成 user_id={} 输出{}字 已耗时{:.0f}s",
        user_id,
        len(profile),
        time.monotonic() - started,
    )
    if not profile:
        raise RuntimeError("AI returned an empty profile")
    db.set_a_coast_profile_state(user_id, scope_key, profile)
    service.consume(user_id, records)
    logger.info(
        "A海岸画像落库完成 user_id={} 消费{}条 总耗时{:.0f}s",
        user_id,
        len(records),
        time.monotonic() - started,
    )
    return profile


archive_records = on_command("发言记录", priority=5, block=True)
archive_search = on_command("发言搜索", priority=5, block=True)
archive_profile = on_command("发言画像", aliases={"画像"}, priority=5, block=True)


@archive_records.handle()
async def _(event: MessageEvent, args=CommandArg()):
    scope = archive_scope(event)
    if not scope:
        await archive_records.finish("发言记录只能在目标 QQ 群内查询。")
    tokens = text_arg(args).split()
    if len(tokens) not in {1, 2}:
        await archive_records.finish("用法：#发言记录 QQ号 [页码]")
    user_id = parse_user_id(tokens[0])
    page = int(tokens[1]) if len(tokens) == 2 and tokens[1].isdigit() else 1
    if user_id is None or page < 1:
        await archive_records.finish("QQ号或页码无效。")
    await finish_message_rows(
        archive_records,
        user_id,
        service.records(user_id, scope, page=page),
        page,
        scope,
    )


@archive_search.handle()
async def _(event: MessageEvent, args=CommandArg()):
    scope = archive_scope(event)
    if not scope:
        await archive_search.finish("发言搜索只能在目标 QQ 群内查询。")
    tokens = text_arg(args).split()
    if len(tokens) < 2:
        await archive_search.finish("用法：#发言搜索 QQ号 关键词 [页码]")
    user_id = parse_user_id(tokens[0])
    page = 1
    if len(tokens) > 2 and tokens[-1].isdigit():
        page = int(tokens.pop())
    keyword = " ".join(tokens[1:]).strip()
    if user_id is None or not keyword or page < 1:
        await archive_search.finish("QQ号、关键词或页码无效。")
    await finish_message_rows(
        archive_search,
        user_id,
        service.records(user_id, scope, keyword=keyword, page=page),
        page,
        scope,
        keyword,
    )


@archive_profile.handle()
async def _(event: MessageEvent, args=CommandArg()):
    if not settings.a_coast_profile_enabled:
        await archive_profile.finish("画像功能已临时关闭，可稍后由管理员重新开启。")
    scope, scope_key = profile_scope(event)
    if not can_request_profile(event) or not scope or not scope_key:
        await archive_profile.finish("发言画像只能在目标 QQ 群内查询。")
    user_id = profile_target_from_args(args)
    if user_id is None:
        await archive_profile.finish("用法：#发言画像 QQ号 或 #发言画像 @用户")

    message_count = service.message_count(user_id, scope)
    if message_count == 0:
        await archive_profile.finish("该用户暂无已存档发言。")
    if not has_enough_profile_messages(message_count):
        await archive_profile.finish(
            f"该用户当前仅有 {message_count} 条已存档发言，发言太少。"
            "请至少发言超过 100 条，再进行画像绘制。"
        )

    previous = db.a_coast_profile_state(user_id, scope_key)
    pending = service.unconsumed(user_id, scope)
    profile_config = profile_service.config()
    if previous and not pending:
        await finish_profile_image(
            archive_profile,
            user_id,
            previous,
            scope,
            scope_key,
            include_ai_profile=profile_config.enabled,
        )
        return
    if not pending:
        await archive_profile.finish("该用户暂无可用于生成画像的已存档发言，请等待新的发言被记录后再试。")

    if profile_gate.active_count >= profile_config.max_concurrent:
        logger.info(
            "A海岸画像排队 user_id={} 并发={}/{} 前面={}",
            user_id,
            profile_gate.active_count,
            profile_config.max_concurrent,
            profile_gate.queue_depth,
        )
        await archive_profile.send(
            f"当前画像并发已满（{profile_gate.active_count}/{profile_config.max_concurrent}），"
            f"你的任务已排队（前面还有 {profile_gate.queue_depth} 个），完成一个后会自动开始。"
        )
    acquired = await profile_gate.acquire(user_id, profile_config.max_concurrent)
    if not acquired:
        await archive_profile.finish(profile_already_running())
    logger.info(
        "A海岸画像任务开始 user_id={} 并发={}/{} 排队={}",
        user_id,
        profile_gate.active_count,
        profile_config.max_concurrent,
        profile_gate.queue_depth,
    )
    try:
        await archive_profile.send(profile_started())
        result = await incremental_ai_profile_text(user_id, scope, scope_key)
    except Exception as exc:
        logger.warning("A海岸画像处理失败 user_id={}: {}", user_id, exc)
        await archive_profile.finish(f"画像分析失败：{type(exc).__name__}。本次原文未被标记为已使用。")
    finally:
        profile_gate.release(user_id)
        logger.info(
            "A海岸画像任务结束 user_id={} 并发={}/{} 排队={}",
            user_id,
            profile_gate.active_count,
            profile_config.max_concurrent,
            profile_gate.queue_depth,
        )
    await finish_profile_image(
        archive_profile,
        user_id,
        result,
        scope,
        scope_key,
        include_ai_profile=profile_config.enabled,
    )
