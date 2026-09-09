"""Fixed GitHub REST operations. No subprocesses, generated commands or redirects."""
import re
import time
import base64
from pathlib import Path
import requests


ACTIONS_WORKFLOW_PATH = '.github/workflows/hermes-pages.yml'
TRUSTED_PAGES_WORKFLOW = """name: Hermes Static Pages
on:
    workflow_dispatch:
        inputs:
            artifact_sha:
                description: Approved immutable artifact commit
                required: true
                type: string
permissions:
    contents: read
jobs:
    deploy:
        runs-on: ubuntu-latest
        permissions:
            contents: read
            pages: write
            id-token: write
        environment:
            name: github-pages
            url: ${{ steps.deployment.outputs.page_url }}
        steps:
            - name: Checkout approved artifact
                uses: actions/checkout@v6
                with:
                    ref: ${{ inputs.artifact_sha }}
            - name: Verify approved artifact
                env:
                    EXPECTED_SHA: ${{ inputs.artifact_sha }}
                run: test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"
            - name: Configure Pages
                uses: actions/configure-pages@v5
            - name: Upload approved static artifact
                uses: actions/upload-pages-artifact@v4
                with:
                    path: .
                    include-hidden-files: true
            - name: Deploy Pages artifact
                id: deployment
                uses: actions/deploy-pages@v4
"""


class PublicationFailure(Exception):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


class GitHubPublisher:
    def __init__(self, credential_file='/run/secrets/github_publish_token', session=None):
        # This file is mounted ONLY into the backend by the optional Compose override.
        try:
            token = Path(credential_file).read_text().strip()
            if not 20 <= len(token) <= 512 or any(c.isspace() for c in token):
                raise ValueError()
        except (OSError, ValueError):
            raise PublicationFailure('authentication_failure') from None
        self.session = session or requests.Session()
        self.session.trust_env = False  # no proxy credentials / netrc
        self.session.headers.update({'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10'})
        self.deadline = time.monotonic() + 240

    def close(self):
        self.session.close()

    def call(self, method, path, category, body=None, missing=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise PublicationFailure('network_failure')
        try:
            response = self.session.request(method, 'https://api.github.com' + path,
                json=body, timeout=(min(5, remaining), min(15, remaining)), allow_redirects=False)
        except requests.RequestException:
            raise PublicationFailure('network_failure') from None
        if missing and response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise PublicationFailure('authentication_failure')
        if not 200 <= response.status_code < 300:
            raise PublicationFailure(category)
        if response.status_code == 204:
            return {}
        try:
            result = response.json()
            if not isinstance(result, dict): raise ValueError()
            return result
        except ValueError:
            raise PublicationFailure(category) from None

    @staticmethod
    def sha(value):
        if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{40}', value):
            raise PublicationFailure('repository_failure')
        return value

    def publish(self, repository, files, previous, emit):
        owner, name = repository.split('/')
        if self.call('GET', '/user', 'authentication_failure').get('login', '').lower() != owner.lower():
            raise PublicationFailure('authentication_failure')
        base = '/repos/' + repository
        emit('repository_creating')
        repo = self.call('GET', base, 'repository_failure', missing=True)
        if previous is None:
            if repo is not None:  # never adopt somebody else's repository, even if empty
                raise PublicationFailure('repository_failure')
            self.call('POST', '/user/repos', 'repository_failure', {
                'name': name, 'private': False, 'auto_init': False,
                'description': 'Managed static website publication', 'has_issues': False,
                'has_projects': False, 'has_wiki': False})
            # Empty repositories cannot accept Git refs. Bootstrap only our fixed marker.
            self.call('PUT', base + '/contents/.nojekyll', 'upload_failure', {
                'message': 'Initialize static publication', 'content': ''})
            repo = self.call('GET', base, 'repository_failure')
            branch = repo.get('default_branch')
            if not isinstance(branch, str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}', branch):
                raise PublicationFailure('repository_failure')
            head = self.sha(self.call('GET', base + '/git/ref/heads/' + branch, 'repository_failure')['object']['sha'])
            # Our deployment source is always gh-pages. Create from trusted bootstrap.
            if branch != 'gh-pages':
                self.call('POST', base + '/git/refs', 'repository_failure',
                    {'ref': 'refs/heads/gh-pages', 'sha': head})
        else:
            if not repo: raise PublicationFailure('repository_failure')
            head = self.sha(self.call('GET', base + '/git/ref/heads/gh-pages', 'repository_failure')['object']['sha'])
            if head != previous: raise PublicationFailure('repository_failure')
        # Disable generated/custom Actions. Pages deploys static content via .nojekyll.
        self.call('PUT', base + '/actions/permissions', 'repository_failure', {'enabled': False})
        emit('uploading')
        tree = [{'path': name, 'mode': '100644', 'type': 'blob', 'content': content}
                for name, content in sorted({**files, '.nojekyll': ''}.items())]
        tree_sha = self.sha(self.call('POST', base + '/git/trees', 'upload_failure', {'tree': tree})['sha'])
        commit = self.sha(self.call('POST', base + '/git/commits', 'upload_failure', {
            'message': 'Publish validated static revision', 'tree': tree_sha, 'parents': [head]})['sha'])
        # Persist commit before mutating the branch: ambiguous network failures remain inspectable.
        emit('uploading', commit_sha=commit)
        self.call('PATCH', base + '/git/refs/heads/gh-pages', 'upload_failure', {'sha': commit, 'force': False})
        emit('deploying')
        pages = self.call('GET', base + '/pages', 'deployment_failure', missing=True)
        config = {'build_type': 'legacy', 'source': {'branch': 'gh-pages', 'path': '/'}}
        self.call('PUT' if pages else 'POST', base + '/pages', 'deployment_failure', config)
        for _ in range(24):
            build = self.call('GET', base + '/pages/builds/latest', 'deployment_failure', missing=True)
            if build and build.get('commit') == commit:
                if build.get('status') == 'errored': raise PublicationFailure('deployment_failure')
                if build.get('status') == 'built':
                    expected_url = f'https://{owner.lower()}.github.io/{name}/'
                    site = self.call('GET', base + '/pages', 'deployment_failure')
                    if (site.get('html_url') != expected_url or site.get('cname') or
                            site.get('source') != {'branch': 'gh-pages', 'path': '/'}):
                        raise PublicationFailure('deployment_failure')
                    emit('completed', public_url=expected_url)
                    return
            time.sleep(min(3, max(0, self.deadline - time.monotonic())))
        raise PublicationFailure('deployment_failure')

    def reconcile(self, repository, expected_commit, emit, public_url, sleep=time.sleep):
        owner, name = repository.split('/')
        base = '/repos/' + repository
        if self.call('GET', '/user', 'authentication_failure').get('login', '').lower() != owner.lower():
            raise PublicationFailure('authentication_failure')
        repo = self.call('GET', base, 'reconciliation_failure', missing=True)
        if not repo:
            raise PublicationFailure('reconciliation_failure')
        head = self.sha(self.call('GET', base + '/git/ref/heads/gh-pages', 'reconciliation_failure')
                        ['object']['sha'])
        if head != expected_commit:
            raise PublicationFailure('reconciliation_failure')
        pages = self.call('GET', base + '/pages', 'reconciliation_failure')
        if (pages.get('build_type') != 'legacy' or
                pages.get('source') != {'branch': 'gh-pages', 'path': '/'} or
                pages.get('html_url') != public_url or pages.get('cname')):
            raise PublicationFailure('reconciliation_failure')
        self.call('POST', base + '/pages/builds', 'reconciliation_failure')
        emit('reconciliation_requested')
        for _ in range(24):
            build = self.call('GET', base + '/pages/builds/latest', 'reconciliation_failure', missing=True)
            if build and build.get('commit') == expected_commit:
                if build.get('status') == 'errored':
                    raise PublicationFailure('reconciliation_failure')
                if build.get('status') == 'built':
                    site = self.call('GET', base + '/pages', 'reconciliation_failure')
                    if site.get('status') == 'built' and self.public_url_ok(public_url):
                        emit('reconciliation_completed', public_url=public_url)
                        return
            sleep(min(3, max(0, self.deadline - time.monotonic())))
        raise PublicationFailure('reconciliation_failure')

    def migrate_actions(self, repository, expected_commit, emit, public_url, sleep=time.sleep):
        owner, name = repository.split('/')
        base = '/repos/' + repository
        if self.call('GET', '/user', 'authentication_failure').get('login', '').lower() != owner.lower():
            raise PublicationFailure('actions_migration_failure')
        repo = self.call('GET', base, 'actions_migration_failure')
        default_branch = repo.get('default_branch')
        if not isinstance(default_branch, str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}', default_branch):
            raise PublicationFailure('actions_migration_failure')
        head = self.sha(self.call('GET', base + '/git/ref/heads/gh-pages', 'actions_migration_failure')
                        ['object']['sha'])
        if head != expected_commit:
            raise PublicationFailure('actions_migration_failure')
        pages = self.call('GET', base + '/pages', 'actions_migration_failure')
        if (pages.get('source') != {'branch': 'gh-pages', 'path': '/'} or
                pages.get('html_url') != public_url or pages.get('cname')):
            raise PublicationFailure('actions_migration_failure')
        self.call('PUT', base + '/actions/permissions', 'actions_migration_failure', {'enabled': True})
        workflow = self.call('GET', base + '/contents/' + ACTIONS_WORKFLOW_PATH + '?ref=' + default_branch,
            'actions_migration_failure', missing=True)
        if workflow is not None:
            if workflow.get('content', '').replace('\n', '') != base64.b64encode(
                    TRUSTED_PAGES_WORKFLOW.encode('utf-8')).decode('ascii'):
                raise PublicationFailure('actions_migration_failure')
            workflow_commit = self.sha(workflow.get('sha'))
        else:
            result = self.call('PUT', base + '/contents/' + ACTIONS_WORKFLOW_PATH,
                'actions_migration_failure', {
                    'message': 'Install trusted Pages deployment workflow',
                    'content': base64.b64encode(TRUSTED_PAGES_WORKFLOW.encode('utf-8')).decode('ascii'),
                    'branch': default_branch})
            workflow_commit = self.sha(result.get('commit', {}).get('sha'))
        emit('actions_workflow_ready', workflow_commit=workflow_commit)
        self.call('PUT', base + '/pages', 'actions_migration_failure', {
            'build_type': 'workflow', 'source': {'branch': default_branch, 'path': '/'}})
        dispatch = self.call('POST', base + '/actions/workflows/' + ACTIONS_WORKFLOW_PATH + '/dispatches',
            'actions_migration_failure', {'ref': default_branch, 'inputs': {'artifact_sha': expected_commit}})
        run_id = dispatch.get('workflow_run_id')
        if not isinstance(run_id, int):
            raise PublicationFailure('actions_migration_failure')
        emit('actions_workflow_dispatched', run_id=str(run_id))
        for _ in range(24):
            run = self.call('GET', base + '/actions/runs/' + str(run_id), 'actions_migration_failure')
            if run.get('status') == 'completed':
                if run.get('head_sha') != workflow_commit or run.get('conclusion') != 'success':
                    raise PublicationFailure('actions_migration_failure')
                deployment = self.call('GET', base + '/pages/deployments/' + workflow_commit,
                    'actions_migration_failure', missing=True)
                if deployment and deployment.get('status') == 'succeed':
                    site = self.call('GET', base + '/pages', 'actions_migration_failure')
                    if (site.get('build_type') == 'workflow' and site.get('html_url') == public_url and
                            not site.get('cname') and self.public_url_ok(public_url)):
                        emit('actions_migration_completed', run_id=str(run_id),
                            workflow_commit=workflow_commit)
                        return
            sleep(min(3, max(0, self.deadline - time.monotonic())))
        raise PublicationFailure('actions_migration_failure')

    @staticmethod
    def public_url_ok(url):
        try:
            response = requests.get(url, timeout=(5, 10), allow_redirects=False)
            return 200 <= response.status_code < 300
        except requests.RequestException:
            return False
