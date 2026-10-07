import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('LM Studio redirects are disabled to keep inference local.')


class LMStudio:
    def __init__(self, base_url=None):
        self.base_url = (base_url or os.getenv('LM_STUDIO_URL', 'http://127.0.0.1:1234/v1')).rstrip('/')
        url = urlparse(self.base_url)
        if url.scheme != 'http' or url.hostname not in {'localhost', '127.0.0.1', '::1'} or url.username or url.password or url.query or url.fragment or url.path != '/v1':
            raise ValueError('LM_STUDIO_URL must be a loopback HTTP URL ending in /v1.')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path, body=None, timeout=10):
        headers = {'Content-Type': 'application/json'}
        if os.getenv('LM_STUDIO_API_KEY'):
            headers['Authorization'] = 'Bearer ' + os.environ['LM_STUDIO_API_KEY']
        req = urllib.request.Request(self.base_url + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            error = exc.read(2000).decode('utf-8', 'replace')
            raise RuntimeError(f'LM Studio returned HTTP {exc.code}: {error}') from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError('Cannot reach LM Studio. Start its local server on port 1234 and load a Qwen model. ' + str(exc)) from exc

    def models(self):
        return [m['id'] for m in self.request('/models').get('data', []) if 'qwen' in m.get('id', '').lower()]

    def chat(self, model, messages, tools=None, max_tokens=2400, schema=None):
        body = dict(model=model, messages=messages, temperature=0.15, max_tokens=max_tokens,
                    stream=False, reasoning_effort='none')
        if tools:
            body.update(tools=tools, tool_choice='auto')
        if schema:
            body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'result', 'strict': True, 'schema': schema}}
        response = self.request('/chat/completions', body, timeout=600)
        choices = response.get('choices', [])
        if not choices or not isinstance(choices[0].get('message'), dict):
            raise RuntimeError('LM Studio returned no assistant message.')
        if choices[0].get('finish_reason') == 'length':
            raise RuntimeError('Model output was truncated. Increase output budget or use a model with thinking disabled.')
        return choices[0]['message'], response.get('usage', {})
