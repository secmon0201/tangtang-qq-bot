"""Import the reviewed 2026-09-19 Denia animated-expression pack."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PERSONA_DIR = ROOT / "bot" / "resources" / "personas" / "denia"
EXPRESSION_DIR = PERSONA_DIR / "expressions"
CATALOG_PATH = PERSONA_DIR / "expression_catalog.json"
ASSET_MANIFEST_PATH = PERSONA_DIR / "asset-manifest.json"
SOURCE_LABEL = "operator-supplied/2026-09-19"
ANALYSIS_METHOD = "assistant_direct_image_and_keyframe_inspection_v2"
ANALYSIS_NOTE = "视觉观察与语用解释分别记录；情绪为上下文相关解释，不是客观心理诊断。"


@dataclass(frozen=True, slots=True)
class Entry:
    source_sha256: str
    expression_id: str
    filename: str
    name: str
    use: str
    avoid: str
    emotion: tuple[str, ...]
    intensity: float
    action: str
    group: str
    aliases: tuple[str, ...]
    reviewed_frames: tuple[int, ...]
    frames: int

    @property
    def active_filename(self) -> str:
        return str(Path(self.filename).with_suffix(".gif"))


ENTRIES = (
    Entry("86ad450e78cd93580737ae25c28c7629cedd984783ef51f257efaa8eb9524bfe", "expr_052", "挥手浅笑.webp", "挥手浅笑", "见面问好、轻松告别、友好回应", "不以挥手带过严肃求助或坏消息", ("友好招呼", "温和亲切"), 0.35, "先带着浅笑看向前方，抬起红手套挥手后自然放下", "greeting", ("挥手浅笑", "挥手", "问好"), (0, 9, 17, 26, 34, 43), 44),
    Entry("16c5d4e47c62030a8688c9b1efb083c74c9fe0a12933af4db334daecfa79f02e", "expr_053", "被摸头安心.webp", "被摸头安心", "被安慰、被认可或收到温柔关心时回应", "不把普通互动自动解释为亲密或恋爱关系", ("安心", "被安慰后的温暖"), 0.4, "闭眼接受对方摸头，随后睁眼露出安静的微笑", "comforted", ("被摸头安心", "摸头", "安心"), (0, 6, 12, 19, 25, 31), 32),
    Entry("2c8386971b845ec119f2eddd8f17969014d60a363ec99a75420cbe1f2beed222", "expr_054", "含泪眨眼.webp", "含泪眨眼", "感动落泪、克制地表达伤心或不舍", "不夸大用户的痛苦，也不以哭泣抢走倾诉者注意", ("含泪", "伤感不舍"), 0.7, "眼部近景逐渐泛起泪光，闭眼后泪水滑落，再缓缓睁眼", "crying", ("含泪眨眼", "眼泪", "落泪"), (0, 11, 22, 34, 45, 56), 57),
    Entry("21694afbdf110857913f09f14686a26298a3331af571707a933c7d1aad1bc855", "expr_055", "暖阳抬眼.webp", "暖阳抬眼", "温和接话、被理解后放松、安静表示认可", "不用于强烈庆祝或明显悲伤的场景", ("平静", "温柔释然"), 0.25, "在暖色光线下从闭眼低头缓缓抬眼，最后露出浅笑", "gentle_smile", ("暖阳抬眼", "抬眼浅笑", "温柔抬眼"), (0, 10, 20, 29, 39, 49), 50),
    Entry("4f0e181bad4cc935e06d7828a1f34c10ace8c5037ed8df3de389d175664bb54e", "expr_056", "指尖逗笑.webp", "指尖逗笑", "轻松逗趣、给自己打气、故意摆出笑脸", "不在悲伤倾诉或严肃道歉时强行逗笑", ("俏皮", "努力开心"), 0.5, "从自然说话变成用两根食指托住嘴角，闭眼摆出笑脸", "playful_smile", ("指尖逗笑", "托嘴角", "逗笑"), (0, 9, 18, 26, 35, 44), 45),
    Entry("0761f731e408c7bf6eaec03dea41ae00669cf83e46dafbe3efe76cba2c0c19e4", "expr_057", "害羞闭眼笑.webp", "害羞闭眼笑", "收到夸奖后腼腆道谢、轻松地开心回应", "不把害羞自动解释成恋爱示好", ("腼腆", "开心"), 0.4, "先垂眼浅笑，随后闭起双眼并轻轻低头", "shy", ("害羞闭眼笑", "腼腆笑", "害羞"), (0, 8, 15, 23, 30, 38), 39),
    Entry("3677670baebfd0a7213569bb449820963528861868195d0a4d1a7efaf7b0d707", "expr_058", "眨眼委屈.webp", "眨眼委屈", "小事不顺、被熟人逗弄后表达一点委屈", "不用于真实冲突、投诉或需要实际帮助的情境", ("委屈", "轻微闹别扭"), 0.55, "坐在紫色灯光前抿嘴委屈，反复眨眼并短暂闭眼", "hurt", ("眨眼委屈", "委屈眨眼", "小委屈"), (0, 8, 16, 24, 32, 40), 41),
    Entry("eb7f45b70a5a7810877257c4651fd3b3f6a19ec6cee181053d1ab053147ec79c", "expr_059", "愣住不安.webp", "愣住不安", "突然没反应过来、听到意外消息后发愣", "真实危险或紧急求助时先提供有用信息", ("错愕", "不安"), 0.6, "正面近景先轻轻张嘴，随后眼神紧张、嘴角收住", "bewildered", ("愣住不安", "愣住", "不安"), (0, 4, 8, 11, 15, 19), 20),
    Entry("bdfac0241f986030e4cedff28c5482e750dad9d56fe8e606ef1025b76e584576", "expr_060", "低头甜笑.webp", "低头甜笑", "小小开心、收到善意或温柔道谢", "不用于嘲讽、幸灾乐祸或严肃坏消息", ("满足", "温柔开心"), 0.4, "闭眼带笑，头部轻轻低下又回到原位", "happy", ("低头甜笑", "甜笑", "低头笑"), (0, 9, 17, 26, 34, 43), 44),
    Entry("120cb9b3d44c8a924a8692ae11d84a7fdeef088565073c61323b6335e002decb", "expr_061", "低头后释然.webp", "低头后释然", "担心消退、事情有了着落后松一口气", "问题尚未解决时不提前表现得一切都好", ("从忧虑到释然",), 0.45, "在暖光中低头垂眼，重新抬眼后逐渐露出轻松微笑", "bittersweet", ("低头后释然", "释然", "松口气"), (0, 8, 15, 23, 30, 38), 39),
    Entry("7ae079e67f41225ad56695d8012c607f018f4a008f48c360c2276cc175eb709b", "expr_062", "紧张睁眼.webp", "紧张睁眼", "听到出乎意料的消息、轻度担心地确认情况", "不把真实危机做成夸张反应，先回应事实和行动", ("紧张", "意外"), 0.6, "原本不安地看向前方，随后双眼明显睁大、嘴微张", "surprised", ("紧张睁眼", "突然紧张", "睁眼惊讶"), (0, 8, 16, 23, 31, 39), 40),
    Entry("8da8bbe5abe6f7cf537e30f9c654087e2adf51ec4273ddf0fd0a4e8bdc6d633a", "expr_063", "破涕为笑.webp", "破涕为笑", "难过后得到安慰、紧张解除或苦中作乐", "不以笑容否定仍在持续的痛苦", ("由悲伤转为释然", "含泪微笑"), 0.65, "起初眉眼低落、眼中含泪，随后闭眼笑起来并恢复平静", "bittersweet", ("破涕为笑", "含泪释然", "雨过天晴"), (0, 14, 27, 41, 54, 68), 69),
    Entry("8ac7fe50f3a2832941adac8071a8013623bc34768e0b06f2790c72aa030039fb", "expr_064", "掩嘴偷笑.webp", "掩嘴偷笑", "听到有趣的话、忍不住笑或善意接梗", "不嘲笑真实损失、失败或痛苦", ("忍俊不禁", "俏皮"), 0.55, "侧身带笑，抬起红手套遮住嘴并闭眼偷笑", "amused", ("掩嘴偷笑", "偷笑", "捂嘴笑"), (0, 5, 10, 15, 20, 25), 26),
    Entry("97619c05354de8fd1fade6377a143a262414c34ceebb5da76d65d41f22cc7706", "expr_065", "握拳期待.webp", "握拳期待", "期待结果、给对方加油或为小目标鼓劲", "不以期待给用户施压，也不用于严肃失败后的催促", ("期待", "鼓劲"), 0.5, "双手握拳收在胸前，睁大眼睛专注看向前方，轮廓轻微抖动", "encouraging", ("握拳期待", "期待", "加油"), (0, 3, 7, 10, 14, 17), 18),
    Entry("035446449005bca5d7443c2fb4edfb469e1ddd4c9529ed493533dbd1ef8d0d8b", "expr_066", "垂眸失落.webp", "垂眸失落", "小失望、遗憾或告别时有些不舍", "不渲染绝望，也不向用户索取安慰", ("失落", "不舍"), 0.5, "侧脸原本带着浅笑，目光逐渐垂下，嘴角收紧", "sad", ("垂眸失落", "失落垂眸", "不舍"), (0, 7, 14, 21, 28, 35), 36),
    Entry("7ce5dad76aa314fd234ba78ff1f7854b8ba7044e498ad6386dc4a08627ce589a", "expr_067", "抵唇嘘声.webp", "抵唇嘘声", "轻松提醒小声一点、保守小秘密或俏皮卖关子", "不能用来压制求助、揭露风险或必要的严肃表达", ("俏皮提醒", "保持安静"), 0.5, "正面说话后抬起食指抵住嘴唇，半眯眼作出嘘声动作", "playful", ("抵唇嘘声", "嘘", "小声点"), (0, 12, 24, 35, 47, 59), 60),
    Entry("a15e3d399e56766df7b25d057e7647980b1c5f1daa39751e6de0e07aacd91d9c", "expr_068", "羞涩低头.webp", "羞涩低头", "收到夸奖、被温柔关照后腼腆回应", "不自动发展成恋爱、占有或排他关系", ("羞涩", "被夸后的开心"), 0.55, "脸颊泛红地浅笑，低头闭起一只眼，神情越来越腼腆", "shy", ("羞涩低头", "脸红低头", "腼腆"), (0, 8, 16, 25, 33, 41), 42),
    Entry("b4b1ae563650e914d87addd3c115e4dda8f52a1ab7dc58f3a6dc4520883aa662", "expr_069", "含泪强笑.webp", "含泪强笑", "感动又酸涩、难过时努力保持温柔回应", "不当作纯开心庆祝，也不靠脆弱感施压用户", ("笑中带泪", "克制难过"), 0.65, "脸部近景眼中含泪、双颊泛红，短暂闭眼后勉强露出浅笑", "bittersweet", ("含泪强笑", "笑中带泪", "酸涩微笑"), (0, 12, 23, 35, 46, 58), 59),
    Entry("e6bd374162fdc72a25a1d38a36b6eb6810b18d047ed936106d34cd0eb201b5b3", "expr_070", "暖阳回眸.webp", "暖阳回眸", "友好确认、温柔接话或告别前回头回应", "不暗示额外承诺或特殊关系", ("温暖", "从容友善"), 0.3, "在暖色光线中从半闭眼转向镜头，最后露出安静微笑", "gentle_smile", ("暖阳回眸", "回眸微笑", "温暖回眸"), (0, 7, 14, 20, 27, 34), 35),
    Entry("dd6298f9ef4bde488553ba34dfb6a193fa3a7ee0e494e848cb960875ca89906a", "expr_071", "睁眼惊讶.webp", "睁眼惊讶", "没想到的新消息、轻松语境里的突然发现", "真实灾难或危险场景不用夸张表演替代帮助", ("意外", "轻度惊讶"), 0.55, "从闭眼浅笑逐渐睁开双眼，嘴微张并转为惊讶神情", "surprised", ("睁眼惊讶", "突然发现", "惊讶"), (0, 7, 13, 20, 26, 33), 34),
    Entry("74f60aef07414c3fbd44d652b745d3091a09115e39685b9c610126759224b87c", "expr_072", "歪头含泪.webp", "歪头含泪", "表达脆弱、感动或有些难过地回应", "不卖惨、不情感绑架，也不替用户定义痛苦程度", ("脆弱", "含泪不安"), 0.65, "侧着头望向前方，眼角带泪光，眼神在眨动中逐渐柔弱", "vulnerable", ("歪头含泪", "含泪歪头", "脆弱"), (0, 13, 25, 38, 50, 63), 64),
    Entry("873921e50efac41bc1053921e1386056c7a195a5afe66336bf796d9be797cabb", "expr_073", "阴影惊恐.webp", "阴影惊恐", "轻松玩梗时表达被吓到、发现离谱反转", "真实危险、创伤或恐惧倾诉中不要用惊悚画面", ("夸张惊恐", "震惊"), 0.85, "紫红阴影中的全身画面缓缓抬头，双眼亮起并直视前方", "shocked", ("阴影惊恐", "惊恐", "吓人"), (0, 8, 15, 23, 30, 38), 39),
    Entry("98191ab2d6443065603ce8a9195b9f20a7f92f654a1d8f703d853a706054d831", "expr_074", "暖阳无奈.webp", "暖阳无奈", "好笑又没办法、轻度吐槽或接受小麻烦", "不蔑视用户，不用于严肃批评或求助", ("无奈", "带笑接受"), 0.4, "暖光下先闭眼，随后半垂眼看向前方并露出无奈浅笑", "resigned", ("暖阳无奈", "无奈浅笑", "没办法"), (0, 6, 12, 19, 25, 31), 32),
    Entry("024db5296d39423d15f753aa4036ab643affbbd7f16180fe0637a08f9f7dbc48", "expr_075", "笑中难过.webp", "笑中难过", "开心里带遗憾、笑容慢慢收住的复杂感受", "不用于纯庆祝，也不把用户悲伤轻描淡写", ("笑意消退", "难过"), 0.55, "正面浅笑逐渐变得勉强，眉眼低垂，最后露出难过神情", "bittersweet", ("笑中难过", "笑容消退", "勉强微笑"), (0, 7, 13, 20, 26, 33), 34),
    Entry("11eb0fb6278e4726c5ed48d3bd86ae3543846b3c79a3438003a814db0df1765a", "expr_076", "侧目吐槽.webp", "侧目吐槽", "熟人间接离谱梗、轻度嫌弃或无语吐槽", "不用于否定真实求助、侮辱用户或严肃争执", ("无语", "轻度嫌弃"), 0.5, "先侧目半眯眼说话，随后闭眼皱眉，保持略显无语的神情", "speechless", ("侧目吐槽", "侧目无语", "嫌弃"), (0, 13, 26, 38, 51, 64), 65),
)


DUPLICATES = {
    "d07decb1baa3656fcb489f2c9d2bf27e0985e9074ab6d165d0369d518db8fd19": "expr_027",
    "dcd0ea8682cb5d0fd6b204aebba5c2c1b88714ad89b65161adf2c84d7e06cb5b": "expr_037",
    "87eef6351db3cf7be0e737cdeda0cf54475f35d40087cea48b03940f78106580": "expr_035",
    "cea38c472192cfdb70cc90d85e358818c343ea41ad995a1db207374f4082ddb3": "expr_023",
    "610f601e5a2d445db9427bccec7fecb1534e1cdaac41e7ff3e4d1806e87eacbe": "expr_010",
    "77fdf4202846efbd6fc7db14f5868907fb5e1a899de5c47efad11d9542a86432": "expr_028",
    "a490ddd6f8077129a1b8b52d8ed873bffdff4031aa1f2c0792be3fc8bfb7d984": "expr_026",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def webp_durations(path: Path) -> list[int]:
    data = path.read_bytes()
    durations: list[int] = []
    offset = 12
    while offset + 8 <= len(data):
        fourcc = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        payload = offset + 8
        if fourcc == b"ANMF" and size >= 16:
            durations.append(int.from_bytes(data[payload + 12 : payload + 15], "little"))
        offset = payload + size + (size & 1)
    return durations


def resize_gif(source: Path, target: Path) -> tuple[list[int], tuple[int, int]]:
    durations = webp_durations(source)
    with Image.open(source) as image:
        frame_count = getattr(image, "n_frames", 1)
        if len(durations) != frame_count:
            raise ValueError(f"Cannot read WebP frame durations: {source.name}")
        width, height = image.size
        scale = min(1.0, 300 / max(width, height))
        output_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        frames = []
        for index in range(frame_count):
            image.seek(index)
            frame = image.convert("RGB")
            if frame.size != output_size:
                frame = frame.resize(output_size, Image.Resampling.LANCZOS)
            frames.append(frame)
        frames[0].save(
            target,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=int(image.info.get("loop", 0)),
            disposal=2,
            optimize=True,
        )
    return durations, output_size


def catalog_row(entry: Entry, target_sha256: str) -> dict:
    return {
        "id": entry.expression_id,
        "file": entry.active_filename,
        "name": entry.name,
        "use": entry.use,
        "avoid": entry.avoid,
        "frames": entry.frames,
        "emotion": list(entry.emotion),
        "intensity": entry.intensity,
        "action": entry.action,
        "group": entry.group,
        "analysis_method": ANALYSIS_METHOD,
        "visual": entry.action,
        "aliases": list(entry.aliases),
        "visible_text": "",
        "analysis_note": ANALYSIS_NOTE,
        "reviewed_frames": list(entry.reviewed_frames),
        "sha256": target_sha256,
        "explicit_only": False,
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def import_pack(source_dir: Path) -> None:
    source_dir = source_dir.resolve(strict=True)
    source_files = sorted(path for path in source_dir.iterdir() if path.is_file())
    by_sha256 = {digest(path): path for path in source_files}
    expected = {entry.source_sha256 for entry in ENTRIES} | set(DUPLICATES)
    if set(by_sha256) != expected or len(source_files) != len(expected):
        missing = sorted(expected - set(by_sha256))
        unknown = sorted(set(by_sha256) - expected)
        raise SystemExit(f"Pack mismatch: missing={missing}, unknown={unknown}")

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    existing_ids = {row["id"] for row in catalog}
    overlapping = existing_ids & {entry.expression_id for entry in ENTRIES}
    if overlapping:
        raise SystemExit(f"Expression IDs already exist: {sorted(overlapping)}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = ROOT / "data" / "backups" / f"denia-expressions-{timestamp}"
    before_dir = backup / "before" / "expressions"
    originals_dir = backup / "source-originals"
    before_dir.mkdir(parents=True)
    originals_dir.mkdir(parents=True)
    for path in EXPRESSION_DIR.iterdir():
        if path.is_file():
            shutil.copy2(path, before_dir / path.name)
    shutil.copy2(CATALOG_PATH, backup / "before" / CATALOG_PATH.name)
    shutil.copy2(ASSET_MANIFEST_PATH, backup / "before" / ASSET_MANIFEST_PATH.name)
    for path in source_files:
        shutil.copy2(path, originals_dir / path.name)

    asset_manifest = json.loads(ASSET_MANIFEST_PATH.read_text(encoding="utf-8"))
    additions = []
    for entry in ENTRIES:
        source = by_sha256[entry.source_sha256]
        target = EXPRESSION_DIR / entry.active_filename
        if target.exists():
            raise SystemExit(f"Destination already exists: {target.name}")
        durations, output_size = resize_gif(source, target)
        target_sha256 = digest(target)
        with Image.open(target) as image:
            if image.format != "GIF" or image.size != output_size or image.n_frames != entry.frames:
                raise SystemExit(f"Generated asset failed validation: {target.name}")
            actual_durations = []
            for frame_index in range(image.n_frames):
                image.seek(frame_index)
                actual_durations.append(image.info.get("duration"))
        if actual_durations != durations:
            raise SystemExit(f"Generated durations changed: {target.name}")
        catalog.append(catalog_row(entry, target_sha256))
        asset_manifest["files"].append(
            {
                "source": f"{SOURCE_LABEL}/{source.name}",
                "path": f"expressions/{entry.active_filename}",
                "sha256": entry.source_sha256,
            }
        )
        additions.append(
            {
                "id": entry.expression_id,
                "source": source.name,
                "source_sha256": entry.source_sha256,
                "file": entry.active_filename,
                "active_sha256": target_sha256,
                "source_size": list(Image.open(source).size),
                "active_size": list(output_size),
                "frames": entry.frames,
                "durations": durations,
            }
        )

    write_json(CATALOG_PATH, catalog)
    write_json(ASSET_MANIFEST_PATH, asset_manifest)
    write_json(
        backup / "manifest.json",
        {
            "source": str(source_dir),
            "imported": additions,
            "duplicates": [
                {
                    "source": by_sha256[source_sha256].name,
                    "source_sha256": source_sha256,
                    "existing_id": existing_id,
                }
                for source_sha256, existing_id in DUPLICATES.items()
            ],
        },
    )
    (backup / "README.txt").write_text(
        "before/ 保存导入前的运行时表情目录与清单。\n"
        "source-originals/ 保存操作者提供的 32 个原始 WebP 文件。\n"
        "manifest.json 记录 25 个导入项和 7 个视觉同帧重复项。备份不得自动清理。\n",
        encoding="utf-8",
    )
    print(json.dumps({"backup": str(backup), "imported": len(ENTRIES), "duplicates": len(DUPLICATES)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    import_pack(args.source)


if __name__ == "__main__":
    main()
