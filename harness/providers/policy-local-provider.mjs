const BASE_URL = (process.env.T1_BASE_URL || 'http://127.0.0.1:8011').replace(/\/$/, '');
const LOGIN_EMAIL = process.env.T1_LOGIN_EMAIL || '';
const LOGIN_PASSWORD = process.env.T1_LOGIN_PASSWORD || '';

function requireAuthConfig() {
  if (!LOGIN_EMAIL || !LOGIN_PASSWORD) {
    throw new Error('T1_LOGIN_EMAIL and T1_LOGIN_PASSWORD are required');
  }
}

function extractCookie(response) {
  const raw = response.headers.get('set-cookie') || '';
  const first = raw.split(',').map((part) => part.trim()).find((part) => part.startsWith('policy_auth='));
  if (!first) {
    return '';
  }
  return first.split(';')[0];
}

async function login() {
  requireAuthConfig();
  const response = await fetch(`${BASE_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email: LOGIN_EMAIL,
      password: LOGIN_PASSWORD,
      next: '/',
    }),
  });
  if (!response.ok) {
    throw new Error(`login failed: HTTP ${response.status} ${await response.text()}`);
  }
  const cookie = extractCookie(response);
  if (!cookie) {
    throw new Error('login succeeded but no auth cookie was returned');
  }
  return cookie;
}

async function callCoach(vars, cookie) {
  const response = await fetch(`${BASE_URL}/check`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Cookie: cookie,
    },
    body: JSON.stringify({
      pledge: String(vars.pledge || '').trim(),
    }),
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`/check failed: HTTP ${response.status} ${text}`);
  }
  const data = JSON.parse(text);
  return String(data.result || '');
}

async function callVerify(vars, cookie) {
  const response = await fetch(`${BASE_URL}/api/pledge/verify`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Cookie: cookie,
    },
    body: JSON.stringify({
      text: String(vars.pledge || '').trim(),
      top_k_platform: Number(vars.top_k_platform || 4),
      top_k_pledge: Number(vars.top_k_pledge || 4),
      top_k_regional: Number(vars.top_k_regional || 5),
      phase: String(vars.phase || 'quick'),
      judge: Boolean(vars.judge || false),
    }),
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`/api/pledge/verify failed: HTTP ${response.status} ${text}`);
  }
  return text;
}

export default class PolicyLocalProvider {
  id() {
    return 'policy-http-live';
  }

  async callApi(_prompt, context) {
    const vars = (context && context.vars) || {};
    try {
      const cookie = await login();
      const mode = String(vars.analysis_mode || 'coach').trim().toLowerCase();
      const output = mode === 'verify'
        ? await callVerify(vars, cookie)
        : await callCoach(vars, cookie);
      return { output };
    } catch (error) {
      return { error: String(error.message || error) };
    }
  }
}
