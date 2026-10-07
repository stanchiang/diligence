const {test} = require('node:test');
const assert = require('node:assert/strict');
const {LocalApiClient} = require('../web/api.js');
const response = (status, data) => ({status, ok: status >= 200 && status < 300, json: async () => data});

test('server restart refreshes token and replays the rejected start exactly once', async () => {
  const requests = [];
  let starts = 0;
  const client = new LocalApiClient(async (path, options) => {
    requests.push({path, options});
    if (path === '/api/status') return response(200, {token: 'new-token'});
    if (options.headers['X-Diligence-Token'] !== 'new-token') return response(403, {code: 'stale_token'});
    starts++;
    return response(202, {id: 'synthetic-run'});
  });
  client.token = 'old-token';
  const body = JSON.stringify({deck_id: 'synthetic', model: 'qwen-fixture'});
  assert.deepEqual(await client.request('/api/runs', {method: 'POST', body}), {id: 'synthetic-run'});
  assert.equal(starts, 1);
  assert.deepEqual(requests.map(r => r.path), ['/api/runs', '/api/status', '/api/runs']);
  assert.equal(requests[2].options.body, body);
});

test('persistent rejection is surfaced without a retry loop', async () => {
  let count = 0;
  const client = new LocalApiClient(async path => {
    count++;
    return path === '/api/status' ? response(200, {token: 'new-token'}) : response(403, {code: 'stale_token', error: 'Rejected'});
  });
  await assert.rejects(client.request('/api/runs', {method: 'POST', body: '{}'}), /Rejected/);
  assert.equal(count, 3);
});

test('origin rejection never causes a token refresh or mutation replay', async () => {
  let count = 0;
  const client = new LocalApiClient(async () => {
    count++;
    return response(403, {code: 'invalid_origin', error: 'Local application origin required.'});
  });
  await assert.rejects(client.request('/api/runs', {method: 'POST', body: '{}'}), /origin required/);
  assert.equal(count, 1);
});

test('uploaded file body and filename survive a token refresh', async () => {
  const file = new Blob(['synthetic-pdf'], {type: 'application/pdf'});
  const sent = [];
  const client = new LocalApiClient(async (path, options) => {
    if (path === '/api/status') return response(200, {token: 'new-token'});
    sent.push(options);
    return sent.length === 1 ? response(403, {code: 'stale_token'}) : response(201, {id: 'deck'});
  });
  await client.request('/api/decks', {method: 'POST', body: file, headers: {'Content-Type': 'application/pdf', 'X-Filename': 'fixture.pdf'}});
  assert.equal(sent[1].body, file);
  assert.equal(sent[1].headers['Content-Type'], 'application/pdf');
  assert.equal(sent[1].headers['X-Filename'], 'fixture.pdf');
});
