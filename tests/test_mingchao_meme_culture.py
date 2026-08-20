from bot.services.mingchao_meme_culture import entries, render_search, search


def test_mingchao_meme_culture_has_sources_for_every_entry():
    all_entries = entries()
    assert len(all_entries) >= 139
    assert len({entry.entry_id for entry in all_entries}) == len(all_entries)
    assert all(entry.source_name for entry in all_entries)
    assert all(entry.source_url.startswith("https://") for entry in all_entries)
    assert all(
        entry.category
        in {"character", "system", "world", "item", "mode", "game", "community", "zhijiang"}
        for entry in all_entries
    )


def test_mingchao_official_database_searches_characters_and_core_terms():
    assert search("秧秧 共鸣者")[0].entry_id == "official-character-yangyang"
    assert search("导电属性")[0].entry_id == "official-element-electro"
    assert search("共鸣回路")[0].entry_id == "official-term-forte-circuit"
    assert search("黑海岸组织")[0].entry_id == "official-faction-black-shores"


def test_mingchao_meme_culture_searches_game_slang_and_community_memes():
    assert search("鸣潮公式")[0].entry_id == "wuwa-formula"
    assert search("牢卡")[0].entry_id == "wuwa-solaris-strongest"
    assert search("雪豹")[0].entry_id == "wuwa-snow-leopard"
    assert search("小土豆")[0].entry_id == "wuwa-small-potato"
    assert search("羊咩之手")[0].entry_id == "wuwa-encore-weapon-hands"
    assert search("神秘剪刀女")[0].entry_id == "wuwa-mysterious-scissor-girl"


def test_mingchao_meme_culture_searches_zhijiang_cross_community_memes():
    assert search("乃琳直播鸣潮")[0].entry_id == "zj-nailin-wuwaves"
    assert search("潮友")[0].entry_id == "zj-chaoyou"
    assert search("感谢巨龙")[0].entry_id == "zj-ganxie-julong"


def test_mingchao_meme_culture_matches_natural_language_questions():
    assert search("鸣潮公式是什么")[0].entry_id == "wuwa-formula"
    assert "wuwa-formula" in {entry.entry_id for entry in search("鸣潮有什么梗")}


def test_mingchao_meme_culture_render_includes_source():
    rendered = render_search("鸣潮公式")
    assert "《鸣潮公式" in rendered
    assert "来源：" in rendered
    assert "https://" in rendered


def test_carol_terms_never_return_from_mingchao_meme_culture():
    assert all(not entry.blocked for entry in entries())
    assert search("珈乐") == ()
    assert search("皇珈骑士") == ()
