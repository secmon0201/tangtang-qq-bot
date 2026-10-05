"""Data-driven daily story packs for the 今日缘分 interaction game.

The interaction engine deliberately knows nothing about a convenience store,
memory pawnshop, or sky rail.  A new pack only needs a :class:`ThemePack` and
compatible scripts, which makes content growth a data-authoring task rather
than a game-logic change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class DirectorConfig:
    """Tunable knobs intentionally kept outside the interaction engine."""

    act_thresholds: tuple[int, int] = (8, 20)
    key_event_chance: float = 0.18
    act_weights: tuple[Mapping[str, int], Mapping[str, int], Mapping[str, int]] = (
        {"direct": 42, "response": 18, "third": 25, "chain": 15},
        {"direct": 30, "response": 30, "third": 28, "chain": 12},
        {"direct": 30, "response": 22, "third": 20, "chain": 28},
    )


@dataclass(frozen=True, slots=True)
class MechanicRule:
    """A theme mechanism that changes outcomes as well as narration."""

    name: str
    event_bias: Mapping[str, int]
    delta_bias: int = 0
    response_mode: str = "focused"
    third_positive_rate: int = 68
    third_spread: bool = False
    chain_success_rate: int = 70


def _rule_group(
    names: tuple[str, ...],
    *,
    event_bias: Mapping[str, int],
    delta_bias: int = 0,
    response_mode: str = "focused",
    third_positive_rate: int = 68,
    third_spread: bool = False,
    chain_success_rate: int = 70,
) -> dict[str, MechanicRule]:
    return {
        name: MechanicRule(
            name,
            event_bias,
            delta_bias,
            response_mode,
            third_positive_rate,
            third_spread,
            chain_success_rate,
        )
        for name in names
    }


# These profiles are deliberately grouped by player-facing mechanism rather
# than by theme ID. Adding a theme means selecting existing profiles or adding
# one row here, without teaching the interaction engine new special cases.
MECHANIC_RULES: dict[str, MechanicRule] = {
    **_rule_group(
        ("代存物", "座位保留", "最后一份小吃", "愿望硬币", "匿名回信", "失主认领", "星屑契约", "失重座位", "云层换乘"),
        event_bias={"direct": 16, "response": 8, "third": -3, "chain": 10},
        delta_bias=3,
        response_mode="shared",
        third_positive_rate=78,
        chain_success_rate=82,
    ),
    **_rule_group(
        ("夜班便签", "换乘指引", "摊位暗号", "错位车票", "秘密问答", "漂流地址", "记忆转交", "愿望交换", "风向车票"),
        event_bias={"direct": 0, "response": 18, "third": 8, "chain": -2},
        response_mode="volatile",
        third_positive_rate=62,
    ),
    **_rule_group(
        ("错拿归还", "遗落车票", "零钱流转", "悖论便签", "错位饮料", "误投信封", "账本代价", "影子摊位", "坠落行李", "时间锚点", "记忆典当", "月光货币"),
        event_bias={"direct": -8, "response": 2, "third": 20, "chain": 14},
        delta_bias=-2,
        response_mode="volatile",
        third_positive_rate=48,
        third_spread=True,
        chain_success_rate=78,
    ),
    **_rule_group(
        ("打烊倒计时", "到站倒计时", "收摊倒计时", "一分钟回溯", "找零预言", "潮汐递送", "赎回选择", "午夜竞价", "星图广播"),
        event_bias={"direct": 5, "response": -4, "third": 8, "chain": 24},
        delta_bias=1,
        third_positive_rate=70,
        chain_success_rate=88,
    ),
}
DEFAULT_MECHANIC_RULE = MechanicRule(
    "同场线索", {"direct": 0, "response": 0, "third": 0, "chain": 0}
)


def mechanic_for(name: str) -> MechanicRule:
    return MECHANIC_RULES.get(name, DEFAULT_MECHANIC_RULE)


@dataclass(frozen=True, slots=True)
class StoryScript:
    id: str
    title: str
    prop: str
    opening: str
    acts: tuple[str, str, str]
    routes: tuple[str, ...]
    setup_fact: str
    central_question: str
    facts: tuple[str, ...]
    choices: Mapping[str, tuple[str, ...]]
    outcomes: Mapping[str, tuple[str, ...]]
    endings: tuple[str, ...]
    start_beat_id: str = "opening"
    beats: Mapping[str, "StoryBeat"] = field(default_factory=dict)
    ending_by_id: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoryTransition:
    """A durable result of one named choice at one story beat."""

    outcome_id: str
    next_beat_id: str
    next_hook: str
    route_delta: Mapping[str, int]
    ending_id: str


@dataclass(frozen=True, slots=True)
class StoryChoice:
    """One player-facing choice, kept local to a script beat."""

    id: str
    label: str
    command: str
    intent: str
    risk_hint: str
    effect_profile: str
    transitions: Mapping[str, StoryTransition]


@dataclass(frozen=True, slots=True)
class StoryBeat:
    """A persisted relation-level node in the daily story graph."""

    id: str
    hook: str
    choices: tuple[StoryChoice, ...]


@dataclass(frozen=True, slots=True)
class ThemePack:
    id: str
    title: str
    style: str
    mechanisms: tuple[str, ...]
    scripts: tuple[StoryScript, ...]
    direct_positive: tuple[str, ...]
    direct_negative: tuple[str, ...]
    response: tuple[str, ...]
    assist_positive: tuple[str, ...]
    assist_negative: tuple[str, ...]
    chain_resolution: tuple[str, ...]
    conclusions: tuple[str, ...]
    director: DirectorConfig = DirectorConfig()


def _scripts(theme_id: str, values: tuple[tuple[str, str, str, tuple[str, str, str]], ...]) -> tuple[StoryScript, ...]:
    scripts: list[StoryScript] = []
    for index, (title, prop, opening, acts) in enumerate(values):
        # The templates deliberately refer only to this script's prop and its
        # current hook.  Theme packs still tune probabilities, but can never
        # leak an unrelated coupon, drink, or ticket into this scene.
        endings = (
            f"{prop}还留在原处，但大家已经知道该怎样回应它。",
            f"关于{prop}的答案没有一次说完，故事却已经有了下一页。",
            f"收场之前，{prop}见证了今天每一次没有被敷衍的回应。",
        )
        scripts.append(
            StoryScript(
                id=f"{theme_id}_{index + 1}",
                title=title,
                prop=prop,
                opening=opening,
                acts=acts,
                routes=("找回", "错过", "被接住", "留到明天"),
                setup_fact=f"《{title}》里，{prop}还留在故事开始的位置。",
                central_question=f"谁会先回应{prop}留下的线索？",
                facts=(
                    f"{prop}还没有被匆忙带走",
                    f"关于{prop}的来处仍有一个答案没说完",
                    f"收场之前，{prop}还在等一次真诚的回应",
                ),
                choices={
                    "靠近": (
                        "{actor}没有绕开“{hook}”，先替{target}留住了{prop}。",
                        "{actor}把{prop}往{target}那边轻轻推近，等对方决定要不要接住。",
                    ),
                    "倾听": (
                        "{actor}没有急着替{target}下结论，只围绕{prop}认真听完“{hook}”。",
                        "{actor}把{prop}留在原处，先让{target}把这段线索说完整。",
                    ),
                    "回应": (
                        "{actor}顺着“{hook}”给出了回应，也把{prop}旁的等待说清楚了。",
                        "{actor}没有让{prop}旁的那句询问落空，认真把答案交回对方手里。",
                    ),
                    "修复": (
                        "{actor}回到{prop}旁，没有回避“{hook}”，而是把误会一点点说开。",
                        "{actor}先承认那次没接住的瞬间，再和{target}一起把{prop}放回原处。",
                    ),
                    "助攻": (
                        "{actor}没有替别人决定结局，只替{left}和{right}把{prop}旁的线索递回去。",
                        "{actor}替{left}和{right}留出一点时间，让他们自己回应{prop}留下的问题。",
                    ),
                },
                outcomes={
                    "positive": (
                        "{mark}让{left}和{right}把这条线索往前推了一步。",
                        "{left}和{right}没有错过{prop}旁的这一刻，{mark}成了新的事实。",
                    ),
                    "negative": (
                        "{mark}让{left}和{right}暂时没能把{prop}旁的话说清楚。",
                        "{left}和{right}在{prop}旁停住了，{mark}留下了需要回应的分岔。",
                    ),
                    "neutral": (
                        "{left}和{right}都记住了{prop}旁的这一幕，答案还可以慢一点给出。",
                    ),
                    "resolved": (
                        "之前悬着的线索被接住了，{left}和{right}终于能从{prop}旁继续往前。",
                    ),
                },
                endings=endings,
                beats=_script_beats(prop),
                ending_by_id={
                    "held_answer": endings[0],
                    "new_page": endings[1],
                    "night_memory": endings[2],
                },
            )
        )
    return tuple(scripts)


def _script_beats(prop: str) -> Mapping[str, StoryBeat]:
    """Create the same authored branch topology for every prop-specific script.

    The prose remains script-local through ``prop`` while the graph gives each
    relationship a durable choice -> result -> next-hook sequence.
    """

    def transition(
        choice_id: str,
        outcome: str,
        next_beat: str,
        hook: str,
        route_delta: Mapping[str, int],
        ending_id: str,
    ) -> StoryTransition:
        return StoryTransition(
            outcome_id=f"{choice_id}_{outcome}",
            next_beat_id=next_beat,
            next_hook=hook,
            route_delta=dict(route_delta),
            ending_id=ending_id,
        )

    def choice(
        choice_id: str,
        label: str,
        intent: str,
        risk_hint: str,
        effect_profile: str,
        transitions: Mapping[str, StoryTransition],
    ) -> StoryChoice:
        return StoryChoice(
            id=choice_id,
            label=label,
            command=intent,
            intent=intent,
            risk_hint=risk_hint,
            effect_profile=effect_profile,
            transitions=dict(transitions),
        )

    opening_hold = "opening_hold"
    opening_listen = "opening_listen"
    return {
        "opening": StoryBeat(
            id="opening",
            hook=f"{prop}还在等一个不仓促的回应",
            choices=(
                choice(
                    opening_hold,
                    "先替对方留住线索",
                    "靠近",
                    "更直接，可能把话推得太快",
                    "bold",
                    {
                        "positive": transition(opening_hold, "positive", "shared_moment", f"{prop}已经被留住，等一句更明确的话", {"留到明天": 2, "被接住": 1}, "new_page"),
                        "negative": transition(opening_hold, "negative", "repair", f"刚才的靠近让{prop}旁多了一点误会", {"错过": 3}, "night_memory"),
                        "neutral": transition(opening_hold, "neutral", "shared_moment", f"{prop}还在原处，彼此都在等下一步", {"留到明天": 2}, "new_page"),
                    },
                ),
                choice(
                    opening_listen,
                    "先听完没说完的话",
                    "倾听",
                    "推进较慢，但能稳住这段关系",
                    "steady",
                    {
                        "positive": transition(opening_listen, "positive", "answer", f"关于{prop}的来处已经有人愿意说下去", {"被接住": 3}, "held_answer"),
                        "negative": transition(opening_listen, "negative", "repair", f"听到一半的话让{prop}旁留下了误解", {"错过": 2}, "night_memory"),
                        "neutral": transition(opening_listen, "neutral", "shared_moment", f"{prop}旁的沉默被认真保留下来", {"留到明天": 2}, "new_page"),
                    },
                ),
            ),
        ),
        "shared_moment": StoryBeat(
            id="shared_moment",
            hook=f"{prop}旁已经有了一次共同停留，下一步由当事人决定",
            choices=(
                choice(
                    "shared_answer",
                    "把答案交回对方手里",
                    "回应",
                    "需要对方也愿意接住这句话",
                    "answer",
                    {
                        "positive": transition("shared_answer", "positive", "after_answer", f"{prop}旁的答案已经抵达，还能继续确认", {"被接住": 4}, "held_answer"),
                        "negative": transition("shared_answer", "negative", "repair", f"答案没有完整抵达，{prop}旁需要一次修复", {"错过": 3}, "night_memory"),
                        "neutral": transition("shared_answer", "neutral", "after_answer", f"{prop}旁留下了一句不必急着回答的话", {"留到明天": 2}, "new_page"),
                    },
                ),
                choice(
                    "shared_hold",
                    "再给彼此一点时间",
                    "倾听",
                    "安全，但故事会慢慢走",
                    "steady",
                    {
                        "positive": transition("shared_hold", "positive", "after_answer", f"{prop}旁的等待被认真接住", {"被接住": 2, "留到明天": 1}, "held_answer"),
                        "negative": transition("shared_hold", "negative", "repair", f"等待太久让{prop}旁的意思变得模糊", {"错过": 2}, "night_memory"),
                        "neutral": transition("shared_hold", "neutral", "shared_moment", f"{prop}还在原处，故事暂时留给明天", {"留到明天": 3}, "new_page"),
                    },
                ),
            ),
        ),
        "answer": StoryBeat(
            id="answer",
            hook=f"有人正在等{prop}旁的回应，答案只差被交到对方手里",
            choices=(
                choice(
                    "answer_reply",
                    "认真给出回应",
                    "回应",
                    "会让等待变得很明确",
                    "answer",
                    {
                        "positive": transition("answer_reply", "positive", "after_answer", f"{prop}旁的回应已经被接住", {"被接住": 4}, "held_answer"),
                        "negative": transition("answer_reply", "negative", "repair", f"回应没能赶上，{prop}旁留下了需要解释的空白", {"错过": 3}, "night_memory"),
                        "neutral": transition("answer_reply", "neutral", "after_answer", f"{prop}旁的答案还在等下一次确认", {"留到明天": 2}, "new_page"),
                    },
                ),
            ),
        ),
        "repair": StoryBeat(
            id="repair",
            hook=f"{prop}旁那次没说清的话还在，愿不愿意把它找回来？",
            choices=(
                choice(
                    "repair_return",
                    "回去把误会说开",
                    "修复",
                    "需要承担一次没接住的瞬间",
                    "repair",
                    {
                        "positive": transition("repair_return", "positive", "after_answer", f"{prop}旁那段误会被找回来了", {"找回": 4, "被接住": 1}, "held_answer"),
                        "negative": transition("repair_return", "negative", "repair", f"这次仍没说清，{prop}旁的分岔还没有结束", {"错过": 3}, "night_memory"),
                        "neutral": transition("repair_return", "neutral", "shared_moment", f"{prop}旁的误会没有消失，但已经能被看见", {"找回": 2, "留到明天": 1}, "new_page"),
                    },
                ),
            ),
        ),
        "after_answer": StoryBeat(
            id="after_answer",
            hook=f"{prop}旁的答案已经抵达，接下来可以把这一页留成共同记忆",
            choices=(
                choice(
                    "after_hold",
                    "留下一段共同记忆",
                    "倾听",
                    "温和收束，结果会留在今天",
                    "steady",
                    {
                        "positive": transition("after_hold", "positive", "after_answer", f"{prop}旁留下了可被记住的共同答案", {"被接住": 2}, "held_answer"),
                        "negative": transition("after_hold", "negative", "repair", f"{prop}旁又冒出一点没说透的担心", {"错过": 2}, "night_memory"),
                        "neutral": transition("after_hold", "neutral", "after_answer", f"{prop}旁的这一页安静留到明天", {"留到明天": 2}, "new_page"),
                    },
                ),
            ),
        ),
    }


THEME_PACKS: tuple[ThemePack, ...] = (
    ThemePack(
        id="night_convenience",
        title="深夜便利店",
        style="现实日常",
        mechanisms=("代存物", "夜班便签", "打烊倒计时", "错拿归还"),
        scripts=_scripts("night_convenience", (
            ("最后一杯热可可", "还冒着热气的纸杯", "收银台上只剩一杯热可可。", ("第一幕｜夜班刚开始", "第二幕｜便签被留在柜台", "第三幕｜自动门即将落锁")),
            ("找零里的电影票", "折成四角的电影票", "找零盒里压着一张没被取走的电影票。", ("第一幕｜零钱发出轻响", "第二幕｜票根换了主人", "第三幕｜最后一盏灯熄灭")),
            ("借走未还的伞", "一把湿漉漉的透明伞", "门边的伞桶里，多了一把没有名字的伞。", ("第一幕｜雨落在玻璃上", "第二幕｜伞被谁借走", "第三幕｜雨声终于小了")),
            ("冷柜旁的旧便签", "贴着笑脸的便签", "饮料冷柜侧面贴着一张快要卷边的便签。", ("第一幕｜冷气还没有散", "第二幕｜便签写了新名字", "第三幕｜夜班结束前")),
            ("凌晨送达的包裹", "没有寄件人的小包裹", "店门口放着一个写错收件人的小包裹。", ("第一幕｜包裹先到了", "第二幕｜收件人出现", "第三幕｜骑手再一次路过")),
        )),
        direct_positive=(
            "你替{target}按住了快合上的冷柜门，{target}没有急着离开。",
            "你把{prop}留在收银台最显眼的位置，{target}正好看见。",
            "你和{target}在夜班便签上写下同一个答案。",
        ),
        direct_negative=(
            "你把{prop}递错了方向，{target}只来得及看见你的背影。",
            "你想替{target}留住位置，却让自动门在最不合适的时候合上。",
            "你把想说的话写在便签背面，最后被雨水晕开了。",
        ),
        response=(
            "{actor}把一份热可可留给还在等候的人，却只能先叫出一个名字。",
            "{actor}回头确认了便签上的字，也让一些等待有了答案。",
            "{actor}把柜台上的东西逐一递回，却有人还是慢了半拍。",
        ),
        assist_positive=(
            "{actor}替{left}和{right}把{prop}代存在柜台，刚好接住了他们的故事。",
            "{actor}在夜班便签上补了一句提醒，让{left}和{right}没有错过彼此。",
        ),
        assist_negative=(
            "{actor}本想替{left}递话，却把{right}叫去了另一排货架。",
            "{actor}错拿了{prop}，让{left}和{right}的等待被打断。",
        ),
        chain_resolution=(
            "上次没送出的东西还在柜台。{actor}和{target}终于一起把它放回原处。",
            "自动门再一次打开时，{actor}没有错过{target}留下的那句解释。",
        ),
        conclusions=(
            "自动门最后一次打开时，没人把故事忘在门外。",
            "夜班结束了，柜台上仍留着有人替别人保管的心意。",
            "灯熄下去前，几段没说完的话都有了新的去处。",
        ),
    ),
    ThemePack(
        id="last_subway",
        title="末班地铁",
        style="现实日常",
        mechanisms=("座位保留", "换乘指引", "遗落车票", "到站倒计时"),
        scripts=_scripts("last_subway", (
            ("换乘通道的游戏币", "一枚滚向轨道边的游戏币", "换乘通道里有一枚没有被捡起的游戏币。", ("第一幕｜末班提示响起", "第二幕｜列车延后半分钟", "第三幕｜车门即将关闭")),
            ("后排座位的耳机", "只剩一边有声音的耳机", "最后一节车厢的座位上放着一副耳机。", ("第一幕｜座位还空着", "第二幕｜耳机开始播放", "第三幕｜终点站将至")),
            ("站台尽头的车票", "被风吹起的纸质车票", "站台尽头的风把一张车票吹到黄线旁。", ("第一幕｜风从隧道出来", "第二幕｜车票被谁按住", "第三幕｜列车灯光靠近")),
            ("错开的换乘图", "被圈了两条路线的地图", "换乘图上有两条被不同人圈出的路线。", ("第一幕｜指示牌亮起", "第二幕｜路线开始分岔", "第三幕｜只剩最后一次选择")),
            ("末班车的空座", "靠窗的一张空座", "车门旁还有一张一直没人坐下的空座。", ("第一幕｜广播报出末班", "第二幕｜有人替人占座", "第三幕｜列车离站之前")),
        )),
        direct_positive=(
            "{actor}替{target}留住了车门旁的位置，列车因此晚了一秒关门。",
            "{actor}和{target}在同一张换乘图上圈出了相同的方向。",
            "{actor}替{target}按住了差点被风卷走的{prop}。",
        ),
        direct_negative=(
            "{actor}看错了换乘方向，{target}只能站在另一边的站台。",
            "{actor}想替{target}留座，却被人潮冲散在车门两侧。",
            "{actor}伸手时慢了半拍，{prop}滚进了看不见的地方。",
        ),
        response=(
            "{actor}在广播声里回头，想接住所有朝自己赶来的脚步。",
            "{actor}把座位旁的位置空出来，却只能等到几个人中的一部分。",
            "{actor}把换乘路线指给等候的人，有人因此赶上，也有人仍在原地。",
        ),
        assist_positive=(
            "{actor}替{left}和{right}按住车门，让他们赶上了同一班末班车。",
            "{actor}把正确的换乘方向递给{left}和{right}，两条路线终于汇合。",
        ),
        assist_negative=(
            "{actor}把{left}的路线图递给了别人，{right}只看到列车离站。",
            "{actor}想把{left}推向车门，却让{right}错过了最后一步。",
        ),
        chain_resolution=(
            "上次错开的路线在下一块指示牌前重合，{actor}和{target}终于没再走散。",
            "{actor}回到那张空座旁，{target}仍替他留着一盏车厢灯。",
        ),
        conclusions=(
            "末班车驶出站台，车厢里多了几段没有走散的故事。",
            "列车带走了一些遗憾，也把几个人留在同一条线路上。",
            "终点站之前，终于有人把一直空着的位置坐满。",
        ),
    ),
    ThemePack(
        id="night_market",
        title="夜市收摊后",
        style="现实日常",
        mechanisms=("摊位暗号", "最后一份小吃", "零钱流转", "收摊倒计时"),
        scripts=_scripts("night_market", (
            ("最后一杯柠檬水", "半杯还冒着气泡的柠檬水", "摊主说这会是今晚最后一杯柠檬水。", ("第一幕｜摊位还亮着", "第二幕｜零钱在桌上转手", "第三幕｜招牌即将收起")),
            ("飞走的优惠券", "被夜风卷起的优惠券", "夜风把一张优惠券吹过好几张桌子。", ("第一幕｜纸片飞过人群", "第二幕｜券被谁留住", "第三幕｜最后一个摊位收摊")),
            ("烤串摊的空位", "靠近烤炉的一张小凳", "烤串摊前只剩一张还没有人坐的小凳。", ("第一幕｜炭火未熄", "第二幕｜有人替人占位", "第三幕｜烟火快散了")),
            ("套圈摊的玩偶", "一只没有被领走的玩偶", "套圈摊角落有一只等待被领走的玩偶。", ("第一幕｜圈还在飞", "第二幕｜玩偶换了方向", "第三幕｜摊主开始收绳")),
            ("收摊前的唱片", "一张封面褪色的唱片", "旧唱片摊把最后一张唱片放在了最前面。", ("第一幕｜旋律还没停", "第二幕｜唱针落下", "第三幕｜摊布被折起")),
        )),
        direct_positive=(
            "{actor}替{target}接住了被风卷走的{prop}，两个人都笑得有点意外。",
            "{actor}和{target}把最后一份小吃掰成了刚好的两半。",
            "{actor}替{target}留住了摊前的位置，等到了迟到的回答。",
        ),
        direct_negative=(
            "{actor}把零钱找错，{target}只好转身去追已经收摊的人。",
            "{actor}伸手去拿{prop}时撞翻了桌角，热闹一下变得尴尬。",
            "{actor}错把暗号告诉了别人，{target}没能等到原本的约定。",
        ),
        response=(
            "{actor}在收摊的人群里回头寻找，却不是每一双眼睛都能被先看见。",
            "{actor}把最后一口柠檬水分出去，甜味落在不同人的故事里。",
            "{actor}把摊主留下的暗号交回去，有人接住，也有人晚到。",
        ),
        assist_positive=(
            "{actor}替{left}和{right}拦住了{prop}，夜风终于没有带走这次相遇。",
            "{actor}把多找的零钱送回去，让{left}和{right}赶上收摊前的最后一句话。",
        ),
        assist_negative=(
            "{actor}好心替{left}递暗号，却让{right}误会那不是写给自己的。",
            "{actor}抢着去接{prop}，反而让{left}和{right}在夜市尽头走散。",
        ),
        chain_resolution=(
            "{actor}在收摊前找回了那张券，{target}没有追问，只和他一起把它按住。",
            "人群散开后，{actor}终于把上次没递出去的话交给了{target}。",
        ),
        conclusions=(
            "招牌收起时，夜风没有带走所有的相遇。",
            "夜市散场了，有人还替别人留着那杯没喝完的柠檬水。",
            "最后一盏摊灯熄下去前，故事刚好停在最值得记住的一刻。",
        ),
    ),
    ThemePack(
        id="reverse_station_clock",
        title="倒流的站台时钟",
        style="轻奇幻",
        mechanisms=("一分钟回溯", "时间锚点", "错位车票", "悖论便签"),
        scripts=_scripts("reverse_station_clock", (
            ("晚一分钟的列车", "停在十一点五十九分的表针", "站台时钟突然把所有人带回一分钟前。", ("第一幕｜表针开始倒走", "第二幕｜同一句话重来", "第三幕｜时间重新向前")),
            ("被擦掉的站名", "一块会变字的站牌", "终点站的名字被时钟擦去了一半。", ("第一幕｜站名消失", "第二幕｜有人写回答案", "第三幕｜列车认出终点")),
            ("重复出现的车票", "印着明天日期的车票", "检票口吐出一张刚刚已经用过的车票。", ("第一幕｜车票又回来", "第二幕｜选择被重写", "第三幕｜票根终于作废")),
            ("倒带的广播", "一句反复播放的广播", "广播把一句提醒倒着念了三遍。", ("第一幕｜广播倒放", "第二幕｜有人听懂了", "第三幕｜最后一次播报")),
            ("钟摆下的留言", "写着未来时间的便签", "站台钟摆下压着一张写给未来的便签。", ("第一幕｜便签来自以后", "第二幕｜答案改变过去", "第三幕｜钟摆停下")),
        )),
        direct_positive=(
            "{actor}用多出来的一分钟替{target}补上了刚才错过的解释。",
            "{actor}和{target}把{prop}压在时间锚点下，终于没有再被倒回原处。",
            "{actor}记住了{target}上一次没说出口的选择，这次没有走错。",
        ),
        direct_negative=(
            "{actor}试图重来，却把{target}带回了更早的误会里。",
            "{actor}改写便签时擦掉了关键一句，{target}只看见空白。",
            "{actor}抓住{prop}的瞬间，时钟又把它送回了没被发现的时候。",
        ),
        response=(
            "{actor}在重复的这一分钟里试着回头，却无法同时接住每个等候的人。",
            "{actor}把同一句回应留给不同的过去，有人听见，有人只听到回声。",
            "{actor}让时钟慢下来，却只能先替一段关系保留答案。",
        ),
        assist_positive=(
            "{actor}替{left}和{right}固定了时间锚点，让他们终于说完那句话。",
            "{actor}把错位的{prop}送回正确的一分钟，{left}和{right}因此没有走散。",
        ),
        assist_negative=(
            "{actor}替{left}回溯时间时，意外让{right}忘记了刚刚的约定。",
            "{actor}改写了错误的便签，{left}和{right}反而在两个时间点错开。",
        ),
        chain_resolution=(
            "这一次时钟没有倒回去。{actor}和{target}终于把旧误会留在上一分钟。",
            "{actor}把未来的便签还给{target}，上次没接住的回应终于抵达。",
        ),
        conclusions=(
            "时钟重新向前，留下的人都带着比刚才更完整的答案。",
            "最后一分钟结束时，有些遗憾被重来，有些选择被好好保留。",
            "站台恢复正常，而几段故事已经不再是原来的样子。",
        ),
    ),
    ThemePack(
        id="talking_vending_machine",
        title="会说话的自动贩卖机",
        style="轻奇幻",
        mechanisms=("愿望硬币", "错位饮料", "秘密问答", "找零预言"),
        scripts=_scripts("talking_vending_machine", (
            ("投不进去的硬币", "一枚刻着名字的硬币", "自动贩卖机只肯收下一枚带名字的硬币。", ("第一幕｜机器开口", "第二幕｜问题被问出", "第三幕｜找零落下")),
            ("不会下落的汽水", "悬在出货口的汽水", "一瓶汽水停在出货口上方，迟迟不肯落下。", ("第一幕｜按钮亮起", "第二幕｜愿望被听见", "第三幕｜出货口打开")),
            ("写着答案的找零", "几枚带字的硬币", "找零槽里滚出几枚写着答案的硬币。", ("第一幕｜硬币说话", "第二幕｜答案被交换", "第三幕｜最后一次找零")),
            ("午夜限定口味", "没有标签的饮料", "菜单上多出一种只在午夜出现的口味。", ("第一幕｜口味出现", "第二幕｜有人先尝", "第三幕｜按钮熄灭")),
            ("不肯关机的机器", "显示明天日期的屏幕", "关灯后，自动贩卖机仍亮着明天的日期。", ("第一幕｜屏幕没有暗", "第二幕｜预言被读出", "第三幕｜机器终于沉默")),
        )),
        direct_positive=(
            "{actor}把愿望硬币递给{target}，机器为你们吐出了刚好的答案。",
            "{actor}和{target}同时按下按钮，{prop}终于落进出货口。",
            "{actor}没有读完找零上的预言，却把最重要的一句留给了{target}。",
        ),
        direct_negative=(
            "{actor}投错了硬币，机器把{prop}推给了不该出现的人。",
            "{actor}替{target}按下按钮，贩卖机却问了一个谁都答不上来的问题。",
            "{actor}把找零的答案念反，{target}只好带着误会离开。",
        ),
        response=(
            "{actor}让机器逐一念出等候者的名字，但预言并不总是温柔。",
            "{actor}把不同口味的回应递出去，有人拿到想要的，有人只拿到谜语。",
            "{actor}替大家按下确认键，却只能先让一个答案真正落地。",
        ),
        assist_positive=(
            "{actor}替{left}和{right}凑齐愿望硬币，机器终于肯交出{prop}。",
            "{actor}解开了机器的秘密问答，让{left}和{right}听见彼此的答案。",
        ),
        assist_negative=(
            "{actor}替{left}猜错了问题，机器把{right}的答案锁进了找零槽。",
            "{actor}按下错误按钮，{left}和{right}拿到彼此不该看的预言。",
        ),
        chain_resolution=(
            "机器又问了一次同样的问题，{actor}这回没有替{target}抢答。",
            "{actor}找回那枚硬币时，{target}已经把原来的答案留在了屏幕上。",
        ),
        conclusions=(
            "机器终于沉默，但今晚听见过的愿望还在发亮。",
            "最后一枚硬币滚进找零槽时，有人拿到了答案，也有人带走了更好的问题。",
            "屏幕暗下去前，贩卖机把所有没说完的话换成了明天的可能。",
        ),
    ),
    ThemePack(
        id="drift_mailbox",
        title="寄不出的漂流信箱",
        style="轻奇幻",
        mechanisms=("匿名回信", "漂流地址", "误投信封", "潮汐递送"),
        scripts=_scripts("drift_mailbox", (
            ("没有收件人的信", "写着空白地址的信封", "海边的信箱吐出一封没有收件人的信。", ("第一幕｜潮水退下", "第二幕｜地址开始出现", "第三幕｜最后一班邮船")),
            ("反复寄回的明信片", "盖满同一枚邮戳的明信片", "一张明信片第三次回到同一个投递口。", ("第一幕｜邮戳又亮起", "第二幕｜背面多了字", "第三幕｜船灯远去")),
            ("写到一半的海图", "被盐粒压住的海图", "信箱里有一张只画了一半路线的海图。", ("第一幕｜海图展开", "第二幕｜路线被补全", "第三幕｜潮水没过脚印")),
            ("夜潮带来的瓶子", "装着纸条的玻璃瓶", "夜潮把一个装着纸条的瓶子推到信箱旁。", ("第一幕｜瓶子抵岸", "第二幕｜纸条被读出", "第三幕｜潮水准备带走它")),
            ("寄往明天的邮票", "一枚日期未到的邮票", "有人在信箱上贴了一枚还没到日期的邮票。", ("第一幕｜邮票来自明天", "第二幕｜回信提前抵达", "第三幕｜邮船鸣笛")),
        )),
        direct_positive=(
            "{actor}替{target}在信封上补全地址，漂流信终于知道该往哪里去。",
            "{actor}和{target}在同一张海图上画出了会合的路线。",
            "{actor}没有拆开{prop}，而是把选择留给了{target}。",
        ),
        direct_negative=(
            "{actor}把地址写错一笔，{prop}又被潮水送回了原点。",
            "{actor}替{target}读了不该读的回信，海风把沉默吹得很长。",
            "{actor}把海图折错方向，{target}只能看见一条断开的路。",
        ),
        response=(
            "{actor}把回信投入信箱，潮水替他把不同的答案带向不同的人。",
            "{actor}在信封背面留下回应，有人收到完整一句，有人只收到半行。",
            "{actor}把漂流瓶逐一推回海里，却无法决定谁先靠岸。",
        ),
        assist_positive=(
            "{actor}替{left}和{right}找到误投的信封，让回信终于没有再漂走。",
            "{actor}把{prop}交给潮水，正好送到{left}和{right}同一段海岸。",
        ),
        assist_negative=(
            "{actor}替{left}投错信箱，让{right}收到了一封不属于自己的信。",
            "{actor}把{prop}交给涨潮，{left}和{right}的地址再次被抹掉。",
        ),
        chain_resolution=(
            "{actor}在退潮后的沙地上找回旧信，{target}终于看见那句没写完的话。",
            "漂流瓶第二次靠岸时，{actor}没有再错过{target}留下的地址。",
        ),
        conclusions=(
            "邮船离岸后，有些信没有寄出，却已经抵达了该去的人手里。",
            "潮水抹平脚印前，信箱替今晚保管了所有还不能说出口的答案。",
            "最后一枚邮票贴好时，明天已经收到了一点今天的心意。",
        ),
    ),
    ThemePack(
        id="memory_pawnshop",
        title="记忆典当铺",
        style="强幻想",
        mechanisms=("记忆典当", "记忆转交", "账本代价", "失主认领", "赎回选择"),
        scripts=_scripts("memory_pawnshop", (
            ("忘不掉的雨声", "装着雨声的记忆瓶", "典当铺的柜台上摆着一瓶谁也忘不掉的雨声。", ("第一幕｜账本翻开", "第二幕｜记忆换了主人", "第三幕｜赎回钟响")),
            ("没有名字的旧照片", "背面写着日期的照片", "一张照片被压在账本最深的一页。", ("第一幕｜照片被认领", "第二幕｜日期开始褪色", "第三幕｜快门再一次响起")),
            ("只剩一半的旋律", "会发光的旧唱片", "典当铺收下一段只剩一半的旋律。", ("第一幕｜唱针落下", "第二幕｜另一半被找到", "第三幕｜旋律归还")),
            ("被抵押的道歉", "封蜡未开的信", "柜台里锁着一句还没来得及说出的道歉。", ("第一幕｜封蜡松动", "第二幕｜账本记下一笔", "第三幕｜信被交还")),
            ("梦里见过的钥匙", "刻着月纹的钥匙", "一把钥匙被典当给了从没见过它的人。", ("第一幕｜钥匙认主", "第二幕｜门后传来声音", "第三幕｜钥匙回到原处")),
        )),
        direct_positive=(
            "{actor}没有典当{target}留下的{prop}，而是陪{target}把它记了下来。",
            "{actor}和{target}在账本上签下同一段回忆，谁也没有替谁遗忘。",
            "{actor}替{target}赎回了那句没说完的话，柜台的灯因此亮了一格。",
        ),
        direct_negative=(
            "{actor}误把{target}的记忆当成自己的典当，账本留下了一笔代价。",
            "{actor}替{target}打开{prop}，却让最重要的细节从指缝里消失。",
            "{actor}想替{target}赎回过去，却换走了现在的一句回应。",
        ),
        response=(
            "{actor}翻开账本，试图把回应留给每个写过自己名字的人。",
            "{actor}归还几段记忆时，有人被完整认出，有人只得到一阵熟悉的风。",
            "{actor}把记忆瓶排在柜台上，却只能先打开其中一瓶。",
        ),
        assist_positive=(
            "{actor}替{left}和{right}保管了{prop}，让他们没有在账本里失去彼此。",
            "{actor}找到账本漏记的一页，{left}和{right}终于赎回同一段记忆。",
        ),
        assist_negative=(
            "{actor}把{left}的记忆瓶交给{right}，账本记下了一笔错位的代价。",
            "{actor}替{left}翻账本时撕开了旧页，{right}只剩一段不完整的回声。",
        ),
        chain_resolution=(
            "{actor}把上次典当的记忆赎回，{target}终于认出那句解释原本属于谁。",
            "账本没有删掉过去。{actor}和{target}只是决定把它放回该在的位置。",
        ),
        conclusions=(
            "典当铺打烊时，账本留下了代价，也替一些人归还了自己。",
            "最后一盏柜台灯熄灭前，几段记忆没有被卖掉，而是被好好记住。",
            "有人带走了过去，有人赎回了明天，账本因此多了一页温柔的空白。",
        ),
    ),
    ThemePack(
        id="moonlight_bazaar",
        title="月光集市",
        style="强幻想",
        mechanisms=("月光货币", "愿望交换", "影子摊位", "星屑契约", "午夜竞价"),
        scripts=_scripts("moonlight_bazaar", (
            ("会发光的找零", "一枚月光找零", "月光集市找回的零钱比月亮还亮。", ("第一幕｜摊位开张", "第二幕｜影子开始交易", "第三幕｜月亮下沉")),
            ("只卖一次的愿望", "写着愿望的价签", "有个摊位声称今晚只卖出一个愿望。", ("第一幕｜价签翻面", "第二幕｜愿望被竞价", "第三幕｜铃铛响起")),
            ("逃跑的影子", "一截不听话的影子", "一段影子从摊位底下逃出来，找不到主人。", ("第一幕｜影子离开脚边", "第二幕｜影子认出名字", "第三幕｜影子回到月下")),
            ("星屑做的项链", "一串会消失的星屑", "摊主说项链只能在被真心看见时存在。", ("第一幕｜星屑落下", "第二幕｜契约被写下", "第三幕｜项链化成光")),
            ("没有价格的面具", "一张映出别人表情的面具", "集市最里面摆着一张没有价格的面具。", ("第一幕｜面具开口", "第二幕｜面孔被交换", "第三幕｜摊位收进月影")),
        )),
        direct_positive=(
            "{actor}用一枚月光找零换回了{target}想要的答案。",
            "{actor}和{target}一起按住逃跑的{prop}，影子终于承认了主人。",
            "{actor}没有替{target}出价，只把愿望选择留在{target}手里。",
        ),
        direct_negative=(
            "{actor}在竞价时喊错名字，{target}的愿望被月光收走。",
            "{actor}抓住{prop}却让自己的影子先逃开，{target}只能站在原地。",
            "{actor}替{target}签下契约，星屑却在最关键的一刻熄灭。",
        ),
        response=(
            "{actor}在月光下分发回应，却发现每个人的影子都在等不同的答案。",
            "{actor}把星屑递给等候的人，有人得到了光，有人只握住一点余温。",
            "{actor}让面具逐一映出心意，但月光不会替谁说完整。",
        ),
        assist_positive=(
            "{actor}替{left}和{right}补足月光货币，让他们的愿望没有被拍走。",
            "{actor}把{prop}送回影子摊位，{left}和{right}因此认出彼此的方向。",
        ),
        assist_negative=(
            "{actor}替{left}竞价时出错，{right}的愿望被另一道月光带走。",
            "{actor}把{prop}交错主人，{left}和{right}的影子在摊位前分开。",
        ),
        chain_resolution=(
            "月亮再次照到摊位，{actor}把错付的愿望交还给{target}。",
            "{actor}找到逃跑的影子时，{target}已经替它留了一枚月光找零。",
        ),
        conclusions=(
            "月光集市散去时，愿望没有全被买走，却都被认真听见。",
            "最后一颗星屑落下前，有人用影子换来了勇气。",
            "摊位收进月影，今晚成交的并不只是一些愿望。",
        ),
    ),
    ThemePack(
        id="sky_rail",
        title="浮空列车",
        style="强幻想",
        mechanisms=("云层换乘", "风向车票", "失重座位", "星图广播", "坠落行李"),
        scripts=_scripts("sky_rail", (
            ("穿过云海的末班车", "印着风向的车票", "浮空列车的最后一班正穿过云海。", ("第一幕｜列车离开云站", "第二幕｜风向开始改变", "第三幕｜星港靠近")),
            ("失重车厢的座位", "漂在半空的座位牌", "一节车厢失去了重力，座位牌在半空缓慢旋转。", ("第一幕｜重力松开", "第二幕｜座位重新排列", "第三幕｜列车穿过气流")),
            ("掉进云里的行李", "系着红绳的行李牌", "一只行李箱从舱门边滑进了云层。", ("第一幕｜行李坠落", "第二幕｜云层传回回音", "第三幕｜索道放下")),
            ("会改路线的星图", "不断重绘的星图", "车厢星图每隔一分钟就换一条路线。", ("第一幕｜星图偏航", "第二幕｜广播请求选择", "第三幕｜星港亮起")),
            ("风向相反的站台", "两张相反方向的登车牌", "两座浮空站台正沿着相反风向靠近。", ("第一幕｜站台相望", "第二幕｜风向交错", "第三幕｜跳板展开")),
        )),
        direct_positive=(
            "{actor}替{target}抓住了差点飞进云里的{prop}，两个人都没再松手。",
            "{actor}和{target}在星图上选了同一条路线，广播因此安静下来。",
            "{actor}替{target}把失重座位推回身边，云层刚好让出一条路。",
        ),
        direct_negative=(
            "{actor}误读了风向车票，{target}被带往另一节车厢。",
            "{actor}想拉住{prop}却踩空一步，云层把答案遮得更远。",
            "{actor}替{target}选了路线，星图立刻改写成相反的方向。",
        ),
        response=(
            "{actor}在失重车厢里回头，想把回应抛向每个朝自己飘来的人。",
            "{actor}把风向车票递出去，有人因此靠近星港，有人仍随云层漂流。",
            "{actor}让广播念出名字，却只能先为一段关系点亮路线。",
        ),
        assist_positive=(
            "{actor}替{left}和{right}固定了失重座位，让他们没有飘向不同的云层。",
            "{actor}读懂星图的下一次改写，替{left}和{right}留住同一条路线。",
        ),
        assist_negative=(
            "{actor}把{left}的风向车票递反，{right}只能看着跳板收起。",
            "{actor}试图打捞{prop}，却让{left}和{right}的车厢被气流隔开。",
        ),
        chain_resolution=(
            "云层散开一次，{actor}和{target}终于在同一张星图上看见对方。",
            "{actor}找回坠落的行李牌，{target}把没说完的话系回红绳上。",
        ),
        conclusions=(
            "浮空列车驶入星港时，云海替几段关系让出了位置。",
            "广播停止报站，仍有人把风向车票留给了后来的人。",
            "最后一节车厢落地前，所有飘散的故事终于有了方向。",
        ),
    ),
)


PACK_BY_ID: dict[str, ThemePack] = {pack.id: pack for pack in THEME_PACKS}
SCRIPT_BY_ID: dict[str, StoryScript] = {
    script.id: script for pack in THEME_PACKS for script in pack.scripts
}


def pack_for(theme_id: str) -> ThemePack:
    return PACK_BY_ID.get(theme_id, THEME_PACKS[0])


def script_for(script_id: str, theme_id: str) -> StoryScript:
    script = SCRIPT_BY_ID.get(script_id)
    return script if script is not None else pack_for(theme_id).scripts[0]
