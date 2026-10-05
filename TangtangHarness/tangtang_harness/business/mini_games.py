from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal

from tangtang_harness.business.db import Database
from tangtang_harness.business.idioms import four_character_words, idiom_words, is_valid_four_character_word, is_valid_idiom


ROULETTE = "roulette"
BOMB = "bomb"
DICE = "dice"
GUESS = "guess"
GAME_DURATION = timedelta(seconds=120)
BOMB_IDIOM_MIN_DURATION_SECONDS = 60
BOMB_IDIOM_MAX_DURATION_SECONDS = 600
BOMB_IDIOM_HOLDER_DURATION = timedelta(seconds=60)
ROULETTE_BARREL_BURST_CHANCE_PERCENT = 5
RANDOM_EVENT_COUNT_RANGES = {
    ROULETTE: (3, 5),
    BOMB: (3, 5),
    DICE: (5, 10),
    GUESS: (2, 3),
}
RANDOM_EVENT_FIRST_DELAY_RANGES = {
    ROULETTE: (5, 10),
    BOMB: (15, 35),
    DICE: (15, 35),
    GUESS: (15, 35),
}
RANDOM_EVENT_INTERVAL_RANGES = {
    ROULETTE: (5, 10),
    BOMB: (10, 20),
    DICE: (10, 20),
    GUESS: (10, 20),
}
BOMB_IDIOM_RANDOM_EVENT_INTERVAL_SECONDS = 30

GAME_LABELS = {
    ROULETTE: "俄罗斯转盘",
    BOMB: "定时炸弹",
    DICE: "幸运骰局",
    GUESS: "猜数字",
}
RANDOM_EVENT_TYPES = {
    ROULETTE: (
        "roulette_bond",
        "roulette_reflection",
        "roulette_shift",
        "roulette_misfire",
        "roulette_finger_cramp",
        "roulette_burst_revolver",
        "roulette_burst_rifle",
        "roulette_aim_drift",
        "roulette_barrel_burst",
        "roulette_area_explosion",
    ),
    BOMB: (
        "bomb_hot_hand",
        "bomb_time_chaos",
        "bomb_tracking",
        "bomb_inertia",
        "bomb_black_hole",
        "bomb_reverse_delivery",
        "bomb_idiom_echo_anchor",
        "bomb_idiom_first_char",
        "bomb_idiom_rewrite",
        "bomb_idiom_free_start",
        "bomb_idiom_lonely_start",
    ),
    DICE: (
        "dice_regret",
        "dice_force",
        "dice_fate_swap",
        "dice_reverse",
        "dice_throne",
        "dice_double_luck",
        "dice_double_misfortune",
        "dice_mirror",
        "dice_adjacent",
        "dice_relief",
        "dice_tax",
        "dice_high_platform",
        "dice_abyss",
        "dice_half",
        "dice_comeback",
        "dice_twin",
    ),
    GUESS: ("guess_divergent", "guess_temperature", "guess_echo", "guess_digit_vision"),
}

RANKING_SECTIONS = {
    ROULETTE: (("传奇耐死王", "阵亡", "roulette_deaths", "roulette_games"),),
    BOMB: (
        ("传奇接盘侠", "被炸", "bomb_deaths", "bomb_games"),
        ("传奇炸弹人", "传递", "bomb_passes", "bomb_games"),
    ),
    DICE: (
        ("传奇欧皇", "欧皇", "dice_highs", "dice_games"),
        ("传奇非酋", "非酋", "dice_lows", "dice_games"),
    ),
    GUESS: (
        ("传奇智力王", "猜中", "guess_wins", "guess_games"),
        ("传奇小🐽", "猜不准", "guess_misses", "guess_games"),
    ),
}

ROULETTE_STARTED_TEXTS = (
    "🔫🎰⚠️ 俄罗斯转盘已装填！120 秒内发送 #开枪，谁敢先试？",
    "⚠️🔫🎲 弹仓合上了，一发真弹混在六格里。120 秒内 #开枪，祝各位手稳。",
    "🎭💥🔫 命运上膛！一把枪、一发子弹、120 秒。第一位 #开枪 的勇士是谁？",
    "🎰🍀🫣 转盘开局！弹仓已经转乱，敢就发 #开枪，别让勇气过期。",
)
ROULETTE_SAFE_TEXTS = (
    "😮‍💨✨ 咔！{name} 躲过一劫，命运暂时放过了 TA。",
    "💨🍀 空枪！{name} 的运气暂时在线，先把冷汗擦一擦。",
    "😏🛟 只是虚惊一场，{name} 安然无恙，危险仍在暗处。",
    "🫡😮‍💨 扳机扣下却没响，{name} 暂时保住小命。",
    "🍀✨ 幸运擦肩而过！{name} 这一枪空了，别高兴得太早。",
    "🔫🫧 咔哒一声没下文，{name} 把命先存进了下一回合。",
    "😵‍💫💨 {name} 扣完扳机才敢呼吸，幸好只是空响一场。",
    "🎰🛟 弹仓眨了眨眼，没有选中 {name}。这次平安通过！",
    "🫣🍀 {name} 的手心全是汗，枪却很给面子地沉默了。",
    "🧊🔫 冷汗落地，空枪落幕，{name} 暂时还在牌桌上。",
    "📣💨 裁判判定：{name} 这一枪无事发生，危机顺延。",
    "🧨❌ 虚惊警报解除！{name} 没碰到那颗真弹，继续围观命运。",
    "😮‍💨🎯 {name} 躲开了命运的准星，弹仓只回了一声空响。",
    "🌙🔫 夜色保佑，{name} 这一扣没有等来枪声。",
    "🪄🍀 幸运魔法生效，{name} 的扳机像是卡进了空气里。",
    "🏃💨 {name} 从空枪边缘溜走，下一位勇士请接力心跳。",
    "🎲🔫 命运骰子没掷到 {name}，这一枪安全落地。",
    "🛡️✨ 防爆护盾短暂亮起，{name} 保住了本局席位。",
    "😏📉 {name} 的危险指数先降一点，真弹还在暗中排队。",
    "🎧🔫 只听见扳机的回音，{name} 可以先把耳朵捂回去。",
    "🍃💨 一阵风吹过弹仓，{name} 的这一枪轻轻落空。",
    "🫡🛟 {name} 完成一次心跳挑战，结果是空枪通关。",
    "🎪🔫 转盘开了个玩笑，{name} 没有成为今天的主角。",
    "📼😮‍💨 惊险片段暂停，{name} 的小命成功续订一集。",
    "🚦🍀 绿灯亮起！{name} 这一枪获准安全通行。",
    "🪙🔫 命运硬币翻到幸运面，{name} 暂时不用谢幕。",
    "🎈💨 危险从 {name} 身旁飘过去了，弹仓依旧藏着悬念。",
    "🧭🛟 指针避开了 {name}，这次扳机没有带来坏消息。",
    "😬✨ 紧张值拉满又放下，{name} 的这发是标准空枪。",
    "🎟️🔫 {name} 抽到了一张空枪免单券，下一次可就未必这么走运。",
)
ROULETTE_HIT_TEXTS = (
    "💥☠️ 砰！{name} 本局阵亡，传奇耐死王榜见。",
    "🔫🎯 命中了！{name} 倒下了，这一枪终于响了。",
    "🫡💀 枪声响起，{name} 本局出局。下次再赌命运！",
    "☠️🎰 命运点名了 {name}！本局落幕，下一局 #装填 再来。",
    "🎯📜 这次真响了！{name} 喜提一次阵亡记录，耐死王候选人加一。",
    "💥🎰 命运终于开口，{name} 被这一声枪响请出了本局。",
    "🚨🔫 红灯亮起！{name} 正中真弹，本局到此为止。",
    "☠️📣 砰的一声定格，{name} 成为这把转盘的倒霉主角。",
    "🫡💥 {name} 的勇气用完了，真弹没有再给第二次机会。",
    "🎯🕯️ 准星这次没偏，{name} 的本局旅程结束。",
    "🔔☠️ 命运铃声敲响，{name} 收到一张本局出局通知。",
    "💣🔫 弹仓终于兑现承诺，{name} 被记上一笔阵亡。",
    "📉💀 好运掉线！{name} 踩中了唯一的危险格。",
    "🎭💥 剧情反转太快，{name} 被枪声送下舞台。",
    "🪦🎰 这次不是特效，{name} 真正命中，耐死王战绩加一。",
    "⚰️🔫 扳机没有留情，{name} 本局正式谢幕。",
)
ROULETTE_TIMEOUT_TEXTS = (
    "🕊️🤫 时间到，弹仓今天选择沉默。本局无人死亡。",
    "😮‍💨🛟 120 秒过去了，大家平安下车，本局无人死亡。",
    "🌙🍀 倒计时结束，幸运女神放过了所有人。本局无人死亡。",
    "🛟🏁 倒计时救场成功，没人敢再扣扳机。本局平安结束。",
)
BOMB_STARTED_TEXTS = (
    "💣🔥 定时炸弹已启动，{name} 拿到了第一棒！快用 #丢给 @群成员。",
    "🧨⏱️ 倒计时开始！炸弹在 {name} 手里，快用 #丢给 @群成员 转手。",
    "⏱️💣 滴答、滴答……{name} 正在拿着炸弹，快 #丢给 @群成员。",
    "😈🤲 接盘游戏开场！{name} 是初始持有人，快找一位群友 #丢给 @他。",
)
BOMB_IDIOM_STARTED_TEXTS = (
    "🧨🀄 成语接龙炸弹已装填！全局倒计时 {duration} 秒，{name} 先拿着；同一人连续持有最多 60 秒。用「四字成语 #丢给 @群成员」传出去！",
    "⏳📚 成语炸弹开局！总时长 {duration} 秒，{name} 是第一位持有人；每位持有人都只有 60 秒。第一句可任意四字成语！",
    "💣✍️ 词库炸弹启动！全局还会响 {duration} 秒，但谁连续拿满 60 秒都会爆。快用「成语 #丢给 @成员」找人接盘！",
)
BOMB_THROWN_TEXTS = (
    "💨💣 {from_name} 一甩手，炸弹飞到 {to_name} 那里！距离上次传递过去了 {elapsed} 秒。",
    "🏃💨 {to_name} 接到了！{from_name} 成功脱身，距离上次传递过去了 {elapsed} 秒！",
    "😈➡️ 祸水东引成功，{to_name} 成为新持有人。距离上次传递过去了 {elapsed} 秒。",
    "🤝🔥 完美交接！{from_name} 把烫手山芋塞给了 {to_name}，距离上次传递过去了 {elapsed} 秒。",
)
BOMB_IDIOM_THROWN_TEXTS = (
    "🀄✅ 「{idiom}」接龙成功！{from_name} 把成语炸弹递给 {to_name}，距离上次传递过去了 {elapsed} 秒。",
    "📚💣 成语「{idiom}」验证通过，{from_name} 成功把炸弹传到 {to_name} 手里！距上次传递 {elapsed} 秒。",
    "✍️🧨 「{idiom}」接上啦！{to_name} 接住炸弹，{from_name} 暂时脱险；距上次传递 {elapsed} 秒。",
)
BOMB_IDIOM_FORMAT_TEXTS = (
    "🀄📝 本局是成语接龙模式，请按「四字成语 #丢给 @群成员」传递，例如：画蛇添足 #丢给 @群友。",
    "📚⚠️ 成语炸弹需要完整格式：四字成语 #丢给 @群成员。先写成语，再真实 @ 接盘人。",
)
BOMB_INVALID_IDIOM_TEXTS = (
    "📖❌ 「{idiom}」不在糖糖的成语词库里，这一手不能传。换一个常用四字成语试试！",
    "🀄🚫 「{idiom}」没有通过成语校验，炸弹还在你手里。请使用有效的四字成语。",
)
BOMB_IDIOM_CHAIN_TEXTS = (
    "🔗❌ 上一句是「{previous}」，下一句必须以「{expected}」开头；「{idiom}」暂时接不上。",
    "📚🧩 接龙断在这里啦！「{previous}」的最后一字是「{expected}」，请用「{expected}」开头的四字成语。",
)
BOMB_PLAIN_FORMAT_TEXTS = (
    "💣📝 本局是原版定时炸弹，直接发送 #丢给 @群成员 即可传递。",
    "🔟⚠️ 原版炸弹不需要接成语，请用 #丢给 @群成员。",
)
BOMB_EXPLODED_TEXTS = (
    "💥💣 砰！时间到，{name} 成为本局接盘侠。",
    "🧨💥 来不及了！{name} 抱着炸弹谢幕。",
    "😵⏱️ 倒计时归零，{name} 荣登本局接盘侠。",
    "⏰💣 滴答停止，{name} 没能甩掉炸弹。本局接盘侠就是你！",
)
BOMB_IDIOM_HOLDER_TIMEOUT_TEXTS = (
    "💥📚 {name} 连续拿着成语炸弹超过 60 秒，词条还没接完，炸弹先炸了！",
    "⏰🀄 60 秒手持时限归零！{name} 没能及时接龙传出，喜提本局接盘侠。",
    "🧨📖 成语卡住太久啦！{name} 连续持有满 60 秒，炸弹当场爆开。",
)
DICE_ROLL_TEXTS = (
    "🎲✨ {name} 投出了 {value}！",
    "🍀🎲 骰子滚啊滚，{name} 得到 {value}。",
    "✨📣 {name} 的点数揭晓：{value}！",
    "🪄🎲 {name} 把骰子掷上桌：{value} 点！",
    "📣🍀 点数播报！{name} 是 {value}，先别急着庆祝。",
)
DICE_SPECIAL_COMMENTS = {
    1: (
        "🫠🎲 1 点开局，这把非酋气息有点浓，但奇迹永远留给坚持的人。",
        "🥶🧊 1 点！骰子先给你上了一课，先别急，后面还有人陪跑。",
    ),
    11: (
        "🥢✨ 11 点，两根筷子正在努力夹住欧气，稳住别慌。",
        "🌱🎲 11 点，欧气刚发芽，后面还有逆袭空间。",
    ),
    66: (
        "🎱🍀 66！双六临门，欧气开始冒泡了。",
        "6️⃣6️⃣✨ 66 点，六六大顺，这把有点说法。",
    ),
    74: (
        "📈🔥 74，起势！这个趋势一起来了，榜首得多看你两眼。",
        "🚀📊 74 起势成功，欧气曲线正在抬头。",
    ),
    88: (
        "🧧💰 88 发发发！这把欧气已经开始敲门。",
        "8️⃣8️⃣🎉 88 点双发，胜利候选人先挂个号。",
    ),
    99: (
        "🛫✨ 99！离三位数只差一步，欧皇雷达已经报警。",
        "💫9️⃣9️⃣ 99 点，这份欧气快要溢出骰盅了。",
    ),
}
DICE_LOW_COMMENTS = (
    "🥶🎲 这点数有点艰难，先把非酋席位暖一暖。",
    "🫠📉 开局吃冷风，不过骰局没结束就还有变数。",
    "🪨😵 这把有点难顶，先祈祷后面有人更倒霉。",
)
DICE_MID_LOW_COMMENTS = (
    "😶‍🌫️🎲 中下游开局，先观望，别急着给自己判死刑。",
    "🪜🍀 这个分数还在爬坡，后面说不定能混进安全区。",
    "🤹📊 有点悬但还能救，围观其他人的发挥吧。",
)
DICE_MID_COMMENTS = (
    "🧭🎲 中规中矩，稳稳坐在观战席等别人表演。",
    "😌🍀 这个点数不亏不赚，接下来就看命运怎么排位。",
    "📦✨ 平平无奇却很耐看，先把悬念留到最后。",
)
DICE_HIGH_COMMENTS = (
    "🔥🍀 欧气在线！这个分数已经很有竞争力。",
    "🏃💨 跑得挺前面，榜首候选人先举个手。",
    "🌟🎲 这把手感不错，胜利概率看起来相当可观。",
)
DICE_TOP_COMMENTS = (
    "👑✨ 欧皇气场拉满！后面的骰子得加把劲了。",
    "🚨🍀 高分预警！这份欧气已经快挡不住。",
    "🏆🎲 顶级开局，传奇欧皇席位正在向你招手。",
)
DICE_RESULT_TEXTS = (
    "🏆✨ 骰局结束！传奇欧皇：{high_name}（{high_value}）\n🥶📉 传奇非酋：{low_name}（{low_value}）",
    "🎉👑 本局封神！欧皇是 {high_name}（{high_value}）\n🫠🎲 非酋是 {low_name}（{low_value}）",
    "🎲🏁 骰子停下来了！最高 {high_name}（{high_value}）\n📉🥶 最低 {low_name}（{low_value}）",
)
DICE_VOID_TEXTS = (
    "🎲⏰ 时间到，骰局人数不足，本局不计榜。",
    "😴📭 骰局散场，参与人数不足，本局不计榜。",
)
GAME_BUSY_TEXTS = (
    "⏳🎮 当前正在{game}，先把这一局玩完再开新局吧。",
    "🚧🕹️ {game}还没结束，新的游戏先在场边排队。",
    "🎮💫 本群已有{game}进行中，别让两场命运撞车。",
)
ROULETTE_NOT_READY_TEXTS = (
    "🔫🈳 还没有装填，先发 #装填 把命运放进弹仓。",
    "🤔🔫 枪还空着呢，先 #装填 再 #开枪。",
    "🫳🎰 想扣扳机？先用 #装填 开一局俄罗斯转盘。",
)
ROULETTE_WRONG_GAME_TEXTS = (
    "🙅🎮 现在是{game}时间，俄罗斯转盘要等本局结束。",
    "🎮🔫 弹仓先收一收，当前正在{game}。",
)
BOMB_NOT_READY_TEXTS = (
    "💣🈳 当前没有炸弹，先发 #装弹 把倒计时叫出来。",
    "🤔💣 手里空空，先 #装弹，拿到炸弹才能 #丢给 @群成员。",
)
BOMB_NOT_HOLDER_TEXTS = (
    "🙅💣 炸弹不在你手里，围观也要保持距离。",
    "🫣🔥 这颗炸弹还没传到你手上，先别急着甩锅。",
    "🚫🤲 你不是当前持有人，等群友把炸弹丢给你再行动。",
)
BOMB_SELF_TARGET_TEXTS = (
    "🙅🔄 不能丢给自己，找一位群友接盘吧。",
    "😵💣 自己接自己不算，快 #丢给 @另一位群成员。",
)
DICE_ALREADY_TEXTS = (
    "🎲🔒 你本局已经投过骰子，等大家的点数揭晓吧。",
    "🛑🎲 一人一骰，{name} 的点数已经锁定，安心围观吧。",
    "📌👀 这局已记下 {name} 的成绩，不能重复投掷哦。",
)
DICE_FULL_TEXTS = (
    "🎲📚 本局 120 个点数都被投完了，等结算吧。",
    "📚👑 骰子点数已经全部占满，下一局再冲欧皇。",
)
GAME_ENDED_TEXTS = (
    "🏁✨ 本局已经结束，想再来就重新开一局吧。",
    "⌛👋 这一局的时间已经走完，下一局见。",
)
ROULETTE_BOND_EVENT_TEXTS = (
    "🔗🩸 同生共死印记落下！{name} 被命运绑在一起，下一次真弹响起时会一同出局。",
    "🪢🎰 命运红绳缠住了 {name}！有人中弹，TA 也会被拖进这场枪声。",
    "🩸⛓️ 随机事件·同生共死！命运印记落在 {name} 身上。之后有人中枪，{name} 会一同出局。",
    "⛓️🎰 随机事件·同生共死！{name} 被命运锁住了，下一次枪响会把 TA 一起带走。",
    "🩸🔗 同生共死签约完成！{name} 被系上命运红线，之后有人中枪就会被一同带离。",
)
ROULETTE_REFLECTION_EVENT_TEXTS = (
    "🪞🔫 反弹法则启动！真弹若找上门，会改道追向上一位开枪者。",
    "↩️💥 枪声开始走回头路：从现在起，中弹伤害优先回敬上一位枪手。",
    "🪞↩️ 随机事件·反弹！从现在起，若有人中枪，伤害会反弹给上一位开枪者；第一枪无处可弹。",
    "↩️🔫 随机事件·反弹启动！命中的枪声优先追向前一位开枪者，第一枪仍按原规则结算。",
    "🪞⚡ 反弹领域展开！从此命中的伤害会回敬上一位扣过扳机的人。",
)
ROULETTE_REFLECTION_HIT_TEXTS = (
    "🪞⚡ 致命枪声被折返！{shooter} 安然躲过，{victim} 替 TA 承受了这一击。",
    "↩️💣 子弹完成回旋，{shooter} 的危险转移给 {victim}，命运当场改写。",
    "🪞💥 砰！{shooter} 的致命一枪被反弹，上一位开枪的 {victim} 中枪出局。",
    "↩️😱 反弹生效！{shooter} 惊险存活，{victim} 替 TA 接下了这一枪。",
    "🪞🎯 枪声折返成功！{shooter} 逃过一劫，前一位开枪者 {victim} 被反弹命中。",
)
ROULETTE_BOND_HIT_TEXTS = (
    "🩸🔗 红线猛然收紧！{victim} 倒下的同时，绑定者 {bonded} 也被一起带走。",
    "⛓️💥 这不是一个人的结局！{victim} 中弹，{bonded} 也无法逃离同生共死。",
    "💥⛓️ 砰！{victim} 中枪，带着被印记锁定的 {bonded} 一同出局。",
    "⛓️💀 枪声响起！{victim} 倒下，同生共死的 {bonded} 也被命运带走。",
    "🔗💥 红线收紧！{victim} 命中真弹，印记绑定的 {bonded} 也同时出局。",
)
DICE_REGRET_EVENT_TEXTS = (
    "🍬🎲 后悔药递到 {name} 手里！再发一次 #骰子，就能把当前点数推倒重来。",
    "🧪🍀 命运给 {name} 留了后门：本局结束前补投一次，旧点数可以被替换。",
    "🧪🎲 随机事件·后悔药！{name} 可在本局结束前再发一次 #骰子 改命；不再投就保留当前点数。",
    "🍬✨ 随机事件·后悔药掉落！{name} 再发一次 #骰子 就会重掷，直到结束不操作则保留原点数。",
    "🧪🍀 后悔药交到 {name} 手里！本局内补发一次 #骰子，就能把当前点数换掉。",
)
DICE_FORCE_EVENT_TEXTS = (
    "🫳⚡ 命运之手按住了 {name}！骰面强行刷新：{old_value} → {new_value}。",
    "💥🎲 {name} 的骰盅被神秘大手接管，点数直接改写为 {old_value} → {new_value}。",
    "💥🫳 随机事件·滋蹦的大手！{name} 的点数被强制重投：{old_value} → {new_value}。",
    "🫳🎲 滋蹦的大手拍下！{name} 无法拒绝，骰子从 {old_value} 改成了 {new_value}。",
    "💫🫳 命运强制刷新！{name} 的骰面被重置：{old_value} → {new_value}。",
)
DICE_REGRET_REROLL_TEXTS = (
    "🔁🍬 后悔药咽下去了！{name} 的命运从 {old_value} 翻到 {new_value}。",
    "✨🎲 {name} 选择重新下注，旧点数 {old_value} 被新点数 {new_value} 顶替。",
    "🧪🔄 重掷完成！{name} 把 {old_value} 换成了 {new_value}，这次算新命运。",
    "🧪✨ 后悔药生效！{name} 的点数从 {old_value} 改成了 {new_value}。",
    "🎲🔄 {name} 喝下后悔药，命运重掷：{old_value} → {new_value}！",
)
ROULETTE_SHIFT_EVENT_TEXTS = (
    "🧭🌀 真弹没有停在原来的命运格，悄悄滑向了剩余弹仓的另一处。",
    "🎰🔀 弹仓暗中洗牌！那一发真弹换了藏身处，接下来的每一步都更难猜。",
    "🧭🔫 随机事件·弹仓偏移！真弹在剩余弹仓里悄悄换了位置。",
    "🎰🌀 弹仓偏移生效！那一发真弹被命运挪到了别处。",
    "🔫🧭 随机事件·弹仓偏移！真弹换了个还没扣动的位置，谁也不知道它躲到哪格。",
)
ROULETTE_MISFIRE_TRIGGER_TEXTS = (
    "😱🔫 {name} 被自己的假枪声吓到腿软！记一次阵亡，但真弹仍留在弹仓里。",
    "🫨💨 枪没开，魂先飞了！{name} 因意外失误倒下，子弹没有向前推进。",
    "😵‍💫🔫 {name} 触发了随机事件·意外失误！TA 被自己的枪声预演吓倒了，记一次阵亡；子弹没有射出，游戏继续。",
    "🫨💥 随机事件·意外失误命中 {name}！人先被吓倒，枪却没响，弹仓没有扣动。",
    "😵‍💫🫳 {name} 被意外失误绊倒！先记一次阵亡，但这次扳机没有推进弹仓。",
)
ROULETTE_FINGER_CRAMP_TRIGGER_TEXTS = (
    "🤏💥 手指抽筋让 {name} 突然失控！一声指令，两次扳机，连续两枪即将到来。",
    "⚡🫨 扳机像黏住了手指，{name} 被迫完成双连扣，危险连续降临。",
    "🤏⚡ {name} 触发了随机事件·手指抽筋！扳机失控，TA 被迫连开两枪！",
    "🫨🔫 手指抽筋发动！{name} 的手指停不下来，这次 #开枪 要连续扣两次扳机。",
    "⚡🤏 随机事件·手指抽筋！{name} 手滑连扣两下，接下来会连续开两枪。",
)
ROULETTE_FINGER_CRAMP_SAFE_TEXTS = (
    "🤏🌪️ 双连扣全部落空！{name} 的手指终于停下，死神擦肩而过。",
    "💨🔫 两次枪响都是虚惊，{name} 靠着离谱手气把连发危机熬了过去。",
    "💨💨 两声都是空枪！{name} 的手指终于恢复知觉，暂时逃过一劫。",
    "😮‍💨🎰 连开两枪竟然全空！{name} 这次被命运放过了。",
    "🛟⚡ 抽筋连发没有命中！{name} 两次都踩在空格上，惊险续命。",
)
ROULETTE_BURST_REVOLVER_TRIGGER_TEXTS = (
    "🚨🎰 副枪模式接管！{name} 误拿连发左轮，子弹将按参与顺序逐一检验。",
    "⚡🔫 枪械认错人了！{name} 手里的独立弹仓已经启动，主枪命运暂不受影响。",
    "🔫⚡ {name} 触发随机事件·连发左轮！拿错了另一把枪，连发轨迹已经锁定，原弹仓不受影响。",
    "🎰💥 连发左轮出鞘！{name} 手里的竟是独立弹仓，接下来按参与顺序连开。",
    "🚨🔫 {name} 误把连发式左轮端了起来！这波只结算副枪，原来的命运转盘继续转。",
)
ROULETTE_BURST_REVOLVER_SAFE_TEXTS = (
    "🛡️💨 副枪扫过一圈仍未找到猎物！{name} 与其他玩家全都暂时安全。",
    "🎰🌫️ 连发左轮打空了整段轨迹，副枪事件结束，主枪继续等待真正的枪声。",
    "✨🔫 连发左轮打完一轮，全是空响！{name} 和前面的玩家都躲过了副枪，原局继续。",
    "💨🎰 副枪的子弹没排到这轮，{name} 一路往前扫却无人倒下，主枪仍在待命。",
    "🛡️⚡ 连发式左轮哑火到底！这次没有人中弹，原弹仓的悬念照旧。",
)
ROULETTE_BURST_REVOLVER_HIT_TEXTS = (
    "💥🚨 副枪在第 {shot} 发突然开口！{victim} 被独立弹仓击中，但主局还在继续。",
    "🔫🩸 连发轨迹终于撞上 {victim}！这是副枪的死亡通知，主弹仓没有被推进。",
    "💥🔫 连发左轮第 {shot} 枪命中！{victim} 被副枪击中，原弹仓与枪次都不变，游戏继续。",
    "🚨🎰 副枪在第 {shot} 发响了！{victim} 中弹出局，但主转盘还没有结束。",
    "☠️⚡ 连发扫射命中 {victim}！这一枪来自独立弹仓，原来的子弹仍留在原位。",
)
ROULETTE_BURST_RIFLE_TRIGGER_TEXTS = (
    "🚨🔫 {name} 误拿连发 AK！独立弹匣塞进 {bullets} 发子弹，接下来按参与顺序倒序扫射。",
    "⚡🔫 枪械拿错了！{name} 手里的连发 AK 装有 {bullets} 发子弹，主弹仓不受影响。",
    "💥🔫 {name} 触发随机事件·连发 AK！独立弹匣装入 {bullets} 发，开始逐位检验。",
)
ROULETTE_BURST_RIFLE_SAFE_TEXTS = (
    "🛡️💨 连发 AK 的子弹全落在了无人位，所有当前参与者暂时安全，主局继续。",
    "🌫️🔫 AK 扫过一轮却没有击中任何参与者，原弹仓仍在等待下一枪。",
)
ROULETTE_BURST_RIFLE_HIT_TEXTS = (
    "💥🔫 连发 AK 命中 {victims}！本次副枪结算 {hits} 人阵亡，主弹仓没有推进。",
    "🚨🩸 AK 扫射击中 {victims}！{hits} 名参与者被副枪命中，主局继续。",
)
ROULETTE_AIM_DRIFT_SAFE_TEXTS = (
    "🫨🎯 枪口甩向 {target} 却只喷出空气，{shooter} 这次手抖得有惊无险。",
    "💨🔫 {shooter} 的枪口偏到 {target} 身边，幸好弹仓给了所有人一次虚惊。",
    "😵‍💫🔫 {shooter} 手抖把枪口甩向了 {target}，幸好这一枪只是空响。",
    "🫨🎰 枪口偏到了 {target} 那边，{shooter} 的这一扣却没有子弹，虚惊一场！",
    "💨🔫 {shooter} 手腕一歪瞄向 {target}，还好弹仓沉默，大家继续紧张。",
)
ROULETTE_AIM_DRIFT_HIT_TEXTS = (
    "🎯💥 枪口突然甩偏！{shooter} 的真弹绕过自己，精准落在 {victim} 身上。",
    "🫨🩸 手抖没有抖掉杀意，{victim} 成了偏枪事故的真正承受者。",
    "😵‍💫💥 {shooter} 手抖偏枪，真弹飞向了 {victim}！这次中弹的是被甩到的目标。",
    "🔫🫨 枪口失控转向 {victim}，{shooter} 躲过一劫，子弹却命中了目标！",
    "🚨🎰 偏枪事故发生！{shooter} 的真弹没有打中自己，而是击中了 {victim}。",
)
ROULETTE_BARREL_BURST_TRIGGER_TEXTS = (
    "💣🛠️ 炸膛巨响震穿全场！{name} 逃过最后一发，真弹报废，本局无人因这枪死亡。",
    "🔥🔫 枪口爆开却没吐出子弹，{name} 被爆膛意外救下，禁言与死亡都不会发生。",
    "💥🛠️ 枪口炸膛！{name} 本该命中的真弹卡在枪里，没有射出。本局无人死亡。",
    "🧯🔫 最后一刻炸膛保命！{name} 听到巨响却毫发无伤，真弹作废，转盘结束。",
    "✨💣 弹仓冒烟但子弹未出！{name} 被炸膛救下，不记死亡也不会禁言。",
)
ROULETTE_AREA_EXPLOSION_TRIGGER_TEXTS = (
    "💥🧪 真弹成分异常！{name} 扣下扳机后发生大范围爆炸，所有参与者全部阵亡，本局结束。",
    "🚨💣 真弹引爆了异常装药！{name} 触发范围爆炸，所有参与者均记阵亡，本局直接结束。",
    "🧨🔫 随机事件·异常真弹！{name} 命中真弹时引发大爆炸，全员阵亡，转盘收场。",
)
GUESS_CURSED_TEXTS = (
    "😈🕯️ {name} 因为猜错恶魔数字受到诅咒！这条猜测已被恶魔吞掉。",
    "🧿⚠️ {name} 触碰了恶魔数字，因为猜错恶魔数字受到诅咒！",
    "👿🔢 {name} 念出了禁忌数字，因为猜错恶魔数字受到诅咒。",
    "🕳️🔮 {name} 的数字掉进了禁忌裂缝，因为猜错恶魔数字受到诅咒！",
    "⚡👁️ 恶魔账本翻开了！{name} 因为猜错恶魔数字受到诅咒。",
    "🌑🗝️ {name} 碰到了不该碰的数字锁，因为猜错恶魔数字受到诅咒！",
    "🧨😈 禁忌数字发出警报，{name} 因为猜错恶魔数字受到诅咒。",
    "🪬🔢 {name} 把恶魔数字念出了声，诅咒立刻找上门！",
    "🕯️👿 数字谜题的暗面苏醒，{name} 因为猜错恶魔数字受到诅咒。",
    "🚫🔮 {name} 的猜测撞上了禁忌结界，因为猜错恶魔数字受到诅咒！",
    "🦇⚠️ 恶魔数字拒绝被猜测，{name} 收到了一份诅咒回执。",
    "🪦🔢 {name} 误读了禁忌数字，糖糖只能宣布：诅咒生效！",
    "⛓️😈 恶魔数字锁定了 {name}，这次猜错要付出一点安静的代价。",
)
GUESS_CLOSE_TEXTS = (
    "🔥📏 已经很接近了，继续在这一小段范围里仔细找！",
    "🌡️✨ 距离答案不到 100，方向对了，收紧搜索范围！",
    "🎯🔍 数字已经在附近徘徊，再细一点就能抓住它。",
)
GUESS_POSITION_MATCH_TEXTS = (
    "🧩🔢 虽然还差得远，但 {positions} 已经对上了，别轻易丢掉这条线索。",
    "🔎✨ 远距离扫描发现：{positions} 正确，剩下的数字再调整一下。",
    "🗝️📍 这次不算接近，不过 {positions} 与答案位置一致！",
)
DICE_FATE_SWAP_EVENT_TEXTS = (
    "🔄🎲 命运桌面突然换牌！{first_name} 与 {second_name} 对调分数：{first_value} ↔ {second_value}。",
    "🪄🔁 点数被调包了！{first_name}、{second_name} 的骰运完成互换：{first_value} ↔ {second_value}。",
    "🔄🎲 随机事件·命运互换！{first_name} 与 {second_name} 交换了点数：{first_value} ↔ {second_value}。",
    "🪄🎲 命运互换发动！{first_name}、{second_name} 的骰运被调包：{first_value} ↔ {second_value}。",
    "🔁🍀 命运洗牌！{first_name} 与 {second_name} 交换骰面：{first_value} ↔ {second_value}。",
)
DICE_REVERSE_EVENT_TEXTS = (
    "🙃🔃 数字翻面完成！全场点数按反转规则重算：{changes}。",
    "🪞📊 骰局方向倒过来了，所有人的新点数已经更新：{changes}。",
    "🙃🎲 随机事件·大小反转！全场骰子点数被翻面：{changes}。",
    "🔃✨ 大小反转生效！点数逐一改写：{changes}。",
    "🙃📊 随机事件·大小反转翻面，全场点数重新结算：{changes}。",
)
DICE_THRONE_TRIGGER_TEXTS = (
    "👑⚡ 王座被掀动！{name} 与当前榜首 {leader_name} 交换点数：{value} → {leader_value}；{leader_value} → {value}。",
    "🏰🎲 王冠换了主人，点数同步对调：{name} {value} → {leader_value}；{leader_name} {leader_value} → {value}。",
    "👑🔄 {name} 触发了随机事件·王座易主！{name}：{value} → {leader_value}；{leader_name}：{leader_value} → {value}。",
    "👑🪑 王座易主！{name}：{value} → {leader_value}；榜首 {leader_name}：{leader_value} → {value}。",
    "🏰🔄 随机事件·王座易主完成交换：{name} {value} → {leader_value}；{leader_name} {leader_value} → {value}。",
)
DICE_THRONE_NOOP_TEXTS = (
    "👑🫠 王座易主抽中了 {name}，可惜 TA 已经坐在最高处，交换没有产生变化。",
    "🏆😎 {name} 本来就是榜首，王座想换人却找不到更高的位置，只能维持原状。",
    "👑😎 {name} 触发了随机事件·王座易主，但 TA 自己已经是当前欧皇，王座纹丝不动。",
    "🪑✨ 王座易主本想发动，{name} 却正坐在榜首，只能原地加冕。",
    "👑🫠 王座易主抽到 {name}，但 TA 已是最高分，换位操作原地取消。",
)
BOMB_HOT_HAND_EVENT_TEXTS = (
    "🔥🧨 炸弹突然变得更烫！滴答节奏明显提速，下一次交接必须更加果断。",
    "🥵💣 烫手警报拉响，炸弹像在催命一样加快节拍，持有者压力翻倍。",
    "🔥🧨 随机事件·烫手加速！炸弹的滴答声突然变急，谁拿着都烫手。",
    "🥵⏩ 烫手加速生效！倒计时明显加快，快把这颗烫手山芋送出去。",
    "🔥⏱️ 随机事件·烫手加速！炸弹温度飙升，滴答声催得人手忙脚乱。",
)
BOMB_TIME_CHAOS_EVENT_TEXTS = (
    "🌀⏰ 时钟彻底失去规律，炸弹的时间节奏开始随机摇摆。",
    "⚡⌛ 倒计时被混乱接管，快慢交替的滴答声让人完全无法判断下一秒。",
    "🌀⏱️ 随机事件·时间失控！倒计时忽快忽慢，谁也摸不准它的脾气。",
    "⚡🕰️ 时间失控发动！炸弹的时钟开始胡乱跳动。",
    "🌀⌛ 随机事件·时间失控！这颗炸弹的时针突然任性，节奏彻底乱了。",
)
BOMB_TRACKING_EVENT_TEXTS = (
    "🎯📡 炸弹开启自动导航，锁定 {name} 后直接改变路线落入 TA 手中。",
    "🛰️💥 追踪信号捕获 {name}！炸弹跳过中间人，直接完成定点投递。",
    "🎯🧨 随机事件·定点追踪！炸弹绕过人群，自动落到了 {name} 手中。",
    "📡💣 定点追踪锁定 {name}！炸弹自己找上门了。",
    "🛰️🧨 随机事件·定点追踪完成，炸弹绕开传递路线，直接塞进 {name} 手里。",
)
BOMB_INERTIA_TRIGGER_TEXTS = (
    "🪃🔥 {name} 的传递撞上回弹力！炸弹拒绝离手，重新弹回原持有者掌心。",
    "↩️💣 交接动作被惯性改写，{name} 刚把炸弹甩出去，它又绕回来了。",
    "↩️🧨 {name} 触发了随机事件·惯性反弹！炸弹没有留在对方手上，又弹回了 TA 自己手里。",
    "🪃💣 惯性反弹生效！{name} 刚甩出的炸弹绕了一圈，还是回到了 TA 手中。",
    "↩️🔥 随机事件·惯性反弹！{name} 的交接被弹回，炸弹拒收并回到原持有人手里。",
)
BOMB_BLACK_HOLE_TRIGGER_TEXTS = (
    "🌌🕳️ 投递路线被黑洞吞噬，{name} 的炸弹从另一条空间通道飞向 {target_name}。",
    "🌀📦 黑洞撕开传递路径！{name} 的目标被改写，炸弹落到了 {target_name} 手里。",
    "🕳️💣 {name} 触发了随机事件·黑洞投递！炸弹吞掉原定路线，改道飞向了 {target_name}。",
    "🌌🧨 黑洞投递发动！{name} 本想交给别人，炸弹却从虫洞落到了 {target_name} 手里。",
    "🕳️📦 随机事件·黑洞投递改写路线！{name} 的炸弹穿过裂缝，转交给 {target_name}。",
)
BOMB_REVERSE_DELIVERY_TRIGGER_TEXTS = (
    "⏪🧨 快递单被倒放！{name} 的炸弹沿着旧路线逆行，回到 {target_name} 手中。",
    "🔁📮 逆向物流启动，刚刚的传递被撤销，炸弹重新寄给 {target_name}。",
    "🔄💣 {name} 触发了随机事件·逆向快递！炸弹沿原路退回，回到了 {target_name} 手中。",
    "↪️🧨 逆向快递发动！{name} 的传递被倒带，炸弹重新砸向上一位持有者 {target_name}。",
    "⏪💣 随机事件·逆向快递回退成功！{name} 刚传出的炸弹沿旧路回到 {target_name}。",
)
BOMB_IDIOM_REPEAT_TEXTS = (
    "🚫📚 「{idiom}」已经在本局留下过脚印，不能再次接龙，请换一个全新的成语。",
    "🔁🀄 成语查重失败：「{idiom}」被判定为重复词条，本次传递没有成功。",
    "🛑📖 「{idiom}」不能二次登场！接龙词册已经把它标记为已使用。",
    "🔁🚫 「{idiom}」本局已经出现过了，成语接龙不能原地套娃。换一条没用过的成语吧！",
    "📚🛑 词库记录显示「{idiom}」已经接过，本局默认禁止重复成语。",
)
BOMB_IDIOM_LONELY_TEXTS = (
    "🌌🚧 孤立规则拦截了「{idiom}」：首字撞上历史末字，不能走这条路。",
    "🧊🀄 「{idiom}」的开头不够孤独，和之前的末字产生冲突，请重新接龙。",
    "🚫🌠 这句成语没能避开历史回声，「{idiom}」的首字被本局规则拒绝。",
    "🪐🚫 孤立开篇生效中：新成语的首字不能是此前任何一句的末字；「{idiom}」不符合这条规则。",
    "🌌🧩 这一手要走孤立路线！「{idiom}」的首字撞上了旧成语的末字，换一句再试。",
)
BOMB_IDIOM_ECHO_ANCHOR_TEXTS = (
    "📻🔁 旧词信号重新上线！接龙锚点回到「{anchor}」，下一句从「{expected}」接起。",
    "🕰️🀄 历史词条被重新点亮：「{anchor}」成为临时锚点，请从「{expected}」继续。",
    "📼🀄 随机事件·旧词回响！接龙锚点回到曾出现过的「{anchor}」，下一句请从「{expected}」开始；「{anchor}」本身仍不可重复。",
    "🕰️📚 旧词回响发动！历史成语「{anchor}」重新成为接龙起点，下一手要以「{expected}」开头，但不能再说「{anchor}」。",
    "📖🔁 随机事件·旧词回响翻开旧页！请借「{anchor}」的末字「{expected}」续龙，历史词仍禁止复读。",
)
BOMB_IDIOM_FIRST_CHAR_TRIGGER_TEXTS = (
    "🌀↩️ 接龙方向突然掉头！「{idiom}」之后不看末字，下一手从首字「{expected}」开始。",
    "🔄📚 首字回环改写规则：{name} 接上的「{idiom}」将从「{expected}」开始下一轮接龙。",
    "↩️🀄 {name} 触发了随机事件·首字回环！「{idiom}」已接上，下一句改从它的第一个字「{expected}」开始接。",
    "🌀📚 首字回环生效！{name} 的「{idiom}」过关了，下一位请以首字「{expected}」续龙，而不是末字。",
    "🔄🀄 随机事件·首字回环改写规则！{name} 接上「{idiom}」后，下一手锁定从「{expected}」开头。",
)
BOMB_IDIOM_REWRITE_TRIGGER_TEXTS = (
    "🗑️💥 词条被随机事件抹除！「{idiom}」作废，{name} 必须重新提交另一句成语。",
    "✏️🚫 本局词册拒收「{idiom}」！炸弹仍在 {name} 手中，换词后才能继续传递。",
    "🗑️🀄 {name} 触发了随机事件·词条作废！刚接上的「{idiom}」被判无效，炸弹还在 TA 手里，必须重新接龙；这条词也不能再用。",
    "📝💥 词条作废发动！「{idiom}」被从本局词册划掉，{name} 未能传出炸弹，请换一条成语重新接龙。",
    "🚫📚 随机事件·词条作废！「{idiom}」被墨水涂掉，{name} 仍是持有人，换词后再试。",
)
BOMB_IDIOM_FREE_START_TEXTS = (
    "🪽🀄 接龙限制暂时解锁！下一手可从任意未使用成语重新开篇。",
    "🎟️📖 自由起步机会到账，下一位不必看末字，只要选择未出现过的有效成语即可。",
    "🍀📚 随机事件·自由开篇！下一次接龙可从任意未使用的有效成语开始，不必接上当前末字。",
    "🪽🀄 自由开篇券到账！当前持有者下一手可任选没出现过的成语开局。",
    "🌟📖 随机事件·自由开篇开启！下一条只要没用过，就能跳过首尾衔接直接传递。",
)
BOMB_IDIOM_LONELY_START_TEXTS = (
    "🪐🚫 孤岛规则展开，下一手可任意选词，但首字必须避开所有历史末字。",
    "🌙🀄 接龙进入独行模式：词语可以自由选择，开头却不能与任何旧末字相同。",
    "🪐📚 随机事件·孤立开篇！下一次可任选未使用成语，但首字不能等于此前任何一句的末字。",
    "🌌🀄 孤立开篇生效！下一手不必接龙，却必须避开所有历史末字作为开头。",
    "🧊📚 随机事件·孤立开篇！下一句可自由选词，但开头要与所有历史末字保持距离。",
)
DICE_DOUBLE_LUCK_TRIGGER_TEXTS = (
    "🍀🎲 双倍好运突然降临！{name} 同时获得 {first_value} 与 {second_value}，最终留下 {value}。",
    "✨🪄 双倍好运的幸运骰盅开了双层机关，{name} 的两次结果是 {first_value}、{second_value}，取高分 {value}。",
    "🍀🎲 {name} 触发了随机事件·双倍好运！骰盅翻出 {first_value} 和 {second_value}，糖糖替 TA 留下更高的 {value}。",
    "✨🎲 双倍好运生效！{name} 有两次机会：{first_value} / {second_value}，取走幸运的 {value}。",
    "🍀🪄 随机事件·双倍好运开箱！{name} 掷出 {first_value} 与 {second_value}，更高的 {value} 留在桌上。",
)
DICE_DOUBLE_MISFORTUNE_TRIGGER_TEXTS = (
    "🌧️🎲 双倍倒霉也翻倍！{name} 的两个结果为 {first_value}、{second_value}，最后只能背走 {value}。",
    "🥶📉 双倍倒霉连续泼冷水，{name} 掷出 {first_value} 和 {second_value}，低分 {value} 被强制留下。",
    "🌧️🎲 {name} 触发了随机事件·双倍倒霉！骰盅翻出 {first_value} 和 {second_value}，偏偏得接下较低的 {value}。",
    "🥶🎲 双倍倒霉生效！{name} 掷出 {first_value} / {second_value}，命运只留下了 {value}。",
    "🌧️📉 随机事件·双倍倒霉结算，{name} 的 {first_value}、{second_value} 中只能收下较低的 {value}。",
)
DICE_MIRROR_EVENT_TEXTS = (
    "🪞🔮 镜中骰面完成折射，{name} 的数字变化：{old_value} → {new_value}。",
    "🌈🎲 镜面裂光扫过骰局，{name} 的点数被重新映照：{old_value} → {new_value}。",
    "🪞🎲 随机事件·镜像骰面！{name} 的点数在镜中翻面：{old_value} → {new_value}。",
    "✨🪞 镜像骰面闪过，{name} 的点数翻面：{old_value} → {new_value}。",
    "🔮🎲 随机事件·镜像骰面折射！{name} 的数字被镜子改写：{old_value} → {new_value}。",
)
DICE_ADJACENT_EVENT_TEXTS = (
    "🏃🎲 随机事件·连号追击！{name} 追向相邻空位：{old_value} → {new_value}。",
    "🔢💨 连号追击发动！{name} 的骰子挪了一格：{old_value} → {new_value}。",
    "🧲🎲 连号追击锁定了隔壁位置，{name} 被数字牵着走：{old_value} → {new_value}。",
    "🏁🔢 {name} 的点数开启短跑模式，刚好冲进相邻空位：{old_value} → {new_value}。",
    "👣✨ 连号的脚步声来了！{name} 紧追一格，点数改写为 {old_value} → {new_value}。",
    "⚙️🎲 数字齿轮轻轻一拨，{name} 的骰面滑到邻座：{old_value} → {new_value}。",
)
DICE_RELIEF_EVENT_TEXTS = (
    "🛟🎲 随机事件·非酋救济！垫底的 {name} 获得翻盘点数：{old_value} → {new_value}。",
    "🍀🆘 非酋救济送达！{name} 告别低分：{old_value} → {new_value}。",
    "🚑🍀 幸运救护车抵达，{name} 从低分区被拉了一把：{old_value} → {new_value}。",
    "🌤️🎲 乌云给 {name} 让路啦，救济骰面到账：{old_value} → {new_value}。",
    "🪜✨ 垫底席位弹出升降梯，{name} 向上翻身：{old_value} → {new_value}。",
    "💌🍀 命运寄来补偿包，{name} 的分数更新为 {old_value} → {new_value}。",
)
DICE_TAX_EVENT_TEXTS = (
    "📉👑 随机事件·欧皇税！榜首 {name} 被命运征税：{old_value} → {new_value}。",
    "🧾🎲 欧皇税开征！{name} 的高分被削了一截：{old_value} → {new_value}。",
    "🏦👑 欧气账户扣款成功，{name} 的点数被收走一部分：{old_value} → {new_value}。",
    "📬💸 命运税单送达！{name} 的榜首余额变成 {old_value} → {new_value}。",
    "⚖️🎲 平衡装置盯上高分，{name} 接受了一次温柔降温：{old_value} → {new_value}。",
    "🧊👑 欧皇光环被借走一点，{name} 的骰面改为 {old_value} → {new_value}。",
)
DICE_HALF_EVENT_TEXTS = (
    "✂️🎲 随机事件·幸运折半！{name} 的点数被对半切开：{old_value} → {new_value}。",
    "➗🍀 幸运折半生效！{name} 的点数变化：{old_value} → {new_value}。",
    "🍰🎲 命运把骰面切成两半，{name} 留下这一份：{old_value} → {new_value}。",
    "🪄➗ 数字缩放术发动，{name} 的幸运值收束为 {old_value} → {new_value}。",
    "✂️✨ 咔嚓一下，{name} 的点数被精确折半：{old_value} → {new_value}。",
    "🧮🍀 幸运分期到账，{name} 的骰面重新结算：{old_value} → {new_value}。",
)
DICE_COMEBACK_EVENT_TEXTS = (
    "🚀🎲 随机事件·末位逆袭！{low_name}：{low_value} → {other_value}；{other_name}：{other_value} → {low_value}。",
    "⚡🏁 末位逆袭发动！{low_name}：{low_value} → {other_value}；{other_name}：{other_value} → {low_value}。",
    "🪜🎲 末位升降机直达高处！{low_name}：{low_value} → {other_value}；{other_name}：{other_value} → {low_value}。",
    "🔁✨ 命运临时调座，{low_name}：{low_value} → {other_value}；{other_name}：{other_value} → {low_value}。",
    "🏎️💨 逆袭超车成功！{low_name}：{low_value} → {other_value}；{other_name}：{other_value} → {low_value}。",
    "🎢🎲 排名过山车翻转，{low_name}：{low_value} → {other_value}；{other_name}：{other_value} → {low_value}。",
)
DICE_HIGH_PLATFORM_TRIGGER_TEXTS = (
    "🏔️🎲 {name} 触发了随机事件·高台跃迁！骰子跃上高分区，落在 {value} 点。",
    "✨📈 高台跃迁生效！{name} 从 91-120 的高分区掷出 {value} 点。",
    "🦅🎲 {name} 踩上随机事件·高台跃迁板，直奔高分云层，定格在 {value} 点！",
    "🚀📈 随机事件·高台跃迁点火！{name} 的骰子空降高分区：{value} 点。",
    "⛰️✨ 随机事件·高台跃迁揭晓山顶骰面，{name} 从高分区带回 {value} 点。",
    "🎯🏔️ 随机事件·高台跃迁锁定成功，{name} 的幸运落点是 {value} 点！",
)
DICE_ABYSS_TRIGGER_TEXTS = (
    "🕳️🎲 {name} 触发了随机事件·深渊试炼！骰子跌入低分区，落在 {value} 点。",
    "🌑📉 深渊试炼生效！{name} 从 1-30 的低分区掷出 {value} 点。",
    "🌌🎲 {name} 踩空掉进随机事件·深渊试炼，骰面停在 {value} 点。",
    "🪂📉 随机事件·深渊试炼风很大，{name} 只能从低分区带回 {value} 点。",
    "🕯️🎲 随机事件·深渊试炼亮起黑暗骰面，{name} 的落点是 {value} 点。",
    "🥶🌑 随机事件·深渊试炼判定完成，{name} 抽到低分区的 {value} 点。",
)
DICE_TWIN_TRIGGER_TEXTS = (
    "👯🎲 {name} 触发了随机事件·双生骰面！骰面镜像：{old_value} → {new_value}。",
    "🪞👯 双生骰面发动！{name} 的点数翻成双生面：{old_value} → {new_value}。",
    "🧬🎲 随机事件·双生骰面苏醒，{name} 的原始点数在镜中重生：{old_value} → {new_value}。",
    "👥✨ 随机事件·双生骰面让两张骰面交换位置，{name}：{old_value} → {new_value}。",
    "🪞⚡ 随机事件·双生骰面中，镜中另一个骰子点头了：{name} {old_value} → {new_value}。",
    "🎭🎲 随机事件·双生骰面翻牌！{name} 的骰面完成对称变换：{old_value} → {new_value}。",
)
GUESS_DIVERGENT_TRIGGER_TEXTS = (
    "🧠🌀 发散思维把答案拧了一下！只改变十位或个位，百位保持不动；本次不计入次数。",
    "🔀🔢 发散思维让答案发生小范围漂移，百位仍然可靠，十位或个位已经换位；当前猜测不消耗次数。",
    "🧠🌀 {name} 触发了随机事件·发散思维！答案只微调十位或个位，百位不变；本次猜数不计入十次限制。",
    "🔢🌪️ 发散思维发动！{name} 一猜，糖糖临时改了十位或个位，结果按新答案重算；这次不消耗次数。",
    "🧠🌀 随机事件·发散思维拐弯！{name} 出手后答案的十位或个位变化，当前猜数不计入十次限制。",
)
GUESS_TEMPERATURE_EVENT_TEXTS = (
    "🌡️📍 温差探测完成：{name} 上一次猜测的距离结果是，{hint}。",
    "🔥🧊 冷热仪表给出补充读数，{name} 最近一次错误猜测，{hint}。",
    "🌡️🧠 随机事件·温差提示！{name} 最近一次错误猜测，{hint}。",
    "🧊🔥 温差提示送达：{name} 上一次猜错时，{hint}。",
    "🌡️📣 随机事件·温差提示播报：{name} 最近一次错误猜测，{hint}。",
)
GUESS_ECHO_EVENT_TEXTS = (
    "📣🔢 数字回声从答案深处传来：{hint}。",
    "🔔🧩 数字回声线索落地，答案悄悄透露了一点信息：{hint}。",
    "🔊🔢 随机事件·数字回声！{hint}。",
    "📣🧠 数字回声传来一条线索：{hint}。",
    "🔔🔢 随机事件·数字回声响起：{hint}。",
)
GUESS_DIGIT_VISION_EVENT_TEXTS = (
    "👁️🔍 数位透视窗口短暂开启！答案的{place}确认是 {digit}。",
    "🪟✨ 数位透视照亮了一格：目标答案{place}为 {digit}。",
    "🔍🔢 随机事件·数位透视！答案的{place}是 {digit}。",
    "👁️✨ 数位透视发动：目标数字的{place}悄悄亮起，是 {digit}。",
    "🔍📣 随机事件·数位透视公开线索：答案的{place}为 {digit}。",
)
GUESS_STARTED_TEXTS = (
    "🔢🧠 猜数字开局！糖糖已经想好 0-999 的一个数，120 秒内发送 #猜 数字。",
    "🎯🔒 秘密数字已锁定在 0-999！快用 #猜 数字 试试你的直觉，倒计时 120 秒。",
    "🕵️🔢 数字谜题上线！答案藏在 0 到 999 之间，发送 #猜 数字 来破案。",
    "🧠✨ 糖糖藏了一个 0-999 的数字！120 秒内 #猜 数字，谁会先看穿它？",
)
GUESS_HIGH_TEXTS = (
    "📈🙈 {name} 猜了 {value}，有点冲太高啦！往小了想想，数字正在下面招手。",
    "🚀📉 {value} 这步迈得太大，{name} 快把视线往低处挪一挪！",
    "⬆️🧊 {name} 的 {value} 偏大了，给答案留点下降空间吧。",
    "🗻🔽 {value} 爬得太高！{name}，这次该往下找线索啦。",
    "📣⬇️ {name} 的方向有点高空作业，{value} 再小一些会更接近。",
    "🌤️📉 {value} 已经飞进高空了，{name} 快往下俯冲一点。",
    "🎈⬇️ {name} 把 {value} 放得太高，答案可没飘到云里。",
    "🧗🔢 {value} 登顶过头！{name}，沿着数字山坡往下走。",
    "🔭📉 望远镜看得太远啦，{name} 的 {value} 需要降一降。",
    "🛗⬇️ {value} 在高楼层，{name} 请按一下向下按钮。",
    "🚁🔽 {name} 的 {value} 正在盘旋高空，答案在更低的位置。",
    "🏔️📣 这次爬太猛了！{value} 比谜底高，往下回撤吧。",
    "📡⬇️ 信号收到：{name} 的 {value} 偏高，请调整到更低频段。",
    "🎢🔽 {value} 冲上坡顶了，{name} 接下来该滑下来啦。",
    "☁️🧮 答案不在云端，{name} 的 {value} 要再小一点。",
    "🪂📉 高空预警！{value} 超过目标，{name} 快安全降落。",
    "🗺️⬇️ {name} 走到了地图上方，{value} 需要往南一点。",
    "🥵📉 {value} 热得冒烟，离答案太高了，降温再猜！",
    "🎯🔽 瞄准线压得不够低，{name} 的 {value} 仍然偏大。",
    "📚⬇️ {value} 翻到答案后面去了，{name} 往前翻几页。",
    "🛰️📉 {name} 发射的 {value} 轨道太高，调低参数重来。",
    "🪜🔽 这架梯子爬多了几格，{value} 要往下退一退。",
    "🚨⬇️ 高位警报！{name}，{value} 请往更小的数字移动。",
    "🎼📉 这音调太高啦，{name} 把 {value} 降几个八度试试。",
)
GUESS_LOW_TEXTS = (
    "📉🔭 {name} 猜了 {value}，还在答案下方！大胆往大了猜。",
    "🪜⬆️ {value} 有点保守啦，{name} 再往上迈几级试试。",
    "🌱🚀 {name} 的 {value} 太小了，答案还在更高处等你。",
    "📣📈 {value} 没够着！{name}，这次把数字往上抬一抬。",
    "🔽🙅 {name} 猜得偏低，离目标还有一段上坡路。",
    "🌋⬆️ {value} 还在山脚，{name} 往上冲一点才有机会。",
    "🎈📈 {name} 的 {value} 没飘够高，继续给数字打气！",
    "🧗🔢 {value} 刚爬第一段，答案还在更高的台阶。",
    "🔭📈 视线压得太低啦，{name} 抬头看看更大的数字。",
    "🛗⬆️ {value} 在低楼层，{name} 请按向上按钮继续找。",
    "🚁🔼 {name} 的 {value} 还没起飞，答案在更高处盘旋。",
    "🏔️📣 山还没爬够！{value} 比谜底低，再往上走。",
    "📡⬆️ 信号提示：{name} 的 {value} 偏低，请调高频段。",
    "🎢🔼 {value} 还在坡底，{name} 下一次大胆冲上去。",
    "🌱🧮 答案不在地板，{name} 的 {value} 要再大一点。",
    "🪂📈 低空提示！{value} 没到目标，继续向上拉升。",
    "🗺️⬆️ {name} 走到了地图下方，{value} 需要往北一点。",
    "🥶📈 {value} 还凉飕飕的，离答案太低了，升温再猜！",
    "🎯🔼 瞄准线抬得不够高，{name} 的 {value} 仍然偏小。",
    "📚⬆️ {value} 翻到答案前面去了，{name} 往后翻几页。",
    "🛰️📈 {name} 发射的 {value} 轨道太低，调高参数重来。",
    "🪜🔼 这架梯子少爬了几格，{value} 要继续往上走。",
    "🚨⬆️ 低位警报！{name}，{value} 请往更大的数字移动。",
    "🎼📈 这音调太低啦，{name} 把 {value} 升几个八度试试。",
    "🧲⬆️ {value} 还没被答案吸住，{name} 再往上调一点。",
    "🏹📈 这一箭落在靶子下方，{name} 下次把 {value} 拉高。",
    "🌊🔼 {value} 还在浅水区，答案藏在更高的浪头后面。",
    "🔋📈 电量不够高！{name} 给 {value} 充点数再出发。",
    "🎪⬆️ {value} 还没够到顶棚，{name} 再抛高一点试试。",
)
GUESS_HIT_TEXTS = (
    "🎉🧠 {name} 猜中了 {value}！数字谜题被破解，传奇智力王候选人登场！",
    "🏆🔢 正解！{name} 一击命中 {value}，这局脑力王就是你！",
    "🎯✨ {name} 猜对了 {value}！糖糖的秘密数字被看穿啦。",
    "🕵️💡 案件告破！{name} 锁定 {value}，本局猜数字结束。",
)
GUESS_TIMEOUT_TEXTS = (
    "⌛🔢 时间到！这局没人猜中，秘密数字是 {value}。本局参与者一起接受禁言惩罚。",
    "🕰️🧠 120 秒结束，答案 {value} 终于公开；没能破案的参与者要暂时静音。",
    "📭🎯 谜底揭晓：{value}。这次数字藏得太深，本局参与者一起冷静一下。",
)
GUESS_ATTEMPTS_EXHAUSTED_TEXTS = (
    "🧨🔢 十次猜测全部 miss！答案是 {value}，本局参与者一起接受 30-60 秒冷静禁言。",
    "📛🧠 十发线索都没锁定谜底 {value}！惩罚机制启动，所有参与者暂时静音 30-60 秒。",
    "⏱️💥 猜数字十连败！正确答案为 {value}，本局所有猜过的人都要安静 30-60 秒。",
)
GUESS_NOT_READY_TEXTS = (
    "🔢🈳 还没有数字谜题，先发送 #猜数 开局吧。",
    "🧠🔒 糖糖还没选数字呢，先用 #猜数 把谜题叫出来。",
    "🕳️🔢 数字舞台已经散场，先发 #猜数 开新局。",
    "📭🧠 这里暂时没有谜底，#猜数 可以重新点亮游戏。",
    "🚪🔢 猜数字入口还没打开，请先发送 #猜数。",
    "🛎️🎯 想继续破案？先用 #猜数 召唤下一道谜题。",
    "🧩🫥 谜题暂时不在场，发送 #猜数 让它回来。",
    "🌙🔢 数字已经休息，#猜数 一声就能重新开局。",
    "📣🧠 当前没有可猜的目标，先发 #猜数。",
    "🗃️🔒 这一轮数字档案已关闭，#猜数 开档再来。",
    "🎬🔢 猜数字还没开演，发送 #猜数 买票入场。",
    "🧭🎯 线索地图为空，先用 #猜数 放置一个新目标。",
    "🫧🧠 谜底没有被藏起来，因为本局还没开始。#猜数 试试。",
    "🔔🔢 糖糖正在等开局指令：#猜数。",
    "🛟🎯 没有活跃数字局，发送 #猜数 就能重新下水。",
    "📦🧠 本轮答案已打包收走，#猜数 可拆下一包。",
    "🏁🔢 上一轮已经结束，新的赛道请从 #猜数 开始。",
    "🪄🎯 数字魔法尚未启动，念出 #猜数 才会生效。",
    "🧱🔢 当前没有答案墙，先发送 #猜数 建一面。",
)
GUESS_WRONG_GAME_TEXTS = (
    "🎮🔢 现在正在玩{game}，数字谜题得等这一局结束。",
    "🚧🧠 {game} 正在进行，#猜数 先在场边排个队。",
)
GUESS_INVALID_TEXTS = (
    "🔢⚠️ 猜数字请用 #猜 0-999，例如 #猜 520。",
    "🤔🔎 这不是 0 到 999 的整数，试试 #猜 123 吧。",
    "🧠🧾 格式小提示：发送 #猜 数字，数字范围是 0-999。",
    "🚧🔢 这次的数字格式没接住，请用 #猜 456 再来。",
    "🧮❓ 糖糖只认识 0-999 的整数，#猜 233 这样发就对啦。",
    "📏⚠️ 猜测超出范围或格式不对，答案只会在 0 到 999 之间。",
    "🗝️🔒 开锁口令是 #猜 数字，例如 #猜 88。",
    "🎯🧾 请把箭射向整数：#猜 0 到 #猜 999 都可以。",
    "🕵️📋 数字侦探守则：#猜 后面跟一个 0-999 的整数。",
    "🚦🔢 这条猜测没通过格式路口，试试 #猜 777。",
    "🧠⚙️ 指令需要一个纯数字，不要加别的文字：#猜 321。",
    "📦❌ 糖糖没能从这条消息里拆出 0-999 的数字。",
    "🔍🎲 再确认一下格式：#猜 空格 数字，例如 #猜 66。",
    "🪄🔢 数字魔法只收整数，范围从 0 到 999。",
    "📣🧮 请输入一位到三位数字：#猜 9、#猜 99 或 #猜 999。",
    "🛠️⚠️ 这次参数不合法，给糖糖一个 0-999 的整数吧。",
    "🧭🔢 方向没问题，数字格式差一点：#猜 100 这样试试。",
    "🎫❌ 这不是有效的猜数入场券，正确格式是 #猜 520。",
    "🔐📏 口令长度限定 1 到 3 位，且数值不能超过 999。",
    "🤹🔢 别让数字打结啦，直接发送 #猜 246 就好。",
    "🧱⚠️ 这条消息撞到范围墙了，答案区间只有 0-999。",
    "🧾✨ 格式示范：#猜 1、#猜 520、#猜 999。",
    "🪧🔎 糖糖需要一个清清楚楚的整数，试试 #猜 314。",
    "📊❌ 这不是可比较的有效数字，改用 #猜 888 吧。",
    "🔢🛑 先把猜测整理成 0-999 的整数，再交给糖糖判断。",
)


@dataclass(frozen=True, slots=True)
class GameEvent:
    group_id: int
    game_type: str
    text: str
    kind: str
    ended: bool = False
    announce: bool = True
    mute_user_ids: tuple[int, ...] = ()
    half_mute_user_ids: tuple[int, ...] = ()
    mention_user_ids: tuple[int, ...] = ()
    mention_replacements: tuple[tuple[str, int], ...] = ()


class MiniGameService:
    """Persistent, group-scoped game state with database-backed scoreboards."""

    def __init__(
        self, database: Database, cursed_guess_numbers: Iterable[int] | None = None
    ) -> None:
        self.database = database
        self._guess_not_ready_cursor = 0
        values = cursed_guess_numbers if cursed_guess_numbers is not None else (510, 731, 444, 33)
        self.cursed_guess_numbers = frozenset(
            int(value) for value in values if 0 <= int(value) <= 999
        )

    def _next_guess_not_ready_text(self) -> str:
        index = self._guess_not_ready_cursor % len(GUESS_NOT_READY_TEXTS)
        self._guess_not_ready_cursor += 1
        return GUESS_NOT_READY_TEXTS[index]

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value).astimezone(timezone.utc)

    @staticmethod
    def _state(row: sqlite3.Row) -> dict[str, Any]:
        try:
            loaded = json.loads(str(row["state_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _name(nickname: str) -> str:
        return nickname.strip()[:40] or "这位群友"

    @staticmethod
    def _phrase(choices: tuple[str, ...], **values: object) -> str:
        return secrets.choice(choices).format(**values)

    @staticmethod
    def _with_idiom_confirmation(event: GameEvent, idiom: str | None, entertainment: bool = False) -> GameEvent:
        if not idiom:
            return event
        return GameEvent(
            event.group_id,
            event.game_type,
            f"{'🎮✅ 四字词' if entertainment else '🀄✅ 成语'}「{idiom}」接龙成功。\n{event.text}",
            event.kind,
            ended=event.ended,
            announce=event.announce,
            mute_user_ids=event.mute_user_ids,
            half_mute_user_ids=event.half_mute_user_ids,
            mention_user_ids=event.mention_user_ids,
            mention_replacements=event.mention_replacements,
        )

    @staticmethod
    def _with_event_prefix(event: GameEvent, prefix: str) -> GameEvent:
        if not prefix:
            return event
        return GameEvent(
            event.group_id,
            event.game_type,
            f"{prefix}\n{event.text}",
            event.kind,
            ended=event.ended,
            announce=event.announce,
            mute_user_ids=event.mute_user_ids,
            half_mute_user_ids=event.half_mute_user_ids,
            mention_user_ids=event.mention_user_ids,
            mention_replacements=event.mention_replacements,
        )

    @staticmethod
    def _with_mentions(event: GameEvent, *user_ids: int) -> GameEvent:
        mentions = tuple(dict.fromkeys((*event.mention_user_ids, *(int(value) for value in user_ids if int(value) > 0))))
        return GameEvent(
            event.group_id,
            event.game_type,
            event.text,
            event.kind,
            ended=event.ended,
            announce=event.announce,
            mute_user_ids=event.mute_user_ids,
            half_mute_user_ids=event.half_mute_user_ids,
            mention_user_ids=mentions,
            mention_replacements=event.mention_replacements,
        )

    @staticmethod
    def _is_entertainment_idiom_mode(state: dict[str, Any]) -> bool:
        return str(state.get("bomb_idiom_ruleset", "professional")) == "entertainment"

    def _is_valid_chain_word(self, state: dict[str, Any], value: str | None) -> bool:
        return is_valid_idiom(value) or (
            self._is_entertainment_idiom_mode(state) and is_valid_four_character_word(value)
        )

    def _chain_words(self, state: dict[str, Any]) -> frozenset[str]:
        return idiom_words() | four_character_words() if self._is_entertainment_idiom_mode(state) else idiom_words()

    def _idiom_state_words(self, state: dict[str, Any], key: str) -> list[str]:
        values = state.get(key)
        if isinstance(values, list):
            return [value for value in values if isinstance(value, str) and self._is_valid_chain_word(state, value)]
        if key == "idiom_used":
            last = state.get("idiom_last")
            return [last] if isinstance(last, str) and self._is_valid_chain_word(state, last) else []
        return []

    def _idiom_unavailable_words(self, state: dict[str, Any]) -> set[str]:
        return set(self._idiom_state_words(state, "idiom_used")) | set(
            self._idiom_state_words(state, "idiom_banned")
        )

    def _has_unused_idiom_starting_with(self, state: dict[str, Any], initial: str) -> bool:
        unavailable = self._idiom_unavailable_words(state)
        return any(word.startswith(initial) and word not in unavailable for word in self._chain_words(state))

    @staticmethod
    def _session_phrase(
        state: dict[str, Any], state_key: str, choices: tuple[str, ...], **values: object
    ) -> str:
        """Select each phrase once per session before replenishing its pool."""
        remaining = state.get(state_key)
        if not isinstance(remaining, list) or any(
            not isinstance(index, int) or not 0 <= index < len(choices) for index in remaining
        ):
            remaining = list(range(len(choices)))
        else:
            remaining = list(remaining)
        if not remaining:
            remaining = list(range(len(choices)))
        index = remaining.pop(secrets.randbelow(len(remaining)))
        state[state_key] = remaining
        return choices[index].format(**values)

    def _dice_comment(self, value: int) -> str:
        point = int(value)
        if point in DICE_SPECIAL_COMMENTS:
            return self._phrase(DICE_SPECIAL_COMMENTS[point])
        if point <= 20:
            return self._phrase(DICE_LOW_COMMENTS)
        if point <= 49:
            return self._phrase(DICE_MID_LOW_COMMENTS)
        if point <= 79:
            return self._phrase(DICE_MID_COMMENTS)
        if point <= 109:
            return self._phrase(DICE_HIGH_COMMENTS)
        return self._phrase(DICE_TOP_COMMENTS)

    def _random_event_state(self, game_type: str, now: datetime) -> dict[str, object]:
        minimum, maximum = RANDOM_EVENT_COUNT_RANGES[game_type]
        total = minimum + secrets.randbelow(maximum - minimum + 1)
        first_min, first_max = RANDOM_EVENT_FIRST_DELAY_RANGES[game_type]
        delay = first_min + secrets.randbelow(first_max - first_min + 1)
        return {
            "random_event_at": self._timestamp(now + timedelta(seconds=delay)),
            "random_event_type": None,
            "random_event_checked": False,
            "random_event_total": total,
            "random_event_count": 0,
            "random_event_actions_since_last": 0,
            "random_event_history": [],
        }

    @staticmethod
    def _random_event_total(game_type: str, state: dict[str, Any]) -> int:
        """Keep pre-change sessions valid while bounding malformed stored totals."""
        if game_type == BOMB and bool(state.get("bomb_idiom_mode")):
            duration = max(1, int(state.get("bomb_duration_seconds", GAME_DURATION.total_seconds()) or 1))
            return max(1, duration // BOMB_IDIOM_RANDOM_EVENT_INTERVAL_SECONDS)
        _minimum, maximum = RANDOM_EVENT_COUNT_RANGES[game_type]
        return min(maximum, max(1, int(state.get("random_event_total", 1) or 1)))

    @staticmethod
    def _random_event_count(state: dict[str, Any]) -> int:
        return max(0, int(state.get("random_event_count", 0) or 0))

    @staticmethod
    def _record_random_event_interaction(state: dict[str, Any]) -> None:
        if int(state.get("random_event_count", 0) or 0) > 0:
            state["random_event_actions_since_last"] = (
                int(state.get("random_event_actions_since_last", 0) or 0) + 1
            )

    def _schedule_next_random_event(
        self, game_type: str, state: dict[str, Any], current: datetime
    ) -> None:
        count = self._random_event_count(state)
        if count >= self._random_event_total(game_type, state):
            state.update({"random_event_at": None, "random_event_type": None})
            return
        if game_type == BOMB and bool(state.get("bomb_idiom_mode")):
            delay = BOMB_IDIOM_RANDOM_EVENT_INTERVAL_SECONDS
        else:
            interval_min, interval_max = RANDOM_EVENT_INTERVAL_RANGES[game_type]
            delay = interval_min + secrets.randbelow(interval_max - interval_min + 1)
        state.update(
            {
                "random_event_at": self._timestamp(current + timedelta(seconds=delay)),
                "random_event_type": None,
                "random_event_actions_since_last": 0,
            }
        )

    @staticmethod
    def _dice_regrets(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw = state.get("dice_regrets")
        if not isinstance(raw, dict):
            raw = {}
            state["dice_regrets"] = raw
        return {
            str(user_id): dict(value)
            for user_id, value in raw.items()
            if str(user_id).isdigit() and isinstance(value, dict)
        }

    def _has_dice_regret(self, state: dict[str, Any], user_id: int) -> bool:
        entry = self._dice_regrets(state).get(str(user_id))
        if entry is not None:
            return not bool(entry.get("resolved"))
        return (
            str(state.get("random_event_triggered", "")) == "dice_regret"
            and int(state.get("dice_regret_user_id", 0) or 0) == int(user_id)
            and not bool(state.get("dice_regret_resolved"))
        )

    def _resolve_dice_regret(self, state: dict[str, Any], user_id: int) -> None:
        regrets = self._dice_regrets(state)
        entry = regrets.get(str(user_id))
        if entry is not None:
            entry["resolved"] = True
            regrets[str(user_id)] = entry
            state["dice_regrets"] = regrets
        if int(state.get("dice_regret_user_id", 0) or 0) == int(user_id):
            state["dice_regret_resolved"] = True

    def _select_random_event_type(
        self,
        game_type: str,
        state: dict[str, Any],
        participants: list[sqlite3.Row],
    ) -> str | None:
        candidates = list(RANDOM_EVENT_TYPES.get(game_type, ()))
        if game_type == ROULETTE and not participants:
            candidates = [
                event_type
                for event_type in candidates
                if event_type not in {"roulette_bond", "roulette_aim_drift"}
            ]
        if game_type == ROULETTE:
            shots = int(state.get("shots", 0) or 0)
            bullet_at = int(state.get("bullet_at", 0) or 0)
            if not [slot for slot in range(shots + 1, 7) if slot != bullet_at]:
                candidates = [event_type for event_type in candidates if event_type != "roulette_shift"]
            # The burst weapons need three prior main-gun shots to have a
            # meaningful firing order. They can therefore affect shot four onward.
            if shots < 3:
                candidates = [
                    event_type
                    for event_type in candidates
                    if event_type not in {"roulette_burst_revolver", "roulette_burst_rifle"}
                ]
        if game_type == BOMB:
            idiom_events = {
                "bomb_idiom_echo_anchor",
                "bomb_idiom_first_char",
                "bomb_idiom_rewrite",
                "bomb_idiom_free_start",
                "bomb_idiom_lonely_start",
            }
            if not bool(state.get("bomb_idiom_mode")):
                candidates = [event_type for event_type in candidates if event_type not in idiom_events]
            else:
                used = self._idiom_state_words(state, "idiom_used")
                if len(used) < 4:
                    candidates = [event_type for event_type in candidates if event_type != "bomb_idiom_echo_anchor"]
            holder_id = int(state.get("holder_id", 0) or 0)
            if not [row for row in participants if int(row["user_id"]) != holder_id]:
                candidates = [event_type for event_type in candidates if event_type != "bomb_tracking"]
            non_holders = [row for row in participants if int(row["user_id"]) != holder_id]
            if len(non_holders) < 2:
                candidates = [event_type for event_type in candidates if event_type != "bomb_black_hole"]
            previous_holder = int(state.get("bomb_previous_holder_id", 0) or 0)
            if previous_holder <= 0 or previous_holder == holder_id:
                candidates = [event_type for event_type in candidates if event_type != "bomb_reverse_delivery"]
        if game_type == DICE:
            regrets = self._dice_regrets(state)
            eligible_regret = [
                row
                for row in participants
                if str(int(row["user_id"])) not in regrets
            ]
            if not eligible_regret:
                candidates = [event_type for event_type in candidates if event_type != "dice_regret"]
            if len(participants) < 2:
                candidates = [
                    event_type
                    for event_type in candidates
                    if event_type not in {"dice_fate_swap", "dice_comeback"}
                ]
            available = self._available_dice_values(participants)
            if len(available) < 2:
                candidates = [
                    event_type
                    for event_type in candidates
                    if event_type not in {"dice_double_luck", "dice_double_misfortune"}
                ]
            used_values = {int(row["dice_value"]) for row in participants if row["dice_value"] is not None}
            if not any(121 - int(row["dice_value"]) not in used_values for row in participants):
                candidates = [event_type for event_type in candidates if event_type != "dice_mirror"]
            if not any(
                neighbor in available
                for row in participants
                for neighbor in (int(row["dice_value"]) - 1, int(row["dice_value"]) + 1)
            ):
                candidates = [event_type for event_type in candidates if event_type != "dice_adjacent"]
            if participants:
                lowest = min(int(row["dice_value"]) for row in participants)
                highest = max(int(row["dice_value"]) for row in participants)
                if not any(value > lowest for value in available):
                    candidates = [event_type for event_type in candidates if event_type != "dice_relief"]
                if not any(value < highest for value in available):
                    candidates = [event_type for event_type in candidates if event_type != "dice_tax"]
            if not any(value < int(row["dice_value"]) for row in participants for value in available):
                candidates = [event_type for event_type in candidates if event_type != "dice_half"]
            if not any(91 <= value <= 120 for value in available):
                candidates = [event_type for event_type in candidates if event_type != "dice_high_platform"]
            if not any(1 <= value <= 30 for value in available):
                candidates = [event_type for event_type in candidates if event_type != "dice_abyss"]
            if not any(121 - value in available for value in available):
                candidates = [event_type for event_type in candidates if event_type != "dice_twin"]
        if game_type == GUESS and not isinstance(state.get("last_wrong_guess"), dict):
            candidates = [event_type for event_type in candidates if event_type != "guess_temperature"]
        configured = str(state.get("random_event_type") or "")
        if configured in candidates:
            return configured
        if game_type == ROULETTE and "roulette_barrel_burst" in candidates:
            if secrets.randbelow(100) < ROULETTE_BARREL_BURST_CHANCE_PERCENT:
                return "roulette_barrel_burst"
            candidates = [
                event_type for event_type in candidates if event_type != "roulette_barrel_burst"
            ]
        return secrets.choice(candidates) if candidates else None

    @staticmethod
    def _available_dice_values(
        participants: list[sqlite3.Row], excluded_user_id: int | None = None
    ) -> list[int]:
        used = {
            int(row["dice_value"])
            for row in participants
            if row["dice_value"] is not None
            and (excluded_user_id is None or int(row["user_id"]) != int(excluded_user_id))
        }
        return [value for value in range(1, 121) if value not in used]

    def _rerolled_dice_values(
        self, participants: list[sqlite3.Row], user_id: int, old_value: int
    ) -> list[int]:
        return [
            value
            for value in self._available_dice_values(participants, user_id)
            if value != int(old_value)
        ]

    def _draw_dice_value(
        self, available: list[int], state: dict[str, Any], nickname: str
    ) -> tuple[int, str, str]:
        """Draw a unique die result, resolving one pending dice event when present."""
        if not available:
            raise ValueError("at least one dice value must be available")
        pool = list(available)
        texts: list[str] = []
        kind = "dice_roll"
        range_mode = str(state.get("dice_range_pending") or "")
        if range_mode == "high":
            pool = [value for value in pool if 91 <= value <= 120]
        elif range_mode == "abyss":
            pool = [value for value in pool if 1 <= value <= 30]
        if range_mode and pool:
            state["dice_range_pending"] = False
        elif range_mode:
            state["dice_range_pending"] = False
            pool = list(available)

        twin_pending = bool(state.get("dice_twin_pending"))
        if twin_pending:
            paired = [value for value in pool if 121 - value in available]
            if paired:
                pool = paired
            else:
                state["dice_twin_pending"] = False
                twin_pending = False

        if bool(state.get("dice_double_pending")) and len(pool) >= 2:
            first_index = secrets.randbelow(len(pool))
            first_value = pool[first_index]
            second_pool = pool[:first_index] + pool[first_index + 1 :]
            second_value = second_pool[secrets.randbelow(len(second_pool))]
            mode = str(state.get("dice_double_mode") or "luck")
            value = max(first_value, second_value) if mode == "luck" else min(first_value, second_value)
            state["dice_double_pending"] = False
            state.pop("dice_double_mode", None)
            double_texts = DICE_DOUBLE_LUCK_TRIGGER_TEXTS if mode == "luck" else DICE_DOUBLE_MISFORTUNE_TRIGGER_TEXTS
            kind = "dice_double_luck_triggered" if mode == "luck" else "dice_double_misfortune_triggered"
            texts.append(
                self._phrase(
                    double_texts,
                    name=self._name(nickname),
                    first_value=first_value,
                    second_value=second_value,
                    value=value,
                )
            )
        else:
            value = pool[secrets.randbelow(len(pool))]

        if range_mode:
            range_texts = DICE_HIGH_PLATFORM_TRIGGER_TEXTS if range_mode == "high" else DICE_ABYSS_TRIGGER_TEXTS
            kind = "dice_high_platform_triggered" if range_mode == "high" else "dice_abyss_triggered"
            texts.insert(0, self._phrase(range_texts, name=self._name(nickname), value=value))
        if twin_pending:
            old_value = value
            value = 121 - old_value
            state["dice_twin_pending"] = False
            kind = "dice_twin_triggered"
            texts.append(
                self._phrase(
                    DICE_TWIN_TRIGGER_TEXTS,
                    name=self._name(nickname),
                    old_value=old_value,
                    new_value=value,
                )
            )
        return value, "\n".join(texts), kind

    def _active(self, connection: sqlite3.Connection, group_id: int) -> sqlite3.Row | None:
        return connection.execute(
            """SELECT * FROM mini_game_sessions
               WHERE group_id=? AND status='active'
               ORDER BY session_id DESC LIMIT 1""",
            (int(group_id),),
        ).fetchone()

    def _participants(self, connection: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """SELECT * FROM mini_game_participants
                   WHERE session_id=? ORDER BY action_order,user_id""",
                (int(session_id),),
            )
        )

    def _create(
        self,
        connection: sqlite3.Connection,
        group_id: int,
        game_type: str,
        creator_id: int,
        state: dict[str, Any],
        now: datetime,
        duration: timedelta = GAME_DURATION,
    ) -> sqlite3.Row | None:
        try:
            cursor = connection.execute(
                """INSERT INTO mini_game_sessions
                   (group_id,game_type,status,creator_id,started_at,ends_at,state_json)
                   VALUES (?,?,'active',?,?,?,?)""",
                (
                    int(group_id),
                    game_type,
                    int(creator_id),
                    self._timestamp(now),
                    self._timestamp(now + duration),
                    json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                ),
            )
        except sqlite3.IntegrityError:
            return None
        return connection.execute(
            "SELECT * FROM mini_game_sessions WHERE session_id=?", (int(cursor.lastrowid),)
        ).fetchone()

    def _add_participant(
        self,
        connection: sqlite3.Connection,
        session_id: int,
        user_id: int,
        nickname: str,
        action_order: int,
        dice_value: int | None,
        now: datetime,
    ) -> bool:
        try:
            connection.execute(
                """INSERT INTO mini_game_participants
                   (session_id,user_id,nickname,action_order,dice_value,joined_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    int(session_id),
                    int(user_id),
                    self._name(nickname),
                    int(action_order),
                    dice_value,
                    self._timestamp(now),
                ),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def _write_state(
        self, connection: sqlite3.Connection, session_id: int, state: dict[str, Any]
    ) -> None:
        connection.execute(
            "UPDATE mini_game_sessions SET state_json=? WHERE session_id=? AND status='active'",
            (json.dumps(state, ensure_ascii=False, separators=(",", ":")), int(session_id)),
        )

    def _bomb_holder_since(self, session: sqlite3.Row, state: dict[str, Any]) -> datetime:
        value = state.get("holder_since_at") or state.get("last_passed_at") or session["started_at"]
        return self._parse_timestamp(str(value))

    def _bomb_holder_expired(
        self, session: sqlite3.Row, state: dict[str, Any], current: datetime
    ) -> bool:
        return bool(state.get("bomb_idiom_mode")) and (
            self._bomb_holder_since(session, state) + BOMB_IDIOM_HOLDER_DURATION <= current
        )

    def _increase_stats(
        self,
        connection: sqlite3.Connection,
        group_id: int,
        user_id: int,
        nickname: str,
        **increments: int,
    ) -> None:
        columns = (
            "roulette_deaths",
            "roulette_games",
            "bomb_deaths",
            "bomb_passes",
            "bomb_games",
            "dice_highs",
            "dice_lows",
            "dice_games",
            "guess_wins",
            "guess_misses",
            "guess_games",
        )
        values = [max(0, int(increments.get(column, 0))) for column in columns]
        updates = ", ".join(f"{column}=mini_game_stats.{column}+excluded.{column}" for column in columns)
        connection.execute(
            f"""INSERT INTO mini_game_stats
                (group_id,user_id,nickname,{','.join(columns)},updated_at)
                VALUES (?,?,?,{','.join('?' for _ in columns)},?)
                ON CONFLICT(group_id,user_id) DO UPDATE SET
                  nickname=excluded.nickname, {updates}, updated_at=excluded.updated_at""",
            (int(group_id), int(user_id), self._name(nickname), *values, self._timestamp(self.now())),
        )

    def _end(
        self,
        connection: sqlite3.Connection,
        session: sqlite3.Row,
        now: datetime,
        reason: str,
    ) -> GameEvent | None:
        session_id = int(session["session_id"])
        group_id = int(session["group_id"])
        game_type = str(session["game_type"])
        state = self._state(session)
        participants = self._participants(connection, session_id)
        result: dict[str, Any] = {"reason": reason}
        claimed = connection.execute(
            """UPDATE mini_game_sessions SET status='ended',ended_at=?,result_json='{}'
               WHERE session_id=? AND status='active'""",
            (self._timestamp(now), session_id),
        )
        if claimed.rowcount != 1:
            return None

        if game_type == ROULETTE:
            for participant in participants:
                self._increase_stats(
                    connection,
                    group_id,
                    int(participant["user_id"]),
                    str(participant["nickname"]),
                    roulette_games=1,
                )
            if reason == "area_explosion":
                trigger_id = int(state.get("area_explosion_trigger_id", 0) or 0)
                trigger_name = self._name(str(state.get("area_explosion_trigger_name", "")))
                victim_ids = tuple(int(participant["user_id"]) for participant in participants)
                for participant in participants:
                    self._increase_stats(
                        connection,
                        group_id,
                        int(participant["user_id"]),
                        str(participant["nickname"]),
                        roulette_deaths=1,
                    )
                result.update(
                    {
                        "trigger_id": trigger_id,
                        "trigger_name": trigger_name,
                        "victim_ids": list(victim_ids),
                    }
                )
                return GameEvent(
                    group_id,
                    ROULETTE,
                    self._phrase(ROULETTE_AREA_EXPLOSION_TRIGGER_TEXTS, name=trigger_name),
                    "roulette_area_explosion_triggered",
                    ended=True,
                    mute_user_ids=victim_ids,
                    half_mute_user_ids=tuple(user_id for user_id in victim_ids if user_id != trigger_id),
                    mention_user_ids=victim_ids,
                    mention_replacements=((trigger_name, trigger_id),),
                )
            if reason == "hit":
                victim_id = int(state.get("victim_id", 0))
                victim_name = self._name(str(state.get("victim_name", "")))
                victims = [(victim_id, victim_name)]
                bonded_rows = state.get("roulette_bonds")
                if not isinstance(bonded_rows, list):
                    bonded_rows = []
                if not bonded_rows and str(state.get("random_event_triggered", "")) == "roulette_bond":
                    bonded_rows = [
                        {
                            "user_id": int(state.get("random_event_bonded_id", 0) or 0),
                            "name": self._name(str(state.get("random_event_bonded_name", ""))),
                        }
                    ]
                known_victims = {victim_id}
                bonded_names: list[str] = []
                for bonded in bonded_rows:
                    if not isinstance(bonded, dict):
                        continue
                    bonded_id = int(bonded.get("user_id", 0) or 0)
                    bonded_name = self._name(str(bonded.get("name", "")))
                    if bonded_id <= 0 or bonded_id in known_victims:
                        continue
                    known_victims.add(bonded_id)
                    victims.append((bonded_id, bonded_name))
                    bonded_names.append(bonded_name)
                for defeated_id, defeated_name in victims:
                    participant = next(
                        (row for row in participants if int(row["user_id"]) == defeated_id), None
                    )
                    if participant is not None:
                        self._increase_stats(
                            connection,
                            group_id,
                            defeated_id,
                            str(participant["nickname"]),
                            roulette_deaths=1,
                        )
                result["victim_id"] = victim_id
                result["victim_name"] = victim_name
                result["victim_ids"] = [defeated_id for defeated_id, _name in victims]
                if len(victims) > 1:
                    text = self._phrase(
                        ROULETTE_BOND_HIT_TEXTS,
                        victim=victim_name,
                        bonded="、".join(bonded_names),
                    )
                elif bool(state.get("roulette_aim_drift")):
                    text = self._phrase(
                        ROULETTE_AIM_DRIFT_HIT_TEXTS,
                        shooter=self._name(str(state.get("roulette_aim_drift_shooter_name", ""))),
                        victim=victim_name,
                    )
                elif bool(state.get("roulette_reflected")):
                    text = self._phrase(
                        ROULETTE_REFLECTION_HIT_TEXTS,
                        shooter=self._name(str(state.get("reflection_shooter_name", ""))),
                        victim=victim_name,
                    )
                else:
                    text = self._phrase(ROULETTE_HIT_TEXTS, name=victim_name)
                kind = "roulette_hit"
            else:
                text = self._phrase(ROULETTE_TIMEOUT_TEXTS)
                kind = "roulette_safe"

        elif game_type == BOMB:
            for participant in participants:
                self._increase_stats(
                    connection,
                    group_id,
                    int(participant["user_id"]),
                    str(participant["nickname"]),
                    bomb_games=1,
                    bomb_passes=int(participant["pass_count"]),
                )
            holder_id = int(state.get("holder_id", 0))
            holder_name = self._name(str(state.get("holder_name", "")))
            holder = next((row for row in participants if int(row["user_id"]) == holder_id), None)
            if holder is not None:
                self._increase_stats(
                    connection,
                    group_id,
                    holder_id,
                    str(holder["nickname"]),
                    bomb_deaths=1,
                )
            result["holder_id"] = holder_id
            result["holder_name"] = holder_name
            if reason == "holder_timeout":
                text = self._phrase(BOMB_IDIOM_HOLDER_TIMEOUT_TEXTS, name=holder_name)
                kind = "bomb_holder_timeout"
            else:
                text = self._phrase(BOMB_EXPLODED_TEXTS, name=holder_name)
                kind = "bomb_exploded"

        elif game_type == DICE:
            if len(participants) < 2:
                text = self._phrase(DICE_VOID_TEXTS)
                kind = "dice_void"
            else:
                highest = max(participants, key=lambda row: int(row["dice_value"]))
                lowest = min(participants, key=lambda row: int(row["dice_value"]))
                for participant in participants:
                    self._increase_stats(
                        connection,
                        group_id,
                        int(participant["user_id"]),
                        str(participant["nickname"]),
                        dice_games=1,
                        dice_highs=int(participant["user_id"]) == int(highest["user_id"]),
                        dice_lows=int(participant["user_id"]) == int(lowest["user_id"]),
                    )
                result.update(
                    {
                        "highest_name": str(highest["nickname"]),
                        "highest_value": int(highest["dice_value"]),
                        "lowest_name": str(lowest["nickname"]),
                        "lowest_value": int(lowest["dice_value"]),
                    }
                )
                text = self._phrase(
                    DICE_RESULT_TEXTS,
                    high_name=str(highest["nickname"]),
                    high_value=int(highest["dice_value"]),
                    low_name=str(lowest["nickname"]),
                    low_value=int(lowest["dice_value"]),
                )
                kind = "dice_result"

        elif game_type == GUESS:
            target = int(state.get("target", 0))
            result["target"] = target
            if reason == "hit":
                winner_id = int(state.get("winner_id", 0))
                winner_name = self._name(str(state.get("winner_name", "")))
                result.update({"winner_id": winner_id, "winner_name": winner_name})
                text = self._phrase(GUESS_HIT_TEXTS, name=winner_name, value=target)
                kind = "guess_hit"
            else:
                result["participant_ids"] = [int(row["user_id"]) for row in participants]
                text = self._phrase(
                    GUESS_ATTEMPTS_EXHAUSTED_TEXTS if reason == "attempts_exhausted" else GUESS_TIMEOUT_TEXTS,
                    value=target,
                )
                kind = "guess_attempts_exhausted" if reason == "attempts_exhausted" else "guess_timeout"

        else:
            return None

        connection.execute(
            """UPDATE mini_game_sessions
               SET result_json=?
               WHERE session_id=?""",
            (
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                session_id,
            ),
        )
        mute_ids: tuple[int, ...] = ()
        if game_type == ROULETTE and kind == "roulette_hit":
            mute_ids = tuple(int(value) for value in result.get("victim_ids", ()) if int(value) > 0)
        elif game_type == BOMB and kind in {"bomb_exploded", "bomb_holder_timeout"}:
            holder_id = int(result.get("holder_id", 0) or 0)
            mute_ids = (holder_id,) if holder_id > 0 else ()
        elif game_type == DICE and kind == "dice_result":
            lowest_id = next(
                (int(row["user_id"]) for row in participants if int(row["dice_value"]) == int(result["lowest_value"])),
                0,
            )
            mute_ids = (lowest_id,) if lowest_id > 0 else ()
        elif game_type == GUESS and kind in {"guess_attempts_exhausted", "guess_timeout"}:
            mute_ids = tuple(int(value) for value in result.get("participant_ids", ()) if int(value) > 0)
        mention_ids: tuple[int, ...] = ()
        mention_replacements: tuple[tuple[str, int], ...] = ()
        if game_type == ROULETTE and kind == "roulette_hit":
            mention_ids = mute_ids
            mention_replacements = tuple((name, user_id) for user_id, name in victims)
            if bool(state.get("roulette_reflected")):
                shooter_id = int(state.get("reflection_shooter_id", 0) or 0)
                shooter_name = self._name(str(state.get("reflection_shooter_name", "")))
                if shooter_id > 0:
                    mention_replacements = (*mention_replacements, (shooter_name, shooter_id))
            if bool(state.get("roulette_aim_drift")):
                shooter_id = int(state.get("roulette_aim_drift_shooter_id", 0) or 0)
                shooter_name = self._name(str(state.get("roulette_aim_drift_shooter_name", "")))
                if shooter_id > 0:
                    mention_replacements = (*mention_replacements, (shooter_name, shooter_id))
        elif game_type == BOMB and kind in {"bomb_exploded", "bomb_holder_timeout"}:
            mention_ids = mute_ids
            mention_replacements = ((str(result.get("holder_name", "")), int(result.get("holder_id", 0))),)
        elif game_type == DICE and kind == "dice_result":
            mention_ids = tuple(
                int(row["user_id"])
                for row in participants
                if int(row["user_id"]) in {
                    next((int(item["user_id"]) for item in participants if int(item["dice_value"]) == int(result["lowest_value"])), 0),
                    next((int(item["user_id"]) for item in participants if int(item["dice_value"]) == int(result["highest_value"])), 0),
                }
            )
            mention_replacements = (
                (str(highest["nickname"]), int(highest["user_id"])),
                (str(lowest["nickname"]), int(lowest["user_id"])),
            )
        elif game_type == GUESS and kind == "guess_hit":
            winner_id = int(result.get("winner_id", 0) or 0)
            mention_ids = (winner_id,) if winner_id > 0 else ()
            mention_replacements = ((str(result.get("winner_name", "")), winner_id),)
        elif game_type == GUESS and kind in {"guess_attempts_exhausted", "guess_timeout"}:
            mention_ids = mute_ids
            mention_replacements = tuple(
                (str(participant["nickname"]), int(participant["user_id"]))
                for participant in participants
                if int(participant["user_id"]) in mute_ids
            )
        return GameEvent(
            group_id,
            game_type,
            text,
            kind,
            ended=True,
            mute_user_ids=mute_ids,
            mention_user_ids=mention_ids,
            mention_replacements=mention_replacements,
        )

    def expire_due(self, now: datetime | None = None, group_id: int | None = None) -> list[GameEvent]:
        current = now or self.now()
        due: list[GameEvent] = []
        with self.database.connect() as connection:
            group_clause = " AND group_id=?" if group_id is not None else ""
            parameters = (self._timestamp(current), BOMB) + ((int(group_id),) if group_id is not None else ())
            sessions = list(
                connection.execute(
                    """SELECT * FROM mini_game_sessions
                       WHERE status='active' AND (ends_at<=? OR game_type=?)
                    """ + group_clause + " ORDER BY session_id",
                    parameters,
                )
            )
            for session in sessions:
                state = self._state(session)
                if self._parse_timestamp(str(session["ends_at"])) <= current:
                    reason = "timeout"
                elif self._bomb_holder_expired(session, state, current):
                    reason = "holder_timeout"
                else:
                    continue
                event = self._end(connection, session, current, reason)
                if event is not None:
                    due.append(event)
        return due

    def trigger_due_random_events(
        self, now: datetime | None = None, group_id: int | None = None
    ) -> list[GameEvent]:
        """Resolve due random events while enforcing their player-action gate."""
        current = now or self.now()
        due: list[GameEvent] = []
        with self.database.connect() as connection:
            group_clause = " AND group_id=?" if group_id is not None else ""
            game_types = tuple(RANDOM_EVENT_TYPES)
            parameters: tuple[object, ...] = game_types
            if group_id is not None:
                parameters += (int(group_id),)
            sessions = list(
                connection.execute(
                    """SELECT * FROM mini_game_sessions
                       WHERE status='active' AND game_type IN ("""
                    + ",".join("?" for _ in game_types)
                    + ")"
                    + group_clause
                    + " ORDER BY session_id",
                    parameters,
                )
            )
            for session in sessions:
                state = self._state(session)
                game_type = str(session["game_type"])
                if self._parse_timestamp(str(session["ends_at"])) <= current:
                    continue
                triggered = self._random_event_count(state)
                if triggered >= self._random_event_total(game_type, state):
                    continue
                due_at = state.get("random_event_at")
                if (
                    not isinstance(due_at, str)
                    or self._parse_timestamp(due_at) > current
                ):
                    continue
                if triggered > 0 and int(state.get("random_event_actions_since_last", 0) or 0) < 1:
                    continue
                event = self._trigger_random_event(connection, session, state, current)
                if event is not None:
                    history = state.get("random_event_history")
                    if not isinstance(history, list):
                        history = []
                    history.append(event.kind)
                    state.update(
                        {
                            "random_event_checked": True,
                            "random_event_count": triggered + 1,
                            "random_event_history": history,
                        }
                    )
                    self._schedule_next_random_event(game_type, state, current)
                self._write_state(connection, int(session["session_id"]), state)
                if event is not None:
                    due.append(event)
        return due

    def _trigger_random_event(
        self,
        connection: sqlite3.Connection,
        session: sqlite3.Row,
        state: dict[str, Any],
        current: datetime,
    ) -> GameEvent | None:
        session_id = int(session["session_id"])
        group_id = int(session["group_id"])
        participants = self._participants(connection, session_id)
        event_type = self._select_random_event_type(str(session["game_type"]), state, participants)
        if event_type is None:
            return None
        state["random_event_type"] = event_type

        if event_type == "roulette_bond":
            if not participants:
                return None
            bonded_rows = state.get("roulette_bonds")
            if not isinstance(bonded_rows, list):
                bonded_rows = []
            marked_ids = {
                int(row.get("user_id", 0) or 0)
                for row in bonded_rows
                if isinstance(row, dict)
            }
            eligible = [row for row in participants if int(row["user_id"]) not in marked_ids]
            marked = secrets.choice(eligible or participants)
            name = self._name(str(marked["nickname"]))
            bonded_rows.append({"user_id": int(marked["user_id"]), "name": name})
            state.update(
                {
                    "random_event_triggered": event_type,
                    "random_event_bonded_id": int(marked["user_id"]),
                    "random_event_bonded_name": name,
                    "roulette_bonds": bonded_rows,
                }
            )
            return GameEvent(
                group_id,
                ROULETTE,
                self._phrase(ROULETTE_BOND_EVENT_TEXTS, name=name),
                "roulette_bond",
                mention_replacements=((name, int(marked["user_id"])),),
            )

        if event_type == "roulette_reflection":
            state.update({"random_event_triggered": event_type, "roulette_reflection_active": True})
            return GameEvent(
                group_id,
                ROULETTE,
                self._phrase(ROULETTE_REFLECTION_EVENT_TEXTS),
                "roulette_reflection",
            )

        if event_type == "roulette_shift":
            shots = int(state.get("shots", 0) or 0)
            bullet_at = int(state.get("bullet_at", 6) or 6)
            slots = [slot for slot in range(shots + 1, 7) if slot != bullet_at]
            if not slots:
                return None
            state.update(
                {
                    "random_event_triggered": event_type,
                    "bullet_at": secrets.choice(slots),
                }
            )
            return GameEvent(
                group_id,
                ROULETTE,
                self._phrase(ROULETTE_SHIFT_EVENT_TEXTS),
                "roulette_shift",
            )

        if event_type == "roulette_misfire":
            state.update({"random_event_triggered": event_type, "roulette_misfire_pending": True})
            return GameEvent(group_id, ROULETTE, "", "roulette_misfire", announce=False)

        if event_type == "roulette_finger_cramp":
            state.update({"random_event_triggered": event_type, "roulette_finger_cramp_pending": True})
            return GameEvent(group_id, ROULETTE, "", "roulette_finger_cramp", announce=False)

        if event_type == "roulette_burst_revolver":
            state.update(
                {
                    "random_event_triggered": event_type,
                    "roulette_burst_revolver_pending": True,
                    "roulette_burst_bullet_at": secrets.randbelow(6) + 1,
                }
            )
            return GameEvent(group_id, ROULETTE, "", "roulette_burst_revolver", announce=False)

        if event_type == "roulette_burst_rifle":
            bullet_count = secrets.randbelow(6) + 1
            state.update(
                {
                    "random_event_triggered": event_type,
                    "roulette_burst_rifle_pending": True,
                    "roulette_burst_rifle_bullet_count": bullet_count,
                    "roulette_burst_rifle_bullet_slots": sorted(
                        secrets.SystemRandom().sample(range(1, 7), bullet_count)
                    ),
                }
            )
            return GameEvent(group_id, ROULETTE, "", "roulette_burst_rifle", announce=False)

        if event_type == "roulette_aim_drift":
            state.update({"random_event_triggered": event_type, "roulette_aim_drift_pending": True})
            return GameEvent(group_id, ROULETTE, "", "roulette_aim_drift", announce=False)

        if event_type == "roulette_barrel_burst":
            state.update({"random_event_triggered": event_type, "roulette_barrel_burst_pending": True})
            return GameEvent(group_id, ROULETTE, "", "roulette_barrel_burst", announce=False)

        if event_type == "roulette_area_explosion":
            state.update({"random_event_triggered": event_type, "roulette_area_explosion_pending": True})
            return GameEvent(group_id, ROULETTE, "", "roulette_area_explosion", announce=False)

        if event_type == "bomb_hot_hand":
            ends_at = self._parse_timestamp(str(session["ends_at"]))
            new_ends_at = max(current + timedelta(seconds=5), ends_at - timedelta(seconds=15))
            connection.execute(
                "UPDATE mini_game_sessions SET ends_at=? WHERE session_id=? AND status='active'",
                (self._timestamp(new_ends_at), session_id),
            )
            state["random_event_triggered"] = event_type
            return GameEvent(group_id, BOMB, self._phrase(BOMB_HOT_HAND_EVENT_TEXTS), "bomb_hot_hand")

        if event_type == "bomb_time_chaos":
            ends_at = self._parse_timestamp(str(session["ends_at"]))
            duration_seconds = int(state.get("bomb_duration_seconds", int(GAME_DURATION.total_seconds())) or 0)
            maximum_end = self._parse_timestamp(str(session["started_at"])) + timedelta(
                seconds=max(1, duration_seconds)
            )
            deltas = [-20, -15] + [
                delta
                for delta in (15, 20)
                if ends_at + timedelta(seconds=delta) <= maximum_end
            ]
            delta = secrets.choice(deltas)
            new_ends_at = max(current + timedelta(seconds=5), ends_at + timedelta(seconds=delta))
            connection.execute(
                "UPDATE mini_game_sessions SET ends_at=? WHERE session_id=? AND status='active'",
                (self._timestamp(new_ends_at), session_id),
            )
            state["random_event_triggered"] = event_type
            return GameEvent(group_id, BOMB, self._phrase(BOMB_TIME_CHAOS_EVENT_TEXTS), "bomb_time_chaos")

        if event_type == "bomb_tracking":
            holder_id = int(state.get("holder_id", 0) or 0)
            targets = [row for row in participants if int(row["user_id"]) != holder_id]
            if not targets:
                return None
            target = secrets.choice(targets)
            target_name = self._name(str(target["nickname"]))
            state.update(
                {
                    "random_event_triggered": event_type,
                    "holder_id": int(target["user_id"]),
                    "holder_name": target_name,
                    "last_passed_at": self._timestamp(current),
                    "holder_since_at": self._timestamp(current),
                }
            )
            return GameEvent(
                group_id,
                BOMB,
                self._phrase(BOMB_TRACKING_EVENT_TEXTS, name=target_name),
                "bomb_tracking",
                mention_replacements=((target_name, int(target["user_id"])),),
            )

        if event_type == "bomb_inertia":
            state.update({"random_event_triggered": event_type, "bomb_inertia_pending": True})
            return GameEvent(group_id, BOMB, "", "bomb_inertia", announce=False)

        if event_type == "bomb_black_hole":
            state.update({"random_event_triggered": event_type, "bomb_black_hole_pending": True})
            return GameEvent(group_id, BOMB, "", "bomb_black_hole", announce=False)

        if event_type == "bomb_reverse_delivery":
            state.update({"random_event_triggered": event_type, "bomb_reverse_delivery_pending": True})
            return GameEvent(group_id, BOMB, "", "bomb_reverse_delivery", announce=False)

        if event_type == "bomb_idiom_echo_anchor":
            used = self._idiom_state_words(state, "idiom_used")
            choices = used[:-1]
            if not choices:
                return None
            anchor = secrets.choice(choices)
            state.update(
                {
                    "random_event_triggered": event_type,
                    "idiom_chain_anchor": anchor,
                    "idiom_next_start_char": None,
                }
            )
            return GameEvent(
                group_id,
                BOMB,
                self._phrase(BOMB_IDIOM_ECHO_ANCHOR_TEXTS, anchor=anchor, expected=anchor[-1]),
                "bomb_idiom_echo_anchor",
            )

        if event_type == "bomb_idiom_first_char":
            state.update({"random_event_triggered": event_type, "bomb_idiom_first_char_pending": True})
            return GameEvent(group_id, BOMB, "", "bomb_idiom_first_char", announce=False)

        if event_type == "bomb_idiom_rewrite":
            state.update({"random_event_triggered": event_type, "bomb_idiom_rewrite_pending": True})
            return GameEvent(group_id, BOMB, "", "bomb_idiom_rewrite", announce=False)

        if event_type == "bomb_idiom_free_start":
            state.update({"random_event_triggered": event_type, "idiom_free_start_pending": True})
            return GameEvent(group_id, BOMB, self._phrase(BOMB_IDIOM_FREE_START_TEXTS), "bomb_idiom_free_start")

        if event_type == "bomb_idiom_lonely_start":
            state.update({"random_event_triggered": event_type, "idiom_lonely_start_pending": True})
            return GameEvent(group_id, BOMB, self._phrase(BOMB_IDIOM_LONELY_START_TEXTS), "bomb_idiom_lonely_start")

        if event_type == "guess_divergent":
            state.update({"random_event_triggered": event_type, "guess_divergent_pending": True})
            return GameEvent(group_id, GUESS, "", "guess_divergent", announce=False)

        if event_type == "guess_temperature":
            wrong = state.get("last_wrong_guess")
            if not isinstance(wrong, dict):
                return None
            value = int(wrong.get("value", -1) or -1)
            target = int(state.get("target", 0) or 0)
            if not 0 <= value <= 999:
                return None
            hint = "距离答案不足 50" if abs(target - value) < 50 else "距离答案超过 50"
            state["random_event_triggered"] = event_type
            return GameEvent(
                group_id,
                GUESS,
                self._phrase(
                    GUESS_TEMPERATURE_EVENT_TEXTS,
                    name=self._name(str(wrong.get("name", ""))),
                    hint=hint,
                ),
                "guess_temperature",
                mention_replacements=((self._name(str(wrong.get("name", ""))), int(wrong.get("user_id", 0) or 0)),),
            )

        if event_type == "guess_echo":
            target = int(state.get("target", 0) or 0)
            digits = f"{target:03d}"
            digit_sum = sum(int(digit) for digit in digits)
            hints = (
                f"答案是{'偶数' if target % 2 == 0 else '奇数'}",
                f"答案三位数字之和是 {digit_sum}",
                f"答案的个位是{'偶数' if int(digits[-1]) % 2 == 0 else '奇数'}",
            )
            state["random_event_triggered"] = event_type
            return GameEvent(
                group_id,
                GUESS,
                self._phrase(GUESS_ECHO_EVENT_TEXTS, hint=secrets.choice(hints)),
                "guess_echo",
            )

        if event_type == "guess_digit_vision":
            target = int(state.get("target", 0) or 0)
            places = (("百位", 0), ("十位", 1), ("个位", 2))
            place, index = secrets.choice(places)
            state["random_event_triggered"] = event_type
            return GameEvent(
                group_id,
                GUESS,
                self._phrase(
                    GUESS_DIGIT_VISION_EVENT_TEXTS,
                    place=place,
                    digit=f"{target:03d}"[index],
                ),
                "guess_digit_vision",
            )

        if not participants:
            return None
        if event_type == "dice_regret":
            regrets = self._dice_regrets(state)
            eligible = [
                row
                for row in participants
                if str(int(row["user_id"])) not in regrets
            ]
            if not eligible:
                return None
            marked = secrets.choice(eligible)
            marked_id = int(marked["user_id"])
            marked_name = self._name(str(marked["nickname"]))
            regrets[str(marked_id)] = {"name": marked_name, "resolved": False}
            state.update(
                {
                    "random_event_triggered": event_type,
                    "dice_regret_user_id": marked_id,
                    "dice_regret_name": marked_name,
                    "dice_regret_resolved": False,
                    "dice_regrets": regrets,
                }
            )
            return GameEvent(
                group_id,
                DICE,
                self._phrase(DICE_REGRET_EVENT_TEXTS, name=marked_name),
                "dice_regret",
                mention_replacements=((marked_name, marked_id),),
            )

        if event_type == "dice_force":
            marked = secrets.choice(participants)
            marked_id = int(marked["user_id"])
            marked_name = self._name(str(marked["nickname"]))
            old_value = int(marked["dice_value"])
            available = self._rerolled_dice_values(participants, marked_id, old_value)
            if not available:
                return None
            new_value = available[secrets.randbelow(len(available))]
            connection.execute(
                """UPDATE mini_game_participants SET dice_value=?
                   WHERE session_id=? AND user_id=?""",
                (new_value, session_id, marked_id),
            )
            state["random_event_triggered"] = event_type
            return GameEvent(
                group_id,
                DICE,
                self._phrase(
                    DICE_FORCE_EVENT_TEXTS,
                    name=marked_name,
                    old_value=old_value,
                    new_value=new_value,
                )
                + "\n"
                + self._dice_comment(new_value),
                "dice_force",
                mention_replacements=((marked_name, marked_id),),
            )

        if event_type == "dice_fate_swap":
            if len(participants) < 2:
                return None
            first = secrets.choice(participants)
            others = [row for row in participants if int(row["user_id"]) != int(first["user_id"])]
            second = secrets.choice(others)
            first_value = int(first["dice_value"])
            second_value = int(second["dice_value"])
            connection.execute(
                "UPDATE mini_game_participants SET dice_value=? WHERE session_id=? AND user_id=?",
                (second_value, session_id, int(first["user_id"])),
            )
            connection.execute(
                "UPDATE mini_game_participants SET dice_value=? WHERE session_id=? AND user_id=?",
                (first_value, session_id, int(second["user_id"])),
            )
            state["random_event_triggered"] = event_type
            return GameEvent(
                group_id,
                DICE,
                self._phrase(
                    DICE_FATE_SWAP_EVENT_TEXTS,
                    first_name=self._name(str(first["nickname"])),
                    second_name=self._name(str(second["nickname"])),
                    first_value=first_value,
                    second_value=second_value,
                ),
                "dice_fate_swap",
                mention_replacements=(
                    (self._name(str(first["nickname"])), int(first["user_id"])),
                    (self._name(str(second["nickname"])), int(second["user_id"])),
                ),
            )

        if event_type == "dice_reverse":
            changes: list[str] = []
            for participant in participants:
                old_value = int(participant["dice_value"])
                new_value = 121 - old_value
                connection.execute(
                    "UPDATE mini_game_participants SET dice_value=? WHERE session_id=? AND user_id=?",
                    (new_value, session_id, int(participant["user_id"])),
                )
                changes.append(
                    f"{self._name(str(participant['nickname']))} {old_value}→{new_value}"
                )
            state["random_event_triggered"] = event_type
            return GameEvent(
                group_id,
                DICE,
                self._phrase(DICE_REVERSE_EVENT_TEXTS, changes="；".join(changes)),
                "dice_reverse",
                mention_replacements=tuple(
                    (self._name(str(participant["nickname"])), int(participant["user_id"]))
                    for participant in participants
                ),
            )

        if event_type == "dice_throne":
            state.update({"random_event_triggered": event_type, "dice_throne_pending": True})
            return GameEvent(group_id, DICE, "", "dice_throne", announce=False)

        if event_type in {"dice_double_luck", "dice_double_misfortune"}:
            state.update(
                {
                    "random_event_triggered": event_type,
                    "dice_double_pending": True,
                    "dice_double_mode": "luck" if event_type == "dice_double_luck" else "misfortune",
                }
            )
            return GameEvent(group_id, DICE, "", event_type, announce=False)

        if event_type == "dice_mirror":
            used_values = {
                int(row["dice_value"])
                for row in participants
                if row["dice_value"] is not None
            }
            eligible = [
                row
                for row in participants
                if 121 - int(row["dice_value"]) not in used_values
            ]
            if not eligible:
                return None
            marked = secrets.choice(eligible)
            old_value = int(marked["dice_value"])
            new_value = 121 - old_value
            connection.execute(
                "UPDATE mini_game_participants SET dice_value=? WHERE session_id=? AND user_id=?",
                (new_value, session_id, int(marked["user_id"])),
            )
            state["random_event_triggered"] = event_type
            return GameEvent(
                group_id,
                DICE,
                self._phrase(
                    DICE_MIRROR_EVENT_TEXTS,
                    name=self._name(str(marked["nickname"])),
                    old_value=old_value,
                    new_value=new_value,
                )
                + "\n"
                + self._dice_comment(new_value),
                "dice_mirror",
                mention_replacements=((self._name(str(marked["nickname"])), int(marked["user_id"])),),
            )

        if event_type == "dice_adjacent":
            eligible: list[tuple[sqlite3.Row, list[int]]] = []
            for participant in participants:
                old_value = int(participant["dice_value"])
                available = self._rerolled_dice_values(participants, int(participant["user_id"]), old_value)
                neighbors = [value for value in (old_value - 1, old_value + 1) if value in available]
                if neighbors:
                    eligible.append((participant, neighbors))
            if not eligible:
                return None
            marked, neighbors = secrets.choice(eligible)
            old_value = int(marked["dice_value"])
            new_value = secrets.choice(neighbors)
            connection.execute(
                "UPDATE mini_game_participants SET dice_value=? WHERE session_id=? AND user_id=?",
                (new_value, session_id, int(marked["user_id"])),
            )
            state["random_event_triggered"] = event_type
            return GameEvent(
                group_id,
                DICE,
                self._phrase(
                    DICE_ADJACENT_EVENT_TEXTS,
                    name=self._name(str(marked["nickname"])),
                    old_value=old_value,
                    new_value=new_value,
                ),
                "dice_adjacent",
                mention_replacements=((self._name(str(marked["nickname"])), int(marked["user_id"])),),
            )

        if event_type in {"dice_relief", "dice_tax"}:
            marked = (
                min(participants, key=lambda row: int(row["dice_value"]))
                if event_type == "dice_relief"
                else max(participants, key=lambda row: int(row["dice_value"]))
            )
            old_value = int(marked["dice_value"])
            available = self._rerolled_dice_values(participants, int(marked["user_id"]), old_value)
            candidates = (
                [value for value in available if value > old_value]
                if event_type == "dice_relief"
                else [value for value in available if value < old_value]
            )
            if not candidates:
                return None
            new_value = candidates[secrets.randbelow(len(candidates))]
            connection.execute(
                "UPDATE mini_game_participants SET dice_value=? WHERE session_id=? AND user_id=?",
                (new_value, session_id, int(marked["user_id"])),
            )
            state["random_event_triggered"] = event_type
            texts = DICE_RELIEF_EVENT_TEXTS if event_type == "dice_relief" else DICE_TAX_EVENT_TEXTS
            return GameEvent(
                group_id,
                DICE,
                self._phrase(
                    texts,
                    name=self._name(str(marked["nickname"])),
                    old_value=old_value,
                    new_value=new_value,
                )
                + "\n"
                + self._dice_comment(new_value),
                event_type,
                mention_replacements=((self._name(str(marked["nickname"])), int(marked["user_id"])),),
            )

        if event_type == "dice_half":
            eligible: list[tuple[sqlite3.Row, list[int]]] = []
            for participant in participants:
                old_value = int(participant["dice_value"])
                available = self._rerolled_dice_values(participants, int(participant["user_id"]), old_value)
                lower_values = [value for value in available if value < old_value]
                if lower_values:
                    eligible.append((participant, lower_values))
            if not eligible:
                return None
            marked, lower_values = secrets.choice(eligible)
            old_value = int(marked["dice_value"])
            half = (old_value + 1) // 2
            new_value = min(lower_values, key=lambda value: (abs(value - half), value))
            connection.execute(
                "UPDATE mini_game_participants SET dice_value=? WHERE session_id=? AND user_id=?",
                (new_value, session_id, int(marked["user_id"])),
            )
            state["random_event_triggered"] = event_type
            return GameEvent(
                group_id,
                DICE,
                self._phrase(
                    DICE_HALF_EVENT_TEXTS,
                    name=self._name(str(marked["nickname"])),
                    old_value=old_value,
                    new_value=new_value,
                )
                + "\n"
                + self._dice_comment(new_value),
                "dice_half",
                mention_replacements=((self._name(str(marked["nickname"])), int(marked["user_id"])),),
            )

        if event_type == "dice_comeback":
            if len(participants) < 2:
                return None
            lowest = min(participants, key=lambda row: int(row["dice_value"]))
            others = [row for row in participants if int(row["user_id"]) != int(lowest["user_id"])]
            other = secrets.choice(others)
            low_value = int(lowest["dice_value"])
            other_value = int(other["dice_value"])
            connection.execute(
                "UPDATE mini_game_participants SET dice_value=? WHERE session_id=? AND user_id=?",
                (other_value, session_id, int(lowest["user_id"])),
            )
            connection.execute(
                "UPDATE mini_game_participants SET dice_value=? WHERE session_id=? AND user_id=?",
                (low_value, session_id, int(other["user_id"])),
            )
            state["random_event_triggered"] = event_type
            return GameEvent(
                group_id,
                DICE,
                self._phrase(
                    DICE_COMEBACK_EVENT_TEXTS,
                    low_name=self._name(str(lowest["nickname"])),
                    low_value=low_value,
                    other_name=self._name(str(other["nickname"])),
                    other_value=other_value,
                ),
                "dice_comeback",
                mention_replacements=(
                    (self._name(str(lowest["nickname"])), int(lowest["user_id"])),
                    (self._name(str(other["nickname"])), int(other["user_id"])),
                ),
            )

        if event_type in {"dice_high_platform", "dice_abyss"}:
            state.update(
                {
                    "random_event_triggered": event_type,
                    "dice_range_pending": "high" if event_type == "dice_high_platform" else "abyss",
                }
            )
            return GameEvent(group_id, DICE, "", event_type, announce=False)

        if event_type == "dice_twin":
            state.update({"random_event_triggered": event_type, "dice_twin_pending": True})
            return GameEvent(group_id, DICE, "", "dice_twin", announce=False)
        return None

    def start_roulette(
        self, group_id: int, user_id: int, nickname: str, now: datetime | None = None
    ) -> GameEvent:
        current = now or self.now()
        with self.database.connect() as connection:
            active = self._active(connection, group_id)
            if active is not None:
                return GameEvent(
                    group_id,
                    ROULETTE,
                    self._phrase(GAME_BUSY_TEXTS, game=GAME_LABELS[str(active["game_type"])]),
                    "busy",
                )
            session = self._create(
                connection,
                group_id,
                ROULETTE,
                user_id,
                {
                    "bullet_at": secrets.randbelow(6) + 1,
                    "shots": 0,
                    "shot_log": [],
                    **self._random_event_state(ROULETTE, current),
                },
                current,
            )
            if session is None:
                return GameEvent(
                    group_id, ROULETTE, self._phrase(GAME_BUSY_TEXTS, game="小游戏"), "busy"
                )
        return GameEvent(
            group_id,
            ROULETTE,
            self._phrase(ROULETTE_STARTED_TEXTS),
            "roulette_started",
        )

    def fire(
        self, group_id: int, user_id: int, nickname: str, now: datetime | None = None
    ) -> GameEvent:
        current = now or self.now()
        with self.database.connect() as connection:
            session = self._active(connection, group_id)
            if session is None:
                return GameEvent(
                    group_id, ROULETTE, self._phrase(ROULETTE_NOT_READY_TEXTS), "no_session"
                )
            if self._parse_timestamp(str(session["ends_at"])) <= current:
                event = self._end(connection, session, current, "timeout")
                return event or GameEvent(
                    group_id, ROULETTE, self._phrase(GAME_ENDED_TEXTS), "ended", ended=True
                )
            if str(session["game_type"]) != ROULETTE:
                return GameEvent(
                    group_id,
                    ROULETTE,
                    self._phrase(
                        ROULETTE_WRONG_GAME_TEXTS,
                        game=GAME_LABELS[str(session["game_type"])],
                    ),
                    "wrong_game",
                )
            state = self._state(session)
            shots = int(state.get("shots", 0))
            if bool(state.get("roulette_misfire_pending")):
                self._add_participant(
                    connection, int(session["session_id"]), user_id, nickname, shots + 1, None, current
                )
                state["roulette_misfire_pending"] = False
                self._record_random_event_interaction(state)
                self._increase_stats(
                    connection,
                    group_id,
                    user_id,
                    nickname,
                    roulette_deaths=1,
                )
                self._write_state(connection, int(session["session_id"]), state)
                return GameEvent(
                    group_id,
                    ROULETTE,
                    self._phrase(ROULETTE_MISFIRE_TRIGGER_TEXTS, name=self._name(nickname)),
                    "roulette_misfire_triggered",
                    mute_user_ids=(int(user_id),),
                    mention_user_ids=(int(user_id),),
                    mention_replacements=((self._name(nickname), int(user_id)),),
                )
            if bool(state.get("roulette_burst_revolver_pending")):
                self._add_participant(
                    connection, int(session["session_id"]), user_id, nickname, shots + 1, None, current
                )
                participants = self._participants(connection, int(session["session_id"]))
                others = [row for row in participants if int(row["user_id"]) != int(user_id)]
                targets = [
                    (int(user_id), self._name(nickname)),
                    *[
                        (int(row["user_id"]), self._name(str(row["nickname"])))
                        for row in reversed(others)
                    ],
                ][:6]
                bullet_at = int(state.get("roulette_burst_bullet_at", 7) or 7)
                state["roulette_burst_revolver_pending"] = False
                self._record_random_event_interaction(state)
                self._write_state(connection, int(session["session_id"]), state)
                trigger_text = self._phrase(
                    ROULETTE_BURST_REVOLVER_TRIGGER_TEXTS, name=self._name(nickname)
                )
                if 1 <= bullet_at <= len(targets):
                    victim_id, victim_name = targets[bullet_at - 1]
                    self._increase_stats(
                        connection, group_id, victim_id, victim_name, roulette_deaths=1
                    )
                    return GameEvent(
                        group_id,
                        ROULETTE,
                        f"{trigger_text}\n"
                        + self._phrase(
                            ROULETTE_BURST_REVOLVER_HIT_TEXTS,
                            shot=bullet_at,
                            victim=victim_name,
                        ),
                        "roulette_burst_revolver_hit",
                        mute_user_ids=(victim_id,),
                        mention_user_ids=(victim_id,),
                        mention_replacements=(
                            (self._name(nickname), int(user_id)),
                            (victim_name, victim_id),
                        ),
                    )
                return GameEvent(
                    group_id,
                    ROULETTE,
                    f"{trigger_text}\n"
                    + self._phrase(ROULETTE_BURST_REVOLVER_SAFE_TEXTS, name=self._name(nickname)),
                    "roulette_burst_revolver_safe",
                    mention_user_ids=tuple(target_id for target_id, _name in targets),
                    mention_replacements=tuple((name, target_id) for target_id, name in targets),
                )

            if bool(state.get("roulette_burst_rifle_pending")):
                self._add_participant(
                    connection, int(session["session_id"]), user_id, nickname, shots + 1, None, current
                )
                participants = self._participants(connection, int(session["session_id"]))
                others = [row for row in participants if int(row["user_id"]) != int(user_id)]
                targets = [
                    (int(user_id), self._name(nickname)),
                    *[
                        (int(row["user_id"]), self._name(str(row["nickname"])))
                        for row in reversed(others)
                    ],
                ][:6]
                bullet_count = int(state.get("roulette_burst_rifle_bullet_count", 1) or 1)
                raw_slots = state.get("roulette_burst_rifle_bullet_slots", ())
                bullet_slots = {
                    int(slot) for slot in raw_slots if isinstance(slot, int) and 1 <= int(slot) <= 6
                } if isinstance(raw_slots, list) else set()
                state["roulette_burst_rifle_pending"] = False
                self._record_random_event_interaction(state)
                self._write_state(connection, int(session["session_id"]), state)
                trigger_text = self._phrase(
                    ROULETTE_BURST_RIFLE_TRIGGER_TEXTS,
                    name=self._name(nickname),
                    bullets=bullet_count,
                )
                hits = [
                    (index, target_id, target_name)
                    for index, (target_id, target_name) in enumerate(targets, 1)
                    if index in bullet_slots
                ]
                if not hits:
                    return GameEvent(
                        group_id,
                        ROULETTE,
                        f"{trigger_text}\n" + self._phrase(ROULETTE_BURST_RIFLE_SAFE_TEXTS),
                        "roulette_burst_rifle_safe",
                        mention_user_ids=tuple(target_id for target_id, _name in targets),
                        mention_replacements=tuple((name, target_id) for target_id, name in targets),
                    )
                for _shot, victim_id, victim_name in hits:
                    self._increase_stats(
                        connection, group_id, victim_id, victim_name, roulette_deaths=1
                    )
                victim_ids = tuple(victim_id for _shot, victim_id, _name in hits)
                return GameEvent(
                    group_id,
                    ROULETTE,
                    f"{trigger_text}\n"
                    + self._phrase(
                        ROULETTE_BURST_RIFLE_HIT_TEXTS,
                        victims="、".join(name for _shot, _id, name in hits),
                        hits=len(hits),
                    ),
                    "roulette_burst_rifle_hit",
                    mute_user_ids=victim_ids,
                    mention_user_ids=victim_ids,
                    mention_replacements=(
                        (self._name(nickname), int(user_id)),
                        *((name, target_id) for _shot, target_id, name in hits),
                    ),
                )

            # Repeated #开枪 is intentional. The unique participant row still
            # makes this one round count as one participation for the player.
            prior_participants = self._participants(connection, int(session["session_id"]))
            self._add_participant(
                connection, int(session["session_id"]), user_id, nickname, shots + 1, None, current
            )
            finger_cramp = bool(state.get("roulette_finger_cramp_pending"))
            if finger_cramp:
                state["roulette_finger_cramp_pending"] = False
                trigger_text = self._phrase(
                    ROULETTE_FINGER_CRAMP_TRIGGER_TEXTS, name=self._name(nickname)
                )
            else:
                trigger_text = ""
            aim_target: tuple[int, str] | None = None
            aim_text = ""
            if bool(state.get("roulette_aim_drift_pending")):
                state["roulette_aim_drift_pending"] = False
                if prior_participants:
                    chosen = secrets.choice(prior_participants)
                    aim_target = (int(chosen["user_id"]), self._name(str(chosen["nickname"])))
                    aim_text = self._phrase(
                        ROULETTE_AIM_DRIFT_SAFE_TEXTS,
                        shooter=self._name(nickname),
                        target=aim_target[1],
                    )
            self._record_random_event_interaction(state)
            shot_log = state.setdefault("shot_log", [])
            if not isinstance(shot_log, list):
                shot_log = []
                state["shot_log"] = shot_log
            for _shot in range(2 if finger_cramp else 1):
                shots += 1
                state["shots"] = shots
                shot_log.append({"user_id": int(user_id), "nickname": self._name(nickname)})
                if shots != int(state.get("bullet_at", 6)):
                    continue
                state["victim_id"] = int(user_id)
                state["victim_name"] = self._name(nickname)
                if (
                    (
                        bool(state.get("roulette_reflection_active"))
                        or str(state.get("random_event_triggered", "")) == "roulette_reflection"
                    )
                    and len(shot_log) >= 2
                ):
                    previous = shot_log[-2]
                    previous_id = int(previous.get("user_id", 0)) if isinstance(previous, dict) else 0
                    previous_name = self._name(
                        str(previous.get("nickname", "")) if isinstance(previous, dict) else ""
                    )
                    if previous_id > 0:
                        state.update(
                            {
                                "victim_id": previous_id,
                                "victim_name": previous_name,
                                "roulette_reflected": True,
                                "reflection_shooter_name": self._name(nickname),
                                "reflection_shooter_id": int(user_id),
                            }
                        )
                if aim_target is not None:
                    state.update(
                        {
                            "victim_id": aim_target[0],
                            "victim_name": aim_target[1],
                            "roulette_aim_drift": True,
                            "roulette_aim_drift_shooter_id": int(user_id),
                            "roulette_aim_drift_shooter_name": self._name(nickname),
                        }
                    )
                if bool(state.get("roulette_area_explosion_pending")):
                    state.update(
                        {
                            "roulette_area_explosion_pending": False,
                            "area_explosion_trigger_id": int(user_id),
                            "area_explosion_trigger_name": self._name(nickname),
                        }
                    )
                    self._write_state(connection, int(session["session_id"]), state)
                    refreshed = connection.execute(
                        "SELECT * FROM mini_game_sessions WHERE session_id=?", (int(session["session_id"]),)
                    ).fetchone()
                    event = self._end(connection, refreshed, current, "area_explosion") if refreshed else None
                    if event is not None:
                        return event
                if bool(state.get("roulette_barrel_burst_pending")):
                    state["roulette_barrel_burst_pending"] = False
                    self._write_state(connection, int(session["session_id"]), state)
                    refreshed = connection.execute(
                        "SELECT * FROM mini_game_sessions WHERE session_id=?", (int(session["session_id"]),)
                    ).fetchone()
                    event = self._end(connection, refreshed, current, "barrel_burst") if refreshed else None
                    if event is not None:
                        return GameEvent(
                            event.group_id,
                            event.game_type,
                            self._phrase(ROULETTE_BARREL_BURST_TRIGGER_TEXTS, name=self._name(nickname)),
                            "roulette_barrel_burst_triggered",
                            ended=True,
                            mention_user_ids=(int(user_id),),
                            mention_replacements=((self._name(nickname), int(user_id)),),
                        )
                self._write_state(connection, int(session["session_id"]), state)
                refreshed = connection.execute(
                    "SELECT * FROM mini_game_sessions WHERE session_id=?", (int(session["session_id"]),)
                ).fetchone()
                event = self._end(connection, refreshed, current, "hit") if refreshed else None
                if event is not None and trigger_text:
                    return GameEvent(
                        event.group_id,
                        event.game_type,
                        f"{trigger_text}\n{event.text}",
                        "roulette_finger_cramp_hit",
                        ended=event.ended,
                        mute_user_ids=event.mute_user_ids,
                        mention_replacements=(*event.mention_replacements, (self._name(nickname), int(user_id))),
                        mention_user_ids=event.mention_user_ids,
                    )
                return event or GameEvent(
                    group_id, ROULETTE, self._phrase(GAME_ENDED_TEXTS), "ended", ended=True
                )
            self._write_state(connection, int(session["session_id"]), state)
            safe_text = (
                self._phrase(ROULETTE_FINGER_CRAMP_SAFE_TEXTS, name=self._name(nickname))
                if finger_cramp
                else self._phrase(ROULETTE_SAFE_TEXTS, name=self._name(nickname))
            )
            if aim_text:
                safe_text = f"{aim_text}\n{safe_text}"
            return GameEvent(
                group_id,
                ROULETTE,
                f"{trigger_text}\n{safe_text}" if trigger_text else safe_text,
                "roulette_finger_cramp_safe"
                if finger_cramp
                else "roulette_aim_drift_safe"
                if aim_text
                else "roulette_safe",
                mention_user_ids=tuple(
                    value for value in (int(user_id), aim_target[0] if aim_target else 0) if value > 0
                ),
                mention_replacements=tuple(
                    value
                    for value in (
                        (self._name(nickname), int(user_id)),
                        (aim_target[1], aim_target[0]) if aim_target else None,
                    )
                    if value is not None
                ),
            )

    def start_bomb(
        self,
        group_id: int,
        user_id: int,
        nickname: str,
        now: datetime | None = None,
        *,
        idiom_mode: bool = False,
        idiom_ruleset: Literal["professional", "entertainment"] = "professional",
        duration_seconds: int | None = None,
    ) -> GameEvent:
        current = now or self.now()
        duration = int(duration_seconds) if duration_seconds is not None else int(GAME_DURATION.total_seconds())
        if idiom_mode and not BOMB_IDIOM_MIN_DURATION_SECONDS <= duration <= BOMB_IDIOM_MAX_DURATION_SECONDS:
            return GameEvent(group_id, BOMB, "⏱️⚠️ 成语炸弹时长只能是 60 到 600 秒。", "bomb_invalid_duration")
        if not idiom_mode:
            duration = int(GAME_DURATION.total_seconds())
        with self.database.connect() as connection:
            active = self._active(connection, group_id)
            if active is not None:
                return GameEvent(
                    group_id,
                    BOMB,
                    self._phrase(GAME_BUSY_TEXTS, game=GAME_LABELS[str(active["game_type"])]),
                    "busy",
                )
            holder_name = self._name(nickname)
            random_state = self._random_event_state(BOMB, current)
            if idiom_mode:
                random_state["random_event_total"] = duration // BOMB_IDIOM_RANDOM_EVENT_INTERVAL_SECONDS
            session = self._create(
                connection,
                group_id,
                BOMB,
                user_id,
                {
                    "holder_id": int(user_id),
                    "holder_name": holder_name,
                    "last_passed_at": self._timestamp(current),
                    "holder_since_at": self._timestamp(current),
                    "bomb_idiom_mode": bool(idiom_mode),
                    "bomb_idiom_ruleset": idiom_ruleset if idiom_mode else "professional",
                    "bomb_duration_seconds": duration,
                    "idiom_last": None,
                    "idiom_used": [],
                    "idiom_banned": [],
                    **random_state,
                },
                current,
                timedelta(seconds=duration),
            )
            if session is None:
                return GameEvent(group_id, BOMB, self._phrase(GAME_BUSY_TEXTS, game="小游戏"), "busy")
            self._add_participant(connection, int(session["session_id"]), user_id, nickname, 1, None, current)
        if idiom_mode:
            return GameEvent(
                group_id,
                BOMB,
                self._phrase(BOMB_IDIOM_STARTED_TEXTS, name=holder_name, duration=duration)
                + ("\n🎮 四字词娱乐模式：成语和词语都能接龙。" if idiom_ruleset == "entertainment" else "\n📚 专业模式：仅收录成语可用。"),
                "bomb_idiom_started",
                mention_user_ids=(int(user_id),),
                mention_replacements=((holder_name, int(user_id)),),
            )
        return GameEvent(
            group_id,
            BOMB,
            self._phrase(BOMB_STARTED_TEXTS, name=holder_name),
            "bomb_started",
            mention_user_ids=(int(user_id),),
            mention_replacements=((holder_name, int(user_id)),),
        )

    def bomb_is_idiom_mode(self, group_id: int) -> bool:
        with self.database.connect() as connection:
            session = self._active(connection, group_id)
            return bool(
                session is not None
                and str(session["game_type"]) == BOMB
                and self._state(session).get("bomb_idiom_mode")
            )

    def throw_bomb(
        self,
        group_id: int,
        user_id: int,
        nickname: str,
        target_id: int,
        target_name: str,
        now: datetime | None = None,
        *,
        idiom: str | None = None,
    ) -> GameEvent:
        current = now or self.now()
        with self.database.connect() as connection:
            session = self._active(connection, group_id)
            if session is None:
                return GameEvent(group_id, BOMB, self._phrase(BOMB_NOT_READY_TEXTS), "no_session")
            if self._parse_timestamp(str(session["ends_at"])) <= current:
                event = self._end(connection, session, current, "timeout")
                return event or GameEvent(
                    group_id, BOMB, self._phrase(GAME_ENDED_TEXTS), "ended", ended=True
                )
            if str(session["game_type"]) != BOMB:
                return GameEvent(
                    group_id,
                    BOMB,
                    self._phrase(GAME_BUSY_TEXTS, game=GAME_LABELS[str(session["game_type"])]),
                    "wrong_game",
                )
            state = self._state(session)
            if self._bomb_holder_expired(session, state, current):
                event = self._end(connection, session, current, "holder_timeout")
                return event or GameEvent(
                    group_id, BOMB, self._phrase(GAME_ENDED_TEXTS), "ended", ended=True
                )
            if int(state.get("holder_id", 0)) != int(user_id):
                return GameEvent(
                    group_id, BOMB, self._phrase(BOMB_NOT_HOLDER_TEXTS), "not_holder"
                )
            if int(target_id) == int(user_id):
                return GameEvent(
                    group_id, BOMB, self._phrase(BOMB_SELF_TARGET_TEXTS), "self_target"
                )
            idiom_mode = bool(state.get("bomb_idiom_mode"))
            idiom_trigger_text = ""
            if idiom_mode:
                if not isinstance(idiom, str) or not self._is_valid_chain_word(state, idiom):
                    if isinstance(idiom, str) and len(idiom) == 4:
                        return GameEvent(
                            group_id,
                            BOMB,
                            self._phrase(BOMB_INVALID_IDIOM_TEXTS, idiom=idiom),
                            "bomb_invalid_idiom",
                        )
                    return GameEvent(group_id, BOMB, self._phrase(BOMB_IDIOM_FORMAT_TEXTS), "bomb_idiom_format")
                if idiom in self._idiom_unavailable_words(state):
                    return GameEvent(
                        group_id,
                        BOMB,
                        self._phrase(BOMB_IDIOM_REPEAT_TEXTS, idiom=idiom),
                        "bomb_idiom_repeat",
                    )
                used = self._idiom_state_words(state, "idiom_used")
                previous_idiom = state.get("idiom_last")
                anchor = state.get("idiom_chain_anchor")
                expected = state.get("idiom_next_start_char")
                if not isinstance(expected, str) or len(expected) != 1:
                    expected = anchor[-1] if isinstance(anchor, str) and anchor else (
                        previous_idiom[-1] if isinstance(previous_idiom, str) and previous_idiom else ""
                    )
                lonely_start = bool(state.get("idiom_lonely_start_pending"))
                free_start = bool(state.get("idiom_free_start_pending"))
                if lonely_start and idiom[0] in {word[-1] for word in used}:
                    return GameEvent(
                        group_id,
                        BOMB,
                        self._phrase(BOMB_IDIOM_LONELY_TEXTS, idiom=idiom),
                        "bomb_idiom_lonely_blocked",
                    )
                if not (free_start or lonely_start) and expected and idiom[0] != expected:
                    return GameEvent(
                        group_id,
                        BOMB,
                        self._phrase(
                            BOMB_IDIOM_CHAIN_TEXTS,
                            previous=anchor if isinstance(anchor, str) and anchor else previous_idiom,
                            expected=expected,
                            idiom=idiom,
                        ),
                        "bomb_idiom_chain_break",
                    )
                if bool(state.get("bomb_idiom_rewrite_pending")):
                    banned = self._idiom_state_words(state, "idiom_banned")
                    state.update(
                        {
                            "bomb_idiom_rewrite_pending": False,
                            "idiom_banned": [*banned, idiom],
                        }
                    )
                    self._record_random_event_interaction(state)
                    self._write_state(connection, int(session["session_id"]), state)
                    return GameEvent(
                        group_id,
                        BOMB,
                        self._phrase(BOMB_IDIOM_REWRITE_TRIGGER_TEXTS, name=self._name(nickname), idiom=idiom),
                        "bomb_idiom_rewrite_triggered",
                        mention_replacements=((self._name(nickname), int(user_id)),),
                    )
                state["idiom_last"] = idiom
                state["idiom_used"] = [*used, idiom]
                state["idiom_chain_anchor"] = None
                state["idiom_next_start_char"] = None
                state["idiom_free_start_pending"] = False
                state["idiom_lonely_start_pending"] = False
                if bool(state.get("bomb_idiom_first_char_pending")):
                    state["bomb_idiom_first_char_pending"] = False
                    if self._has_unused_idiom_starting_with(state, idiom[0]):
                        state["idiom_next_start_char"] = idiom[0]
                        idiom_trigger_text = self._phrase(
                            BOMB_IDIOM_FIRST_CHAR_TRIGGER_TEXTS,
                            name=self._name(nickname),
                            idiom=idiom,
                            expected=idiom[0],
                        )
            elif idiom is not None:
                return GameEvent(group_id, BOMB, self._phrase(BOMB_PLAIN_FORMAT_TEXTS), "bomb_plain_format")
            participants = self._participants(connection, int(session["session_id"]))
            connection.execute(
                """UPDATE mini_game_participants SET pass_count=pass_count+1
                   WHERE session_id=? AND user_id=?""",
                (int(session["session_id"]), int(user_id)),
            )
            previous_pass_at = state.get("last_passed_at")
            previous_pass = (
                self._parse_timestamp(previous_pass_at)
                if isinstance(previous_pass_at, str)
                else self._parse_timestamp(str(session["started_at"]))
            )
            elapsed = max(0, int((current - previous_pass).total_seconds()))
            state["last_passed_at"] = self._timestamp(current)
            self._record_random_event_interaction(state)
            if bool(state.get("bomb_inertia_pending")):
                state["bomb_inertia_pending"] = False
                state["holder_id"] = int(user_id)
                state["holder_name"] = self._name(nickname)
                self._write_state(connection, int(session["session_id"]), state)
                event = GameEvent(
                    group_id,
                    BOMB,
                    self._phrase(BOMB_INERTIA_TRIGGER_TEXTS, name=self._name(nickname)),
                    "bomb_inertia_triggered",
                    mention_replacements=((self._name(nickname), int(user_id)),),
                )
                return self._with_event_prefix(
                    self._with_idiom_confirmation(event, idiom if idiom_mode else None), idiom_trigger_text
                )
            if bool(state.get("bomb_black_hole_pending")):
                targets = [
                    row
                    for row in participants
                    if int(row["user_id"]) not in {int(user_id), int(target_id)}
                ]
                if targets:
                    target = secrets.choice(targets)
                    redirected_id = int(target["user_id"])
                    redirected_name = self._name(str(target["nickname"]))
                    state.update(
                        {
                            "bomb_black_hole_pending": False,
                            "bomb_previous_holder_id": int(user_id),
                            "bomb_previous_holder_name": self._name(nickname),
                            "holder_id": redirected_id,
                            "holder_name": redirected_name,
                            "holder_since_at": self._timestamp(current),
                        }
                    )
                    self._write_state(connection, int(session["session_id"]), state)
                    event = GameEvent(
                        group_id,
                        BOMB,
                        self._phrase(
                            BOMB_BLACK_HOLE_TRIGGER_TEXTS,
                            name=self._name(nickname),
                            target_name=redirected_name,
                        ),
                        "bomb_black_hole_triggered",
                        mention_replacements=(
                            (self._name(nickname), int(user_id)),
                            (redirected_name, redirected_id),
                        ),
                    )
                    return self._with_event_prefix(
                        self._with_idiom_confirmation(event, idiom if idiom_mode else None), idiom_trigger_text
                    )
                state["bomb_black_hole_pending"] = False
            if bool(state.get("bomb_reverse_delivery_pending")):
                previous_id = int(state.get("bomb_previous_holder_id", 0) or 0)
                previous_name = self._name(str(state.get("bomb_previous_holder_name", "")))
                if previous_id > 0 and previous_id != int(user_id):
                    state.update(
                        {
                            "bomb_reverse_delivery_pending": False,
                            "bomb_previous_holder_id": int(user_id),
                            "bomb_previous_holder_name": self._name(nickname),
                            "holder_id": previous_id,
                            "holder_name": previous_name,
                            "holder_since_at": self._timestamp(current),
                        }
                    )
                    self._write_state(connection, int(session["session_id"]), state)
                    event = GameEvent(
                        group_id,
                        BOMB,
                        self._phrase(
                            BOMB_REVERSE_DELIVERY_TRIGGER_TEXTS,
                            name=self._name(nickname),
                            target_name=previous_name,
                        ),
                        "bomb_reverse_delivery_triggered",
                        mention_replacements=(
                            (self._name(nickname), int(user_id)),
                            (previous_name, previous_id),
                        ),
                    )
                    return self._with_event_prefix(
                        self._with_idiom_confirmation(event, idiom if idiom_mode else None), idiom_trigger_text
                    )
                state["bomb_reverse_delivery_pending"] = False
            self._add_participant(
                connection,
                int(session["session_id"]),
                target_id,
                target_name,
                len(participants) + 1,
                None,
                current,
            )
            state["bomb_previous_holder_id"] = int(user_id)
            state["bomb_previous_holder_name"] = self._name(nickname)
            state["holder_id"] = int(target_id)
            state["holder_name"] = self._name(target_name)
            state["holder_since_at"] = self._timestamp(current)
            self._write_state(connection, int(session["session_id"]), state)
            event = GameEvent(
                group_id,
                BOMB,
                self._phrase(
                    BOMB_IDIOM_THROWN_TEXTS if idiom_mode else BOMB_THROWN_TEXTS,
                    from_name=self._name(nickname),
                    to_name=self._name(target_name),
                    elapsed=elapsed,
                    idiom=idiom or "",
                ),
                "bomb_thrown",
                mention_replacements=(
                    (self._name(nickname), int(user_id)),
                    (self._name(target_name), int(target_id)),
                ),
            )
            if idiom_trigger_text:
                return GameEvent(
                    event.group_id,
                    event.game_type,
                    f"{idiom_trigger_text}\n{event.text}",
                    "bomb_idiom_first_char_triggered",
                    ended=event.ended,
                    announce=event.announce,
                    mention_replacements=event.mention_replacements,
                )
            return event

    def _guess_cursed_numbers(self, state: dict[str, Any]) -> frozenset[int]:
        stored = state.get("cursed_numbers")
        if isinstance(stored, list):
            return frozenset(
                int(value)
                for value in stored
                if isinstance(value, int) and 0 <= int(value) <= 999
            )
        return self.cursed_guess_numbers

    def _guess_feedback_hint(self, state: dict[str, Any], value: int, target: int) -> str:
        distance = abs(int(target) - int(value))
        if distance < 100:
            return self._session_phrase(state, "guess_close_phrases", GUESS_CLOSE_TEXTS)
        if distance <= 100:
            return ""
        places = ("百位", "十位", "个位")
        matching = [
            place
            for place, guessed, answer in zip(places, f"{int(value):03d}", f"{int(target):03d}")
            if guessed == answer
        ]
        if not matching:
            return ""
        return self._session_phrase(
            state,
            "guess_position_phrases",
            GUESS_POSITION_MATCH_TEXTS,
            positions="、".join(matching),
        )

    def start_guess(
        self, group_id: int, user_id: int, nickname: str, now: datetime | None = None
    ) -> GameEvent:
        current = now or self.now()
        with self.database.connect() as connection:
            active = self._active(connection, group_id)
            if active is not None:
                return GameEvent(
                    group_id,
                    GUESS,
                    self._phrase(GAME_BUSY_TEXTS, game=GAME_LABELS[str(active["game_type"])]),
                    "busy",
                )
            session = self._create(
                connection,
                group_id,
                GUESS,
                user_id,
                {
                    "target": secrets.choice(
                        [value for value in range(1000) if value not in self.cursed_guess_numbers]
                    ),
                    "cursed_numbers": sorted(self.cursed_guess_numbers),
                    "guess_attempts": 0,
                    **self._random_event_state(GUESS, current),
                },
                current,
            )
            if session is None:
                return GameEvent(group_id, GUESS, self._phrase(GAME_BUSY_TEXTS, game="小游戏"), "busy")
        return GameEvent(group_id, GUESS, self._phrase(GUESS_STARTED_TEXTS), "guess_started")

    def guess_number(
        self,
        group_id: int,
        user_id: int,
        nickname: str,
        value: int | None,
        now: datetime | None = None,
    ) -> GameEvent:
        current = now or self.now()
        with self.database.connect() as connection:
            session = self._active(connection, group_id)
            if session is None:
                return GameEvent(group_id, GUESS, self._next_guess_not_ready_text(), "no_session")
            if self._parse_timestamp(str(session["ends_at"])) <= current:
                event = self._end(connection, session, current, "timeout")
                return event or GameEvent(
                    group_id, GUESS, self._phrase(GAME_ENDED_TEXTS), "ended", ended=True
                )
            if str(session["game_type"]) != GUESS:
                return GameEvent(
                    group_id,
                    GUESS,
                    self._phrase(
                        GUESS_WRONG_GAME_TEXTS,
                        game=GAME_LABELS[str(session["game_type"])],
                    ),
                    "wrong_game",
                )
            state = self._state(session)
            divergent_text = ""
            if value is None or not 0 <= int(value) <= 999:
                self._record_random_event_interaction(state)
                text = self._session_phrase(
                    state, "guess_invalid_phrases", GUESS_INVALID_TEXTS
                )
                self._write_state(connection, int(session["session_id"]), state)
                return GameEvent(group_id, GUESS, text, "invalid")

            if int(value) in self._guess_cursed_numbers(state):
                return GameEvent(
                    group_id,
                    GUESS,
                    self._phrase(GUESS_CURSED_TEXTS, name=self._name(nickname)),
                    "guess_cursed",
                    mute_user_ids=(int(user_id),),
                    mention_user_ids=(int(user_id),),
                    mention_replacements=((self._name(nickname), int(user_id)),),
                )

            free_attempt = bool(state.get("guess_divergent_pending"))
            if free_attempt:
                old_target = int(state.get("target", 0) or 0)
                digits = list(f"{old_target:03d}")
                position = secrets.choice((1, 2))
                digits[position] = secrets.choice([digit for digit in "0123456789" if digit != digits[position]])
                state["target"] = int("".join(digits))
                state["guess_divergent_pending"] = False
                divergent_text = self._phrase(
                    GUESS_DIVERGENT_TRIGGER_TEXTS, name=self._name(nickname)
                )

            target = int(state.get("target", -1))
            participants = self._participants(connection, int(session["session_id"]))
            first_guess = self._add_participant(
                connection,
                int(session["session_id"]),
                user_id,
                nickname,
                len(participants) + 1,
                None,
                current,
            )
            is_hit = int(value) == target
            self._increase_stats(
                connection,
                group_id,
                user_id,
                nickname,
                guess_games=int(first_guess),
                guess_wins=int(is_hit),
                guess_misses=int(not is_hit),
            )
            if not free_attempt:
                state["guess_attempts"] = int(state.get("guess_attempts", 0)) + 1
            self._record_random_event_interaction(state)
            if is_hit:
                state.update({"winner_id": int(user_id), "winner_name": self._name(nickname)})
                self._write_state(connection, int(session["session_id"]), state)
                refreshed = connection.execute(
                    "SELECT * FROM mini_game_sessions WHERE session_id=?",
                    (int(session["session_id"]),),
                ).fetchone()
                event = self._end(connection, refreshed, current, "hit") if refreshed is not None else None
                if event is not None and divergent_text:
                    return GameEvent(
                        event.group_id,
                        event.game_type,
                        f"{divergent_text}\n{event.text}",
                        event.kind,
                        ended=event.ended,
                        mute_user_ids=event.mute_user_ids,
                        mention_user_ids=event.mention_user_ids,
                        mention_replacements=event.mention_replacements,
                    )
                return event or GameEvent(
                    group_id, GUESS, self._phrase(GAME_ENDED_TEXTS), "ended", ended=True
                )

            if int(value) > target:
                text = self._session_phrase(
                    state,
                    "guess_high_phrases",
                    GUESS_HIGH_TEXTS,
                    name=self._name(nickname),
                    value=int(value),
                )
                kind = "guess_high"
            else:
                text = self._session_phrase(
                    state,
                    "guess_low_phrases",
                    GUESS_LOW_TEXTS,
                    name=self._name(nickname),
                    value=int(value),
                )
                kind = "guess_low"
            state["last_wrong_guess"] = {
                "user_id": int(user_id),
                "name": self._name(nickname),
                "value": int(value),
            }
            feedback_hint = self._guess_feedback_hint(state, int(value), target)
            self._write_state(connection, int(session["session_id"]), state)
            if int(state["guess_attempts"]) >= 10:
                refreshed = connection.execute(
                    "SELECT * FROM mini_game_sessions WHERE session_id=?",
                    (int(session["session_id"]),),
                ).fetchone()
                event = self._end(connection, refreshed, current, "attempts_exhausted") if refreshed is not None else None
                if event is not None:
                    if divergent_text:
                        return GameEvent(
                            event.group_id,
                            event.game_type,
                            f"{divergent_text}\n{event.text}",
                            event.kind,
                            ended=event.ended,
                            mute_user_ids=event.mute_user_ids,
                            mention_user_ids=event.mention_user_ids,
                            mention_replacements=(*event.mention_replacements, (self._name(nickname), int(user_id))),
                        )
                    return event
            return GameEvent(
                group_id,
                GUESS,
                "\n".join(
                    part for part in (divergent_text, text, feedback_hint) if part
                ),
                kind,
                mention_replacements=((self._name(nickname), int(user_id)),),
            )

    def roll_dice(
        self, group_id: int, user_id: int, nickname: str, now: datetime | None = None
    ) -> GameEvent:
        current = now or self.now()
        with self.database.connect() as connection:
            session = self._active(connection, group_id)
            if session is not None and self._parse_timestamp(str(session["ends_at"])) <= current:
                event = self._end(connection, session, current, "timeout")
                return event or GameEvent(
                    group_id, DICE, self._phrase(GAME_ENDED_TEXTS), "ended", ended=True
                )
            if session is not None and str(session["game_type"]) != DICE:
                return GameEvent(
                    group_id,
                    DICE,
                    self._phrase(GAME_BUSY_TEXTS, game=GAME_LABELS[str(session["game_type"])]),
                    "wrong_game",
                )
            if session is None:
                session = self._create(
                    connection,
                    group_id,
                    DICE,
                    user_id,
                    self._random_event_state(DICE, current),
                    current,
                )
                if session is None:
                    return GameEvent(group_id, DICE, self._phrase(GAME_BUSY_TEXTS, game="小游戏"), "busy")
            participants = self._participants(connection, int(session["session_id"]))
            existing = next((row for row in participants if int(row["user_id"]) == int(user_id)), None)
            if existing is not None:
                state = self._state(session)
                if self._has_dice_regret(state, user_id):
                    return self._apply_dice_regret_reroll(
                        connection,
                        session,
                        state,
                        existing,
                        participants,
                        nickname,
                    )
                return GameEvent(
                    group_id,
                    DICE,
                    self._phrase(DICE_ALREADY_TEXTS, name=self._name(nickname)),
                    "already",
                    mention_replacements=((self._name(nickname), int(user_id)),),
                )
            available = self._available_dice_values(participants)
            if not available:
                return GameEvent(group_id, DICE, self._phrase(DICE_FULL_TEXTS), "dice_full")
            state = self._state(session)
            value, double_text, double_kind = self._draw_dice_value(available, state, nickname)
            self._add_participant(
                connection,
                int(session["session_id"]),
                user_id,
                nickname,
                len(participants) + 1,
                value,
                current,
            )
            value, throne_text, event_kind = self._resolve_dice_throne(
                connection, session, state, user_id, nickname, value
            )
            self._record_random_event_interaction(state)
            self._write_state(connection, int(session["session_id"]), state)
        return GameEvent(
            group_id,
            DICE,
            (f"{double_text}\n" if double_text else "")
            + (f"{throne_text}\n" if throne_text else "")
            + self._phrase(DICE_ROLL_TEXTS, name=self._name(nickname), value=value)
            + "\n"
            + self._dice_comment(value),
            event_kind if throne_text else double_kind,
            mention_replacements=(
                (self._name(nickname), int(user_id)),
                *(
                    [(self._name(str(state.get("dice_throne_leader_name", ""))), int(state.get("dice_throne_leader_id", 0)))]
                    if throne_text and int(state.get("dice_throne_leader_id", 0) or 0) > 0
                    else []
                ),
            ),
        )

    def _resolve_dice_throne(
        self,
        connection: sqlite3.Connection,
        session: sqlite3.Row,
        state: dict[str, Any],
        user_id: int,
        nickname: str,
        value: int,
    ) -> tuple[int, str, str]:
        if not bool(state.get("dice_throne_pending")):
            return value, "", "dice_roll"
        state["dice_throne_pending"] = False
        participants = self._participants(connection, int(session["session_id"]))
        highest = max(participants, key=lambda row: int(row["dice_value"]))
        name = self._name(nickname)
        if int(highest["user_id"]) == int(user_id):
            return value, self._phrase(DICE_THRONE_NOOP_TEXTS, name=name), "dice_throne_noop"
        leader_value = int(highest["dice_value"])
        state["dice_throne_leader_id"] = int(highest["user_id"])
        state["dice_throne_leader_name"] = self._name(str(highest["nickname"]))
        connection.execute(
            "UPDATE mini_game_participants SET dice_value=? WHERE session_id=? AND user_id=?",
            (leader_value, int(session["session_id"]), int(user_id)),
        )
        connection.execute(
            "UPDATE mini_game_participants SET dice_value=? WHERE session_id=? AND user_id=?",
            (value, int(session["session_id"]), int(highest["user_id"])),
        )
        return (
            leader_value,
            self._phrase(
                DICE_THRONE_TRIGGER_TEXTS,
                name=name,
                leader_name=self._name(str(highest["nickname"])),
                value=value,
                leader_value=leader_value,
            ),
            "dice_throne_triggered",
        )

    def _apply_dice_regret_reroll(
        self,
        connection: sqlite3.Connection,
        session: sqlite3.Row,
        state: dict[str, Any],
        player: sqlite3.Row,
        participants: list[sqlite3.Row],
        nickname: str,
    ) -> GameEvent:
        user_id = int(player["user_id"])
        old_value = int(player["dice_value"])
        available = self._rerolled_dice_values(participants, user_id, old_value)
        if not available:
            return GameEvent(int(session["group_id"]), DICE, self._phrase(DICE_FULL_TEXTS), "dice_full")
        new_value, double_text, double_kind = self._draw_dice_value(available, state, nickname)
        connection.execute(
            """UPDATE mini_game_participants SET dice_value=?,nickname=?
               WHERE session_id=? AND user_id=?""",
            (new_value, self._name(nickname), int(session["session_id"]), user_id),
        )
        self._resolve_dice_regret(state, user_id)
        new_value, throne_text, event_kind = self._resolve_dice_throne(
            connection, session, state, user_id, nickname, new_value
        )
        self._record_random_event_interaction(state)
        self._write_state(connection, int(session["session_id"]), state)
        return GameEvent(
            int(session["group_id"]),
            DICE,
            (f"{double_text}\n" if double_text else "")
            + (f"{throne_text}\n" if throne_text else "")
            + self._phrase(
                DICE_REGRET_REROLL_TEXTS,
                name=self._name(nickname),
                old_value=old_value,
                new_value=new_value,
            )
            + "\n"
            + self._dice_comment(new_value),
            event_kind if throne_text else (double_kind if double_text else "dice_reroll"),
            mention_replacements=(
                (self._name(nickname), user_id),
                *(
                    [(self._name(str(state.get("dice_throne_leader_name", ""))), int(state.get("dice_throne_leader_id", 0)))]
                    if throne_text and int(state.get("dice_throne_leader_id", 0) or 0) > 0
                    else []
                ),
            ),
        )

    def cancel_group_session(self, group_id: int, now: datetime | None = None) -> bool:
        current = now or self.now()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """UPDATE mini_game_sessions SET status='cancelled',ended_at=?,result_json=?
                   WHERE group_id=? AND status='active'""",
                (
                    self._timestamp(current),
                    json.dumps({"reason": "feature_disabled"}, ensure_ascii=False),
                    int(group_id),
                ),
            )
            return cursor.rowcount > 0

    def clear_group_records(self, group_id: int) -> dict[str, int]:
        """Permanently remove one group's mini-game sessions and rankings."""
        with self.database.connect() as connection:
            participants = connection.execute(
                """DELETE FROM mini_game_participants
                   WHERE session_id IN (
                       SELECT session_id FROM mini_game_sessions WHERE group_id=?
                   )""",
                (int(group_id),),
            )
            sessions = connection.execute(
                "DELETE FROM mini_game_sessions WHERE group_id=?", (int(group_id),)
            )
            stats = connection.execute(
                "DELETE FROM mini_game_stats WHERE group_id=?", (int(group_id),)
            )
        return {
            "participants": int(participants.rowcount),
            "sessions": int(sessions.rowcount),
            "stats": int(stats.rowcount),
        }

    def ranking(
        self,
        game_type: str,
        group_id: int | None = None,
        visible_group_ids: Iterable[int] | None = None,
        include_group_details: bool = True,
    ) -> dict[str, Any]:
        if game_type not in RANKING_SECTIONS:
            raise ValueError("unsupported game type")
        sections = []
        for title, value_label, value_column, games_column in RANKING_SECTIONS[game_type]:
            sections.append(
                {
                    "title": title,
                    "value_label": value_label,
                    "rows": self._ranking_rows(
                        value_column, games_column, group_id, visible_group_ids
                    ),
                }
            )
        show_group_details = group_id is None and include_group_details
        if not show_group_details:
            for section in sections:
                for row in section["rows"]:
                    row["group_ids"] = ()
        involved = (
            sorted(
                {
                    group
                    for section in sections
                    for row in section["rows"]
                    for group in row.get("group_ids", ())
                }
            )
            if show_group_details
            else []
        )
        names = self._group_names(involved)
        return {
            "title": f"{GAME_LABELS[game_type]}{'总榜单' if group_id is None else '榜单'}",
            "game_type": game_type,
            "global": group_id is None,
            "show_group_details": show_group_details,
            "sections": sections,
            "groups": [{"group_id": value, "group_name": names.get(value, "未命名群")} for value in involved],
        }

    def _group_names(self, group_ids: list[int]) -> dict[int, str]:
        if not group_ids:
            return {}
        marks = ",".join("?" for _ in group_ids)
        with self.database.connect() as connection:
            rows = list(
                connection.execute(
                    f"SELECT group_id,group_name FROM managed_groups WHERE group_id IN ({marks})",
                    tuple(group_ids),
                )
            )
        return {int(row["group_id"]): str(row["group_name"] or "未命名群") for row in rows}

    def _ranking_rows(
        self,
        value_column: str,
        games_column: str,
        group_id: int | None,
        visible_group_ids: Iterable[int] | None,
    ) -> list[dict[str, Any]]:
        if group_id is not None:
            with self.database.connect() as connection:
                rows = list(
                    connection.execute(
                        f"""SELECT user_id,nickname,{value_column} AS value,{games_column} AS games
                            FROM mini_game_stats
                            WHERE group_id=? AND {value_column}>0
                            ORDER BY {value_column} DESC,user_id ASC LIMIT 20""",
                        (int(group_id),),
                    )
                )
            values = [
                {
                    "user_id": int(row["user_id"]),
                    "nickname": self._name(str(row["nickname"])),
                    "value": int(row["value"]),
                    "games": int(row["games"]),
                    "group_ids": (),
                }
                for row in rows
            ]
        else:
            allowed_groups = (
                tuple(sorted({int(value) for value in visible_group_ids}))
                if visible_group_ids is not None
                else None
            )
            if allowed_groups == ():
                return []
            group_filter = ""
            parameters: tuple[object, ...] = ()
            if allowed_groups is not None:
                group_filter = " AND group_id IN (" + ",".join("?" for _ in allowed_groups) + ")"
                parameters = allowed_groups
            with self.database.connect() as connection:
                rows = list(
                    connection.execute(
                        f"""SELECT group_id,user_id,nickname,{value_column} AS value,
                                   {games_column} AS games,updated_at
                            FROM mini_game_stats
                            WHERE ({value_column}>0 OR {games_column}>0){group_filter}""",
                        parameters,
                    )
                )
            aggregate: dict[int, dict[str, Any]] = {}
            for row in rows:
                user_id = int(row["user_id"])
                current = aggregate.setdefault(
                    user_id,
                    {
                        "user_id": user_id,
                        "nickname": self._name(str(row["nickname"])),
                        "nickname_at": str(row["updated_at"]),
                        "value": 0,
                        "games": 0,
                        "group_ids": set(),
                    },
                )
                current["value"] += int(row["value"])
                current["games"] += int(row["games"])
                if int(row["games"]) > 0:
                    current["group_ids"].add(int(row["group_id"]))
                if str(row["updated_at"]) > str(current["nickname_at"]):
                    current["nickname"] = self._name(str(row["nickname"]))
                    current["nickname_at"] = str(row["updated_at"])
            values = [
                {
                    "user_id": item["user_id"],
                    "nickname": item["nickname"],
                    "value": item["value"],
                    "games": item["games"],
                    "group_ids": tuple(sorted(item["group_ids"])),
                }
                for item in aggregate.values()
                if int(item["value"]) > 0
            ]
            values.sort(key=lambda item: (-int(item["value"]), int(item["user_id"])))
            values = values[:20]

        previous: int | None = None
        rank = 0
        for index, item in enumerate(values, 1):
            if previous != int(item["value"]):
                rank = index
                previous = int(item["value"])
            item["rank"] = rank
        return values
