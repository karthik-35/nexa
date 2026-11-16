"""Per-request verification and bounded recovery. No browser ownership or execution here."""
import json
from app.browser_observation import state_evidence

BROWSER_ACTIONS = frozenset({
    'open_browser_url', 'search_youtube', 'open_first_youtube_result', 'go_back',
    'refresh_page', 'click_text', 'click_element', 'click_coordinates', 'type_text',
    'press_enter', 'scroll_page', 'stop_scrolling',
})
CLICK_ACTIONS = frozenset({'click_text', 'click_element', 'click_coordinates'})
OBSERVATIONS = frozenset({'get_page_elements', 'analyze_current_screen'})
MAX_TOOL_STEPS = 12
MAX_ATTEMPTS = 3
MAX_INFRASTRUCTURE_FAILURES = 2


def rendered(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


class ActionWorkflow:
    def __init__(self):
        self.pending = None
        self.attempts = 0
        self.observation = None
        self.used_observations = set()
        self.tried = set()
        self.verified = False
        self.blocked = False
        self.infrastructure_failure = None
        self.stale_target_failure = None
        self.infrastructure_failures = 0

    def fingerprint(self, name, arguments):
        if not isinstance(arguments, dict):
            return (name, 'invalid arguments')
        if name == 'click_element' and self.observation:
            target = next((e for e in self.observation.get('elements', [])
                           if e['id'] == arguments.get('element_id')), {})
            # Conservatively reject the same named control across methods and fresh IDs.
            return ('click', (target.get('name') or '').strip().casefold())
        if name == 'click_coordinates' and self.observation:
            visual = next((e for e in self.observation.get('analysis', {}).get('elements', [])
                           if (e.get('x'), e.get('y')) == (arguments.get('x'), arguments.get('y'))), None)
            if visual:
                return ('vision', visual['description'].strip().casefold())
        if name == 'click_text':
            return ('click', str(arguments.get('text') or '').strip().casefold())
        return (name, rendered({k: v for k, v in arguments.items() if k != 'observation_id'}))

    def complete_expected_outcome(self):
        """Evaluate a browser-generated predicate against fresh evidence, not model prose."""
        if not self.pending:
            return False
        result = self.pending['result']
        expected = result.get('expected_outcome', {})
        verification = result.get('verification', {})
        after = verification.get('after', {})
        if not (result.get('success') is True and result.get('action_executed') is True
                and expected.get('kind') == 'control_property'
                and verification.get('after_observed_at', 0) > expected.get('created_at', float('inf'))
                and after.get('document') == expected.get('document')
                and expected.get('control_id')):
            return False
        matches = [control for control in after.get('controls', [])
                   if control.get('control_id') == expected['control_id']]
        if len(matches) != 1 or matches[0].get(expected['property']) != expected['equals']:
            return False
        verification.update(outcome_verified=True, outcome_evidence={
            'field': 'controls', 'control_id': expected['control_id'],
            'property': expected['property'], 'value': matches[0][expected['property']],
            'source': 'post_action_observation',
            'observation_id': verification.get('after_observation_id'),
        })
        self.pending = None
        self.verified = True
        self.attempts = 0
        self.tried.clear()
        self.blocked = False
        return True

    def complete_navigation_prerequisite(self):
        """A fresh page observation can prove navigation without blocking the next step."""
        if not self.pending or self.pending['tool'] != 'open_browser_url' or not self.observation:
            return
        result = self.pending['result']
        observed_url = self.observation.get('state', {}).get('url')
        after = result.get('verification', {}).get('after', {})
        if (result.get('success') is True and result.get('action_executed') is True
                and observed_url and observed_url == result.get('url') == after.get('url')):
            result['verification'].update(outcome_verified=True, outcome_evidence={
                'field': 'url', 'evidence': observed_url, 'source': 'fresh_observation'})
            self.pending = None
            self.verified = True
            self.attempts = 0
            self.tried.clear()

    def guard(self, name, arguments):
        if name in BROWSER_ACTIONS:
            self.complete_expected_outcome()
            self.complete_navigation_prerequisite()
        if self.infrastructure_failures >= MAX_INFRASTRUCTURE_FAILURES:
            self.blocked = True
            return 'Browser infrastructure recovery limit reached. Stop and report failure.'
        if name in BROWSER_ACTIONS and self.infrastructure_failure and self.infrastructure_failure['tool'] == 'open_browser_url':
            failed = self.infrastructure_failure
            if name != 'open_browser_url' or arguments != failed['arguments']:
                return 'The requested navigation did not execute. Retry that prerequisite or stop.'
        if name in BROWSER_ACTIONS and self.stale_target_failure and not self.observation:
            return 'Stale target rejected before execution. Obtain a fresh observation before acting.'
        if name not in BROWSER_ACTIONS or self.pending is None:
            return None
        if self.attempts >= MAX_ATTEMPTS:
            self.blocked = True
            return 'Recovery limit reached. Stop; the requested outcome could not be verified.'
        if not self.observation:
            return 'Recovery requires a fresh get_page_elements or analyze_current_screen observation before acting.'
        if name in {'click_element', 'click_coordinates'} and arguments.get('observation_id') != self.observation.get('observation_id'):
            return 'Recovery must use the latest observation ID.'
        if name == 'click_text' and not any(e.get('text') == arguments.get('text') for e in self.observation.get('elements', [])):
            return 'Recovery text must match exact visible text in the fresh observation.'
        if name == 'press_enter':
            return 'An uncertain submission cannot be safely repeated. Inspect the result or stop.'
        old_name = self.pending['tool']
        if not (name in CLICK_ACTIONS and old_name in CLICK_ACTIONS) and name != old_name:
            return 'Unverified action: inspect and verify before starting a different action.'
        if self.fingerprint(name, arguments) in self.tried:
            return 'This target was already attempted. Inspect and choose a valid alternative; do not repeat it.'
        return None

    def record(self, action):
        name, result = action['tool'], action['result']
        if result.get('error') == 'stale_observation' and result.get('action_executed') is False:
            self.stale_target_failure = action
            self.observation = None
            self.verified = False
            return  # No target activated; a fresh ID may safely reference the same control.
        if result.get('failure_kind') == 'infrastructure' and result.get('action_executed') is False:
            self.infrastructure_failure = action
            self.infrastructure_failures += 1
            self.observation = None
            self.verified = False
            if self.infrastructure_failures >= MAX_INFRASTRUCTURE_FAILURES:
                self.blocked = True
            return  # No user input happened: do not count a semantic attempt or target.
        if name in OBSERVATIONS:
            self.observation = None
        if name in OBSERVATIONS and result.get('success'):
            oid = result.get('observation_id')
            if oid and oid not in self.used_observations:
                self.used_observations.add(oid)
                self.observation = result
                # Update only the pending action, and preserve freshness provenance.
                if self.pending and result.get('state'):
                    pending_result = self.pending['result']
                    expected = pending_result.get('expected_outcome', {})
                    if result.get('browser_recovery') and not expected:
                        return  # Semantic changes in a replacement session prove nothing about the old action.
                    observed_at = result.get('observed_at', 0)
                    if expected and observed_at <= expected.get('created_at', float('inf')):
                        return  # An old/unproven snapshot cannot verify a later action.
                    before = pending_result.get('verification', {}).get('before')
                    if before:
                        pending_result['verification'] = {
                            **state_evidence(before, result['state']),
                            'after_observed_at': observed_at, 'after_observation_id': oid,
                        }
        if name in BROWSER_ACTIONS:
            if result.get('success'):
                self.infrastructure_failure = None
                self.stale_target_failure = None
            self.tried.add(self.fingerprint(name, action['arguments']))
            self.attempts += 1
            self.pending = action
            self.verified = False
            self.observation = None

    def verify_outcome(self, field: str, evidence: str):
        if not self.pending:
            return {'success': False, 'message': 'No pending browser action to verify.'}
        result = self.pending['result']
        if result.get('expected_outcome'):
            # A citation to some other changed fact must not override a known exact predicate.
            if self.complete_expected_outcome():
                return {'success': True, 'outcome_verified': True,
                        'evidence': result['verification']['outcome_evidence'],
                        'message': 'The observed target state exactly matches the requested outcome.'}
            return {'success': False, 'outcome_verified': False,
                    'message': 'Fresh evidence does not match the requested target state exactly. Inspect again or report unverified.'}
        verification = result.get('verification', {})
        before, after = verification.get('before', {}), verification.get('after', {})
        if field == 'removed_dialogs':
            current = verification.get('removed_dialogs', [])
            previous = []
        else:
            current, previous = after.get(field), before.get(field)
        # Validate a full fact/line, rather than allowing an arbitrary matching character.
        if field == 'controls':
            try:
                fact = json.loads(evidence)
            except ValueError:
                fact = None
            grounded = fact is not None and fact in (current or []) and fact not in (previous or [])
        elif field in {'text', 'dialogs', 'menus', 'removed_dialogs'}:
            lines = lambda value: (value if isinstance(value, str) else '\n'.join(value or [])).splitlines()
            grounded = evidence in lines(current) and evidence not in lines(previous)
        else:
            grounded = evidence == rendered(current) and current != previous
        supported = (result.get('success') is True and result.get('action_executed') is True and bool(evidence.strip())
                     and current is not None and grounded)
        if not supported:
            return {'success': False, 'outcome_verified': False,
                    'message': 'Evidence does not identify a new observed outcome. Inspect again or report unverified.'}
        verification.update(outcome_verified=True, outcome_evidence={'field': field, 'evidence': evidence})
        self.pending = None
        self.verified = True
        self.attempts = 0
        self.tried.clear()
        self.blocked = False
        return {'success': True, 'outcome_verified': True, 'evidence': {'field': field, 'value': evidence},
                'message': 'Observed evidence accepted. Finish only if it satisfies the user request.'}

    def finish(self, actions, message):
        exact_outcome_verified = self.complete_expected_outcome()
        self.complete_navigation_prerequisite()
        if self.infrastructure_failure:
            return {'success': False, 'outcome_verified': False, 'actions': actions,
                    'message': 'Nexa Chromium was unavailable; the requested browser action could not be completed.'}
        if self.pending or self.blocked or self.stale_target_failure:
            executed = self.pending and self.pending['result'].get('action_executed')
            return {'success': False, 'outcome_verified': False, 'actions': actions,
                    'message': ('The browser action executed, but the requested outcome could not be verified.'
                                if executed else 'The requested outcome could not be verified; recovery stopped.')}
        success = not actions or actions[-1]['result'].get('success', False)
        if actions and actions[-1]['tool'] == 'verify_outcome' and self.verified:
            # Optional model bookkeeping cannot negate an objectively proven outcome.
            substantive = [action for action in actions if action['tool'] != 'verify_outcome']
            success = bool(substantive) and substantive[-1]['result'].get('success', False)
        if success and exact_outcome_verified:
            message = 'Verified that the target control exactly matches the requested state.'
        return {'success': success, 'actions': actions,
                'message': message if success else 'A tool failed; the requested outcome could not be verified.',
                'outcome_verified': self.verified if success else False}
