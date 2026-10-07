'use strict';

class LocalApiClient {
  constructor(fetcher = (...args) => fetch(...args)) {
    this.fetcher = fetcher;
    this.token = '';
    this.refreshing = null;
  }

  async refreshToken() {
    if (!this.refreshing) {
      this.refreshing = (async () => {
        const res = await this.fetcher('/api/status', {cache: 'no-store'});
        const status = await res.json();
        if (!res.ok || !status.token) throw new Error(status.error || 'Could not reconnect to the local app.');
        this.token = status.token;
      })().finally(() => { this.refreshing = null; });
    }
    await this.refreshing;
  }

  async request(path, options = {}, retry = true) {
    const headers = {'X-Diligence-Token': this.token, ...options.headers};
    if (options.body && !(options.body instanceof Blob)) headers['Content-Type'] = 'application/json';
    const res = await this.fetcher(path, {...options, headers, cache: 'no-store'});
    const data = await res.json();
    if (res.status === 403 && data.code === 'stale_token' && retry) {
      // The server rejects stale tokens before reading the body or starting work,
      // so replaying the exact request once cannot duplicate an accepted action.
      await this.refreshToken();
      return this.request(path, options, false);
    }
    if (!res.ok) throw new Error(data.error || 'Request failed.');
    if (path === '/api/status' && data.token) this.token = data.token;
    return data;
  }
}

if (typeof module !== 'undefined' && module.exports) module.exports = {LocalApiClient};
