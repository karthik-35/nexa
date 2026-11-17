from pathlib import Path
from functools import wraps
from hashlib import sha256
import tempfile
import time
from urllib.parse import urljoin, urlsplit, parse_qs
from playwright.sync_api import sync_playwright, Error as PlaywrightError, TimeoutError as PlaywrightTimeout
from app.ai import analyze_screenshot, png_dimensions
from app.urls import normalize_web_url
from app.browser_observation import (
    ELEMENT_DETAILS, CONTROL_DETAILS, observe, screen_state, validate_observation, bind_vision,
    visible_state, click_evidence,
)

playwright = None
context = None
page = None
_observation = None
_context_closed = False
_retired_session = None
_lifecycle_events = []

PROFILE_PATH = Path.home() / ".nexa" / "browser-profile"


def clear_observation():
    global _observation
    if _observation is not None:
        _observation.dispose()
    _observation = None


def is_closed_target(error):
    message = str(error).lower()
    return any(part in message for part in (
        'target page, context or browser has been closed', 'browser has been closed',
        'browser closed', 'context closed', 'connection closed',
        'connection is closed', 'playwright connection closed',
        'event loop is closed', 'target crashed', 'page crashed',
    ))


class BrowserUnavailable(PlaywrightError):
    """Browser acquisition/recovery failure, with conservative dispatch information."""

    def __init__(self, message, *, action_executed=False):
        super().__init__(message)
        self.action_executed = action_executed


def close_browser():
    global playwright, context, page, _context_closed, _retired_session
    clear_observation()
    old_context, old_playwright = _retired_session or (context, playwright)
    # Detach even if either cleanup operation raises. Never reuse partially disposed objects.
    playwright = context = page = None
    _context_closed = False
    errors = []
    for resource, method in ((old_context, 'close'), (old_playwright, 'stop')):
        if resource is not None:
            try:
                getattr(resource, method)()
            except PlaywrightError as error:
                if not is_closed_target(error):
                    errors.append(error)
    _retired_session = (old_context, old_playwright) if errors else None
    if errors:
        raise BrowserUnavailable('Browser cleanup failed. No replacement Chromium was launched.') from errors[0]


def _closed_context(closed_context):
    global _context_closed
    if context is closed_context:
        _context_closed = True


def browser_tool(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        _lifecycle_events.clear()
        try:
            result = function(*args, **kwargs)
        except BrowserUnavailable as error:
            clear_observation()
            result = {"success": False, "error": "browser_unavailable",
                      "failure_kind": "infrastructure", "action_executed": error.action_executed,
                      "message": str(error).split("Call log:")[0][:400]}
        except PlaywrightTimeout:
            clear_observation()
            result = {"success": False, "error": "browser_timeout",
                      "failure_kind": "action", "message": "The browser target did not become ready. Inspect the page before retrying."}
        except PlaywrightError as error:
            clear_observation()
            # During an action the dispatch state may be unknown. Do not invite a blind replay.
            result = {"success": False, "error": "browser_error",
                      "failure_kind": "infrastructure" if is_closed_target(error) else "action",
                      "action_executed": None,
                      "message": str(error).split("Call log:")[0][:400]}
        if _lifecycle_events:
            result['browser_recovery'] = {'attempted': True, 'reinitialized': result.get('success', False),
                                          'events': list(_lifecycle_events)}
        return result
    return guarded


def verified_action(function):
    """Keep dispatch success separate from observable outcome evidence."""
    @wraps(function)
    def action(*args, **kwargs):
        current = get_page()
        try:
            before = visible_state(current)
        except PlaywrightError as error:
            if is_closed_target(error):
                raise BrowserUnavailable('Browser target closed before the action executed. Retry after browser reacquisition.') from error
            raise
        result = function(*args, **kwargs)
        result.setdefault('action_executed', result.get('success', False) or result.get('error') == 'http_error')
        if result['action_executed'] or result.get('already_satisfied'):
            result['verification'] = click_evidence(current, before)
        return result
    return action


def navigation_expectation(current, href):
    if not href:
        return None
    try:
        destination = normalize_web_url(urljoin(current.url, href))
    except ValueError:
        return None
    return {'kind': 'url', 'equals': destination, 'created_at': time.monotonic()}


@browser_tool
@verified_action
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

def _use_page(candidate):
    global page
    candidate.evaluate('1')  # Round trip: cached Python objects can outlive their targets.
    if candidate is not page:
        clear_observation()
        _lifecycle_events.append('reacquired_page')
    candidate.set_default_timeout(5000)
    candidate.set_default_navigation_timeout(30000)
    page = candidate
    return page


def get_page():
    """Called only on Nexa's serialized Playwright owner thread."""
    global playwright, context, page, _context_closed
    if _retired_session is not None:
        close_browser()  # Fail closed if an earlier session cannot be disposed safely.
    if context is not None:
        try:
            browser = context.browser
            if not _context_closed and (browser is None or (browser.is_connected() and context in browser.contexts)):
                live_pages = context.pages
                candidates = ([page] if page in live_pages and page.context is context else [])
                candidates += [candidate for candidate in live_pages if candidate is not page]
                for candidate in candidates:
                    if not candidate.is_closed():
                        try:
                            return _use_page(candidate)
                        except PlaywrightError as error:
                            if not is_closed_target(error):
                                raise
                clear_observation()
                return _use_page(context.new_page())
        except PlaywrightError as error:
            if not is_closed_target(error):
                raise
        _lifecycle_events.append('discarded_dead_session')
        close_browser()
    try:
        if playwright is None:
            playwright = sync_playwright().start()
        PROFILE_PATH.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_PATH), headless=False,
        )
        _context_closed = False
        owned_context = context
        context.on('close', lambda *_: _closed_context(owned_context))
        if context.browser is not None:
            context.browser.on('disconnected', lambda *_: _closed_context(owned_context))
        _lifecycle_events.append('created_persistent_context')
        candidates = [candidate for candidate in context.pages if not candidate.is_closed()]
        return _use_page(candidates[0] if candidates else context.new_page())
    except PlaywrightError as error:
        close_browser()
        raise BrowserUnavailable('Nexa Chromium could not be acquired: ' + str(error).split('Call log:')[0][:250]) from error


@browser_tool
def open_browser_url(url: str):
    """Explicit navigation is safe to reacquire/retry once on a closed target."""
    try:
        url = normalize_web_url(url)
    except ValueError as error:
        return {"success": False, "error": "invalid_url", "action_executed": False, "message": str(error)}

    dispatch_started = False
    for attempt in range(2):
        clear_observation()
        try:
            current = get_page()
            before = visible_state(current)
            navigation_started = time.monotonic()
            dispatch_started = True
            response = current.goto(url, wait_until="domcontentloaded")
        except PlaywrightError as error:
            if not isinstance(error, BrowserUnavailable) and not is_closed_target(error):
                raise
            if _retired_session is not None:
                raise  # Cleanup did not complete: never launch a second owner.
            close_browser()
            if attempt == 1:
                raise BrowserUnavailable('Nexa Chromium remained unavailable after one navigation recovery attempt: ' + str(error)[:250],
                                         action_executed=None if dispatch_started else False) from error
            _lifecycle_events.append('retrying_requested_navigation')
            continue
        verification = click_evidence(current, before)
        if response is not None and response.status >= 400:
            return {"success": False, "error": "http_error", "browser": "nexa",
                    "action_executed": True, "verification": verification,
                    "url": current.url, "status": response.status,
                    "message": "The website returned an HTTP error in Nexa Chromium."}
        return {"success": True, "browser": "nexa", "url": current.url,
                "expected_outcome": {"kind": "url", "equals": current.url, "created_at": navigation_started},
                "action_executed": True, "verification": verification,
                "message": "Opened the website in Nexa Chromium."}


@browser_tool
@verified_action
def search_youtube(query: str):
    clear_observation()
    page = get_page()

    expected = {"kind": "query", "equals": query, "created_at": time.monotonic()}
    if urlsplit(page.url).hostname not in {'youtube.com', 'www.youtube.com'} or not page.locator('input[name="search_query"]').count():
        page.goto("https://www.youtube.com")

    search_box = page.locator('input[name="search_query"]')
    if (urlsplit(page.url).path == '/results' and parse_qs(urlsplit(page.url).query).get('search_query') == [query]
            and search_box.input_value() == query):
        return {"success": True, "action_executed": False, "already_satisfied": True,
                "expected_outcome": expected, "message": "The requested search results are already displayed."}
    if search_box.input_value() != query:
        search_box.fill(query)
    search_box.focus()
    page.keyboard.press("Enter")

    page.wait_for_url("**/results?search_query=**")

    return {
        "success": True,
        "expected_outcome": expected,
        "message": f"Searched YouTube for '{query}'."
    }

@browser_tool
@verified_action
def open_first_youtube_result():
    clear_observation()
    page = get_page()

    first_video = page.locator("ytd-video-renderer a#video-title").first

    expected = navigation_expectation(page, first_video.get_attribute('href'))
    first_video.click()

    page.wait_for_load_state("domcontentloaded")

    return {
        "success": True,
        "expected_outcome": expected,
        "message": "Opened the first YouTube search result."
    }

@browser_tool
@verified_action
def go_back():
    clear_observation()
    page = get_page()
    started = time.monotonic()
    page.go_back()

    return {
        "success": True,
        "expected_outcome": {"kind": "url", "equals": page.url, "created_at": started},
        "message": "Went back to the previous page."
    }

@browser_tool
@verified_action
def refresh_page():
    clear_observation()
    page = get_page()
    started = time.monotonic()
    page.reload()

    return {
        "success": True,
        "expected_outcome": {"kind": "reload", "equals": page.url, "created_at": started},
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
@verified_action
def click_text(text: str):
    clear_observation()
    page = get_page()

    element = page.get_by_text(text, exact=True).first
    expected = navigation_expectation(page, element.evaluate("el => el.closest('a[href]')?.href || null"))
    element.click()

    return {
        "success": True,
        "expected_outcome": expected,
        "message": f"Clicked '{text}'."
    }

@browser_tool
@verified_action
def type_text(field: str, text: str):
    clear_observation()
    page = get_page()

    input_box = page.get_by_placeholder(field).first
    target = input_box.evaluate(CONTROL_DETAILS)
    expected = {"kind": "control_property", "control_id": target['control_id'],
                "property": "value", "equals": text,
                "document": page.evaluate('performance.timeOrigin'), "created_at": time.monotonic()}
    already = input_box.input_value() == text
    if already:
        input_box.focus()  # A subsequent requested Enter still targets this input.
    else:
        input_box.fill(text)

    return {"action_executed": not already, "already_satisfied": already,
        "success": True,
        "expected_outcome": expected,
        "message": f"The field already contains the requested text; focused '{field}'." if already else f"Typed '{text}' into '{field}'."
    }

@browser_tool
@verified_action
def press_enter():
    clear_observation()
    page = get_page()
    focused = page.evaluate('() => document.activeElement?.value ?? null')
    expected = {'kind': 'query', 'equals': focused, 'created_at': time.monotonic()} if isinstance(focused, str) and focused else None
    page.keyboard.press("Enter")

    return {
        "success": True, "expected_outcome": expected,
        "message": "Pressed Enter."
    }

@browser_tool
@verified_action
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
        "observed_at": _observation.created,
        "elements": [details for _, details in _observation.targets.values()],
        "state": visible_state(page),
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
    page.evaluate("window.__nexaScrolling = false; window.__nexaScrollGeneration = (window.__nexaScrollGeneration || 0) + 1")
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
        "success": True, "observation_id": observation.id, "observed_at": observation.created,
        "dimensions": {"width": width, "height": height, "coordinate_space": "viewport_css_pixels",
                       "devicePixelRatio": observation.state['devicePixelRatio']},
        "elements": elements, "analysis": analysis, "state": visible_state(page),
    }


@browser_tool
def click_element(observation_id: str, element_id: str):
    page = get_page()
    stale = validate_observation(_observation, page, observation_id)
    if stale:
        clear_observation()
        return {"success": False, "action_executed": False, "error": "stale_observation", "message": stale}
    target = _observation.targets.get(element_id)
    if target is None:
        return {"success": False, "message": "Element ID was not observed. Inspect again."}
    handle, details = target
    current = handle.evaluate(ELEMENT_DETAILS)
    if not handle.evaluate('el => el.isConnected') or any(current[key] != details[key] for key in ('role', 'name', 'text', 'box')):
        clear_observation()
        return {"success": False, "action_executed": False, "error": "stale_observation", "message": "Target identity changed. Inspect again."}
    before = visible_state(page)
    expected = navigation_expectation(page, details.get("href"))
    try:
        handle.click(timeout=5000)
        return {"success": True, "action_executed": True, "method": "dom",
                "target": details, "expected_outcome": expected, "verification": click_evidence(page, before)}
    finally:
        clear_observation()


@browser_tool
def click_coordinates(x: int, y: int, observation_id: str):
    page = get_page()
    stale = validate_observation(_observation, page, observation_id)
    if stale:
        clear_observation()
        return {"success": False, "action_executed": False, "error": "stale_observation", "message": stale}
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
