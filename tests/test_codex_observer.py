"""No Hermes SDK required: final-merge policy and diagnostic redaction guards."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from hermes_codex_observer import check_request, StreamObserver, initialize, CodexPolicyViolation, ALLOWED
from hermes_diagnostics import safe_diagnostics


class CodexObserverTests(unittest.TestCase):
    def agent(self):
        return SimpleNamespace(diagnostics={}, _emit_repair_diagnostics=Mock(), repair_mode=False)

    def payload(self):
        return {'tools':[{'type':'function','name':name} for name in sorted(ALLOWED)],
                'tool_choice':{'type':'function','name':'project_write_file'},'parallel_tool_calls':False}

    def test_effective_extra_body_override_fails_closed(self):
        for override in [{'tools':[{'type':'function','name':'terminal'}]},
                         {'tool_choice':'auto'}, {'parallel_tool_calls':True}]:
            agent=self.agent();payload=self.payload();payload['extra_body']=override
            with self.assertRaises(CodexPolicyViolation):check_request(agent,payload)
            self.assertTrue(agent.diagnostics['final_request_policy_rejected'])
            self.assertNotIn('terminal',safe_diagnostics(agent.diagnostics).values())

    def test_sdk_bypass_merge_and_no_payload_logging(self):
        agent=self.agent();payload=self.payload()
        payload['extra_body']={'tools':payload.pop('tools'),'input':'sensitive-input'}
        payload['instructions']='sensitive-instructions';payload['extra_headers']={'Authorization':'sensitive-token'}
        check_request(agent,payload)
        self.assertEqual(agent.diagnostics['final_request_tool_count'],3)
        self.assertNotIn('sensitive',str(safe_diagnostics(agent.diagnostics)))

    def test_repair_and_post_write_auto_unchanged(self):
        for setting in ['repair_mode','_project_file_written']:
            agent=self.agent();setattr(agent,setting,True)
            payload=self.payload();payload['tool_choice']='auto';payload['parallel_tool_calls']=True
            check_request(agent,payload)
            self.assertEqual(agent.diagnostics['final_request_tool_choice_type'],'auto')

    def test_unassociated_and_failed_events_distinguishable(self):
        agent=self.agent();initialize(agent);observer=StreamObserver(agent)
        observer.observe({'type':'response.function_call_arguments.delta','item_id':'unknown','delta':'private-argument'})
        observer.observe({'type':'response.failed','response':{'error':{'message':'private-error'}}})
        self.assertEqual(agent.diagnostics['codex_unassociated_argument_events'],1)
        self.assertEqual(agent.diagnostics['codex_function_argument_bytes'],len('private-argument'))
        self.assertTrue(agent.diagnostics['codex_response_failed_seen'])
        self.assertNotIn('private',str(safe_diagnostics(agent.diagnostics)))

    def test_diagnostic_schema_rejects_content_in_every_new_field(self):
        agent=self.agent();check_request(agent,self.payload())
        poisoned={key:'sensitive-contents' for key in agent.diagnostics}
        poisoned.update(arguments='sensitive',raw_event='sensitive',reasoning='sensitive')
        self.assertEqual(safe_diagnostics(poisoned),{})

    def test_incomplete_done_item_remains_visible_after_terminal(self):
        agent=self.agent();initialize(agent);observer=StreamObserver(agent)
        item={'type':'function_call','id':'one','status':'incomplete'}
        observer.observe({'type':'response.output_item.added','item':item})
        observer.observe({'type':'response.output_item.done','item':item})
        observer.observe({'type':'response.completed','response':{'output':[item]}})
        self.assertEqual(agent.diagnostics['codex_incomplete_function_calls'],1)

    def test_tracking_is_bounded_and_overflow_is_explicit(self):
        agent=self.agent();initialize(agent);observer=StreamObserver(agent)
        for number in range(140):
            observer.observe({'type':'response.output_item.added','item':{'type':'function_call','id':str(number)}})
        self.assertEqual(len(observer.pending),128)
        self.assertTrue(agent.diagnostics['codex_observation_tracking_overflow'])
