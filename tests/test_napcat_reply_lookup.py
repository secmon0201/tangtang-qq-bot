from pathlib import Path


NAPCAT_BUNDLE = Path(__file__).parents[1] / "NapCat.Shell" / "napcat.mjs"


def test_napcat_reply_lookup_treats_zero_random_as_missing():
    bundle = NAPCAT_BUNDLE.read_text(encoding="utf-8")
    reply_lookup = bundle.split(
        "replyElement: async (element, msg, _, quick_reply) => {", 1
    )[1].split("videoElement: async", 1)[0]

    assert 'String(msgRandom) !== "0"' in reply_lookup
    assert reply_lookup.count("findReplyMsg(replyMsgList") == 3
    assert "msgRandom ? replyMsgList" not in reply_lookup
