import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


UI_HTML = (
    Path(__file__).parents[1]
    / "src"
    / "clicoding_orchestrator"
    / "web"
    / "index.html"
).read_text(encoding="utf-8")

BASE_URL = "http://cds-ai.test"


async def open_chat_page(playwright):
    browser = await playwright.chromium.launch()
    page = await browser.new_page()
    async def serve_ui(route):
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=UI_HTML)
    await page.route(BASE_URL + "/", serve_ui)
    await page.goto(BASE_URL + "/")
    return browser, page


def test_browser_renders_specialist_progress_and_keeps_conversations_separate():
    async def scenario():
        async with async_playwright() as playwright:
            browser, page = await open_chat_page(playwright)
            created = []
            requests = []

            async def create_conversation(route):
                conversation_id = f"conversation-{len(created) + 1}"
                created.append(conversation_id)
                await route.fulfill(json={"id": conversation_id})

            async def complete_chat(route):
                request = route.request.post_data_json
                requests.append(request)
                if request["task_type"] == "coding":
                    stream = (
                        'event: progress\ndata: {"choices":[{"index":0,"delta":{},"finish_reason":null}],'
                        '"x_clicoding":{"progress":{"type":"plan.created","task_count":2}}}\n\n'
                        'event: progress\ndata: {"choices":[{"index":0,"delta":{},"finish_reason":null}],'
                        '"x_clicoding":{"progress":{"type":"task.started","task_id":"internal-task"}}}\n\n'
                        'data: {"choices":[{"index":0,"delta":{"content":"پاسخ تخصصی آماده شد."},'
                        '"finish_reason":null}]}\n\n'
                        'data: [DONE]\n\n'
                    )
                else:
                    stream = (
                        'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                        'data: {"choices":[{"index":0,"delta":{"content":"پاسخ گفت‌وگوی دوم"},"finish_reason":null}]}\n\n'
                        'data: [DONE]\n\n'
                    )
                await route.fulfill(
                    status=200,
                    headers={"content-type": "text/event-stream", "cache-control": "no-cache"},
                    body=stream,
                )

            await page.route("**/api/v1/conversations", create_conversation)
            await page.route("**/v1/chat/completions", complete_chat)
            try:
                await page.locator("#taskType").select_option("coding")
                await page.locator("#prompt").fill("این پروژه را بررسی کن")
                await page.locator("#sendButton").click()
                await page.wait_for_function(
                    "() => document.querySelector('#status').textContent === 'پاسخ کامل شد'"
                )
                progress = await page.locator(".progress").inner_text()
                first_answer = await page.locator(".assistant .bubble").last.inner_text()
                assert "برنامه‌ریزی انجام شد" in progress
                assert "اجرای یک مرحله آغاز شد" in progress
                assert "پاسخ تخصصی آماده شد." in first_answer
                assert "internal-task" not in progress

                await page.locator("#newChat").click()
                await page.locator("#prompt").fill("یک گفت‌وگوی مستقل")
                await page.locator("#sendButton").click()
                await page.wait_for_function(
                    "() => document.querySelector('#status').textContent === 'پاسخ کامل شد'"
                )
                assert created == ["conversation-1", "conversation-2"]
                assert len(await page.locator("#chatList .chat-item").all()) == 2
                assert "پاسخ گفت‌وگوی دوم" in await page.locator(".assistant .bubble").last.inner_text()
                assert requests[0]["conversation_id"] != requests[1]["conversation_id"]
            finally:
                await browser.close()

    asyncio.run(scenario())


def test_browser_uploads_image_and_stop_calls_cancellation_endpoint():
    async def scenario():
        async with async_playwright() as playwright:
            browser, page = await open_chat_page(playwright)
            uploaded = []
            completion_started = asyncio.Event()
            cancellation_requested = asyncio.Event()
            attachment_id = "9e37a0a4-9a8e-4e3d-9a9c-c22c1d05d1f4"

            async def create_conversation(route):
                await route.fulfill(json={"id": "vision-conversation"})

            async def upload_attachment(route):
                uploaded.append({
                    "content_type": route.request.headers.get("content-type"),
                    "filename": route.request.headers.get("x-filename"),
                    "size": len(route.request.post_data_buffer or b""),
                })
                await route.fulfill(json={
                    "content_part": {"type": "attachment", "attachment_id": attachment_id}
                })

            async def complete_chat(route):
                request = route.request.post_data_json
                content = request["messages"][-1]["content"]
                assert isinstance(content, list)
                assert content[-1] == {
                    "type": "attachment",
                    "attachment_id": attachment_id,
                }
                assert request["task_type"] == "vision"
                completion_started.set()
                try:
                    await asyncio.wait_for(cancellation_requested.wait(), timeout=5)
                    await asyncio.sleep(0.3)
                    await route.fulfill(
                        status=200,
                        headers={"content-type": "text/event-stream"},
                        body="data: [DONE]\n\n",
                    )
                except Exception:
                    # The browser aborts the stream after the cancel API confirms.
                    return

            async def cancel_turn(route):
                cancellation_requested.set()
                await route.fulfill(json={"cancelled": True})

            await page.route("**/api/v1/conversations", create_conversation)
            await page.route("**/api/v1/conversations/*/attachments", upload_attachment)
            await page.route("**/api/v1/conversations/*/cancel", cancel_turn)
            await page.route("**/v1/chat/completions", complete_chat)
            try:
                png = bytes.fromhex(
                    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                    "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
                )
                await page.locator("#imageFile").set_input_files({
                    "name": "sample.png",
                    "mimeType": "image/png",
                    "buffer": png,
                })
                await page.locator("#prompt").fill("این تصویر را بررسی کن")
                await page.locator("#sendButton").click()
                await asyncio.wait_for(completion_started.wait(), timeout=5)
                assert uploaded and uploaded[0]["filename"] == "sample.png"
                assert uploaded[0]["size"] == len(png)

                await page.locator("#stopButton").click()
                await page.wait_for_function(
                    "() => document.querySelector('#status').textContent === 'درخواست متوقف شد'"
                )
                assert cancellation_requested.is_set()
            finally:
                await browser.close()

    asyncio.run(scenario())
