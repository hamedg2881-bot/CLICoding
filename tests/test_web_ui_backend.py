import asyncio
import socket

import uvicorn
from playwright.async_api import async_playwright

from clicoding_orchestrator.app import create_app
from clicoding_orchestrator.config import Settings
from clicoding_orchestrator.database import Database
from clicoding_orchestrator.gateway import Completion
from clicoding_orchestrator.registry import Worker, WorkerRegistry


class BrowserBackendGateway:
    def __init__(self):
        self.vision_started = asyncio.Event()
        self.vision_cancelled = asyncio.Event()
        self.never_release = asyncio.Event()

    async def complete(self, messages, *, tools=None, tool_choice=None, options=None):
        contains_image = any(
            isinstance(message.get("content"), list)
            and any(part.get("type") == "image_url" for part in message["content"])
            for message in messages
        )
        if contains_image:
            self.vision_started.set()
            try:
                await self.never_release.wait()
            finally:
                self.vision_cancelled.set()
        return Completion(content="unused completion", model="browser-backend-test")

    async def stream(self, messages, *, options=None):
        contains_image = any(
            isinstance(message.get("content"), list)
            and any(part.get("type") == "image_url" for part in message["content"])
            for message in messages
        )
        if contains_image:
            self.vision_started.set()
            try:
                yield "تحلیل تصویر آغاز شد"
                await self.never_release.wait()
            finally:
                self.vision_cancelled.set()
            return
        yield "پاسخ زنده "
        yield "از Backend واقعی"

 
def test_browser_chat_uses_live_backend_history_image_upload_and_cancellation(tmp_path):
    async def scenario():
        database_path = tmp_path / "browser-backend.sqlite3"
        database = Database(str(database_path))
        gateway = BrowserBackendGateway()
        registry = WorkerRegistry([
            Worker(
                "browser-e2e-worker",
                "http://127.0.0.1:9/v1",
                "browser-e2e-model",
                frozenset({"chat", "vision"}),
            )
        ])
        app = create_app(
            settings=Settings(
                database_path=str(database_path),
                attachment_storage_dir=str(tmp_path / "attachments"),
            ),
            gateway=gateway,
            database=database,
            registry=registry,
        )

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="critical", access_log=False, lifespan="off")
        )
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        browser = None
        try:
            for _ in range(200):
                if server.started:
                    break
                await asyncio.sleep(0.025)
            assert server.started, "FastAPI test server did not start"

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                page = await browser.new_page()
                await page.goto(f"http://127.0.0.1:{port}/")

                await page.locator("#prompt").fill("پیام اول")
                await page.locator("#sendButton").click()
                await page.wait_for_function(
                    "() => document.querySelector('#status').textContent === 'پاسخ کامل شد'",
                    timeout=10000,
                )
                first_answer = await page.locator(".assistant .bubble").last.inner_text()
                assert "پاسخ زنده از Backend واقعی" in first_answer

                chat_index = await page.evaluate(
                    "() => JSON.parse(sessionStorage.getItem('cds-ai-chat-index-v1'))"
                )
                first_conversation_id = chat_index[0]["id"]
                history = await page.evaluate(
                    """async (id) => {
                      const response = await fetch('/api/v1/conversations/' + encodeURIComponent(id));
                      if (!response.ok) throw new Error('history request failed: ' + response.status);
                      return await response.json();
                    }""",
                    first_conversation_id,
                )
                assert any(
                    event["role"] == "user" and event["content"] == "پیام اول"
                    for event in history["events"]
                )
                assert any(
                    event["role"] == "assistant" and "پاسخ زنده از Backend واقعی" in event["content"]
                    for event in history["events"]
                )

                await page.locator("#newChat").click()
                png = bytes.fromhex(
                    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
                )
                async with page.expect_file_chooser() as chooser_info:
                    await page.locator("#attachButton").click()
                chooser = await chooser_info.value
                await chooser.set_files({
                    "name": "live-vision.png",
                    "mimeType": "image/png",
                    "buffer": png,
                })
                await page.locator("#prompt").fill("این تصویر را بررسی کن")
                await page.locator("#sendButton").click()

                await asyncio.wait_for(gateway.vision_started.wait(), timeout=10)
                await page.locator("#stopButton").click()
                await page.wait_for_function(
                    "() => document.querySelector('#status').textContent === 'درخواست متوقف شد'",
                    timeout=10000,
                )
                await asyncio.wait_for(gateway.vision_cancelled.wait(), timeout=10)

                final_index = await page.evaluate(
                    "() => JSON.parse(sessionStorage.getItem('cds-ai-chat-index-v1'))"
                )
                assert len(final_index) == 2
                assert final_index[0]["id"] != first_conversation_id
        finally:
            if browser is not None:
                await browser.close()
            server.should_exit = True
            await asyncio.wait_for(server_task, timeout=10)
            listener.close()

    asyncio.run(scenario())
