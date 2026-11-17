"""Short-lived, grounded browser targets. No model-provided selectors or scripts."""

from dataclasses import dataclass, field
from hashlib import sha256
import time
from uuid import uuid4

from playwright.sync_api import ElementHandle, Page, Error as PlaywrightError


TARGET_SELECTOR = 'button, a[href], input:not([type=hidden]), textarea, select, [role=button], [role=link], [tabindex="0"]'
ELEMENT_DETAILS = """el => {
    const box = el.getBoundingClientRect();
    const labelledBy = (el.getAttribute('aria-labelledby') || '').split(/\\s+/)
        .map(id => el.ownerDocument.getElementById(id)?.textContent || '').join(' ').trim();
    const text = (el.innerText || '').trim();
    const name = labelledBy || el.getAttribute('aria-label') ||
        Array.from(el.labels || []).map(e => e.innerText).join(' ') ||
        text || el.getAttribute('title') || el.getAttribute('placeholder') || '';
    const role = el.getAttribute('role') ||
        ({BUTTON: 'button', A: 'link', INPUT: 'textbox', TEXTAREA: 'textbox', SELECT: 'combobox'}[el.tagName]) || 'control';
    return {role, name: name.slice(0, 200), text: text.slice(0, 200), href: el.href || null,
        box: {x: box.x, y: box.y, width: box.width, height: box.height}};
}"""


# Opaque identities stay outside the DOM so inspection does not invalidate targets.
# They are scoped to actual nodes and disappear on document replacement.
CONTROL_DETAILS = """el => {
    const ids = window.__nexaControlIds || (window.__nexaControlIds = new WeakMap());
    if (!ids.has(el)) ids.set(el, Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-'));
    return {control_id: ids.get(el),
        name: (el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.innerText || '').slice(0, 200),
        value: el.type === 'password' ? null : (el.value ?? null),
        selected: el.getAttribute('aria-selected'), expanded: el.getAttribute('aria-expanded'),
        checked: el.getAttribute('aria-checked') ?? (el.type === 'checkbox' ? el.checked : null)};
}"""


@dataclass
class Observation:
    page: Page
    state: dict
    id: str = field(default_factory=lambda: uuid4().hex)
    created: float = field(default_factory=time.monotonic)
    targets: dict[str, tuple[ElementHandle, dict]] = field(default_factory=dict)
    coordinates: set[tuple[int, int]] = field(default_factory=set)
    image_hash: str | None = None

    def dispose(self):
        for handle, _ in self.targets.values():
            try:
                handle.dispose()
            except PlaywrightError:
                pass  # Navigation/closure may already have destroyed these handles.


def screen_state(page: Page) -> dict:
    state = page.evaluate('''() => {
        const body = document.body?.cloneNode(true);
        for (const el of body ? [body, ...body.querySelectorAll('[style]')] : []) {
            const css = el.style.cssText;
            if (css) el.setAttribute('style', css); else el.removeAttribute('style');
        }
        return {url: location.href, document: performance.timeOrigin,
        width: innerWidth, height: innerHeight, devicePixelRatio,
        scrollX, scrollY, dom: JSON.stringify([body?.outerHTML || '',
            Array.from(document.querySelectorAll('input, textarea, select')).map(el => el.value)]),
        visualScale: visualViewport?.scale || 1};
    }''')
    # Compare current DOM content, avoiding transient screenshot-injected style mutations.
    state['dom'] = sha256(state['dom'].encode()).hexdigest()
    return state


def observe(page: Page) -> Observation:
    observation = Observation(page, screen_state(page))
    handles = page.locator(TARGET_SELECTOR).element_handles()
    try:
        for handle in handles:
            details = handle.evaluate(ELEMENT_DETAILS)
            box = details['box']
            if (len(observation.targets) < 100 and handle.is_visible() and
                    box['width'] > 0 and box['height'] > 0 and
                    box['x'] < observation.state['width'] and box['x'] + box['width'] > 0 and
                    box['y'] < observation.state['height'] and box['y'] + box['height'] > 0):
                target_id = f'e{len(observation.targets) + 1}'
                observation.targets[target_id] = (handle, {'id': target_id, **details})
            else:
                handle.dispose()
    except PlaywrightError:
        observation.dispose()
        raise
    return observation


def validate_observation(observation: Observation | None, page: Page, observation_id: str):
    if observation is None or observation.id != observation_id or observation.page is not page:
        return 'Unknown or superseded observation. Inspect the current screen again.'
    if time.monotonic() - observation.created > 120:
        return 'Observation expired. Inspect the current screen again.'
    if screen_state(page) != observation.state:
        return 'Page DOM, viewport or scroll position changed. Inspect the current screen again.'
    return None


def bind_vision(observation: Observation, analysis: dict, image: bytes):
    observation.image_hash = sha256(image).hexdigest()
    for element in analysis['elements']:
        target_id = element['element_id']
        if target_id is not None and target_id not in observation.targets:
            raise ValueError('Vision returned an unobserved DOM target. Inspect again.')
        x, y = element['x'], element['y']
        if x is None and y is None:
            continue
        if (type(x) is not int or type(y) is not int or
                not 0 <= x < observation.state['width'] or
                not 0 <= y < observation.state['height']):
            raise ValueError('Vision returned invalid image coordinates. Inspect again.')
        # A known DOM target must use its handle, never a less reliable coordinate.
        if target_id is None:
            observation.coordinates.add((x, y))


def visible_state(page: Page) -> dict:
    """Bounded semantic evidence; no screenshots or model calls in the action path."""
    return page.evaluate("""() => {
        const visible = el => !!(el.getClientRects().length) && getComputedStyle(el).visibility !== 'hidden';
        const texts = selector => Array.from(document.querySelectorAll(selector))
            .filter(visible).slice(0, 30).map(el => (el.innerText || el.getAttribute('aria-label') || '').slice(0, 1000));
        return {url: location.href, title: document.title, document: performance.timeOrigin,
            dialogs: texts('dialog[open], [role=dialog], [role=alertdialog]'),
            menus: texts('[role=menu], [role=listbox]'),
            text: (document.body?.innerText || '').slice(0, 5000),
            controls: Array.from(document.querySelectorAll('button, a[href], input, textarea, select, [aria-selected], [aria-expanded], [aria-checked]'))
                .filter(visible).slice(0, 100).map(""" + CONTROL_DETAILS + """),
            scroll: {x: scrollX, y: scrollY, active: !!window.__nexaScrolling}};
    }""")


def state_evidence(before: dict, after: dict) -> dict:
    changed = [key for key in after if after[key] != before.get(key)]
    return {
        'status': 'observed_change' if changed else 'unverified',
        'outcome_verified': False,
        'changed_fields': changed,
        'url_changed': after['url'] != before.get('url'),
        'new_dialogs': [text for text in after['dialogs'] if text not in before.get('dialogs', [])],
        'removed_dialogs': [text for text in before.get('dialogs', []) if text not in after['dialogs']],
        'before': before, 'after': after,
        'message': 'Compare evidence with the user request. Observed change is not task success.',
    }


def click_evidence(page: Page, before: dict) -> dict:
    """Report evidence, not a claim that the user's intended task succeeded."""
    deadline = time.monotonic() + 2
    try:
        observed_at = time.monotonic()
        after = visible_state(page)
        while after == before and time.monotonic() < deadline:
            page.wait_for_timeout(100)
            observed_at = time.monotonic()
            after = visible_state(page)
    except PlaywrightError:
        return {'status': 'unverified', 'outcome_verified': False, 'before': before,
                'message': 'Action dispatched, but post-action observation failed. Inspect before acting again.'}
    return {**state_evidence(before, after), 'after_observed_at': observed_at}
