"""GitHub protocol acceptance with a fake transport, never a remote request."""
import unittest
import time
import base64
from unittest.mock import Mock, patch
import requests
from app.github_publication import (ACTIONS_WORKFLOW_PATH, TRUSTED_PAGES_WORKFLOW,
    GitHubPublisher, PublicationFailure)


class GitHubProtocol(unittest.TestCase):
    def adapter(self):
        with patch('app.github_publication.Path.read_text', return_value='offline-fake-token-only'):
            transport = Mock(); transport.headers = {}
            return GitHubPublisher(session=transport)

    def test_create_and_update_exact_static_payload(self):
        for previous in [None, 'a'*40]:
            adapter = self.adapter(); calls = []; events = []
            def call(method, path, category, body=None, missing=False):
                calls.append((method,path,body))
                if path == '/user': return {'login':'example'}
                if path == '/repos/example/managed-test' and method == 'GET':
                    count = sum(p == path and m == 'GET' for m,p,b in calls)
                    return None if previous is None and count == 1 else {'default_branch':'main'}
                if '/git/ref/heads/' in path: return {'object':{'sha':'a'*40}}
                if path.endswith('/git/trees'): return {'sha':'b'*40}
                if path.endswith('/git/commits'): return {'sha':'c'*40}
                if path.endswith('/pages') and method == 'GET':
                    if any(p.endswith('/pages/builds/latest') for m,p,b in calls):
                        return {'html_url':'https://example.github.io/managed-test/', 'cname':None,
                            'source':{'branch':'gh-pages','path':'/'}}
                    return None
                if path.endswith('/pages/builds/latest'): return {'status':'built','commit':'c'*40}
                return {}
            adapter.call = call
            with patch('subprocess.Popen', side_effect=AssertionError('No execution')):
                adapter.publish('example/managed-test', {'index.html':'<script>untrusted()</script>'}, previous,
                    lambda state, **data: events.append((state,data)))
            self.assertEqual(events[-1][0], 'completed')
            tree = next(body for method,path,body in calls if path.endswith('/git/trees'))
            self.assertEqual({item['path'] for item in tree['tree']}, {'index.html','.nojekyll'})
            self.assertNotIn('base_tree', tree)
            ref = next(body for method,path,body in calls if method == 'PATCH')
            self.assertFalse(ref['force'])
            self.assertIn(('PUT','/repos/example/managed-test/actions/permissions',{'enabled':False}), calls)
            config = next(body for method,path,body in calls if path.endswith('/pages') and method == 'POST')
            self.assertEqual(config, {'build_type':'legacy','source':{'branch':'gh-pages','path':'/'}})

    def test_unmanaged_repository_rejected(self):
        adapter = self.adapter()
        adapter.call = Mock(side_effect=[{'login':'example'}, {'default_branch':'main'}])
        with self.assertRaisesRegex(PublicationFailure, 'repository_failure'):
            adapter.publish('example/test', {}, None, Mock())
        self.assertEqual(adapter.call.call_count, 2)

    def test_changed_remote_head_rejected(self):
        adapter = self.adapter()
        adapter.call = Mock(side_effect=[{'login':'example'}, {}, {'object':{'sha':'b'*40}}])
        with self.assertRaises(PublicationFailure): adapter.publish('example/test', {}, 'a'*40, Mock())

    def test_http_failures_safe_and_never_retried(self):
        for status, category in [(401,'authentication_failure'),(403,'authentication_failure'),
                                 (422,'upload_failure'),(302,'upload_failure'),(500,'upload_failure')]:
            adapter = self.adapter(); adapter.session.request.return_value.status_code = status
            with self.assertRaisesRegex(PublicationFailure, category): adapter.call('POST','/fixed','upload_failure',{})
            self.assertEqual(adapter.session.request.call_count, 1)
        adapter = self.adapter(); adapter.session.request.side_effect = requests.Timeout('secret response')
        with self.assertRaisesRegex(PublicationFailure, '^network_failure$'): adapter.call('GET','/fixed','upload_failure')

    def test_deadline_prevents_request(self):
        adapter = self.adapter(); adapter.deadline = 0
        with self.assertRaises(PublicationFailure): adapter.call('POST','/fixed','upload_failure')
        adapter.session.request.assert_not_called()

    def test_wrong_build_revision_never_completed_and_polling_bounded(self):
        adapter = self.adapter(); events = []
        def call(method,path,category,body=None,missing=False):
            if path == '/user': return {'login':'example'}
            if path.endswith('/git/ref/heads/gh-pages'): return {'object':{'sha':'a'*40}}
            if path.endswith('/git/trees'): return {'sha':'b'*40}
            if path.endswith('/git/commits'): return {'sha':'c'*40}
            if path.endswith('/pages/builds/latest'): return {'status':'built','commit':'d'*40}
            return {'exists':True}
        adapter.call = Mock(side_effect=call)
        with patch('app.github_publication.time.sleep'), self.assertRaisesRegex(PublicationFailure, 'deployment_failure'):
            adapter.publish('example/test', {'index.html':'safe'}, 'a'*40, lambda state,**data: events.append(state))
        self.assertNotIn('completed',events)
        self.assertEqual(sum(c.args[1].endswith('/pages/builds/latest') for c in adapter.call.call_args_list),24)

    def test_reconciliation_requests_one_build_without_uploading(self):
        adapter = self.adapter(); calls = []; events = []
        commit = 'c'*40
        def call(method, path, category, body=None, missing=False):
            calls.append((method, path, body))
            if path == '/user': return {'login':'example'}
            if path.endswith('/git/ref/heads/gh-pages'): return {'object':{'sha':commit}}
            if path.endswith('/pages') and method == 'GET':
                if any(m == 'POST' and p.endswith('/pages/builds') for m,p,b in calls):
                    return {'status':'built', 'html_url':'https://example.github.io/managed-test/',
                        'cname':None, 'source':{'branch':'gh-pages','path':'/'}}
                return {'status':None, 'build_type':'legacy', 'html_url':'https://example.github.io/managed-test/',
                    'cname':None, 'source':{'branch':'gh-pages','path':'/'}}
            if path.endswith('/pages/builds'): return {'status':'queued'}
            if path.endswith('/pages/builds/latest'): return {'status':'built','commit':commit}
            return {'exists':True}
        adapter.call = call
        with patch.object(adapter, 'public_url_ok', return_value=True):
            adapter.reconcile('example/managed-test', commit, lambda state,**data: events.append(state),
                'https://example.github.io/managed-test/', sleep=lambda _: None)
        self.assertEqual(events, ['reconciliation_requested', 'reconciliation_completed'])
        self.assertEqual(sum(m == 'POST' and p.endswith('/pages/builds') for m,p,b in calls), 1)
        self.assertFalse(any('/git/trees' in p or '/git/commits' in p or '/git/refs/' in p for m,p,b in calls))

    def test_reconciliation_rejects_wrong_repository_or_commit(self):
        for repository, remote_head in [('other/managed-test', 'c'*40), ('example/managed-test', 'd'*40)]:
            adapter = self.adapter(); calls = []
            def call(method, path, category, body=None, missing=False):
                calls.append((method, path))
                if path == '/user': return {'login':'example'}
                if path.endswith('/git/ref/heads/gh-pages'): return {'object':{'sha':remote_head}}
                return {'exists':True}
            adapter.call = call
            with self.assertRaises(PublicationFailure):
                adapter.reconcile(repository, 'c'*40, Mock(), 'https://example.github.io/managed-test/', sleep=lambda _: None)
            self.assertFalse(any(m == 'POST' for m,p in calls))

    def test_reconciliation_missing_build_stays_pending_and_requires_exact_commit(self):
        adapter = self.adapter(); calls = []; commit = 'c'*40
        def call(method, path, category, body=None, missing=False):
            calls.append((method, path))
            if path == '/user': return {'login':'example'}
            if path.endswith('/git/ref/heads/gh-pages'): return {'object':{'sha':commit}}
            if path.endswith('/pages') and method == 'GET': return {'build_type':'legacy',
                'html_url':'https://example.github.io/managed-test/', 'cname':None,
                'source':{'branch':'gh-pages','path':'/'}, 'status':'building'}
            if path.endswith('/pages/builds'): return {'status':'queued'}
            if path.endswith('/pages/builds/latest'): return None
            return {'exists':True}
        adapter.call = call
        adapter.deadline = time.monotonic() + 1
        with patch.object(adapter, 'public_url_ok', return_value=True), \
             self.assertRaisesRegex(PublicationFailure, 'reconciliation_failure'):
            adapter.reconcile('example/managed-test', commit, Mock(),
                'https://example.github.io/managed-test/', sleep=lambda _: None)
        self.assertEqual(sum(m == 'POST' and p.endswith('/pages/builds') for m,p in calls), 1, calls)

    def test_actions_migration_uses_fixed_workflow_and_no_artifact_mutation(self):
        adapter = self.adapter(); calls = []; events = []
        artifact_commit = 'c'*40; workflow_commit = 'e'*40
        pages_workflow = False
        def call(method, path, category, body=None, missing=False):
            nonlocal pages_workflow
            calls.append((method, path, body))
            if path == '/user': return {'login':'example'}
            if path == '/repos/example/managed-test': return {'default_branch':'main'}
            if path.endswith('/git/ref/heads/gh-pages'): return {'object':{'sha':artifact_commit}}
            if path.endswith('/pages') and method == 'GET':
                return {'build_type':'workflow' if pages_workflow else 'legacy',
                    'html_url':'https://example.github.io/managed-test/',
                    'cname':None, 'source':{'branch':'gh-pages','path':'/'}}
            if path.endswith('/contents/'+ACTIONS_WORKFLOW_PATH+'?ref=main'): return None
            if method == 'PUT' and '/contents/' in path: return {'commit':{'sha':workflow_commit}}
            if method == 'PUT' and path.endswith('/pages'):
                pages_workflow = True
                return {}
            if method == 'POST' and path.endswith('/dispatches'): return {'workflow_run_id':123}
            if path.endswith('/actions/runs/123'): return {'status':'completed','conclusion':'success',
                'head_sha':workflow_commit}
            if path.endswith('/pages/deployments/'+workflow_commit): return {'status':'succeed'}
            return {}
        adapter.call = call
        with patch.object(adapter, 'public_url_ok', return_value=True):
            adapter.migrate_actions('example/managed-test', artifact_commit,
                lambda state,**data: events.append((state,data)),
                'https://example.github.io/managed-test/', sleep=lambda _: None)
        self.assertEqual(events[-1][0], 'actions_migration_completed')
        workflow_put = next(body for method,path,body in calls if method == 'PUT' and '/contents/' in path)
        self.assertEqual(workflow_put['content'], base64.b64encode(TRUSTED_PAGES_WORKFLOW.encode()).decode())
        dispatch = next(body for method,path,body in calls if method == 'POST' and path.endswith('/dispatches'))
        self.assertEqual(dispatch['inputs'], {'artifact_sha':artifact_commit})
        self.assertIn(('PUT', '/repos/example/managed-test/actions/permissions', {'enabled':True}), calls)
        self.assertFalse(any('/git/trees' in path or '/git/commits' in path or '/git/refs/' in path
            for method,path,body in calls))
        self.assertFalse(any(path == '/user/repos' for method,path,body in calls))

    def test_actions_workflow_is_trusted_and_static_only(self):
        self.assertEqual(ACTIONS_WORKFLOW_PATH, '.github/workflows/hermes-pages.yml')
        self.assertIn('actions/checkout@v6', TRUSTED_PAGES_WORKFLOW)
        self.assertIn('actions/upload-pages-artifact@v4', TRUSTED_PAGES_WORKFLOW)
        self.assertIn('actions/deploy-pages@v4', TRUSTED_PAGES_WORKFLOW)
        self.assertIn('pages: write', TRUSTED_PAGES_WORKFLOW)
        self.assertIn('id-token: write', TRUSTED_PAGES_WORKFLOW)
        self.assertIn('git rev-parse HEAD', TRUSTED_PAGES_WORKFLOW)
        self.assertNotIn('npm ', TRUSTED_PAGES_WORKFLOW)
        self.assertNotIn('python ', TRUSTED_PAGES_WORKFLOW)
        self.assertNotIn('curl ', TRUSTED_PAGES_WORKFLOW)

    def test_actions_migration_rejects_wrong_artifact_commit(self):
        adapter = self.adapter(); calls = []
        def call(method, path, category, body=None, missing=False):
            calls.append((method,path))
            if path == '/user': return {'login':'example'}
            if path == '/repos/example/managed-test': return {'default_branch':'main'}
            if path.endswith('/git/ref/heads/gh-pages'): return {'object':{'sha':'d'*40}}
            return {'build_type':'legacy', 'source':{'branch':'gh-pages','path':'/'},
                'html_url':'https://example.github.io/managed-test/'}
        adapter.call = call
        with self.assertRaisesRegex(PublicationFailure, 'actions_migration_failure'):
            adapter.migrate_actions('example/managed-test', 'c'*40, Mock(),
                'https://example.github.io/managed-test/', sleep=lambda _: None)
        self.assertFalse(any(method in {'PUT','POST','PATCH'} for method,path in calls))

    def test_actions_migration_requires_exact_workflow_deployment_sha(self):
        adapter = self.adapter(); calls = []; artifact_commit = 'c'*40; workflow_commit = 'e'*40
        def call(method, path, category, body=None, missing=False):
            calls.append((method,path))
            if path == '/user': return {'login':'example'}
            if path == '/repos/example/managed-test': return {'default_branch':'main'}
            if path.endswith('/git/ref/heads/gh-pages'): return {'object':{'sha':artifact_commit}}
            if path.endswith('/pages') and method == 'GET': return {'build_type':'legacy',
                'html_url':'https://example.github.io/managed-test/','cname':None,
                'source':{'branch':'gh-pages','path':'/'}}
            if path.endswith('/contents/'+ACTIONS_WORKFLOW_PATH+'?ref=main'):
                return {'sha':workflow_commit, 'content':base64.b64encode(TRUSTED_PAGES_WORKFLOW.encode()).decode()}
            if method == 'POST' and path.endswith('/dispatches'): return {'workflow_run_id':123}
            if path.endswith('/actions/runs/123'): return {'status':'completed','conclusion':'success',
                'head_sha':'f'*40}
            return {}
        adapter.call = call
        with self.assertRaisesRegex(PublicationFailure, 'actions_migration_failure'):
            adapter.migrate_actions('example/managed-test', artifact_commit, Mock(),
                'https://example.github.io/managed-test/', sleep=lambda _: None)
