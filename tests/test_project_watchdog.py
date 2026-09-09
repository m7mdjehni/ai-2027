import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import hermes_project_watchdog as watchdog
from hermes_codex_observer import StreamObserver, initialize
from hermes_diagnostics import safe_diagnostics
from hermes_project_worker import ProjectToolBoundary


class ProjectWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.timer = patch.object(watchdog.time, 'monotonic', side_effect=lambda: self.now)
        self.timer.start()
        self.addCleanup(self.timer.stop)

    def state(self, initial=90):
        return watchdog.Watchdog(initial, 60, 120)

    def test_active_arguments_survive_initial_but_not_absolute_deadline(self):
        state = self.state()
        for second in range(300):
            self.now = 1000 + second
            state.observe(structured=True)
            self.assertIsNone(state.snapshot()[0])
        self.now = 1300
        state.observe(structured=True)
        reason, data = state.snapshot()
        self.assertEqual(reason, 'hard_absolute_deadline')
        self.assertEqual(data['active_stream_watchdog_mode'], 'structured_argument_progress')
        self.assertEqual(data['watchdog_elapsed_seconds'], 300)

    def test_inactivity_and_no_first_event(self):
        state = self.state()
        state.observe(structured=True)
        self.now += 60
        self.assertEqual(state.snapshot()[0], 'stream_inactivity')
        state = self.state(initial=200)
        self.now += 120
        self.assertEqual(state.snapshot()[0], 'time_to_first_event')

    def test_keepalives_cannot_extend_initial_watchdog(self):
        state = self.state()
        for second in range(90):
            self.now = 1000 + second
            state.observe()
        self.now += 1
        self.assertEqual(state.snapshot()[0], 'initial_request_watchdog')

    def test_continuation_waits_for_progress_without_reusing_old_timestamps(self):
        state = watchdog.Watchdog(90, 60, 120, continuation=True)
        self.now += 91
        self.assertIsNone(state.snapshot()[0])
        self.assertFalse(state.snapshot()[1]['structured_argument_progress_observed'])
        self.now += 29
        self.assertEqual(state.snapshot()[0], 'time_to_first_event')

    def test_continuation_keeps_inactivity_and_hard_deadline(self):
        state = watchdog.Watchdog(90, 60, 120, continuation=True)
        for second in range(300):
            self.now = 1000 + second
            state.observe()  # No fabricated structured progress from an earlier turn.
            self.assertIsNone(state.snapshot()[0])
        self.now = 1300
        self.assertEqual(state.snapshot()[0], 'hard_absolute_deadline')
        state = watchdog.Watchdog(90, 60, 120, continuation=True)
        state.observe(True)
        self.now += 60
        self.assertEqual(state.snapshot()[0], 'stream_inactivity')

    def test_only_successful_project_codex_dispatch_enables_continuation(self):
        for provider, mode, result, expected in [
            ('openai-codex', 'codex_responses', '{"path":"index.html"}', True),
            ('openai-codex', 'codex_responses', '{"error":"denied"}', False),
            ('openai-codex', 'codex_responses', 'invalid JSON', False),
            ('openrouter', 'codex_responses', '{"path":"index.html"}', False),
            ('openai-codex', 'chat_completions', '{"path":"index.html"}', False),
        ]:
            with self.subTest(provider=provider, mode=mode, result=result):
                agent = ProjectToolBoundary()
                agent.provider = provider; agent.api_mode = mode; agent.project_id = 'a'*32
                with patch('hermes_project_worker.dispatch', return_value=result):
                    agent._invoke_tool('project_write_file', {'path':'index.html','content':'fixture'})
                self.assertEqual(getattr(agent, '_project_successful_tool_dispatch', False), expected)

    def test_turn_diagnostics_are_redacted_and_preserved(self):
        data = {'model_turn_number':2, 'watchdog_request_number':4,
                'prior_tool_call_dispatched':True, 'prior_successful_tool_call':True,
                'watchdog_turn_phase':'post_tool_continuation',
                'active_stream_watchdog_mode':'post_tool_continuation',
                'watchdog_elapsed_seconds':120, 'total_build_elapsed_seconds':250,
                'watchdog_termination_reason':'time_to_first_event'}
        self.assertEqual(safe_diagnostics(data), data)
        self.assertEqual(safe_diagnostics({key:'secret-content' for key in data}), {})

    def test_old_progress_and_retry_state_do_not_extend_wait(self):
        state = self.state()
        state.observe(structured=True)
        self.now += 90
        state.observe()  # Keepalive is not argument progress.
        self.assertEqual(state.snapshot()[0], 'initial_request_watchdog')
        retry = self.state()
        self.assertFalse(retry.snapshot()[1]['structured_argument_progress_observed'])
        state.cancelled = True
        with self.assertRaises(TimeoutError): state.observe(True)
        self.assertIsNone(retry.last_event)

    def test_only_associated_nonempty_allowed_argument_deltas_extend(self):
        for name, ident, delta, extends in [
            ('project_write_file','one','x',True), ('terminal','one','x',False),
            ('project_write_file','unknown','x',False), ('project_write_file','one','',False),
            ('project_write_file','one',None,False)]:
            with self.subTest(name=name, ident=ident, delta=delta):
                state = self.state()
                agent = SimpleNamespace(diagnostics={}, _emit_repair_diagnostics=Mock())
                initialize(agent)
                token = watchdog.CURRENT_WATCHDOG.set(state)
                try: observer = StreamObserver(agent)
                finally: watchdog.CURRENT_WATCHDOG.reset(token)
                event = {'type':'response.output_item.added',
                         'item':{'type':'function_call','id':'one','name':name}}
                observer.observe(event)
                observer.observe({'type':'response.function_call_arguments.delta','item_id':ident,'delta':delta})
                self.assertEqual(state.last_argument is not None, extends)
                self.assertEqual(event['item']['name'], name)  # Original events unchanged.
                observer.observe({'type':'response.output_item.done','item':event['item']})
                previous = state.last_argument
                self.now += 1
                observer.observe({'type':'response.function_call_arguments.delta','item_id':'one','delta':'x'})
                self.assertEqual(state.last_argument, previous)

    def test_non_project_and_non_codex_delegation_unchanged(self):
        class Base:
            def _interruptible_api_call(self, payload): return 'original'
        class Agent(ProjectToolBoundary, Base): pass
        for provider, mode in [('openrouter','codex_responses'), ('openai-codex','chat_completions')]:
            agent = Agent(); agent.provider = provider; agent.api_mode = mode
            with patch('hermes_project_worker.interruptible_project_codex_call') as call:
                self.assertEqual(agent._interruptible_api_call({}), 'original')
                call.assert_not_called()
        agent.provider = 'openai-codex'; agent.api_mode = 'codex_responses'
        with patch('hermes_project_worker.interruptible_project_codex_call', return_value='project') as call:
            self.assertEqual(agent._interruptible_api_call({}), 'project')
            call.assert_called_once_with(agent, {})

    def test_invalid_timeout_inputs_cannot_disable_deadlines(self):
        for value in (float('nan'), float('inf'), -1, 0, 'bad', None):
            self.assertEqual(watchdog.positive_seconds(value, 60), 60)
            self.assertEqual(self.state(value).initial, 90)
        self.assertEqual(self.state(10000).initial, 300)

    def test_diagnostics_are_allowlisted_scalars(self):
        state = self.state()
        state.observe(True)
        data = state.snapshot()[1]
        data['watchdog_termination_reason'] = 'hard_absolute_deadline'
        self.assertEqual(safe_diagnostics(data), data)
        self.assertEqual(safe_diagnostics({key:'secret-content' for key in data}), {})
        self.assertEqual(safe_diagnostics({'watchdog_elapsed_seconds': math.inf}), {})


if __name__ == '__main__': unittest.main()
