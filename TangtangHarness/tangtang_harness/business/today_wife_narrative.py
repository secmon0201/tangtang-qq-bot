"""Composable, style-aware narration for the 今日缘分 game.

This module is intentionally separate from game state and command handling.
Authors can grow a style by appending fragments to one of the pools below;
the runtime combines one setting beat, one event beat, and one aftermath.
That gives each event class thousands of readable combinations without adding
branches to the interaction engine.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from tangtang_harness.business.config import RESOURCE_DIR
import string


StyleBank = Mapping[str, tuple[str, ...]]


STYLE_BANKS: dict[str, StyleBank] = {
    "现实日常": {
        "openings": (
            "周围的灯还亮着，{prop}安静地留在原来的位置。",
            "人群短暂散开，{prop}忽然显得比刚才更重要。",
            "风从门缝里钻进来，把刚才的话吹得有些清楚。",
            "没人催促，时间刚好给这件小事留了一个空当。",
            "嘈杂声退到身后，{mechanism}成了眼前唯一的线索。",
            "收银提示、列车广播或摊主的招呼，恰好在这一刻停了一拍。",
            "有人从旁边经过，却没有打断这一段刚刚开始的犹豫。",
            "桌面上多了一点空位，足够放下一句不必立刻回答的话。",
            "夜色把距离拉近了一点，也把细节照得更真。",
            "一件本来普通的小事，在这一分钟里突然有了后续。",
            "远处还在热闹，近处只剩下{prop}和没有说完的意思。",
            "路灯把影子拉得很长，谁都没有急着迈出下一步。",
            "这不是一个适合大声说话的瞬间，反而刚好听得见心意。",
            "门口的风铃响了一声，像是在提醒谁别再错过。",
            "最后一点热气还没有散，事情也还来得及换一种走向。",
            "大家都在忙自己的事，偏偏这段关系被留在了安静处。",
        ),
        "aftermaths": (
            "旁人未必看得出来，但这件事已经被两个人记住了。",
            "没有人宣布结果，气氛却悄悄往前走了一步。",
            "这句话没有落空，只是被留给了更合适的下一秒。",
            "之后再回想时，谁都会记得这一点恰好的停顿。",
            "小小的变化没有惊动任何人，却把故事往前推了一格。",
            "这不是结论，只是一盏提醒以后还能回来的灯。",
            "有人把它当成偶然，有人知道它已经不是了。",
            "事情没有变得夸张，却比刚才多了一层值得在意的意思。",
            "你们都没有多说，但沉默这次没有显得尴尬。",
            "原本要被略过的细节，最后成了今天最清楚的镜头。",
            "没有掌声，也没有人起哄，正好让这份变化留得更久。",
            "下一次见面前，这件事会一直停在彼此心里。",
            "连路过的人都不知道，刚才其实有一段关系转了方向。",
            "这点温度不大，却足够让之后的选择变得不一样。",
            "你们没有约定什么，但也没有像之前那样各自走开。",
            "故事没有被说破，于是还保留着继续生长的余地。",
        ),
        "endings": (
            "夜色收好最后一点热闹，今天的关系没有被匆忙带走。",
            "人群散去以后，仍有人记得自己曾被好好接住。",
            "最后一个普通的瞬间，也成了今天最不普通的留白。",
            "灯一盏盏暗下去，故事却没有因此失去明天。",
            "今天没有谁替谁下结论，但每个人都带走了一点答案。",
            "不必把话说满，这一页已经足够值得留档。",
            "熟悉的地方恢复原样，几段关系却已经不再和刚才一样。",
            "晚一点再回想，大家会发现今天比想象中多了一点温柔。",
        ),
    },
    "轻奇幻": {
        "openings": (
            "{prop}像是听懂了你们的犹豫，轻轻亮了一下。",
            "{mechanism}没有立刻给出答案，只把问题留在半空。",
            "空气里多了一点不属于平常夜晚的光，刚好照向{prop}。",
            "某个看不见的规则在此刻松动，允许故事多停留一分钟。",
            "风把一枚不该出现的提示送到你们面前。",
            "周围的人照常经过，只有这里像被悄悄按下了暂停。",
            "没有人能解释{prop}为什么会在这时出现，但它显然不是偶然。",
            "远处传来一声轻响，像是今晚的世界替谁翻开了下一页。",
            "一条原本普通的路线忽然多出一个只对你们开放的岔口。",
            "{mechanism}把名字藏进细节里，等着有人认真发现。",
            "光影绕过人群，偏偏在你们之间留下了一条细细的线。",
            "一件小东西开始不讲道理，却刚好讲得通你们的故事。",
            "答案没有从天而降，只是在{prop}旁边露出一点边角。",
            "今晚的规则比平时温柔，允许没说完的话再试一次。",
            "有个看不见的旁观者似乎正等着看你们怎么选择。",
            "时间没有倒流，但某些错过忽然有了回头的机会。",
        ),
        "aftermaths": (
            "那点光没有消失，只是藏进了之后还会再遇见的时刻。",
            "世界没有替谁完成心愿，却给了故事继续的许可。",
            "谜底仍留着一半，正好让这段关系有理由往下走。",
            "没有人知道刚才的异样会不会再来，但你们都记住了。",
            "线索没有直接指向答案，却把两个人带到了同一条路上。",
            "奇怪的事情恢复安静，只有心里的位置没有回到原样。",
            "这一次没有得到全部回应，却也不再是毫无回应。",
            "被改变的不只是{prop}，还有你们看待彼此的方式。",
            "答案被折成一小片，留给明天再慢慢展开。",
            "世界装作什么都没发生，却替这段故事盖下了一枚印记。",
            "原本只属于传闻的事，此刻成了你们共同知道的秘密。",
            "光从这里离开前，把一点勇气留在了原地。",
            "今晚没有结束一段谜题，只是让它有了更愿意等待的人。",
            "有些解释不必马上出现，线索已经足够说明方向。",
            "之后每次再看见类似的光，你们都会想起这一刻。",
            "故事没有变得确定，却变得值得认真对待。",
        ),
        "endings": (
            "最后一点异光熄灭时，今晚留下的并不只是一个谜题。",
            "世界恢复寻常，但有人已经带走了一枚只属于今天的线索。",
            "没有人拿到完整答案，反而让明天有了被期待的理由。",
            "那些没被说破的心意，被今晚轻轻保管起来。",
            "谜面合上以后，关系却比打开时更清楚了一点。",
            "奇遇退场时，没有带走任何一段已经被认真看见的关系。",
            "今晚的规则最终归位，而你们的故事没有。",
            "最后一声轻响过后，有人终于知道该往哪里走。",
        ),
    },
    "强幻想": {
        "openings": (
            "{prop}在夜色里显出真正的轮廓，像在等待被选择。",
            "{mechanism}启动时，四周的规则暂时不再完全可靠。",
            "远处的光轨改变方向，把所有人的视线引向这一刻。",
            "命运没有直接开口，只让{prop}在风里发出回音。",
            "世界的边缘像被翻开一页，露出一条原本不存在的路。",
            "某种古老的约定忽然生效，要求每个人都认真对待自己的选择。",
            "影子比主人先迈出一步，像是在替谁试探答案。",
            "时间的刻度短暂错位，给没来得及说的话留出了缝隙。",
            "星光落在{prop}上，像为这段关系写下了一条注脚。",
            "一阵不属于这里的风穿过人群，把故事推向了更大的舞台。",
            "每个人都听见了不同的召唤，只有你们听见的是同一句。",
            "漂浮的光点围成一圈，安静等着有人先作出回应。",
            "世界没有停下，却把这一处空间让给了正在发生的事。",
            "隐藏的门在视线尽头出现，钥匙恰好就在{prop}旁边。",
            "某个被遗忘的规则重新被唤醒，关系也因此多了一次机会。",
            "夜空像一张正在重写的地图，而你们刚好站在交叉点。",
        ),
        "aftermaths": (
            "余波越过很远的地方，最后仍落回这段关系身上。",
            "没有谁被命运替代，选择却从此有了更清楚的重量。",
            "世界收回了异象，却没有收回已经产生的牵引。",
            "这一刻没有解决所有问题，但让之后的道路不再孤立。",
            "被点亮的东西暂时沉默，等待下一次有人愿意伸手。",
            "故事的尺度突然变大，心意反而显得比之前更明确。",
            "所有光都散开后，还留着一条只属于你们的方向。",
            "代价没有立刻出现，回应却已经在关系里落了印。",
            "有些门不会为每个人打开，今天它恰好没有把你们分开。",
            "未来仍在变化，但这件事已经成为不能忽略的一笔。",
            "命运没有承诺结局，只承认了这次相遇确实发生过。",
            "你们没有赢得整个世界，却赢得了一次继续并肩的资格。",
            "回声穿过很远的地方，仍把答案送回了正确的人手里。",
            "这段关系被写进更大的故事，却没有失去自己的名字。",
            "光轨重新稳定后，谁都知道刚才不是一次普通的擦肩。",
            "夜空恢复沉默，而有些选择已经不可能当作没发生。",
        ),
        "endings": (
            "异象退去时，今天的关系仍在更大的故事里发亮。",
            "星图合上前，有人终于在彼此身上找到了坐标。",
            "世界没有给出奖赏，却承认了每一次认真伸出的手。",
            "最后一道光离开后，明天仍然值得被一起抵达。",
            "被改写的不是结局，而是大家面对结局的方式。",
            "今晚的门全部关闭，只有已经建立的联系没有消失。",
            "传说回到传说里，真实发生过的事却留在了人心中。",
            "远方的回声停止后，故事才刚刚有了自己的方向。",
        ),
    },
}


EVENT_BEATS: dict[str, tuple[str, ...]] = {
    "direct_positive": (
        "{actor}没有抢着替{target}作答，只把{prop}留在对方看得见的地方。",
        "{actor}顺手接住了{target}差点错过的{prop}，又把选择还给了对方。",
        "{actor}先停下来听完{target}的意思，{mechanism}因此没有走偏。",
        "{actor}把原本只够一个人的机会分给了{target}。",
        "{actor}没有把话说满，却让{target}知道自己没有被忽略。",
        "{actor}替{target}守住了{prop}，直到真正的回应赶上来。",
        "{actor}在最容易错开的地方回了头，正好看见{target}。",
        "{actor}把一件不起眼的小事做得很认真，{target}因此愿意留下。",
        "{actor}没有替{target}决定方向，只陪着把{mechanism}走完。",
        "{actor}把本该匆匆过去的片刻留给了{target}。",
        "{actor}先把{prop}推回原位，再等{target}自己伸手。",
        "{actor}给了{target}一个不需要解释的台阶，也给了故事一个继续的理由。",
    ),
    "direct_negative": (
        "{actor}本想替{target}处理{prop}，却在关键处多替对方走了一步。",
        "{actor}听见了一半意思就作出判断，{mechanism}因此偏离了原来的方向。",
        "{actor}赶得太急，反而让{target}错过了最合适的时机。",
        "{actor}把好意递得太用力，{target}一时不知道该怎么接住。",
        "{actor}以为自己懂得{target}的选择，结果把{prop}放到了错误的位置。",
        "{actor}想把话说清楚，却让最重要的一句留在了后面。",
        "{actor}没有注意到{target}已经停下，于是两个人擦过了同一个答案。",
        "{actor}替{target}做了决定，{mechanism}却没有因此变得简单。",
        "{actor}把注意力放在了{prop}上，没看见{target}此刻真正想要的回应。",
        "{actor}来得不算晚，却刚好晚过了最自然的那一步。",
        "{actor}把一件小事想得太复杂，{target}只好先把心意收起来。",
        "{actor}没有故意让谁难堪，只是这次没有找对说话的方式。",
    ),
    "response": (
        "{actor}终于把回应送了出来，只是每一段等待听见的并不完全相同。",
        "{actor}回头看见了那些朝自己靠近的线索，开始一一把{prop}递回去。",
        "{actor}没有假装所有人都一样重要，而是认真处理了每一份等候。",
        "{actor}让{mechanism}替自己开口，回应因此带着一点意外的偏差。",
        "{actor}把原本藏着的意思放到明面上，却不能保证每个人都听到同一句。",
        "{actor}这次没有躲开视线，等候的人终于得到各自的答案。",
        "{actor}把{prop}放到大家面前，回应像水面一样向不同方向荡开。",
        "{actor}尝试照顾每一段关系，但今晚的规则不允许所有愿望同时落地。",
        "{actor}先叫出了一个名字，其他人也因此知道等待没有被彻底忘记。",
        "{actor}让迟到的回应抵达，温度却会在不同关系里留下不同痕迹。",
        "{actor}没有把答案藏起来，只是答案本身比想象中更有分量。",
        "{actor}把{mechanism}推向所有等待的人，关系因此出现了分岔。",
    ),
    "assist_positive": (
        "{actor}没有把自己写进主角位置，只替{left}和{right}守住了{prop}。",
        "{actor}看见{left}和{right}快要错开，便把{mechanism}往回拨了一点。",
        "{actor}替{left}递出了那句差点被吞回去的话，{right}刚好听见。",
        "{actor}不声张地补上一个空位，让{left}和{right}有机会站到同一边。",
        "{actor}把{prop}交给了真正需要的人，{left}和{right}因此没有走散。",
        "{actor}没有替谁决定结局，只让{left}和{right}重新拥有选择。",
        "{actor}把混乱的线索理顺，{left}和{right}终于看见彼此的方向。",
        "{actor}给{left}和{right}留了一点时间，足够让误会不必继续长大。",
        "{actor}把自己的机会往后放了一步，正好替{left}和{right}接住了后续。",
        "{actor}从旁边推了推{mechanism}，没有抢镜，却改变了关系的落点。",
        "{actor}替{left}和{right}挡开了一个不合时宜的岔路。",
        "{actor}在无人注意的地方完成了一次接力，让{left}和{right}没再错过。",
    ),
    "assist_negative": (
        "{actor}本想替{left}和{right}帮忙，却把{prop}送进了错误的节奏里。",
        "{actor}以为自己在接力，结果让{left}和{right}听见了彼此不该先听见的话。",
        "{actor}好心替{left}解释，却让{right}把重点听成了别的意思。",
        "{actor}把{mechanism}推得太快，{left}和{right}反而没能跟上。",
        "{actor}想替{left}和{right}制造机会，却让{prop}成了新的误会。",
        "{actor}没有恶意，只是这一推刚好把{left}和{right}推向了不同方向。",
        "{actor}替{left}抢着回应，{right}却因此更难判断真正的心意。",
        "{actor}把一条线索交错了人，{left}和{right}只好暂时各自沉默。",
        "{actor}试图修好气氛，结果让{mechanism}把旧问题又翻了出来。",
        "{actor}想让{left}和{right}快一点和好，却错过了他们各自需要的节奏。",
        "{actor}介入得太早，{prop}还没来得及说清自己该属于谁。",
        "{actor}把好意放在了错误的一秒，{left}和{right}只好先把话截住。",
    ),
    "chain": (
        "{actor}没有绕开上次留下的问题，而是带着{prop}回到了它最初出现的地方。",
        "{actor}终于肯把之前没说完的部分补上，{mechanism}因此重新转动。",
        "{actor}在旧线索前停住，没有急着辩解，只先等{target}看清。",
        "{actor}把上次弄乱的顺序慢慢摆正，给{target}留出了回应的余地。",
        "{actor}没有否认误会，只是愿意和{target}一起把它走完。",
        "{actor}把遗落的{prop}找回来，像是在替那天的沉默补一句解释。",
        "{actor}选择回到最尴尬的地方，这次没有再让{target}一个人面对。",
        "{actor}把旧的结打开放在{target}面前，等待关系自己决定要不要继续。",
        "{actor}没有把后续交给运气，而是亲自把{mechanism}推回正确的轨道。",
        "{actor}记得那次失手，于是这次先学会了停下来。",
        "{actor}把没送出的回应带回来，终于没有让{target}再猜一次。",
        "{actor}愿意承认之前的笨拙，故事因此有了修复的可能。",
    ),
    "same_scene": (
        "{actor}和{target}同在这一幕里，却都还没有决定要让故事落到哪里。",
        "{actor}看见{target}经过{prop}，两个人都把那一瞬间当作还没命名的线索。",
        "{actor}没有贸然介入，只和{target}共享了一段短暂而安静的同场。",
        "{actor}与{target}在{mechanism}旁停了一会，关系暂时没有被谁强行改写。",
        "{actor}和{target}各自带着答案路过，却在同一个细节上多看了一眼。",
        "{actor}没有把这次相遇变成结论，只让{target}知道自己也在这里。",
    ),
}

# Keep immutable startup baselines so audits can load an isolated content
# directory repeatedly without inheriting extensions from a previous load.
_BUILTIN_STYLE_BANKS: dict[str, StyleBank] = {
    name: {pool: tuple(values) for pool, values in bank.items()}
    for name, bank in STYLE_BANKS.items()
}
_BUILTIN_EVENT_BEATS: dict[str, tuple[str, ...]] = dict(EVENT_BEATS)


_PLACEHOLDERS = frozenset({"actor", "target", "left", "right", "prop", "mechanism"})
_RESOURCE_DIRECTORY = RESOURCE_DIR / "today_wife"


def _template_fields(template: str) -> set[str]:
    fields: set[str] = set()
    formatter = string.Formatter()
    try:
        parsed = formatter.parse(template)
    except ValueError as error:
        raise ValueError(f"Invalid narrative template: {template!r}") from error
    for _literal, field_name, _format_spec, _conversion in parsed:
        if field_name:
            fields.add(field_name)
    return fields


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty JSON list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{label} must contain non-empty strings only")
    result = tuple(item.strip() for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{label} contains duplicate templates")
    unknown = set().union(*(_template_fields(item) for item in result)) - _PLACEHOLDERS
    if unknown:
        raise ValueError(f"{label} uses unknown placeholders: {sorted(unknown)!r}")
    return result


def _append_unique(current: tuple[str, ...], added: tuple[str, ...], label: str) -> tuple[str, ...]:
    duplicates = set(current).intersection(added)
    if duplicates:
        raise ValueError(f"{label} repeats an existing template: {sorted(duplicates)[0]!r}")
    return current + added


def _scope_matches(payload: Mapping[str, object], resource_path: Path, theme_id: str | None, script_id: str | None, mechanism: str | None) -> bool:
    scope = payload.get("applies_to", {})
    if not isinstance(scope, dict):
        raise ValueError(f"{resource_path.name}: applies_to must be a JSON object")
    requested = {
        "theme_ids": theme_id,
        "script_ids": script_id,
        "mechanisms": mechanism,
    }
    for key, actual in requested.items():
        accepted = scope.get(key)
        if accepted is None:
            continue
        if not isinstance(accepted, list) or not all(isinstance(value, str) and value for value in accepted):
            raise ValueError(f"{resource_path.name}: applies_to.{key} must be a string list")
        # A report with no story context intentionally sees every pack; a
        # live event must meet every declared condition to receive its copy.
        if actual is not None and actual not in accepted:
            return False
    return True


def load_narrative_resources(
    resource_directory: Path = _RESOURCE_DIRECTORY,
    *,
    theme_id: str | None = None,
    script_id: str | None = None,
    mechanism: str | None = None,
) -> tuple[dict[str, StyleBank], dict[str, tuple[str, ...]], tuple[tuple[str, ...], ...], tuple[Path, ...]]:
    """Load every independent JSON content pack in filename order.

    A pack may extend styles, event beats, or add one named camera layer.
    This keeps future seasonal and theme-specific copy out of the interaction
    engine and makes a bad authoring change fail at startup with its filename.
    """
    styles = {name: dict(bank) for name, bank in _BUILTIN_STYLE_BANKS.items()}
    events = dict(_BUILTIN_EVENT_BEATS)
    layers: dict[str, tuple[str, ...]] = {}
    loaded: list[Path] = []
    if not resource_directory.is_dir():
        return styles, events, (), ()

    for resource_path in sorted(resource_directory.glob("*.json")):
        try:
            payload = json.loads(resource_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Unable to load today-wife narrative resource: {resource_path}") from error
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError(f"{resource_path.name} must declare schema_version=1")
        if not _scope_matches(payload, resource_path, theme_id, script_id, mechanism):
            continue

        extensions = payload.get("styles", {})
        if not isinstance(extensions, dict):
            raise ValueError(f"{resource_path.name}: styles must be a JSON object")
        for style, extension in extensions.items():
            if style not in styles or not isinstance(extension, dict):
                raise ValueError(f"{resource_path.name}: unknown or invalid narrative style {style!r}")
            for pool_name in ("openings", "aftermaths", "endings"):
                added = extension.get(pool_name, [])
                if added:
                    styles[style][pool_name] = _append_unique(
                        tuple(styles[style][pool_name]),
                        _strings(added, f"{resource_path.name}: styles.{style}.{pool_name}"),
                        f"{resource_path.name}: styles.{style}.{pool_name}",
                    )

        event_extensions = payload.get("events", {})
        if not isinstance(event_extensions, dict):
            raise ValueError(f"{resource_path.name}: events must be a JSON object")
        for kind, added in event_extensions.items():
            if kind not in events:
                raise ValueError(f"{resource_path.name}: unknown narrative event kind {kind!r}")
            if added:
                events[kind] = _append_unique(
                    tuple(events[kind]),
                    _strings(added, f"{resource_path.name}: events.{kind}"),
                    f"{resource_path.name}: events.{kind}",
                )

        layer_extensions = payload.get("layers", {})
        if not isinstance(layer_extensions, dict):
            raise ValueError(f"{resource_path.name}: layers must be a JSON object")
        for name, values in layer_extensions.items():
            added = _strings(values, f"{resource_path.name}: layers.{name}")
            layers[name] = _append_unique(layers.get(name, ()), added, f"{resource_path.name}: layers.{name}")
        loaded.append(resource_path)

    return styles, events, tuple(layers.values()), tuple(loaded)


STYLE_BANKS, EVENT_BEATS, _NARRATIVE_LAYERS, _LOADED_RESOURCE_PATHS = load_narrative_resources()


@lru_cache(maxsize=128)
def _content_for_context(
    theme_id: str | None,
    script_id: str | None,
    mechanism: str | None,
) -> tuple[dict[str, StyleBank], dict[str, tuple[str, ...]], tuple[tuple[str, ...], ...]]:
    styles, events, layers, _paths = load_narrative_resources(
        theme_id=theme_id,
        script_id=script_id,
        mechanism=mechanism,
    )
    # The production directory always has an unscoped base layer.  Preserve a
    # safe fallback if an author later scopes every extension away from one
    # context, rather than making a normal interaction impossible to render.
    return styles, events, layers or _NARRATIVE_LAYERS


def narrative_resource_report() -> dict[str, object]:
    """Describe the loadable content surface for the audit command and tests."""
    return {
        "paths": tuple(str(path) for path in _LOADED_RESOURCE_PATHS),
        "styles": {name: {pool: len(values) for pool, values in bank.items()} for name, bank in STYLE_BANKS.items()},
        "events": {name: len(values) for name, values in EVENT_BEATS.items()},
        "layers": tuple(len(layer) for layer in _NARRATIVE_LAYERS),
    }


def _pick(pool: tuple[str, ...], seed: str, slot: str) -> str:
    digest = hashlib.blake2s(f"{seed}:{slot}".encode("utf-8"), digest_size=8).digest()
    return pool[int.from_bytes(digest, "big") % len(pool)]


def compose_narrative(
    style: str,
    kind: str,
    seed: str,
    values: Mapping[str, str],
    *,
    theme_id: str | None = None,
    script_id: str | None = None,
) -> str:
    """Build a three-beat public event narration from declarative pools."""
    styles, events, layers = _content_for_context(theme_id, script_id, values.get("mechanism"))
    bank = styles.get(style, styles["现实日常"])
    event_pool = events.get(kind, events["same_scene"])
    merged = dict(values)
    camera = "".join(_pick(layer, seed, f"camera:{index}") for index, layer in enumerate(layers))
    return "".join(
        (
            _pick(bank["openings"], seed, "opening").format(**merged),
            camera.format(**merged),
            _pick(event_pool, seed, "event").format(**merged),
            _pick(bank["aftermaths"], seed, "aftermath").format(**merged),
        )
    )


def compose_conclusion(
    style: str,
    seed: str,
    *,
    theme_id: str | None = None,
    script_id: str | None = None,
    mechanism: str | None = None,
) -> str:
    styles, _events, _layers = _content_for_context(theme_id, script_id, mechanism)
    bank = styles.get(style, styles["现实日常"])
    return _pick(bank["endings"], seed, "ending")


def narrative_capacity(
    *,
    theme_id: str | None = None,
    script_id: str | None = None,
    mechanism: str | None = None,
) -> dict[str, int]:
    """Expose actual combinations for a content context or the whole library."""
    styles, events, layers = _content_for_context(theme_id, script_id, mechanism) if theme_id or script_id or mechanism else (STYLE_BANKS, EVENT_BEATS, _NARRATIVE_LAYERS)
    layer_capacity = 1
    for layer in layers:
        layer_capacity *= len(layer)
    result: dict[str, int] = {}
    for style, bank in styles.items():
        for kind, event_pool in events.items():
            result[f"{style}:{kind}"] = len(bank["openings"]) * len(event_pool) * len(bank["aftermaths"]) * layer_capacity
    return result
