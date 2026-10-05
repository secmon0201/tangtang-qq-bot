"""Render synthetic Harness ranking previews without QQ or model calls."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tangtang_harness.business.community_web import CommunityWebRenderer, page_html
from tangtang_harness.store import Store
from tangtang_harness.tools import ToolExecutor
from tangtang_harness.types import InboundEvent, ToolCall


class OfflineGateway:
    self_id = 999

    async def call_api(self, action, **params):
        raise AssertionError(f"Unexpected QQ call: {action}")


async def main() -> None:
    output = ROOT / "runtime" / "ranking-review"
    renderer = CommunityWebRenderer(output)
    with TemporaryDirectory(prefix="harness-ranking-") as directory:
        root = Path(directory)
        store = Store(root)
        store.set_setting("avatar_fetch_enabled", False)
        tools = ToolExecutor(OfflineGateway(), root, store)
        try:
            now = tools._now()
            for group, name in ((201, "示例群"), (202, "示例二群")):
                tools.domains.ensure_group(group, group_name=name)
                for index in range(12):
                    for offset in range(7):
                        with tools.db.connect() as connection:
                            connection.execute(
                                "INSERT INTO daily_counts(group_id,user_id,day,nickname,message_count,updated_at) VALUES(?,?,?,?,?,?)",
                                (group, 101 + index, (now.date() - timedelta(days=offset)).isoformat(),
                                 f"示例成员{index + 1:02d}", (12 - index) * (offset + 1) * 7, now.isoformat()))
            group_payload = await tools.web.ranking_data(201, "day")
            path = await renderer.render_ranking(group_payload)
            print(f"Group preview: {path}")
            page = renderer._page
            assert await page.locator(".ranking-row").count() == 12
            assert not await page.locator(".interactive-bar").is_visible()
            assert await page.locator(".chart-plot").is_visible()
            domain = tools.domains.create_cluster("示例集群")
            for group in (201, 202):
                tools.domains.add_group_to_cluster(group, domain.domain_id)
            cluster_payload = await tools.web.ranking_data(201, "week", cluster=True)
            path = await renderer.render_ranking(cluster_payload)
            print(f"Cluster preview: {path}")
            assert await page.locator(".chart-avatar-back").count() == 2
            payload = await tools.web.read("ranking", group_id=201)
            async def api(route):
                await route.fulfill(json=await tools.web.read("ranking", group_id=201, scope="week"))
            await page.route("**/business/ranking/api*", api)
            source = tools.web._community_paths(page_html("ranking", payload))
            await page.route("http://example.invalid/", lambda route: route.fulfill(body=source, content_type="text/html"))
            for width in (1080, 390):
                await page.set_viewport_size({"width": width, "height": 900})
                await page.goto("http://example.invalid/")
                assert await page.locator(".interactive-bar").is_visible()
                await page.screenshot(path=str(output / f"web-{width}.png"), full_page=True)
                await page.locator('[data-scope="week"]').click()
                await page.wait_for_function("document.querySelector('.title-secondary').textContent === '本周发言榜'")
            event = InboundEvent("review", 999, 101, 201, "")
            result = await tools.execute(event, ToolCall("ranking", {"scope": "day"}))
            assert result.status == "ok"
            print("Capture controls, group/cluster charts, mobile page and scope filter: passed")
        finally:
            await tools.close()
            await renderer.close()


if __name__ == "__main__":
    asyncio.run(main())
