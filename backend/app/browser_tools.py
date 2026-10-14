from playwright.sync_api import sync_playwright


def search_google(query: str):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        page = browser.new_page()

        page.goto("https://www.google.com")

        page.locator('textarea[name="q"]').fill(query)
        page.keyboard.press("Enter")

        page.wait_for_timeout(5000)

        browser.close()

    return {
        "success": True,
        "message": f"Searched Google for '{query}'."
    }