from pathlib import Path
import time
from playwright.sync_api import sync_playwright

playwright = None
context = None
page = None

PROFILE_PATH = Path.home() / ".nexa" / "browser-profile"


def scroll_page(direction: str):
    page = get_page()

    if direction.lower() == "down":
        speed = 1
    elif direction.lower() == "up":
        speed = -1
    else:
        return {
            "success": False,
            "message": "Direction must be 'up' or 'down'."
        }

    page.evaluate(
        f"""
        // Give this scrolling session a unique number
        window.__nexaScrollGeneration =
            (window.__nexaScrollGeneration || 0) + 1;

        const myGeneration = window.__nexaScrollGeneration;

        window.__nexaScrolling = true;

        // Add the stop listener only once per page
        if (!window.__nexaStopListenerAdded) {{

            const stopScrolling = () => {{
                window.__nexaScrolling = false;
                window.__nexaScrollGeneration++;
            }};

            // Capture=true means Nexa sees the click BEFORE YouTube handles it
            document.addEventListener(
                "pointerdown",
                stopScrolling,
                true
            );

            window.__nexaStopListenerAdded = true;
        }}

        function smoothScroll() {{

            // Stop old/duplicate scrolling loops
            if (
                !window.__nexaScrolling ||
                myGeneration !== window.__nexaScrollGeneration
            ) {{
                return;
            }}

            window.scrollBy(0, {speed});

            requestAnimationFrame(smoothScroll);
        }}

        requestAnimationFrame(smoothScroll);
        """
    )

    return {
        "success": True,
        "message": f"Started slowly scrolling {direction}."
    }

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

def go_back():
    page = get_page()
    page.go_back()

    return {
        "success": True,
        "message": "Went back to the previous page."
    }

def refresh_page():
    page = get_page()
    page.reload()

    return {
        "success": True,
        "message": "Refreshed the current page."
    }

def read_page():
    page = get_page()

    text = page.locator("body").inner_text()

    return {
        "success": True,
        "content": text[:5000]
    }

def click_text(text: str):
    page = get_page()

    element = page.get_by_text(text, exact=True).first
    element.click()

    return {
        "success": True,
        "message": f"Clicked '{text}'."
    }

def type_text(field: str, text: str):
    page = get_page()

    input_box = page.get_by_placeholder(field).first
    input_box.fill(text)

    return {
        "success": True,
        "message": f"Typed '{text}' into '{field}'."
    }

def press_enter():
    page = get_page()
    page.keyboard.press("Enter")

    return {
        "success": True,
        "message": "Pressed Enter."
    }

def stop_scrolling():
    page = get_page()

    page.evaluate("""
        window.__nexaScrolling = false;

        if (window.__nexaScrollGeneration) {
            window.__nexaScrollGeneration++;
        }
    """)

    return {
        "success": True,
        "message": "Stopped scrolling."
    }