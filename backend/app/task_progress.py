"""Ordered intent and verified progress; never dispatches tools or predicts DOM targets."""
import time
from urllib.parse import parse_qs, urlsplit
from app.urls import destination_matches
from app.action_workflow import BROWSER_ACTIONS, CLICK_ACTIONS, OBSERVATIONS, MAX_ATTEMPTS


def query_matches(state, query):
    if query is None:
        return True
    url_values = parse_qs(urlsplit(state.get('url', '')).query, keep_blank_values=True).values()
    return any(query in values for values in url_values)


def url_matches(actual, requested):
    """Website-presence goals may accept a landing page; resource goals use strict identity."""
    try:
        a, b = urlsplit(actual), urlsplit(requested)
        if (b.path or '/') != '/' or b.query or b.fragment:
            return destination_matches(requested, actual)
        # Existing website prerequisite semantics, separate from link/history destination identity.
        same_site = (a.hostname or '').removeprefix('www.') == (b.hostname or '').removeprefix('www.')
        safe_scheme = a.scheme == b.scheme or (b.scheme == 'http' and a.scheme == 'https')
        return a.scheme in {'http', 'https'} and safe_scheme and same_site and a.port == b.port
    except ValueError:
        return False


class TaskProgress:
    def __init__(self, request, plan):
        self.request = request
        self.steps = plan.steps
        self.completed = []
        self.seen_actions = set()
        self.seen_observations = set()
        self.needs_observation = False
        self.last_transition = 0
        self.history = []
        self.verified_actions = []
        self.site_url = None
        self.attempts = 0
        self.blocked = False

    @property
    def current(self):
        return self.steps[len(self.completed)] if len(self.completed) < len(self.steps) else None

    @property
    def done(self):
        return self.current is None

    def complete(self, action_index, evidence, already=False):
        self.attempts = 0
        self.blocked = False
        if self.current.kind == 'navigate':
            self.site_url = self.current.value
        self.completed.append({'index': len(self.completed), **self.current.model_dump(),
                               'status': 'already_satisfied' if already else 'verified',
                               'action_index': action_index, 'evidence': evidence})

    def record(self, action):
        name, result = action['tool'], action['result']
        if self.current and name in BROWSER_ACTIONS:
            relevant = {
                'navigate': {'open_browser_url', 'search_youtube'},
                'type': {'type_text', 'search_youtube'},
                'submit': CLICK_ACTIONS | {'press_enter', 'search_youtube'},
                'activate': CLICK_ACTIONS | {'open_first_youtube_result'},
                'back': {'go_back'}, 'refresh': {'refresh_page'},
                'scroll': {'scroll_page'}, 'stop': {'stop_scrolling'},
            }.get(self.current.kind, set())
            pre_dispatch = (result.get('action_executed') is False
                            and (result.get('failure_kind') == 'infrastructure' or result.get('error') == 'stale_observation'))
            if name in relevant and not pre_dispatch:
                self.attempts += 1
        if name in OBSERVATIONS and result.get('success'):
            oid = result.get('observation_id')
            if (oid and oid not in self.seen_observations
                    and result.get('observed_at', 0) > self.last_transition):
                self.seen_observations.add(oid)
                self.needs_observation = False
        elif name in BROWSER_ACTIONS and name != 'type_text' and result.get('action_executed') is not False:
            self.needs_observation = True
            self.last_transition = time.monotonic()

    def matches(self, step, action):
        name, args, result = action['tool'], action['arguments'], action['result']
        if not result.get('success'):
            return False
        after = result.get('verification', {}).get('after', {})
        if step.kind == 'observe':
            if name in OBSERVATIONS:
                return not self.needs_observation and result.get('observation_id') in self.seen_observations
            return name in {'read_page', 'take_screenshot'}
        if step.kind == 'external':
            return name in {'open_app', 'open_website', 'open_path'}
        if not result.get('verification', {}).get('outcome_verified'):
            return False
        if step.kind == 'navigate':
            return name in {'open_browser_url', 'search_youtube'} and url_matches(after.get('url', ''), step.value)
        if step.kind in {'type', 'submit'} and self.site_url:
            site = urlsplit(self.site_url)._replace(path='/', query='', fragment='').geturl()
            if not url_matches(after.get('url', ''), site):
                return False
        if step.kind == 'type':
            return (name == 'type_text' and args.get('text') == step.value
                    or name == 'search_youtube' and args.get('query') == step.value)
        if step.kind == 'submit':
            if name not in CLICK_ACTIONS | {'press_enter', 'search_youtube'}:
                return False
            if query_matches(after, step.value):
                return True
            # SPA results can retain a URL: semantic verification must show new results text
            # as well as the exact query value, not just the pre-existing input value.
            before = result['verification'].get('before', {})
            return (after.get('text') != before.get('text')
                    and any(c.get('value') == step.value for c in after.get('controls', [])))
        if step.kind == 'activate':
            return name in CLICK_ACTIONS | {'open_first_youtube_result'}
        if step.kind == 'back' and name == 'go_back':
            before = result['verification'].get('before', {})
            if len(self.history) > 1:
                return (destination_matches(self.history[-1], before.get('url', ''))
                        and destination_matches(self.history[-2], after.get('url', '')))
            return after.get('url') != before.get('url')
        return name == {'refresh': 'refresh_page', 'scroll': 'scroll_page', 'stop': 'stop_scrolling'}.get(step.kind)

    def sync(self, workflow, actions):
        workflow.complete_expected_outcome()
        workflow.complete_navigation_prerequisite()
        for i, action in enumerate(actions):
            if i in self.seen_actions:
                continue
            result = action['result']
            verified = result.get('verification', {}).get('outcome_verified')
            if not verified and not (result.get('success') and action['tool'] not in BROWSER_ACTIONS):
                continue
            self.seen_actions.add(i)
            if verified:
                self.verified_actions.append({'action_index': i, 'tool': action['tool'],
                                              'evidence': result['verification'].get('outcome_evidence')})
            if self.current and self.matches(self.current, action):
                evidence = result.get('verification', {}).get('outcome_evidence', {'tool': action['tool']})
                completed_kind = self.current.kind
                self.complete(i, evidence, already=bool(result.get('already_satisfied')))
                if (action['tool'] == 'search_youtube' and completed_kind == 'navigate' and self.current
                        and self.current.kind == 'type' and self.matches(self.current, action)):
                    self.complete(i, evidence, already=bool(result.get('already_satisfied')))
                # Existing combined search tool legitimately covers entering AND submitting.
                if (action['tool'] == 'search_youtube' and completed_kind != 'submit' and self.current
                        and self.current.kind == 'submit' and self.matches(self.current, action)):
                    self.complete(i, evidence, already=bool(result.get('already_satisfied')))
            if verified:
                before = result['verification'].get('before', {}).get('url')
                after = result['verification'].get('after', {}).get('url')
                if before and after and before != after:
                    if not self.history or not destination_matches(self.history[-1], before):
                        self.history = [before]
                    if action['tool'] == 'go_back' and len(self.history) > 1 and destination_matches(self.history[-2], after):
                        self.history.pop()
                    else:
                        self.history.append(after)
            # Observe an already-open requested URL rather than reopening it.
            if self.current and self.current.kind == 'navigate' and action['tool'] in OBSERVATIONS:
                state = result.get('state', {})
                if result.get('observation_id') in self.seen_observations and url_matches(state.get('url', ''), self.current.value):
                    self.complete(i, {'field': 'url', 'value': state['url']}, already=True)

    def guard(self, name, arguments):
        if name in {'open_app', 'open_website', 'open_path'}:
            return None if self.current and self.current.kind == 'external' else 'An external action is not the current requested step.'
        if name not in BROWSER_ACTIONS:
            return None
        if self.attempts >= MAX_ATTEMPTS:
            self.blocked = True
            return 'Current step recovery limit reached. Stop and report partial progress.'
        if self.done:
            return 'All requested steps are verified. Finish; no additional browser action is needed.'
        if name in {'type_text', 'search_youtube'}:
            goal = self.current
            if name == 'search_youtube' and goal.kind == 'navigate' and len(self.completed) + 1 < len(self.steps):
                goal = self.steps[len(self.completed) + 1]
            if goal.kind not in {'type', 'submit'}:
                return 'Search/input steps are already complete or not requested next. Continue the current step.'
            requested = arguments.get('text') if name == 'type_text' else arguments.get('query')
            if requested != goal.value:
                return 'Use the exact literal text for the current requested step.'
        if self.current.kind == 'type' and name == 'press_enter':
            return 'Verify the requested input value before submitting it.'
        allowed = {
            'navigate': {'open_browser_url', 'search_youtube'},
            'type': {'type_text', 'search_youtube', 'scroll_page', 'stop_scrolling'},
            'submit': CLICK_ACTIONS | {'press_enter', 'search_youtube', 'type_text', 'scroll_page', 'stop_scrolling'},
            'activate': CLICK_ACTIONS | {'open_first_youtube_result', 'scroll_page', 'stop_scrolling'},
            'back': {'go_back'}, 'refresh': {'refresh_page'},
            'scroll': {'scroll_page'}, 'stop': {'stop_scrolling'}, 'observe': {'stop_scrolling'},
        }.get(self.current.kind, set())
        if name not in allowed:
            return 'That action is not appropriate for the current requested step. Observe and continue in order.'
        if self.needs_observation and name in CLICK_ACTIONS | {'type_text', 'open_first_youtube_result'}:
            return 'The page changed. Obtain a fresh observation before selecting the next target.'
        return None

    def state(self, workflow=None):
        return {'request': self.request, 'completed_steps': self.completed, 'verified_actions': self.verified_actions,
                'current_step': None if self.done else {'index': len(self.completed), **self.current.model_dump()},
                'remaining_steps': len(self.steps) - len(self.completed), 'current_step_attempts': self.attempts,
                'work_remaining': not self.done,
                'unresolved_action': workflow.pending['tool'] if workflow and workflow.pending else None,
                'current_action_verified': workflow.verified if workflow else False}

    def finish(self, result, workflow):
        state = self.state(workflow)
        success = result['success'] and self.done and not self.blocked
        state['final_outcome_verified'] = bool(success and self.steps)
        result.update(success=success, outcome_verified=state['final_outcome_verified'], task=state)
        if not success and self.current:
            completed = '; '.join(step['description'] for step in self.completed)
            result['message'] = (f'Completed: {completed}. ' if completed else '') + f'Could not verify: {self.current.description}.'
        return result
