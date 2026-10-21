from pathlib import Path
from playwright.sync_api import sync_playwright


playwright = None
context = None
page = None

PROFILE_PATH = Path.home() / ".nexa" / "browser-profile"


def get_page():
    global playwright, context, page

    if playwright is None:
        playwright = sync_playwright().start()

    if context is None:
        PROFILE_PATH.mkdir(parents=True, exist_ok=True)

        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_PATH),
            headless=False,
        )

        page = context.pages[0] if context.pages else context.new_page()

    if page is None or page.is_closed():
        page = context.new_page()

    return page


def search_youtube(query: str):
    page = get_page()

    page.goto("https://www.youtube.com")

    search_box = page.locator('input[name="search_query"]')
    search_box.fill(query)
    page.keyboard.press("Enter")

    page.wait_for_url("**/results?search_query=**")

    return {
        "success": True,
        "message": f"Searched YouTube for '{query}'."
    }

def open_first_youtube_result():
    page = get_page()

    first_video = page.locator("ytd-video-renderer a#video-title").first

    first_video.click()

    page.wait_for_load_state("domcontentloaded")

    return {
        "success": True,
        "message": "Opened the first YouTube search result."
    }