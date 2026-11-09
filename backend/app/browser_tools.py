from pathlib import Path
from functools import wraps
from hashlib import sha256
import tempfile
from playwright.sync_api import sync_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeout
from app.ai import analyze_screenshot, png_dimensions
from app.urls import normalize_web_url
from app.browser_observation import (
    ELEMENT_DETAILS, observe, screen_state, validate_observation, bind_vision,
    visible_state, click_evidence,
)

playwright = None
context = None
page = None
_observation = None

PROFILE_PATH = Path.home() / ".nexa" / "browser-profile"


def clear_observation():
    global _observation
    if _observation is not None:
        _observation.dispose()
    _observation = None


def close_browser():
    global playwright, context, page
    clear_observation()
    try:
        if context is not None:
            context.close()
    finally:
        if playwright is not None:
            playwright.stop()
        playwright = context = page = None


def browser_tool(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PlaywrightTimeout:
            clear_observation()
            return {"success": False, "error": "browser_timeout",
                    "message": "The browser target did not become ready. Inspect the page before retrying."}
        except PlaywrightError as error:
            clear_observation()
            return {"success": False, "error": "browser_error",
                    "message": str(error).split("Call log:")[0][:400]}
    return guarded


@browser_tool
def scroll_page(direction: str):
    clear_observation()
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
        page.set_default_timeout(5000)
        page.set_default_navigation_timeout(30000)

    if page is None or page.is_closed():
        page = context.new_page()
        page.set_default_timeout(5000)
        page.set_default_navigation_timeout(30000)

    return page


@browser_tool
def open_browser_url(url: str):
    """Navigate the current page in Nexa's dedicated persistent Chromium context."""
    try:
        url = normalize_web_url(url)
    except ValueError as error:
        return {"success": False, "error": "invalid_url", "message": str(error)}

    clear_observation()
    page = get_page()
    response = page.goto(url, wait_until="domcontentloaded")
    if response is not None and response.status >= 400:
        return {"success": False, "error": "http_error", "browser": "nexa",
                "url": page.url, "status": response.status,
                "message": "The website returned an HTTP error in Nexa Chromium."}
    return {"success": True, "browser": "nexa", "url": page.url,
            "message": "Opened the website in Nexa Chromium."}


@browser_tool
def search_youtube(query: str):
    clear_observation()
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

@browser_tool
def open_first_youtube_result():
    clear_observation()
    page = get_page()

    first_video = page.locator("ytd-video-renderer a#video-title").first

    first_video.click()

    page.wait_for_load_state("domcontentloaded")

    return {
        "success": True,
        "message": "Opened the first YouTube search result."
    }

@browser_tool
def go_back():
    clear_observation()
    page = get_page()
    page.go_back()

    return {
        "success": True,
        "message": "Went back to the previous page."
    }

@browser_tool
def refresh_page():
    clear_observation()
    page = get_page()
    page.reload()

    return {
        "success": True,
        "message": "Refreshed the current page."
    }

@browser_tool
def read_page():
    page = get_page()

    text = page.locator("body").inner_text()

    return {
        "success": True,
        "content": text[:5000]
    }

@browser_tool
def click_text(text: str):
    clear_observation()
    page = get_page()

    element = page.get_by_text(text, exact=True).first
    element.click()

    return {
        "success": True,
        "message": f"Clicked '{text}'."
    }

@browser_tool
def type_text(field: str, text: str):
    clear_observation()
    page = get_page()

    input_box = page.get_by_placeholder(field).first
    input_box.fill(text)

    return {
        "success": True,
        "message": f"Typed '{text}' into '{field}'."
    }

@browser_tool
def press_enter():
    clear_observation()
    page = get_page()
    page.keyboard.press("Enter")

    return {
        "success": True,
        "message": "Pressed Enter."
    }

@browser_tool
def stop_scrolling():
    clear_observation()
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

@browser_tool
def get_page_elements():
    global _observation
    clear_observation()
    page = get_page()

    buttons = page.locator("button:visible").all_inner_texts()
    links = page.locator("a:visible").all_inner_texts()

    # Remove empty text and duplicates
    buttons = list(dict.fromkeys(
        text.strip() for text in buttons if text.strip()
    ))

    links = list(dict.fromkeys(
        text.strip() for text in links if text.strip()
    ))

    _observation = observe(page)
    return {
        "success": True,
        "buttons": buttons[:30],
        "links": links[:30],
        "observation_id": _observation.id,
        "elements": [details for _, details in _observation.targets.values()],
    }

@browser_tool
def take_screenshot():
    page = get_page()

    screenshot_path = "/tmp/nexa_screenshot.png"

    image = page.screenshot(
        path=screenshot_path,
        full_page=False,
        scale="css",
    )

    return {
        "success": True,
        "path": screenshot_path,
        "dimensions": {"image": png_dimensions(image), "viewport": screen_state(page)},
        "message": "Captured the current browser screen."
    }

@browser_tool
def analyze_current_screen():
    global _observation
    clear_observation()
    page = get_page()
    # Freeze Nexa's own scrolling before associating an image with DOM targets.
    stop_scrolling()
    observation = observe(page)
    elements = [details for _, details in observation.targets.values()]
    try:
        with tempfile.TemporaryDirectory(prefix="nexa-vision-") as directory:
            path = str(Path(directory) / "screen.png")
            image = page.screenshot(path=path, full_page=False, scale="css")
            width, height = png_dimensions(image)
            if (width, height) != (observation.state['width'], observation.state['height']):
                raise ValueError("Screenshot does not match the CSS viewport. No click is authorized.")
            analysis = analyze_screenshot(path, dom_elements=elements)
        bind_vision(observation, analysis, image)
        stale = validate_observation(observation, page, observation.id)
        if stale:
            raise ValueError(stale)
        _observation = observation
    finally:
        if _observation is not observation:
            observation.dispose()
    return {
        "success": True, "observation_id": observation.id,
        "dimensions": {"width": width, "height": height, "coordinate_space": "viewport_css_pixels",
                       "devicePixelRatio": observation.state['devicePixelRatio']},
        "elements": elements, "analysis": analysis,
    }


@browser_tool
def click_element(observation_id: str, element_id: str):
    page = get_page()
    stale = validate_observation(_observation, page, observation_id)
    if stale:
        return {"success": False, "message": stale}
    target = _observation.targets.get(element_id)
    if target is None:
        return {"success": False, "message": "Element ID was not observed. Inspect again."}
    handle, details = target
    current = handle.evaluate(ELEMENT_DETAILS)
    if any(current[key] != details[key] for key in ('role', 'name', 'text')):
        clear_observation()
        return {"success": False, "message": "Target identity changed. Inspect again."}
    before = visible_state(page)
    try:
        handle.click(timeout=5000)
        return {"success": True, "action_executed": True, "method": "dom",
                "target": details, "verification": click_evidence(page, before)}
    finally:
        clear_observation()


@browser_tool
def click_coordinates(x: int, y: int, observation_id: str):
    page = get_page()
    stale = validate_observation(_observation, page, observation_id)
    if stale:
        return {"success": False, "message": stale}
    if type(x) is not int or type(y) is not int or (x, y) not in _observation.coordinates:
        return {"success": False, "message": "Use exact fallback coordinates from this observation, or click_element for an observed DOM ID."}
    # Fail closed on changing images. Vision coordinates cannot be safely reused on a new frame.
    image = page.screenshot(full_page=False, scale="css")
    if sha256(image).hexdigest() != _observation.image_hash:
        clear_observation()
        return {"success": False, "message": "Screen changed since vision analysis. Inspect again or use a DOM target."}
    before = visible_state(page)
    try:
        page.mouse.click(x, y)
        return {"success": True, "action_executed": True, "method": "coordinates",
                "coordinates": {"x": x, "y": y},
                "verification": click_evidence(page, before)}
    finally:
        clear_observation()
