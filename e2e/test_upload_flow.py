from __future__ import annotations

import hashlib
import re
from pathlib import Path

from playwright.sync_api import Page, Route, expect

GIF_1X1 = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")
FILENAME = "NОТО_e2e.gif"


def test_upload_share_preview_and_download(page: Page, tmp_path: Path, app_url: str) -> None:
    page.goto(f"{app_url}/")
    page.locator("#fileInput").set_input_files({"name": FILENAME, "mimeType": "image/gif", "buffer": GIF_1X1})

    expect(page.locator("#fileSelectionSummary")).to_contain_text("1 file selected")
    page.locator("#uploadBtn").click()
    page.wait_for_url(re.compile(rf"{re.escape(app_url)}/s/[0-9a-f]{{32}}$"), timeout=30_000)

    expect(page.locator("#fileList")).to_contain_text(FILENAME)
    expected_hash = hashlib.sha256(GIF_1X1).hexdigest()
    expect(page.locator(".file-hash")).to_have_text(expected_hash)

    page.get_by_role("button", name="Preview").click()
    expect(page.locator(".media-container img")).to_be_visible()

    with page.expect_download() as download_info:
        page.get_by_role("link", name="Download").click()
    download = download_info.value
    assert download.suggested_filename == FILENAME
    target = tmp_path / download.suggested_filename
    download.save_as(target)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == expected_hash


def test_browser_stops_sending_chunks_when_server_reports_already_completed(
    page: Page,
    app_url: str,
) -> None:
    chunk_requests = 0

    def complete_immediately(route: Route) -> None:
        nonlocal chunk_requests
        chunk_requests += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"status":"success","already_completed":true}',
        )

    page.route("**/api/uploads/chunks", complete_immediately)
    page.route(
        "**/api/uploads/finalize",
        lambda route: route.fulfill(
            status=409,
            content_type="application/json",
            body='{"error":{"message":"test finalization stop"}}',
        ),
    )
    page.goto(f"{app_url}/")
    page.locator("#fileInput").set_input_files(
        {
            "name": "resume.jpg",
            "mimeType": "image/jpeg",
            "buffer": b"x" * (1024 * 1024 + 1),
        }
    )

    page.locator("#uploadBtn").click()
    expect(page.locator("#uploadBtn")).to_have_text("Retry finalization")
    expect(page.locator("#fileList .upload-info")).to_have_text("Uploaded")
    assert chunk_requests == 1


def test_browser_keeps_file_retryable_when_last_chunk_is_not_confirmed(
    page: Page,
    app_url: str,
) -> None:
    chunk_requests = 0

    def accept_without_completion(route: Route) -> None:
        nonlocal chunk_requests
        chunk_requests += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"status":"chunk_received"}',
        )

    page.route("**/api/uploads/chunks", accept_without_completion)
    page.goto(f"{app_url}/")
    page.locator("#fileInput").set_input_files(
        {
            "name": "incomplete.jpg",
            "mimeType": "image/jpeg",
            "buffer": b"x" * (1024 * 1024 + 1),
        }
    )

    page.locator("#uploadBtn").click()
    expect(page.locator("#uploadBtn")).to_have_text("Retry unfinished uploads")
    expect(page.locator("#fileList .upload-info")).to_contain_text("server did not confirm")
    assert chunk_requests == 2
