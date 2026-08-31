"""Reusable local HTML-to-PNG screenshot boundary for QQ visual outputs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import time_ns
from typing import Any


class LocalWebScreenshotRenderer:
    """Keep one local browser warm and capture a named element as PNG."""

    WIDTH = 1080

    def __init__(self, output_dir: Path, output_subdir: str) -> None:
        self.output_dir = output_dir / output_subdir
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    async def warmup(self) -> None:
        async with self._lock:
            await self._ensure_page()

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
            if self._playwright is not None:
                await self._playwright.stop()
            self._playwright = self._browser = self._page = None

    async def render_html(self, html: str, prefix: str, selector: str = "#capture-root") -> Path:
        async with self._lock:
            page = await self._ensure_page()
            await page.set_content(html, wait_until="load")
            await page.evaluate("document.fonts.ready")
            await page.wait_for_function(
                "Array.from(document.images).every((image) => image.complete)", timeout=12_000
            )
            root = page.locator(selector)
            await root.wait_for(state="visible", timeout=5_000)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.output_dir / f"{prefix}_{time_ns()}.png"
            await root.screenshot(path=str(path), type="png", animations="disabled")
            return path

    async def _ensure_page(self) -> Any:
        if self._page is not None:
            return self._page
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        executable = self._browser_executable()
        launch_options: dict[str, Any] = {"headless": True}
        if executable is not None:
            launch_options["executable_path"] = str(executable)
        self._browser = await self._playwright.chromium.launch(**launch_options)
        context = await self._browser.new_context(
            viewport={"width": self.WIDTH, "height": 900},
            device_scale_factor=1,
            reduced_motion="reduce",
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        self._page = await context.new_page()
        return self._page

    @staticmethod
    def _browser_executable() -> Path | None:
        candidates = (
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        )
        return next((path for path in candidates if path.is_file()), None)
