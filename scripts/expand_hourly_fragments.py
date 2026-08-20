from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "bot" / "resources" / "zhijiang_hourly_copy.json"
PERIOD_KEYS = ("morning", "daytime", "evening", "night")
SEGMENT_KEYS = ("before", "middle", "after")
TARGET_PER_SEGMENT = 100


# Each seed is combined with four different sentence frames. The resulting
# fragments remain independent source entries; they are not the final runtime
# three-part messages.
EXTRA_SEEDS = {
    "morning": {
        "before": (
            "把窗台的露水排成一行",
            "在窗边确认今天的风向",
            "把第一节课的勇气装进书包",
            "给还在打哈欠的闹钟递上通知",
            "把晨雾折成一张轻薄的地图",
            "在食堂门口闻到热气就重新上线",
            "给空白日程留出一个开心位置",
            "把昨夜的梦藏进校服口袋",
            "向刚亮的路灯完成交接",
            "把第一声鸟鸣听成开场音乐",
            "在镜子前把精神状态调整到可见",
            "给书页夹上一枚粉色小书签",
            "把迟到的灵魂从被窝里请出来",
            "和门口的风交换一条好消息",
            "把太阳的亮度调到不刺眼",
            "为今天的待办事项排好座位",
            "把一碗热饭写进上午计划",
            "向长廊尽头投递一份早安",
            "给鞋带系上今天的出发信号",
        ),
        "middle": (
            "校钟把清醒分成两半",
            "云朵为早八留下了缓冲区",
            "食堂的第一锅热气完成了签到",
            "广播站把闹钟余音剪成了前奏",
            "阿草把课程表贴到了最亮的地方",
            "窗帘替房间挡住了一点过分积极的阳光",
            "书包里的便签开始进行今日点名",
            "一阵海风把困意推到走廊外面",
            "校门口的脚步声逐渐找回节拍",
            "咖啡杯和清水杯交换了值班表",
            "晨雾在远处给校园让出视线",
            "第一节课的投影光终于稳定下来",
            "一声笑从宿舍楼拐到广播站",
            "早饭的香气把所有支线拉回主线",
            "闹钟把重复提醒改成了一次诚恳邀请",
            "枝江的街道给新一天铺好底色",
            "书页在风里翻到了可以开始的位置",
            "阳光把桌角照成了临时舞台",
            "长廊的回声替大家确认了已经出发",
        ),
        "after": (
            "早餐和勇气都要按时领取",
            "今天的灵魂允许晚几分钟加载",
            "出门之前先确认水杯已经在手边",
            "第一项成就可以是平安抵达",
            "清醒不必满格，能启动就值得掌声",
            "早八的剧情从一口热饭开始",
            "把迟到的理由留给笑话，不留给自己",
            "今天的好心情已经获得临时通行证",
            "先把眼前的路走完，再讨论远方",
            "闹钟完成工作，接下来轮到行动接班",
            "清晨的光不催促，只负责照亮选择",
            "给今天写下一个不难完成的开头",
            "出发就是最可靠的状态更新",
            "热气腾腾的生活已经重新上线",
            "先照顾好自己，再去照顾其他计划",
            "一枚小小的完成足够开启上午",
            "今天的第一声笑应该属于自己",
            "把勇气放在最容易找到的口袋里",
            "早安不是口号，是给自己的确认",
        ),
    },
    "daytime": {
        "before": (
            "把午后的光切成几块清晰的工作区",
            "从待办清单里捞出一个最轻的目标",
            "给进度条旁边留一张休息便签",
            "让键盘声先完成一段热身",
            "在屏幕边发现一块不急着消失的晴天",
            "把下午茶写进今天的正式议程",
            "给会议室的空气换一层柔光滤镜",
            "把一阵风放进工作间隙暂存",
            "向投影仪递交一份清醒申请",
            "为卡住的页面准备一个重新加载按钮",
            "把窗外的云朵请来旁听半小时",
            "给桌面的阳光安排一个固定工位",
            "把复杂的任务拆成三张小卡片",
            "在午后的静音里找到一颗笑点",
            "让咖啡杯先替脑子保管灵感",
            "把工作群的消息按轻重排好队",
            "给今天的专注力发放一张临时证件",
            "从文件夹深处找回一页未完成的勇气",
            "把下班前的目标放到看得见的位置",
        ),
        "middle": (
            "投影仪把每个字都照得有了边界",
            "进度条在中途做了一次深呼吸",
            "阿草给完成事项逐项盖上小印章",
            "窗外的云朵决定暂时不参加会议",
            "工作群把一个问题传成了接力赛",
            "桌角的便签从装饰升级成了导航",
            "风扇开始主持一场低调的圆桌会",
            "键盘声在某个瞬间找回了节拍",
            "食堂的热气替下午保留了一点人情味",
            "广播站把困意调成了不会打扰人的音量",
            "一杯水在屏幕旁边完成了安静陪伴",
            "阳光把每个小目标照出了轮廓",
            "会议的严肃感被一张表情包轻轻推开",
            "待办清单在最后一页发现了新的空间",
            "校钟没有催促，只准确报告时间经过",
            "午后风把文件翻到了下一段",
            "一块糖纸把沉默包装成了花絮",
            "枝江的窗户统一开启了柔光模式",
            "屏幕右下角的提醒语气突然变得体面",
        ),
        "after": (
            "摸鱼可以维护灵感，但记得回来签到",
            "先把水喝完，进度条会尊重认真",
            "工作不会蒸发，下午茶也不该缺席",
            "让复杂事情拆成三步，第一步先点头",
            "给脑子留一个空位，好点子才进得来",
            "会议顺利结束就是今天的阶段胜利",
            "把一件小事做完，下午就有了主线",
            "屏幕可以亮，心情也要保留亮度",
            "进度条走得慢，也不能让它失去信号",
            "先吃饭再思考，答案通常更友好",
            "把待办写下来，别让它在脑内循环",
            "短暂发呆是系统整理缓存，不算掉线",
            "效率不靠喊话，靠一个接一个的小完成",
            "给下午的自己发一枚鼓励印章",
            "先关掉脑内会议，再打开真正的页面",
            "把疲惫放到休息区，别让它抢主舞台",
            "阳光已经给出提示，剩下的交给行动",
            "工作群可以晚点回，水分不能晚点补",
            "在下班之前，给自己留一份完成感",
        ),
    },
    "evening": {
        "before": (
            "把晚霞的颜色收进回家路",
            "从排练室最后一个节拍里走出来",
            "给今天的疲惫安排一个门口寄存处",
            "让食堂窗口的热气先接管情绪",
            "把下班后的风调成慢速播放",
            "在校园门口和一盏路灯完成交接",
            "给月亮预留一个适合看戏的位置",
            "把白天的待办折成一张小纸船",
            "向回家方向投递一封不加班申请",
            "把排练室的灯光轻轻关到一半",
            "在晚饭前找回今天的笑点",
            "让云层替忙碌的一天完成收尾",
            "给回家路上的脚步换一段伴奏",
            "把夕阳折成一张可以带走的车票",
            "从操场边捡起一阵不催人的晚风",
            "把一盏小灯留在长廊尽头",
            "向食堂的香气确认今晚的主线",
            "给今晚的计划删掉一项不必要的加班",
            "把掌声和晚霞一起装进书包",
        ),
        "middle": (
            "放学的人流从教学楼汇到食堂",
            "晚霞把舞台灯染成柔软的颜色",
            "排练室的节拍在黄昏里完成交接",
            "阿草把落日折成一张节目单",
            "回家路上的风替每个人按下慢速播放",
            "月亮提前来到广播站门口排队",
            "食堂把热气稳稳留给晚归的人",
            "枝江的云层像一张刚拆开的唱片",
            "窗台的小灯开始接管下班后的视线",
            "夜色把操场的脚步声收进盒子",
            "远处的钟楼把晚风调成柔和模式",
            "广播站把白天的忙碌剪成花絮",
            "排练室门缝里漏出一小束坚持",
            "云层开始为夜间节目更换布景",
            "晚风把白天没说完的话翻到下一页",
            "一盏路灯在枝江长廊边准时亮起",
            "阿草把后台线缆整理成一条小路",
            "星星从远处发来今晚的值班表",
            "晚餐把所有人的情绪调回生活",
        ),
        "after": (
            "今天的辛苦先放下，晚饭是正式收尾",
            "回家路线已经加载，沙发正在发出召唤",
            "把疲惫交给晚风，别把好心情一起交出去",
            "傍晚的任务只有一个，让自己回到生活",
            "先吃一口热的，再考虑下一场剧情",
            "下班不是结束，是把自己接回来的开始",
            "让今天的努力在一盏灯下获得确认",
            "晚霞不催人，大家也可以慢一点走",
            "把工作模式关小声，把生活模式开大声",
            "主舞台暂时落幕，观众请平安回家",
            "一顿饭至少能解决今天的饥饿问题",
            "把手机亮度调低，把回家的心情调高",
            "晚风已经替你吹远了不必要的内耗",
            "今天做得够多了，剩下的明天再开场",
            "给疲惫发一张下班通行证",
            "回到自己的房间，就是今天的稳健胜利",
            "让热气腾腾的生活重新上线",
            "平安就是漂亮的结尾，不需要额外特效",
            "把晚饭写进日程，把快乐写在边角",
        ),
    },
    "night": {
        "before": (
            "把夜色调成适合耳语的音量",
            "给今天的待办事项逐项改成明天",
            "从宿舍窗边确认星星还在线",
            "把白天的喧闹叠好放在门外",
            "给枕边的小盒子收进一个笑点",
            "把耳机里的最后一首歌调低一格",
            "向月亮递交一份今晚的值班表",
            "给清醒设置一条柔软边界",
            "把手机充电线安排成续航队长",
            "在夜色里给明天留下一句预告",
            "把不急着解决的心事放到候场区",
            "给窗帘交代好今晚的保密任务",
            "从深夜广播里捞出一段轻音乐",
            "让最后一点喧闹在门口排队",
            "把月光铺到桌角当作临时台灯",
            "向远处的星轨确认回梦路线",
            "给疲惫留出一块不会被打扰的空地",
            "把今天的故事翻到适合暂停的页码",
            "为明天的自己预留一杯水的位置",
        ),
        "middle": (
            "城市的灯光在远处排成温柔弹幕",
            "午夜电台把音量放到刚好听见的位置",
            "排练室只保留一颗小灯照着坚持",
            "阿草把晚安通知贴得端端正正",
            "夜风沿长廊完成一轮安静巡逻",
            "星星把今天的待办逐项改成明日",
            "宿舍楼的充电线承担全场续航",
            "月亮在广播站值班表上签了名",
            "窗帘把余下的喧闹轻轻折好",
            "一段旋律陪人经过安静夜色",
            "枝江深夜把所有声音调成耳语",
            "小灯在桌边守住清醒的边界",
            "晚安像一枚小印章落在屏幕上",
            "夜色把白天的褶皱慢慢抚平",
            "广播站给仍在线的人留片尾音乐",
            "月光把窗台照成一块安静舞台",
            "阿草确认后台所有小灯安全值班",
            "星轨把远处的光点排成柔软路线",
            "梦境在远处打开一扇不催促人的门",
        ),
        "after": (
            "把晚安说出口，剩下的交给梦境",
            "今天已经很努力，睡眠是正式奖励",
            "手机可以明天再看，眼睛今晚先下班",
            "让被窝接管下一小时，世界不会掉线",
            "把心事折小一点，明天再慢慢展开",
            "今晚不必继续开新地图，安全回梦就好",
            "给自己发一张休息许可，立即盖章生效",
            "黑夜不是空白，是重新加载的背景",
            "把余下的清醒交给枕头保管",
            "夜班到此收尾，大家平安进入梦境",
            "先睡好，再处理那些看起来很大的事情",
            "今天的故事停在这里，明天继续更新",
            "关灯不是退出生活，是保存当前进度",
            "让一杯水成为今晚最后的小成就",
            "把疲惫放进梦里回收，醒来再领取勇气",
            "晚安不需要长篇大论，准时抵达就体面",
            "把屏幕交给黑夜，把自己交给休息",
            "睡一觉，很多问题会获得更好的语气",
            "梦境已经开门，欢迎自己平安入场",
        ),
    },
}


# A few cross-period seeds cover collisions with the original hand-written pool.
# They stay scoped to the fragment position so each segment remains coherent.
BONUS_SEEDS = {
    "before": (
        "把今天最想完成的事放到第一格",
        "给正在加载的心情留一秒缓冲",
        "让第一条消息先带来好消息",
    ),
    "middle": (
        "给卡住的思路换一个入口",
        "把已经完成的部分圈出来",
        "让注意力从一件小事重新上线",
    ),
    "after": (
        "给今天的收尾留出一盏灯",
        "把想说的话放到明天也来得及",
        "让最后一件小事成为安心信号",
    ),
}


FRAME_TEMPLATES = {
    "before": (
        "{name}{idea}，",
        "晨光替{name}{idea}，",
        "广播站看见{name}{idea}，",
        "阿草记录：{name}{idea}，",
    ),
    "middle": (
        "{idea}，",
        "与此同时，{idea}，",
        "广播站补充：{idea}，",
        "风从旁边经过，{idea}，",
    ),
    "after": (
        "{idea}。",
        "最后留下的批语是：{idea}。",
        "大家听完只记住一件事：{idea}。",
        "本时段的收尾写着：{idea}。",
    ),
}


def expand_payload(payload: dict[str, object]) -> tuple[int, int]:
    periods = payload.get("periods")
    if not isinstance(periods, dict):
        raise ValueError("hourly copy source has no periods")
    added = 0
    total = 0
    for period_key in PERIOD_KEYS:
        raw_period = periods.get(period_key)
        if not isinstance(raw_period, dict):
            raise ValueError(f"missing period: {period_key}")
        raw_segments = raw_period.get("segments")
        if not isinstance(raw_segments, dict):
            raise ValueError(f"missing segments: {period_key}")
        for segment_key in SEGMENT_KEYS:
            raw_segment = raw_segments.get(segment_key)
            if not isinstance(raw_segment, dict) or not isinstance(raw_segment.get("texts"), list):
                raise ValueError(f"missing segment texts: {period_key}.{segment_key}")
            texts = raw_segment["texts"]
            existing = {str(text) for text in texts}
            extras: list[str] = []
            ideas = (*EXTRA_SEEDS[period_key][segment_key], *BONUS_SEEDS[segment_key])
            for idea in ideas:
                for template in FRAME_TEMPLATES[segment_key]:
                    text = template.format(name="{name}", idea=idea)
                    if text not in existing:
                        existing.add(text)
                        extras.append(text)
            texts.extend(extras)
            added += len(extras)
            total += len(texts)
            if len(texts) < TARGET_PER_SEGMENT:
                raise ValueError(f"{period_key}.{segment_key} has only {len(texts)} fragments")
    payload["target_combinations"] = max(int(payload.get("target_combinations", 5000)), 5000)
    return added, total


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand each runtime hourly-copy segment to 100 entries")
    parser.add_argument("--check", action="store_true", help="validate counts without writing")
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    args = parser.parse_args()
    payload = json.loads(args.source.read_text(encoding="utf-8-sig"))
    added, total = expand_payload(payload)
    if not args.check:
        args.source.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"hourly fragments {'checked' if args.check else 'expanded'}: added={added}; total_fragments={total}; per_segment=100+")


if __name__ == "__main__":
    main()
