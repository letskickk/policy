import { spawnSync } from 'node:child_process';
import path from 'node:path';

const ROOT = process.cwd();
const PYTHON = process.platform === 'win32'
  ? path.join(ROOT, '.venv', 'Scripts', 'python.exe')
  : 'python3';
const BRIDGE = path.join(ROOT, 'harness', 'scripts', 'local_api_bridge.py');

function sanitizeText(value) {
  return String(value ?? '').replace(/[\uD800-\uDFFF]/g, '\uFFFD');
}

function sanitizeDeep(value) {
  if (typeof value === 'string') {
    return sanitizeText(value);
  }
  if (Array.isArray(value)) {
    return value.map(sanitizeDeep);
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, entry]) => [sanitizeText(key), sanitizeDeep(entry)]));
  }
  return value;
}

function sanitizePayload(payload) {
  return sanitizeDeep(payload);
}

function callBridge(payload) {
  const completed = spawnSync(PYTHON, [BRIDGE], {
    cwd: ROOT,
    env: process.env,
    input: JSON.stringify(sanitizePayload(payload)),
    encoding: 'utf8',
    maxBuffer: 10 * 1024 * 1024,
  });
  if (completed.error) {
    throw completed.error;
  }
  const raw = (completed.stdout || '').trim();
  let parsed;
  try {
    parsed = raw ? JSON.parse(raw) : {};
  } catch (error) {
    throw new Error(`bridge produced invalid JSON: ${raw || completed.stderr || error.message}`);
  }
  if (completed.status !== 0 || parsed.error) {
    throw new Error(parsed.error || completed.stderr || `bridge exited with ${completed.status}`);
  }
  return sanitizeText(parsed.output);
}

export default class PolicyLocalProvider {
  id() {
    return 'policy-http-live';
  }

  async callApi(_prompt, context) {
    const vars = (context && context.vars) || {};
    try {
      const mode = String(vars.analysis_mode || 'coach').trim().toLowerCase();
      const output = callBridge({
        mode,
        pledge: sanitizeText(vars.pledge).trim(),
        top_k_platform: Number(vars.top_k_platform || 4),
        top_k_pledge: Number(vars.top_k_pledge || 4),
        top_k_regional: Number(vars.top_k_regional || 5),
        phase: String(vars.phase || 'quick'),
        judge: Boolean(vars.judge || false),
      });
      return { output };
    } catch (error) {
      return { error: sanitizeText(error.message || error) };
    }
  }
}
