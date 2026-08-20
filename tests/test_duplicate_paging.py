from bot.services.duplicate import render_duplicate_page, split_duplicate_message


def make_result(count: int) -> list[dict[str, object]]:
    return [
        {
            "user_id": index,
            "groups": [
                {"group_id": 1001, "group_name": "群一"},
                {"group_id": 1002, "group_name": "群二"},
            ],
        }
        for index in range(1, count + 1)
    ]


def test_duplicate_results_within_image_capacity_are_not_paginated():
    labels = ((1001, "群一"), (1002, "群二"))
    message, total_pages = render_duplicate_page(make_result(4), 1, "#", labels)
    assert total_pages == 1
    assert "查重结果：4 名重复成员" in message
    assert "第 1/" not in message
    assert "页2" not in message
    assert "1️⃣ 群一(1001) [起点]" in message
    assert "2️⃣ 群二(1002) [目标]" in message
    assert message.count("：1️⃣、2️⃣") == 4


def test_duplicate_pages_use_one_stable_result_set():
    result = make_result(5)
    labels = ((1001, "群一"), (1002, "群二"))
    first_page, total_pages = render_duplicate_page(result, 1, "#", labels)
    second_page, second_total_pages = render_duplicate_page(result, 2, "#", labels)
    assert total_pages == second_total_pages == 2
    assert "查重结果：5 名重复成员，第 1/2 页" in first_page
    assert "查重结果：5 名重复成员，第 2/2 页" in second_page
    assert "4：1️⃣、2️⃣" in first_page
    assert "5：1️⃣、2️⃣" in second_page


def test_duplicate_page_marks_three_groups_without_repeating_group_details():
    result = make_result(1)
    result[0]["groups"] = [
        {"group_id": 1001, "group_name": "群一"},
        {"group_id": 1002, "group_name": "群二"},
        {"group_id": 1003, "group_name": "群三"},
    ]

    message, total_pages = render_duplicate_page(
        result,
        1,
        "#",
        ((1001, "群一"), (1002, "群二"), (1003, "群三")),
    )

    assert total_pages == 1
    assert "1️⃣、2️⃣、3️⃣" in message
    assert message.count("群一(1001)") == 1
    assert message.count("群二(1002)") == 1
    assert message.count("群三(1003)") == 1


def test_duplicate_page_supports_all_groups_mode():
    message, total_pages = render_duplicate_page(
        make_result(1),
        1,
        "#",
        ((1001, "群一"), (1002, "群二")),
        scope_mode="all",
        command_name="查重1",
    )

    assert total_pages == 1
    assert "查重范围：所有指定群互相查重" in message
    assert "1️⃣ 群一(1001) [参与查重]" in message
    assert "2️⃣ 群二(1002) [参与查重]" in message
    assert "起点" not in message
    assert "目标" not in message


def test_duplicate_page_combines_all_members_without_pagination():
    result = make_result(9)
    message, total_pages = render_duplicate_page(
        result,
        1,
        "#",
        ((1001, "群一"), (1002, "群二")),
        page_size=None,
    )

    assert total_pages == 1
    assert "第 1/" not in message
    assert "下一页" not in message
    assert message.count("：1️⃣、2️⃣") == 9


def test_long_duplicate_output_splits_only_at_line_boundaries():
    chunks = split_duplicate_message("标题\n" + "\n".join(f"{index}: 内容" for index in range(100)), limit=80)
    assert len(chunks) > 1
    assert "\n" not in chunks[0][-1:]
    assert "0: 内容" in chunks[0]
    assert "99: 内容" in "\n".join(chunks)
