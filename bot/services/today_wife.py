from __future__ import annotations

import hashlib
import math
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from bot.db import Database
from bot.services.today_wife_story import StoryDirector

@dataclass(frozen=True, slots=True)
class TodayWifeEpisode:
    key: str
    title: str
    location: str
    prop: str


# One deterministic episode is shared by every draw in a group for one day.
# Individual draws still select their own wording and relationship title.
EPISODES: tuple[TodayWifeEpisode, ...] = (
    TodayWifeEpisode(
        "rainy_platform", "雨天末班车", "站台", "一把没收起的伞",
    ),
    TodayWifeEpisode(
        "night_market", "夜市收摊后", "夜市尽头", "最后一杯柠檬水",
    ),
    TodayWifeEpisode(
        "empty_classroom", "放学后的空教室", "靠窗的教室", "一页没写完的笔记",
    ),
    TodayWifeEpisode(
        "late_convenience", "深夜便利店", "便利店门口", "最后一杯热可可",
    ),
    TodayWifeEpisode(
        "closing_mall", "商场关门前十分钟", "商场中庭", "一张差点飞走的优惠券",
    ),
    TodayWifeEpisode(
        "library_window", "图书馆靠窗位", "图书馆窗边", "夹在书里的电影票根",
    ),
    TodayWifeEpisode(
        "shared_table", "临时拼桌", "食堂靠里一桌", "一瓶没开封的汽水",
    ),
    TodayWifeEpisode(
        "last_subway", "末班地铁", "换乘通道", "一枚掉在地上的游戏币",
    ),
    TodayWifeEpisode(
        "sunset_crossing", "晚霞路口", "十字路口", "一张被风吹跑的收据",
    ),
    TodayWifeEpisode(
        "track_lights", "操场亮灯时", "操场跑道", "一只忘在看台上的耳机盒",
    ),
    TodayWifeEpisode(
        "record_store", "唱片店下午", "旧唱片店", "一张封面褪色的专辑",
    ),
)

# These are deliberately broad pools rather than per-template scripts: no
# player-facing line depends on a tiny collection of fixed sentences.
RELATIONSHIP_TITLES = (
    "便利店夜班搭子", "雨天共伞同路人", "食堂拼桌搭子", "晚自习前后桌",
    "奶茶第二杯共享人", "末班车邻座", "路口等灯的人", "夜市收摊同行者",
    "图书馆靠窗邻座", "操场亮灯时的同伴", "快递站偶遇的人", "唱片店同好",
    "小卖部排队搭子", "临时充电救场人", "电影散场同行者", "公交后排邻座",
    "下雨天借伞的人", "早餐店豆浆同好", "游戏加载界面队友", "街角花店顺路人",
    "便利店热可可共享人", "自动售货机偶遇者", "书店高处书架搭子", "晚风散步同伴",
    "展览最后一位观众", "球场边的看台邻座", "午后咖啡拼桌人", "商场关门前同行者",
    "耳机另一边的听众", "夜跑最后一圈同伴", "同站下车的人", "临时避雨搭子",
)
STORY_OPENINGS = (
    "{location}的灯刚亮起来，{prop}还留在最显眼的地方。",
    "今天的故事从{location}开始，{prop}像是专门等着被人发现。",
    "人群经过{location}时，{prop}被风推到了你脚边。",
    "{location}比平时安静一些，只有{prop}提醒着这里刚发生过什么。",
    "你在{location}停下脚步时，正好看见了{prop}。",
    "{location}的空气里有一点晚风，{prop}却让人很难装作没看见。",
    "原本只是路过{location}，{prop}却把今天的偶遇留了下来。",
    "{location}刚好空出一点位置，{prop}安静地躺在中间。",
    "今天的篇章落在{location}，{prop}成了最先出现的线索。",
    "{location}的人来人往没有停下，{prop}却让你慢了半拍。",
    "你以为{location}只是今天普通的一站，直到看见{prop}。",
    "{location}的背景声很轻，{prop}却把这一刻显得格外清楚。",
)
STORY_MIDDLES = (
    "{target_name}正好站在另一边，也像是在等同一个答案。",
    "你回头时，{target_name}已经替你留住了那一点恰好的位置。",
    "{target_name}先看见了你，随后又看见了你手里的线索。",
    "你们几乎同时伸手，又都在最后一秒停了下来。",
    "{target_name}没有急着开口，只把目光落在和你相同的方向。",
    "你们都没有刻意靠近，但脚步刚好停在了同一处。",
    "{target_name}顺手做的一件小事，让这一幕忽然有了后续。",
    "你原本想绕开人群，却和{target_name}走进了同一条空隙。",
    "{target_name}抬头的瞬间，刚好和你撞上了视线。",
    "你们像是同时想起了什么，又谁都没有先解释。",
    "{target_name}把选择留在你面前，像是给今天多开了一扇门。",
    "这一点微小的巧合，被{target_name}看见以后就不再普通。",
)
STORY_CLOSINGS = (
    "于是今天的好运，刚好有了可以分享的人。",
    "故事还没走远，但这一页已经值得留下。",
    "原来随机相遇，也会挑一个刚刚好的时刻。",
    "你们没有约好，却像是都没有走错方向。",
    "这一段路没有变短，只是忽然不再像一个人走。",
    "今天的关系图，从这一点小事开始有了第一根线。",
    "没有人提前知道答案，巧合却先替你们写了开头。",
    "这一刻没有大事发生，但已经足够成为今天的故事。",
    "你把这次相遇记下时，夜色正好往前走了一步。",
    "今天的篇章因此有了一个不太舍得跳过的镜头。",
    "有些默契不需要说明，刚好同时发生就已经足够。",
    "接下来的路怎么走还不知道，至少相遇已经是真的。",
)
RESULT_INTROS = (
    "你今日的老婆是{target_name}。", "今天的缘分签，把{target_name}写在了你的名字旁边。",
    "随机转了一圈，停在了{target_name}这里。", "今天和你结缘的人，是{target_name}。",
    "关系图的第一根线，从你指向了{target_name}。", "你今天抽中的老婆是{target_name}。",
    "命运把{target_name}推到了你的今日故事里。", "今天这段随机相遇，另一端是{target_name}。",
    "你的缘分卡翻开后，出现的是{target_name}。", "今天有人被你抽进了故事里：{target_name}。",
    "这一天的巧合，刚好落在{target_name}身上。", "你和{target_name}，被今天的随机写进了同一页。",
)
GROUP_SPOTLIGHT_LINES = (
    "{headline}：{detail}", "今日关系现场｜{headline}，{detail}", "关系图高光是{headline}，{detail}",
    "今天最热闹的一笔来自{headline}，{detail}", "这张图暂时把镜头留给{headline}，{detail}",
    "缘分线在{headline}这里打了个结，{detail}", "如果今天有主线，大概就是{headline}，{detail}",
    "群像故事正经过{headline}，{detail}", "今天的箭头绕到{headline}时，{detail}",
    "关系网最不肯安静的地方是{headline}，{detail}", "这一页最值得回看的，是{headline}，{detail}",
    "故事没有旁白预告，但{headline}已经说明了很多，{detail}",
)
EMPTY_SPOTLIGHT_LINES = (
    "第一根缘分线还没有出现。", "今天的篇章还在等第一位主角。", "关系图暂时留着一整片空白。",
    "故事已经选好了场景，只差有人推门进来。", "今天的道具已经放好，缘分线还没落笔。",
    "群像剧尚未开场，第一张缘分签仍在等待。", "所有位置都还空着，今天可以从任何方向开始。",
    "关系网尚未展开，下一次抽取会写下第一笔。", "今天还没有箭头，但篇章已经悄悄开始。",
    "第一场偶遇尚未发生，故事仍保留全部可能。", "今天的群像图还是空白页。", "舞台已经亮灯，主角还没有入场。",
)


def _combine_lines(openers: tuple[str, ...], closers: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((opener, closer) for opener in openers for closer in closers)


def _combine_messages(openers: tuple[str, ...], closers: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(opener + closer for opener in openers for closer in closers)


RICH_MUTUAL_CONTEXTS = _combine_lines(
    (
        "对方今天抽中的人，刚好也是你。", "你回头才发现，对方今天也把缘分抽到了你这里。",
        "原来对方的今日答案，和你的名字写在同一页。", "这条线绕了一圈，另一端还是落回了你手上。",
        "对方那边的抽取结果，也悄悄指向了你。", "你们各自按下指令，却抽到了彼此。",
        "今天的两张缘分签，意外地写成了同一个方向。", "对方的故事刚好从另一边接住了你。",
        "不是你一个人在等这条线，对方也正好走来。", "这份巧合没有绕远路，直接把你们放在了彼此面前。",
    ),
    ("这不是擦肩，是从两边同时抵达的双向奔赴。", "今天这段关系图，有了两根朝彼此靠近的箭头，算是双向奔赴。", "原来所谓双向奔赴，就是刚好都没有选错方向。"),
)
RICH_CONTESTED_CONTEXTS = _combine_lines(
    (
        "不过，{context_name}已经先一步抽中了对方。", "不过，对方今天已经和{context_name}结缘。",
        "不过，{context_name}的缘分签已经落在对方身上。", "不过，这位群友刚刚被{context_name}抽走了。",
        "不过，对方的第一条缘分线，来自{context_name}。", "不过，{context_name}已经在今天的关系图里牵住了对方。",
        "不过，你到站时，{context_name}已经先占了同一班车。", "不过，对方今天的故事里，已经出现了{context_name}。",
        "不过，{context_name}比你早一点按下了抽取。", "不过，这条缘分的前排座位已经坐着{context_name}。",
    ),
    ("但缘分不按进群顺序排队。", "后来的人，也可以走进故事正中。", "今天这张关系图，仍然保留着你画出的箭头。"),
)
RICH_POPULAR_CONTEXTS = _combine_lines(
    (
        "不过，对方今天已经收到不止一条缘分。", "不过，对方的缘分信箱已经热闹起来了。",
        "不过，今天指向对方的线已经有好几根。", "不过，这位群友显然是今天的热门目的地。",
        "不过，对方刚刚又收到了一张新的缘分签。", "不过，关系图上对方周围已经围起了一小圈箭头。",
        "不过，今天想和对方结缘的人不止你一个。", "不过，对方的名字今天被抽到了好几次。",
        "不过，这位今日老婆的门口已经有些热闹。", "不过，对方今天的人气，看起来比天气还要高一点。",
    ),
    ("热闹一点，故事才更有后续。", "不必急着退场，今天的缘分从不只看先后。", "你的这条线也有自己的来处。"),
)
RICH_TAKEN_CONTEXTS = _combine_lines(
    (
        "你当前已经是{taken_by_name}的老婆，但你有自己的想法。", "{taken_by_name}刚刚把你写进今天的缘分里，而你翻到了新的名字。",
        "有人正在为抽到你高兴，你却在另一张缘分卡前停下。", "关系线落在你身上，但它没有规定你的方向。",
        "{taken_by_name}那边的故事还没有结尾，你的故事却已经继续往前走。", "你不是谁的奖品，轮到你抽取时当然可以选择新的方向。",
        "今天已经有人把你牵进故事里，而你也正好遇见了另一个人。", "{taken_by_name}还在看着那条指向你的线，你却已经走到新的路口。",
    ),
    ("这不是离开谁，只是今天的关系图本来就有很多方向。", "缘分不是围墙，今天仍然可以有新的下一页。", "故事没有要求你停在原地。"),
)
RICH_CYCLE_CONTEXTS = _combine_lines(
    (
        "你这条线接上去以后，三个人的关系图忽然绕成了一个圈。", "原来有人从另一端走来，把今天的关系线刚好闭合。",
        "你们并不知道彼此的抽取结果，箭头却已经在群里围成了一圈。", "这不是普通的偶遇，关系图悄悄完成了一次回环。",
        "故事绕了几个人，最后又回到了起点附近。", "有人把线递给你时，另一端早已在等待这一笔。",
        "今天的关系图多了一条最后的边，刚好成了闭环。", "所有人各自按下指令，最后却写出了同一个圆。",
    ),
    ("这一段关系，今晚大概会成为群缘分图的高光。", "巧合绕得很远，落点却刚好完整。", "看来今天的故事不打算只写直线。"),
)
RICH_ECHO_CONTEXTS = _combine_lines(
    (
        "你以前也曾抽到过对方，今天的名字又回到了这里。", "这不是第一次在缘分记录里看见对方。",
        "旧故事没有被重复，只是在今天换了一个开场。", "你翻过以前的缘分页，发现对方的名字还留在那里。",
        "有些人不是只出现一次，而是在随机里留下回声。", "今天的抽取结果像是把一张旧票根又递回到你手里。",
        "原来前一段巧合没有走远，只是等着换个时间再出现。", "你们以前的那根线断过又松过，今天却又被轻轻碰到。",
    ),
    ("这一次不必照着从前走，新的篇章已经开始。", "熟悉不是答案，只是今天故事的一部分。", "前缘回声响了一下，接下来仍然是新的随机。"),
)
RICH_REUNION_CONTEXTS = _combine_lines(
    (
        "你们曾经在缘分记录里告别，今天却又在新的抽取里相遇。", "上一页已经写过再见，今天却把对方重新放到了你面前。",
        "你以为那段关系停在了离婚卡里，名字却又出现在今天。", "有些巧合会先走散，再在完全随机的时候折回来。",
        "旧故事曾被你合上，今天却意外从另一页打开。", "你们以前放开过那根线，今天它又被风吹到了手边。",
        "过去的告别还在记录里，今天的相遇也是真的。", "没有人安排复合，只是随机又把你们带到了同一个位置。",
    ),
    ("这次怎么写，不必和上次一样。", "故事是否继续，先交给今天这一页。", "重逢只是一个新的开头，不是旧结局的重播。"),
)
RICH_REDRAW_CONTEXTS = _combine_lines(
    (
        "你把上一段缘分留在门口，转身遇见了新的名字。", "告别之后，今天的关系图为你空出了一条新线。",
        "上一页已经合上，这一页从新的巧合开始。", "你没有回头，新的随机相遇先一步走来了。",
        "重抽不是重来，是今天故事换了一个镜头。", "你把旧票根收好，手里却多了一张新的缘分签。",
        "这次的名字和刚才不同，故事自然也该换个方向。", "解缘之后的空白没有停太久，新的箭头已经落下。",
    ),
    ("这一次，就把它当作真正的新篇章。", "今天还没有结束，新的相遇已经开始。", "旧故事留在前面，接下来的路属于新的随机。"),
)
DIVORCE_LINES = _combine_lines(
    (
        "你和{target_name}今天的故事停在了这里。", "你把和{target_name}牵着的线轻轻放开了。",
        "和{target_name}并肩的一小段路，今天先走到这里。", "你们今天的缘分签，已经被收进了口袋。",
        "关于{target_name}的这一页，暂时合上了。", "你和{target_name}的关系图，今天少了一根箭头。",
        "那杯还没喝完的热可可，就留在今天的故事里吧。", "这段临时相遇已经到了说再见的时候。",
        "你把今天的缘分还给了风，也还给了自己。", "和{target_name}的巧合没有消失，只是停在了这里。",
    ),
    ("明天醒来，新的随机故事还会继续。", "今天就到这里，下一次相遇留给明天。", "不用遗憾，故事合上以后也还是今天的一部分。"),
)
NO_DRAW_MESSAGES = _combine_messages(
    (
        "你今天还没有抽到过缘分。", "今天的缘分签还没有写到你这里。", "你还没开始今天的缘分故事。",
        "这一天的抽取记录里暂时没有你的名字。", "今天的关系图上，还没画出属于你的箭头。", "你的今日缘分还停在空白页。",
        "今天还没有人被你抽进故事里。", "你的抽取次数还安静地留在今天。", "今天这份缘分还没从你这里开始。", "你还没有领取今天的随机相遇。",
    ),
    ("先发一次 #今日老婆 再来解缘吧。", "等抽到一位群友后，再决定要不要告别。", "今天的故事还没开场，自然也还不用落幕。"),
)
NO_CANDIDATE_MESSAGES = _combine_messages(
    (
        "现在群里还没有可以抽取的其他群友。", "今天的缘分名单暂时只有你自己。", "这会儿还凑不出一场两个人的随机相遇。",
        "群里的可选缘分席位暂时是空的。", "今天还没有另一位群友可以写进故事。", "目前的名单里没有可抽取的对象。",
        "缘分转盘转了一圈，暂时没找到另一端。", "今天的群缘分还缺一位可以相遇的人。", "这张关系图还没有第二个节点。", "现在还不能从群友里抽出另一位主角。",
    ),
    ("等群里有人出现后再试一次。", "晚点热闹起来，再回来抽签吧。", "先把这张空白缘分签留给下一位群友。"),
)
ALREADY_DIVORCED_MESSAGES = _combine_messages(
    (
        "你今天的缘分已经解开了。", "今天那条关系线已经被你收好了。", "你和今天的随机相遇已经正式告别。",
        "这一天的缘分故事已经在上一页结束。", "今天的解缘记录已经留好了。", "那根箭头已经从今天的关系图里拿走了。",
        "你今天已经为这段随机故事按下了结束。", "这份缘分已经回到明天再见的状态。", "今天的故事已经合上，不需要再解一次。", "你今天已经和那段巧合好好道别过了。",
    ),
    ("明天再来抽新的缘分吧。", "今天不能重新抽取，但新的故事会在明天出现。", "把这份空位留给明天的随机相遇。"),
)
MEMBER_LIST_UNAVAILABLE_MESSAGES = (
    "今天的缘分名单暂时没有取到，过一会儿再来翻开这一页。",
    "群友名单刚好没有回应，这次抽取先留到稍后。",
    "缘分转盘还没拿到完整名单，晚一点再让它转起来。",
    "这次没能读到群里的候选人，等名单恢复后再试一次。",
    "今天的关系图还在加载群友名单，稍后再来画第一根线。",
    "候选名单临时走丢了一会儿，这张缘分签先不落笔。",
    "群成员信息暂时不可用，今天的抽取机会没有被消耗。",
    "这一次没有拿到可用名单，缘分故事还没有正式开始。",
    "名单接口暂时没有回应，等它恢复后再抽也不迟。",
    "今天的候选席位还没加载完成，稍后重新发起抽取即可。",
    "群友名单暂时空白，系统没有替你写下错误的缘分。",
    "这次抽取停在名单读取阶段，稍后再试时仍算第一次。",
)
EMPTY_HISTORY_MESSAGES = (
    "这里还没有留下过缘分故事，第一条记录会从下一次抽取开始。",
    "你的缘分档案目前还是空白页，尚未写下结缘或解缘。",
    "暂时没有可以回看的关系记录，新的随机相遇还在前面。",
    "历史列表里还没有名字，第一次缘分会成为这里的开场。",
    "你的关系时间线尚未开始，当前没有结缘与告别记录。",
    "这本缘分档案还没有第一页，下一次抽取会替它落笔。",
    "目前没有保存过的缘分章节，所有位置都还留给未来。",
    "这里暂时看不到旧故事，因为你的第一次相遇还没发生。",
    "缘分记录仍是一张空白卡，等待下一次随机写下名字。",
    "你的历史关系图还没有箭头，第一段故事尚未出现。",
    "暂时没有结缘或离婚记录，这里仍保留着完整的空白。",
    "过去的页面还没有内容，下一位被抽中的人会出现在这里。",
)
LOCKED_MESSAGES = (
    "今晚的篇章已经在 23:50 封场，先用 #我的缘分 或 #群缘分 回看今天的结局吧。",
    "今日故事正在放映终章，新的抽取和互动要留到零点以后。",
    "关系线已经暂时定格；现在可以翻看个人档案和群像结局。",
    "今晚的缘分签已经收好，等明天的篇章翻开后再继续行动。",
    "终章时间里不再改写关系，#我的缘分 和 #群缘分 仍可查看。",
    "今天的故事已经停笔，新的抽卡、离婚与互动会在明天恢复。",
    "封场后只保留回看入口，让这一天先好好收尾。",
    "此刻的关系图正在结算，等零点钟声以后再开启下一段故事。",
    "今天已经进入片尾，先看看大家把故事写成了什么样子吧。",
    "故事暂时不能再被改写，但所有今天的印记都还在档案里。",
)
CLEAR_FORBIDDEN_MESSAGES = (
    "这个清理操作只向超级管理员开放。",
    "只有超级管理员可以清空本群的今日老婆关系记录。",
    "当前账号没有清缘维护权限，关系数据保持不变。",
    "清空全群缘分历史需要超级管理员权限。",
    "这项维护指令不能由普通成员执行。",
    "本次清缘请求没有管理员授权，因此不会修改数据。",
    "全群关系记录属于维护范围，仅超级管理员可以清理。",
    "权限校验没有通过，本群今日老婆数据未发生变化。",
    "要清空整个群的缘分历史，需要由超级管理员发起。",
    "当前请求无权执行清缘，所有记录仍然保留。",
)
CLEAR_WARNING_MESSAGES = (
    "即将永久清空本群全部今日老婆关系记录，包括结缘、解缘与重抽历史。近三天活跃计数和小游戏数据不会受影响。60 秒内发送 #确认清缘 执行，发送 #取消清缘 放弃。",
    "本次操作会不可恢复地删除本群所有缘分与离婚记录，但保留活跃统计和小游戏数据。请在 60 秒内发送 #确认清缘，或发送 #取消清缘。",
    "清缘将把本群今日老婆历史恢复为空白状态，结缘、解缘和二抽都会删除；活跃计数与小游戏不变。60 秒内用 #确认清缘 或 #取消清缘 作出选择。",
    "确认后，本群的全部今日老婆关系历史会永久消失，其他小游戏和近三天活跃统计保持原样。请在 60 秒内发送 #确认清缘，取消则发送 #取消清缘。",
    "这会清空本群所有今日老婆故事记录，且无法从机器人内恢复；活跃权重来源和小游戏数据不会清除。60 秒内发送 #确认清缘 执行，#取消清缘 放弃。",
    "本群缘分档案即将被重置，所有结缘、离婚和重抽章节都会删除，活跃统计与小游戏不受影响。请在 60 秒内发送 #确认清缘 或 #取消清缘。",
    "清理范围仅为本群今日老婆关系历史，执行后不可撤销；近三天活跃计数和全部小游戏记录会保留。60 秒内发送 #确认清缘，或用 #取消清缘 停止。",
    "准备把本群今日老婆记录恢复到从未游玩过的状态，其他功能数据不会改动。若确定，请在 60 秒内发送 #确认清缘；否则发送 #取消清缘。",
    "这次维护会永久删除本群每一条缘分、解缘与二抽记录，但不会触碰活跃计数或小游戏。60 秒内发送 #确认清缘 执行，发送 #取消清缘 放弃。",
    "本群关系图历史将被彻底清空，删除后只能依靠外部备份恢复；活跃统计和小游戏保持不变。请在 60 秒内发送 #确认清缘，取消请发送 #取消清缘。",
)
CLEAR_CANCEL_MISSING_MESSAGES = (
    "当前没有等待取消的清缘请求。", "这里没有尚未执行的清缘操作可以取消。",
    "本群目前没有挂起的缘分清理确认。", "没有找到属于你的待取消清缘请求。",
    "清缘确认窗口当前并未开启。", "现在没有需要撤回的关系数据清理操作。",
    "待确认列表里没有这次清缘请求。", "本群今日老婆数据当前没有处于待清理状态。",
    "没有进行中的清缘确认，所有记录保持原样。", "当前找不到可取消的清缘维护请求。",
)
CLEAR_CANCELLED_MESSAGES = (
    "清缘请求已取消，本群关系记录保持不变。", "已经停止这次清理，所有缘分历史仍然保留。",
    "本次清缘已撤回，没有删除任何今日老婆数据。", "取消成功，关系图和历史记录都没有改动。",
    "清理确认已经关闭，本群缘分档案保持原样。", "这次重置不再执行，现有关系故事全部保留。",
    "清缘操作已放弃，没有任何记录被移除。", "已取消删除，本群今日老婆历史仍在原处。",
    "维护请求已经撤销，活跃与关系数据都未发生变化。", "这次清空已经取消，缘分档案没有受到影响。",
)
CLEAR_CONFIRM_MISSING_MESSAGES = (
    "当前没有可确认的清缘请求，请先发送 #清缘。", "没有找到待执行的清缘操作，先用 #清缘 发起确认。",
    "清缘确认窗口不存在或已经过期，请重新发送 #清缘。", "这次确认没有对应的清理请求，需要先发送 #清缘。",
    "当前不能直接执行 #确认清缘，请先发起 #清缘。", "待确认列表为空，先发送 #清缘 才能开始清理。",
    "没有有效的清缘授权请求，请从 #清缘 重新开始。", "本群没有正在等待确认的清理操作，先发送 #清缘。",
    "清缘请求可能已经超时，请重新发送 #清缘 后再确认。", "这条确认没有匹配到清缘申请，先用 #清缘 建立请求。",
)
CLEAR_SUCCESS_MESSAGES = (
    "本群今日老婆关系历史已清空，共删除 {deleted} 条记录；近三天活跃计数与小游戏数据均未改动。",
    "清缘完成，{deleted} 条结缘、解缘或重抽记录已经删除，活跃统计和小游戏保持原样。",
    "本群缘分档案已恢复为空白状态，本次移除 {deleted} 条关系记录；其他游戏数据未受影响。",
    "关系历史清理完成，共清除 {deleted} 条今日老婆记录，近三天活跃计数与小游戏全部保留。",
    "本群今日老婆数据已重新开始，旧关系记录删除 {deleted} 条；活跃统计和小游戏没有变化。",
    "清理已执行，{deleted} 条缘分历史永久移除，活跃权重来源与小游戏记录仍然完整。",
    "本群关系图历史已经归零，共删除 {deleted} 条记录；近三天活跃与小游戏数据保持不变。",
    "缘分档案重置成功，本次清空 {deleted} 条关系记录，未触碰活跃计数和任何小游戏数据。",
    "今日老婆历史已从本群移除，共计 {deleted} 条；活跃统计、小游戏对局与战绩全部保留。",
    "清缘维护完成，删除了 {deleted} 条关系历史，本群其他统计和游戏记录没有改动。",
)
HISTORY_PAGE_INVALID_MESSAGES = (
    "缘分页码需要是正整数，例如 #我的缘分 2。",
    "想翻到过去的缘分页，请在 #我的缘分 后填写正整数页码。",
    "留档页码从 1 开始；请发送类似 #我的老婆 3 的指令。",
    "这一页的页码没法识别，请填入 1、2、3 这样的正整数。",
    "缘分留档按页翻阅，页码请从 1 开始填写。",
    "请在命令后写正整数页码，例如 #我的缘分 1。",
    "想看的旧章节需要一个页码；负数和文字不能作为页码。",
    "留档翻页只接受正整数，请换一个页码再试。",
    "页码格式不对。发送 #我的老婆 2 可以查看第二页。",
    "这一页还没有编号，请用从 1 开始的正整数指定。",
)
GROUP_HISTORY_ARGUMENT_INVALID_MESSAGES = (
    "群缘分日期请使用 YYYY-MM-DD，或发送 #群缘分 历史。",
    "要查看往日群像，请填写标准日期，例如 #群缘分 2026-08-12；历史摘要用 #群缘分 历史。",
    "日期格式没有认出来。请使用 YYYY-MM-DD，或直接发送 #群缘分 历史。",
    "这不是可识别的日期；请写成 YYYY-MM-DD，或查看 #群缘分 历史。",
    "往日群像需要完整日期，例如 #群老婆 2026-08-12。",
    "日期中请保留横线，格式为 YYYY-MM-DD。",
    "想翻阅旧篇章可发送 #群缘分 历史；指定某日请使用标准日期。",
    "群像日期没有读出来，请改用 2026-08-12 这样的格式。",
    "这个参数不是日期。可以填 YYYY-MM-DD，或者直接填“历史”。",
    "日期格式需要年、月、日，例如 #群缘分 2026-08-12。",
)
GROUP_HISTORY_MISSING_MESSAGES = (
    "这一天没有可查看的群缘分记录。",
    "那一天还没有被写进本群缘分留档。",
    "没有找到这个日期的群像摘要。",
    "这一天的群缘分篇章没有留下记录。",
    "翻到这一天时，留档里还是空白。",
    "没有找到对应日期的关系图或公开摘要。",
    "这天没有人把缘分写进群像，因此没有可展示的内容。",
    "所选日期没有完成的缘分篇章。",
    "这个日期在本群的缘分留档中不存在。",
    "没有查到那一天的结缘、互动或终章摘要。",
)

STATE_MESSAGE_POOLS: dict[str, tuple[str, ...]] = {
    "not_found": NO_DRAW_MESSAGES,
    "no_candidates": NO_CANDIDATE_MESSAGES,
    "already_divorced": ALREADY_DIVORCED_MESSAGES,
    "member_list_unavailable": MEMBER_LIST_UNAVAILABLE_MESSAGES,
    "empty_history": EMPTY_HISTORY_MESSAGES,
    "locked": LOCKED_MESSAGES,
    "clear_forbidden": CLEAR_FORBIDDEN_MESSAGES,
    "clear_warning": CLEAR_WARNING_MESSAGES,
    "clear_cancel_missing": CLEAR_CANCEL_MISSING_MESSAGES,
    "clear_cancelled": CLEAR_CANCELLED_MESSAGES,
    "clear_confirm_missing": CLEAR_CONFIRM_MISSING_MESSAGES,
    "clear_success": CLEAR_SUCCESS_MESSAGES,
}

PLAYER_MESSAGE_POOLS: dict[str, tuple[Any, ...]] = {
    "relationship_titles": RELATIONSHIP_TITLES,
    "story_openings": STORY_OPENINGS,
    "story_middles": STORY_MIDDLES,
    "story_closings": STORY_CLOSINGS,
    "result_intros": RESULT_INTROS,
    "group_spotlights": GROUP_SPOTLIGHT_LINES,
    "empty_group_spotlights": EMPTY_SPOTLIGHT_LINES,
    "mutual_contexts": RICH_MUTUAL_CONTEXTS,
    "contested_contexts": RICH_CONTESTED_CONTEXTS,
    "popular_contexts": RICH_POPULAR_CONTEXTS,
    "taken_contexts": RICH_TAKEN_CONTEXTS,
    "cycle_contexts": RICH_CYCLE_CONTEXTS,
    "echo_contexts": RICH_ECHO_CONTEXTS,
    "reunion_contexts": RICH_REUNION_CONTEXTS,
    "redraw_contexts": RICH_REDRAW_CONTEXTS,
    "divorce_lines": DIVORCE_LINES,
    **STATE_MESSAGE_POOLS,
    "history_page_invalid": HISTORY_PAGE_INVALID_MESSAGES,
    "group_history_argument_invalid": GROUP_HISTORY_ARGUMENT_INVALID_MESSAGES,
    "group_history_missing": GROUP_HISTORY_MISSING_MESSAGES,
}


@dataclass(frozen=True, slots=True)
class TodayWifeOutcome:
    kind: str
    record: dict[str, Any] | None = None


class TodayWifeService:
    """Persist one daily relationship and one post-divorce redraw per participant."""

    def __init__(self, database: Database, timezone_name: str = "Asia/Shanghai") -> None:
        self.database = database
        self.zone = ZoneInfo(timezone_name)

    def draw(
        self,
        group_id: int,
        actor_id: int,
        actor_nickname: str,
        members: Iterable[Mapping[str, Any]],
        now: datetime | None = None,
        selected_target_id: int | None = None,
        *,
        draw_source: str = "random",
    ) -> TodayWifeOutcome:
        current = self._now(now)
        day = current.date().isoformat()
        normalized_draw_source = "directed" if str(draw_source) == "directed" else "random"
        # The game day-state is the sole source of scene identity.  The legacy
        # episode fields remain for older archive rows, but new draws must not
        # invent a second world for their opening card.
        from bot.services.today_wife_game import TodayWifeGameService

        game_service = TodayWifeGameService(self.database, self.zone.key)
        day_state = game_service.day_state(int(group_id), current)
        story_context = StoryDirector.context_from_day_state(day_state)
        with self.database.connect() as connection:
            existing = self._latest_record(connection, int(group_id), day, int(actor_id))
        if existing is not None and not self._can_redraw(existing):
            return self._existing_draw_outcome(existing)

        normalized = self._members(members)
        candidates = [member for user_id, member in normalized.items() if user_id != int(actor_id)]
        if not candidates:
            return TodayWifeOutcome("no_candidates")
        timestamp = current.isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._latest_record(connection, int(group_id), day, int(actor_id))
            if existing is not None and not self._can_redraw(existing):
                return self._existing_draw_outcome(existing)
            draw_index = 1 if existing is None else int(existing["draw_index"]) + 1

            if selected_target_id is not None:
                target = normalized.get(int(selected_target_id))
                if target is None or int(selected_target_id) == int(actor_id):
                    raise ValueError("selected target must be another current group member")
            else:
                eligible = self._recently_active_candidates(connection, int(group_id), day, candidates)
                if existing is not None:
                    # A redraw starts a new main line.  The former relationship
                    # remains visible as a frozen old thread, but it cannot be
                    # selected again by the random draw.
                    former_target = int(existing["target_id"])
                    eligible = [member for member in eligible if int(member["user_id"]) != former_target]
                if not eligible:
                    return TodayWifeOutcome("no_candidates")
                weights = self._candidate_weights(connection, int(group_id), day, int(actor_id), eligible)
                target = self._weighted_candidate(eligible, weights)

            target_id = int(target["user_id"])
            target_nickname = str(target["nickname"])
            story_id = str(story_context["scene_id"])
            relationship_key = StoryDirector.relationship_label(
                story_context,
                {
                    "group_id": int(group_id),
                    "day": day,
                    "actor_id": int(actor_id),
                    "target_id": target_id,
                    "draw_index": draw_index,
                },
            )
            branch, context_nickname, story_flags, taken_by_nickname = self._story_state_for_new_draw(
                connection, int(group_id), day, int(actor_id), target_id, draw_index
            )
            connection.execute(
                """INSERT INTO today_wife_records
                   (group_id,day,actor_id,draw_index,actor_nickname,target_id,target_nickname,
                     relationship_key,story_id,branch,draw_source,context_nickname,story_flags,taken_by_nickname,status,drawn_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'active', ?)""",
                (
                    int(group_id),
                    day,
                    int(actor_id),
                    draw_index,
                    self._name(actor_nickname),
                    target_id,
                    self._name(target_nickname),
                    relationship_key,
                    story_id,
                    branch,
                    normalized_draw_source,
                    context_nickname,
                    story_flags,
                    taken_by_nickname,
                    timestamp,
                ),
            )
            record = connection.execute(
                """SELECT * FROM today_wife_records
                   WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?""",
                (int(group_id), day, int(actor_id), draw_index),
            ).fetchone()
        result = self._presentation_record(record)
        # Create the relationship arc at the same time as its initial affection.
        # Its scene and opening hook are then reused by draw, interaction, and
        # conclusion cards rather than independently randomized.
        game_service.ensure_relation_for_draw(result, current)
        return TodayWifeOutcome("drawn", result)

    def force_draw(
        self,
        group_id: int,
        actor_id: int,
        actor_nickname: str,
        members: Iterable[Mapping[str, Any]],
        now: datetime | None = None,
        target_id: int | None = None,
    ) -> TodayWifeOutcome:
        """Draw a caller-selected member without changing ordinary draw rules.

        A forced draw is still a daily draw: an active relationship blocks it,
        while the existing post-divorce second-draw rule remains available.
        Invalid targets return a non-mutating outcome so command handlers do
        not need to translate service exceptions into user-facing failures.
        """

        try:
            normalized_target_id = int(target_id) if target_id is not None else 0
        except (TypeError, ValueError):
            normalized_target_id = 0
        try:
            normalized_members = self._members(members)
        except (TypeError, AttributeError):
            normalized_members = {}
        actor = int(actor_id)
        if (
            normalized_target_id <= 0
            or normalized_target_id == actor
            or normalized_target_id not in normalized_members
        ):
            return TodayWifeOutcome("force_invalid_target")

        current = self._now(now)
        day = current.date().isoformat()
        with self.database.connect() as connection:
            existing = self._latest_record(connection, int(group_id), day, actor)
        if existing is not None and not self._can_redraw(existing):
            existing_outcome = self._existing_draw_outcome(existing)
            if existing_outcome.kind == "existing":
                return TodayWifeOutcome("force_existing", existing_outcome.record)
            return existing_outcome
        if existing is not None and normalized_target_id == int(existing["target_id"]):
            # A redraw must start a new relationship line; do not immediately
            # recreate the just-divorced pair through the targeted command.
            return TodayWifeOutcome("force_invalid_target")

        # Feed a materialized member list to draw because callers may provide
        # a one-shot iterator.  The ordinary draw implementation remains the
        # single source of truth for persistence, story state, and redraws.
        outcome = self.draw(
            group_id,
            actor,
            actor_nickname,
            tuple(normalized_members.values()),
            current,
            selected_target_id=normalized_target_id,
            draw_source="directed",
        )
        # The command layer normally holds the group lock, but preserve the
        # forced-failure contract if another caller wins a race between the
        # read above and draw's transactional recheck.
        if outcome.kind == "existing":
            return TodayWifeOutcome("force_existing", outcome.record)
        return outcome

    def candidate_weights(
        self,
        group_id: int,
        actor_id: int,
        members: Iterable[Mapping[str, Any]],
        now: datetime | None = None,
    ) -> dict[int, float]:
        """Expose current local weighting for tests without revealing it to group users."""
        current = self._now(now)
        normalized = self._members(members)
        candidates = [member for user_id, member in normalized.items() if user_id != int(actor_id)]
        with self.database.connect() as connection:
            return self._candidate_weights(
                connection, int(group_id), current.date().isoformat(), int(actor_id), candidates
            )

    def record_activity(
        self, event_id: str, group_id: int, user_id: int, message_at: datetime
    ) -> bool:
        return self.database.record_today_wife_activity(event_id, group_id, user_id, message_at)

    def divorce(
        self, group_id: int, actor_id: int, now: datetime | None = None
    ) -> TodayWifeOutcome:
        current = self._now(now)
        day = current.date().isoformat()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM today_wife_records
                   WHERE group_id=? AND day=? AND actor_id=?
                   ORDER BY draw_index DESC LIMIT 1""",
                (int(group_id), day, int(actor_id)),
            ).fetchone()
            if row is None:
                return TodayWifeOutcome("not_found")
            if str(row["status"]) == "divorced":
                return TodayWifeOutcome("already_divorced", self._presentation_record(row))
            connection.execute(
                """UPDATE today_wife_records SET status='divorced',divorced_at=?
                   WHERE group_id=? AND day=? AND actor_id=? AND draw_index=? AND status='active'""",
                (
                    current.isoformat(timespec="seconds"),
                    int(group_id),
                    day,
                    int(actor_id),
                    int(row["draw_index"]),
                ),
            )
            row = self._latest_record(connection, int(group_id), day, int(actor_id))
        result = self._presentation_record(row)
        from bot.services.today_wife_game import TodayWifeGameService

        TodayWifeGameService(self.database, self.zone.key).freeze_relation(result, current)
        return TodayWifeOutcome("divorced", result)

    def history(self, group_id: int, actor_id: int, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM today_wife_records
                   WHERE group_id=? AND actor_id=?
                   ORDER BY day DESC, draw_index DESC, drawn_at DESC LIMIT ? OFFSET ?""",
                (int(group_id), int(actor_id), max(1, min(int(limit), 50)), max(0, int(offset))),
            ).fetchall()
        return [self._presentation_record(row) for row in rows]

    def group_records(self, group_id: int, now: datetime | None = None) -> list[dict[str, Any]]:
        day = self._now(now).date().isoformat()
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM today_wife_records
                   WHERE group_id=? AND day=? AND status='active'
                   ORDER BY drawn_at,actor_id""",
                (int(group_id), day),
            ).fetchall()
        return [self._presentation_record(row) for row in rows]

    def clear_group_records(self, group_id: int) -> int:
        return self.database.clear_today_wife_relationships(group_id)

    def story_lines(self, record: Mapping[str, Any]) -> tuple[str, ...]:
        # New records store the daily StoryDirector script ID.  Preserve the
        # legacy episode fallback for archive rows, but never translate a
        # recognized script back into an unrelated old episode.
        story_context = StoryDirector.context_for_story_id(str(record.get("story_id") or ""))
        if story_context is not None:
            reveal = StoryDirector.compose_draw_reveal(story_context, record, {})
            return tuple(
                str(block.get("text") or "")
                for block in reveal.get("blocks", ())
                if isinstance(block, Mapping) and str(block.get("text") or "").strip()
            )
        episode = self._episode_by_key(str(record.get("story_id") or ""))
        if episode is None:
            episode = self._episode_for(
                int(record.get("group_id") or 0), str(record.get("day") or "")
            )
        seed = self._record_seed(record, "scene")
        target_name = str(record.get("target_nickname") or "这位群友")
        values = {"location": episode.location, "prop": episode.prop, "target_name": target_name}
        return (
            self._variant(STORY_OPENINGS, seed + ":opening").format(**values),
            self._variant(STORY_MIDDLES, seed + ":middle").format(**values),
            self._variant(STORY_CLOSINGS, seed + ":closing").format(**values),
        )

    def intro_line(self, record: Mapping[str, Any]) -> str:
        target_name = str(record.get("target_nickname") or "这位群友")
        return self._variant(RESULT_INTROS, self._record_seed(record, "intro")).format(
            target_name=target_name
        )

    def _existing_draw_outcome(self, row: Any) -> TodayWifeOutcome:
        record = self._presentation_record(row)
        kind = "existing_divorced" if str(record.get("status")) == "divorced" else "existing"
        return TodayWifeOutcome(kind, record)

    @staticmethod
    def _can_redraw(row: Any) -> bool:
        return str(row["status"]) == "divorced" and int(row["draw_index"]) == 1

    @staticmethod
    def _latest_record(connection: Any, group_id: int, day: str, actor_id: int) -> Any:
        return connection.execute(
            """SELECT * FROM today_wife_records
               WHERE group_id=? AND day=? AND actor_id=?
               ORDER BY draw_index DESC LIMIT 1""",
            (group_id, day, actor_id),
        ).fetchone()

    def context_lines(self, record: Mapping[str, Any]) -> tuple[str, ...]:
        branch = str(record.get("branch") or "ordinary")
        context_name = str(record.get("context_nickname") or "这位群友")
        taken_by_name = str(record.get("taken_by_nickname") or "这位群友")
        flags = self._story_flags(record)
        seed = self._record_seed(record, "context")
        lines: list[str] = []
        if branch == "mutual":
            lines.extend(self._variant(RICH_MUTUAL_CONTEXTS, seed + ":mutual"))
        elif "cycle" in flags:
            lines.extend(self._variant(RICH_CYCLE_CONTEXTS, seed + ":cycle"))
        elif "taken" in flags:
            lines.extend(
                line.format(taken_by_name=taken_by_name)
                for line in self._variant(RICH_TAKEN_CONTEXTS, seed + ":taken")
            )
        elif branch == "contested":
            lines.extend(
                line.format(context_name=context_name)
                for line in self._variant(RICH_CONTESTED_CONTEXTS, seed + ":contested")
            )
        elif branch == "popular":
            lines.extend(self._variant(RICH_POPULAR_CONTEXTS, seed + ":popular"))
        if "reunion" in flags:
            lines.extend(self._variant(RICH_REUNION_CONTEXTS, seed + ":reunion"))
        elif "echo" in flags:
            lines.extend(self._variant(RICH_ECHO_CONTEXTS, seed + ":echo"))
        if "redraw" in flags:
            lines.extend(self._variant(RICH_REDRAW_CONTEXTS, seed + ":redraw"))
        return tuple(lines)

    def divorce_lines(self, record: Mapping[str, Any]) -> tuple[str, str]:
        target_name = str(record.get("target_nickname") or "这位群友")
        seed = ":".join(
            (
                "divorce",
                str(record.get("group_id") or ""),
                str(record.get("day") or ""),
                str(record.get("actor_id") or ""),
                str(record.get("target_id") or ""),
                str(record.get("divorced_at") or ""),
            )
        )
        lines = self._variant(DIVORCE_LINES, seed)
        return tuple(line.format(target_name=target_name) for line in lines)  # type: ignore[return-value]

    def state_message(
        self,
        kind: str,
        group_id: int,
        actor_id: int,
        now: datetime | None = None,
        **values: Any,
    ) -> str:
        choices = STATE_MESSAGE_POOLS.get(kind)
        if choices is None:
            raise ValueError(f"unsupported today-wife state message: {kind}")
        day = self._now(now).date().isoformat()
        return str(self._variant(choices, f"{kind}:{group_id}:{day}:{actor_id}")).format(**values)

    def player_message(
        self,
        kind: str,
        group_id: int,
        actor_id: int,
        now: datetime | None = None,
        **values: Any,
    ) -> str:
        choices = PLAYER_MESSAGE_POOLS.get(kind)
        if choices is None:
            raise ValueError(f"unsupported today-wife player message: {kind}")
        day = self._now(now).date().isoformat()
        return str(self._variant(choices, f"{kind}:{group_id}:{day}:{actor_id}")).format(**values)

    @staticmethod
    def _variant(choices: tuple[Any, ...], seed: str) -> Any:
        index = int.from_bytes(hashlib.blake2s(seed.encode("utf-8"), digest_size=4).digest(), "big")
        return choices[index % len(choices)]

    def _candidate_weights(
        self,
        connection: Any,
        group_id: int,
        day: str,
        actor_id: int,
        candidates: list[dict[str, Any]],
    ) -> dict[int, float]:
        activity = {
            int(row["user_id"]): int(row["message_count"])
            for row in connection.execute(
                """SELECT user_id,message_count FROM today_wife_activity_counts
                   WHERE group_id=? AND day=?""",
                (group_id, day),
            )
        }
        active_rows = connection.execute(
            """SELECT actor_id,target_id FROM today_wife_records
               WHERE group_id=? AND day=? AND status='active'""",
            (group_id, day),
        ).fetchall()
        incoming: dict[int, int] = {}
        graph_users: set[int] = set()
        edges: list[tuple[int, int]] = []
        for row in active_rows:
            source, target = int(row["actor_id"]), int(row["target_id"])
            incoming[target] = incoming.get(target, 0) + 1
            graph_users.update((source, target))
            edges.append((source, target))
        reciprocal = {
            source for source, target in edges if target == actor_id
        }
        cutoff = (date.fromisoformat(day) - timedelta(days=30)).isoformat()
        recent_targets = {
            int(row["target_id"])
            for row in connection.execute(
                """SELECT DISTINCT target_id FROM today_wife_records
                   WHERE group_id=? AND actor_id=? AND day>=? AND day<?""",
                (group_id, actor_id, cutoff, day),
            )
        }
        weights: dict[int, float] = {}
        activity_weights: dict[int, float] = {}
        fresh_candidates: set[int] = set()
        actor_is_taken = actor_id in incoming
        for candidate in candidates:
            user_id = int(candidate["user_id"])
            activity_bonus = min(
                2.0,
                0.35 * math.log1p(max(0, activity.get(user_id, 0) - 3)),
            )
            # Being drawn never removes a member's base chance. It only
            # cools the extra advantage earned by very high message volume.
            cooled_bonus = activity_bonus * (0.40 ** incoming.get(user_id, 0))
            freshness_weight = 1.20 if user_id not in recent_targets else 1.0
            weight = (1.0 + cooled_bonus) * freshness_weight
            if actor_is_taken and user_id in graph_users and user_id not in reciprocal:
                # A person already written into the day's story is a little
                # more likely to continue it, forming readable chains.
                weight += 0.28 + 0.18 * activity_bonus
            if user_id not in reciprocal and self._has_directed_path(edges, user_id, actor_id):
                # Closing a longer path is rare but deliberately dramatic.
                weight += 0.72 + 0.20 * activity_bonus
            weights[user_id] = weight
            activity_weights[user_id] = 1.0 + activity_bonus
            if user_id not in recent_targets:
                fresh_candidates.add(user_id)

        reciprocal_ids = {user_id for user_id in reciprocal if user_id in weights}
        if reciprocal_ids:
            reciprocal_total = sum(weights[user_id] for user_id in reciprocal_ids)
            other_total = sum(
                weight for user_id, weight in weights.items() if user_id not in reciprocal_ids
            )
            if reciprocal_total > 0 and other_total > 0:
                drama_candidate = max(
                    reciprocal_ids,
                    key=lambda user_id: (activity_weights[user_id], user_id in fresh_candidates),
                )
                activity_progress = (activity_weights[drama_candidate] - 1.0) / 2.0
                desired_share = 0.45
                if drama_candidate in fresh_candidates:
                    desired_share += 0.25 * activity_progress
                reciprocal_scale = (desired_share * other_total) / (
                    (1.0 - desired_share) * reciprocal_total
                )
                for user_id in reciprocal_ids:
                    weights[user_id] *= max(1.0, reciprocal_scale)
        return weights

    @staticmethod
    def _recently_active_candidates(
        connection: Any, group_id: int, day: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        cutoff = (date.fromisoformat(day) - timedelta(days=2)).isoformat()
        active_ids = {
            int(row["user_id"])
            for row in connection.execute(
                """SELECT user_id FROM today_wife_activity_counts
                   WHERE group_id=? AND day>=? AND day<=? AND message_count>0""",
                (group_id, cutoff, day),
            )
        }
        return [candidate for candidate in candidates if int(candidate["user_id"]) in active_ids]

    @staticmethod
    def _weighted_candidate(
        candidates: list[dict[str, Any]], weights: Mapping[int, float]
    ) -> dict[str, Any]:
        total = sum(max(0.0, float(weights.get(int(candidate["user_id"]), 0.0))) for candidate in candidates)
        if total <= 0:
            return candidates[secrets.randbelow(len(candidates))]
        ticket = (secrets.randbelow(1_000_000_000) / 1_000_000_000) * total
        cursor = 0.0
        for candidate in candidates:
            cursor += max(0.0, float(weights.get(int(candidate["user_id"]), 0.0)))
            if ticket < cursor:
                return candidate
        return candidates[-1]

    def _story_state_for_new_draw(
        self,
        connection: Any,
        group_id: int,
        day: str,
        actor_id: int,
        target_id: int,
        draw_index: int,
    ) -> tuple[str, str, str, str]:
        rows = connection.execute(
            """SELECT actor_id,target_id,actor_nickname,target_nickname
               FROM today_wife_records
               WHERE group_id=? AND day=? AND status='active'
               ORDER BY drawn_at,actor_id""",
            (group_id, day),
        ).fetchall()
        edges = [(int(row["actor_id"]), int(row["target_id"])) for row in rows]
        reciprocal = (target_id, actor_id) in edges
        target_incoming = [row for row in rows if int(row["target_id"]) == target_id]
        actor_incoming = [row for row in rows if int(row["target_id"]) == actor_id]

        if reciprocal:
            branch = "mutual"
        elif len(target_incoming) == 1:
            branch = "contested"
        elif len(target_incoming) >= 2:
            branch = "popular"
        else:
            branch = "ordinary"

        flags: set[str] = set()
        graph_users = {user_id for edge in edges for user_id in edge}
        if actor_incoming and not reciprocal:
            flags.add("taken")
            if target_id in graph_users:
                flags.add("relay")
        if not reciprocal and self._has_directed_path(edges, target_id, actor_id, minimum_hops=2):
            flags.add("cycle")
        previous = connection.execute(
            """SELECT status FROM today_wife_records
               WHERE group_id=? AND actor_id=? AND target_id=? AND day<?
               ORDER BY day DESC,draw_index DESC LIMIT 1""",
            (group_id, actor_id, target_id, day),
        ).fetchone()
        if previous is not None:
            flags.add("reunion" if str(previous["status"]) == "divorced" else "echo")
        if draw_index == 2:
            flags.add("redraw")

        context_nickname = (
            self._name(str(target_incoming[0]["actor_nickname"])) if target_incoming else ""
        )
        taken_by_nickname = (
            self._name(str(actor_incoming[0]["actor_nickname"])) if actor_incoming else ""
        )
        return branch, context_nickname, ",".join(sorted(flags)), taken_by_nickname

    def _story_for(self, group_id: int, day: str, actor_id: int, target_id: int) -> tuple[str, str]:
        episode = self._episode_for(group_id, day)
        seed = f"{group_id}:{day}:{actor_id}:{target_id}:relationship"
        return episode.key, self._variant(RELATIONSHIP_TITLES, seed)

    def episode(self, group_id: int, day: str | date) -> dict[str, str]:
        episode = self._episode_for(int(group_id), str(day))
        return {
            "key": episode.key,
            "title": episode.title,
            "location": episode.location,
            "prop": episode.prop,
        }

    def group_spotlight(
        self,
        rows: Iterable[Mapping[str, Any]],
        group_id: int,
        day: str | date,
    ) -> str:
        values = list(rows)
        if not values:
            return self._variant(
                EMPTY_SPOTLIGHT_LINES, f"empty-spotlight:{int(group_id)}:{day}"
            )
        names: dict[int, str] = {}
        edges: list[tuple[int, int]] = []
        incoming: dict[int, int] = {}
        for row in values:
            actor, target = int(row["actor_id"]), int(row["target_id"])
            names[actor] = str(row.get("actor_nickname") or "这位群友")
            names[target] = str(row.get("target_nickname") or "这位群友")
            edges.append((actor, target))
            incoming[target] = incoming.get(target, 0) + 1
        edge_set = set(edges)
        mutual = next(((a, b) for a, b in edges if (b, a) in edge_set and a < b), None)
        cycle_edge = next(
            ((a, b) for a, b in edges if self._has_directed_path(edges, b, a, minimum_hops=2)),
            None,
        )
        if mutual:
            headline = "双向奔赴"
            detail = f"{names[mutual[0]]}和{names[mutual[1]]}从两边同时走到了彼此面前。"
        elif cycle_edge:
            headline = "关系闭环"
            detail = "几条各自出发的缘分线，最后在同一个圈里接住了彼此。"
        elif max(incoming.values()) >= 3:
            target = max(incoming, key=lambda user_id: (incoming[user_id], -user_id))
            headline = "多人撞车"
            detail = f"今天已有{incoming[target]}条箭头同时指向{names[target]}。"
        elif max(incoming.values()) == 2:
            target = max(incoming, key=lambda user_id: (incoming[user_id], -user_id))
            headline = "缘分撞车"
            detail = f"两段故事在{names[target]}这里遇到了一起。"
        elif len(edges) >= 3:
            headline = "关系接力"
            detail = "新的箭头正在把原本分散的相遇连成同一段群像故事。"
        else:
            headline = "今日开场"
            detail = "最先出现的几根缘分线，已经替今天写下了开头。"
        seed = f"{int(group_id)}:{day}:" + ":".join(
            f"{left}>{right}" for left, right in sorted(edges)
        )
        return self._variant(GROUP_SPOTLIGHT_LINES, seed).format(headline=headline, detail=detail)

    def story_tags(self, record: Mapping[str, Any]) -> tuple[str, ...]:
        tags: list[str] = []
        if str(record.get("draw_source") or "random") == "directed":
            tags.append("指定缘分")
        branch = str(record.get("branch") or "ordinary")
        flags = self._story_flags(record)
        if branch == "mutual":
            tags.append("双向")
        elif "cycle" in flags:
            tags.append("闭环")
        elif branch == "popular":
            tags.append("多人撞车")
        elif branch == "contested":
            tags.append("撞车")
        if "taken" in flags:
            tags.append("自有想法")
        if "relay" in flags:
            tags.append("关系接力")
        if "reunion" in flags:
            tags.append("旧缘重逢")
        elif "echo" in flags:
            tags.append("前缘回声")
        if "redraw" in flags:
            tags.append("新篇章")
        return tuple(dict.fromkeys(tags))

    def _presentation_record(self, row: Any) -> dict[str, Any]:
        record = self._row(row)
        story_context = StoryDirector.context_for_story_id(str(record.get("story_id") or ""))
        if story_context is not None:
            record.update(
                episode_key=str(story_context["scene_id"]),
                episode_title=str(story_context["title"]),
                episode_location=str(story_context["theme_title"]),
                episode_prop=str(story_context["prop"]),
            )
        else:
            episode = self._episode_by_key(str(record.get("story_id") or "")) or self._episode_for(
                int(record.get("group_id") or 0), str(record.get("day") or "")
            )
            record.update(
                episode_key=episode.key,
                episode_title=episode.title,
                episode_location=episode.location,
                episode_prop=episode.prop,
            )
        record["story_tags"] = self.story_tags(record)
        record["intro_line"] = self.intro_line(record)
        return record

    @staticmethod
    def _record_seed(record: Mapping[str, Any], namespace: str) -> str:
        return ":".join(
            (
                namespace,
                str(record.get("group_id") or ""),
                str(record.get("day") or ""),
                str(record.get("actor_id") or ""),
                str(record.get("target_id") or ""),
                str(record.get("draw_index") or ""),
            )
        )

    @staticmethod
    def _story_flags(record: Mapping[str, Any]) -> set[str]:
        return {item for item in str(record.get("story_flags") or "").split(",") if item}

    @staticmethod
    def _has_directed_path(
        edges: Iterable[tuple[int, int]], start: int, end: int, minimum_hops: int = 1
    ) -> bool:
        adjacent: dict[int, set[int]] = {}
        for left, right in edges:
            adjacent.setdefault(int(left), set()).add(int(right))
        pending = [(int(start), 0)]
        seen = {int(start)}
        while pending:
            current, hops = pending.pop(0)
            if current == int(end) and hops >= minimum_hops:
                return True
            for neighbor in adjacent.get(current, ()):
                if neighbor not in seen or neighbor == int(end):
                    seen.add(neighbor)
                    pending.append((neighbor, hops + 1))
        return False

    @staticmethod
    def _episode_by_key(key: str) -> TodayWifeEpisode | None:
        return next((episode for episode in EPISODES if episode.key == key), None)

    @staticmethod
    def _episode_for(group_id: int, day: str) -> TodayWifeEpisode:
        digest = hashlib.blake2s(f"{group_id}:{day}:episode".encode("utf-8"), digest_size=8).digest()
        return EPISODES[int.from_bytes(digest, "big") % len(EPISODES)]

    def _now(self, value: datetime | None) -> datetime:
        return (value or datetime.now(self.zone)).astimezone(self.zone)

    @staticmethod
    def _members(members: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for member in members:
            try:
                user_id = int(member.get("user_id", 0))
            except (TypeError, ValueError):
                continue
            if user_id <= 0:
                continue
            name = str(member.get("card") or member.get("nickname") or "这位群友")
            result[user_id] = {"user_id": user_id, "nickname": TodayWifeService._name(name)}
        return result

    @staticmethod
    def _name(value: str) -> str:
        return str(value).strip()[:40] or "这位群友"

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return {key: row[key] for key in row.keys()}
