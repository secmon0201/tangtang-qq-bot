from bot.services.zhijiang_knowledge import entries, render_search, search


def test_local_zhijiang_knowledge_has_sources_for_every_entry():
    assert len(entries()) >= 10
    assert all(entry.source_name and entry.source_url.startswith("https://") for entry in entries())


def test_local_zhijiang_knowledge_searches_characters_and_history():
    assert search("嘉然")[0].entry_id == "diana-profile"
    history = search("冬日奇迹")
    assert any(entry.entry_id == "public-timeline-2024" for entry in history)


def test_local_zhijiang_knowledge_covers_current_generation_and_culture():
    assert search("心宜")[0].entry_id == "fiona-profile"
    assert search("思诺")[0].entry_id == "gladys-profile"
    assert search("小心思")[0].entry_id == "xiaoxinsi"
    assert search("闪耀舞台")[0].entry_id == "xiaoxinsi"
    assert "二期生" in search("闪耀舞台")[0].summary
    assert search("阿草")[0].entry_id == "zhijiang-entertainment"
    assert search("灵境少女")[0].entry_id == "chinese-group-name"
    assert any(
        entry.entry_id in {"timeline-2026", "wuwaves-livestreams"}
        for entry in search("2026 鸣潮", limit=5)
    )
    assert search("粉丝名")[0].entry_id == "fan-name-slogan"


def test_local_zhijiang_knowledge_records_xinyi_and_sinuo_livestream_memes():
    xinyi = search("发动ruby咬鼠你们")[0]
    assert xinyi.entry_id == "fiona-profile"
    assert {"略宜区", "偷电", "老鼠", "小海豹"} <= set(xinyi.tags)
    assert "待公开直播原片或切片复核" in xinyi.source_note

    sinuo_hits = search("思诺直播间", limit=3)
    sinuo = next(entry for entry in sinuo_hits if entry.entry_id == "gladys-profile")
    assert sinuo.source_url == "https://live.bilibili.com/30858592"
    assert {"铁柱", "四看一", "只把笑脸给她的电脑和手机", "30858592"} <= set(sinuo.tags)
    assert search("30858592")[0].entry_id == "gladys-profile"


def test_local_zhijiang_knowledge_matches_natural_language_questions():
    assert search("嘉然是谁")[0].entry_id == "diana-profile"
    assert search("介绍一下贝拉")[0].entry_id == "bella-profile"
    assert search("糖糖 心宜思诺什么关系")[0].entry_id == "xiaoxinsi"


def test_carol_related_entries_stay_internal_but_are_not_returned():
    blocked_ids = {entry.entry_id for entry in entries() if entry.blocked}
    assert {
        "carol-profile",
        "original-members",
        "debut-timeline",
        "shining-stage",
        "acaocao",
        "member-singles",
        "fan-name-glossary",
        "official-accounts",
        "current-formation-2026",
    } <= blocked_ids
    assert search("珈乐") == ()
    assert search("皇珈骑士") == ()
    assert all(not entry.blocked for entry in search("A-SOUL", limit=20))


def test_render_search_mentions_cross_referenced_entries():
    rendered = render_search("鸣潮", limit=5)
    assert "相关词条：" in rendered
    assert "乃琳直播《鸣潮》与企划回暖" in rendered


def test_song_version_query_only_returns_matching_song_entries():
    hits = search("你喜欢听哪个版本的Quiet呢", limit=5)
    ids = {entry.entry_id for entry in hits}
    assert hits[0].entry_id == "song-quiet-current-version"
    assert {"song-version-rule", "song-new-five-member-status"} <= ids
    assert not ids & {
        "song-chaomin-gan-current-version",
        "song-chuxi-current-version",
        "song-chuanshuo-shijie-current-version",
        "song-other-old-repertoire-current",
    }
