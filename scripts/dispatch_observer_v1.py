#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path('/root/.openclaw/workspace-agent-team')
STATE_DIR = ROOT / 'state'
STATE_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = STATE_DIR / 'dispatch_observer_report.json'

NODE_SCRIPT = r'''
const fs = require('fs');
const os = require('os');
const path = require('path');
const { t: GatewayClient } = require('/usr/lib/node_modules/openclaw/dist/client-mAkhLNco.js');
const { _: GATEWAY_CLIENT_NAMES, g: GATEWAY_CLIENT_MODES } = require('/usr/lib/node_modules/openclaw/dist/message-channel-BHZEWLw5.js');
const { l: READ_SCOPE, d: WRITE_SCOPE } = require('/usr/lib/node_modules/openclaw/dist/method-scopes-BfEsKHVS.js');
const { n: VERSION } = require('/usr/lib/node_modules/openclaw/dist/version-BI-p49mK.js');

const outPath = process.argv[2];
const watchPrefix = process.argv[3] || 'agent:agent-team-';
const timeoutMs = Number(process.argv[4] || '15000');

const observed = [];
let client = null;
let timer = null;

function finish(ok, extra = {}) {
  const payload = {
    ok,
    observed,
    ...extra,
    finishedAt: Date.now(),
  };
  fs.writeFileSync(outPath, JSON.stringify(payload, null, 2));
}

function resolveGatewayUrl() {
  const cfgPath = path.join(os.homedir(), '.openclaw', 'openclaw.json');
  try {
    const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
    const port = cfg?.gateway?.port || 18789;
    return `ws://127.0.0.1:${port}`;
  } catch {
    return 'ws://127.0.0.1:18789';
  }
}

(async () => {
  try {
    client = new GatewayClient({
      url: resolveGatewayUrl(),
      token: process.env.OPENCLAW_GATEWAY_TOKEN,
      password: process.env.OPENCLAW_GATEWAY_PASSWORD,
      clientName: GATEWAY_CLIENT_NAMES.CLI,
      clientDisplayName: 'Agent Team Dispatch Observer',
      clientVersion: VERSION,
      mode: GATEWAY_CLIENT_MODES.CLI,
      scopes: [READ_SCOPE, WRITE_SCOPE],
      onEvent: (event) => {
        if (event.event !== 'chat') return;
        const payload = event.payload || {};
        const sessionKey = typeof payload.sessionKey === 'string' ? payload.sessionKey : '';
        if (!sessionKey.startsWith(watchPrefix)) return;
        const state = typeof payload.state === 'string' ? payload.state : '';
        if (!['final', 'error', 'aborted'].includes(state)) return;
        observed.push({
          event: event.event,
          runId: typeof payload.runId === 'string' ? payload.runId : '',
          sessionKey,
          state,
          stopReason: typeof payload.stopReason === 'string' ? payload.stopReason : null,
          errorMessage: typeof payload.errorMessage === 'string' ? payload.errorMessage : null,
          payload,
          ts: Date.now(),
        });
      },
      onHelloOk: async () => {
        try {
          await client.request('sessions.subscribe', {});
        } catch (err) {
          finish(false, { error: `sessions.subscribe failed: ${String(err)}` });
          try { await client.stopAndWait(); } catch {}
          process.exit(1);
        }
      },
      onConnectError: (error) => {
        finish(false, { error: String(error) });
        process.exit(1);
      },
      onClose: (code, reason) => {
        if (!fs.existsSync(outPath)) finish(false, { error: `gateway closed: ${code} ${reason}` });
      },
    });
    client.start();
    timer = setTimeout(async () => {
      finish(true, { timedOut: true });
      try { await client.stopAndWait(); } catch {}
      process.exit(0);
    }, timeoutMs);
    timer.unref?.();
  } catch (err) {
    finish(false, { error: String(err) });
    process.exit(1);
  }
})();
'''


def main() -> int:
    with tempfile.NamedTemporaryFile('w', suffix='.cjs', delete=False) as f:
        script_path = Path(f.name)
        f.write(NODE_SCRIPT)

    try:
        res = subprocess.run(
            ['node', str(script_path), str(OUT_PATH), 'agent:agent-team-', '5000'],
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        try:
            script_path.unlink(missing_ok=True)
        except Exception:
            pass

    if not OUT_PATH.exists():
        sys.stderr.write(res.stderr or res.stdout or 'dispatch observer failed\n')
        return 1

    report = json.loads(OUT_PATH.read_text(encoding='utf-8'))
    if not report.get('ok'):
        sys.stderr.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        return 1

    sys.path.insert(0, str(ROOT))
    from services.agent_team_service import AgentTeamService  # noqa: WPS433

    svc = AgentTeamService()
    try:
        applied = []
        for item in report.get('observed') or []:
            run_id = item.get('runId')
            state = item.get('state')
            if not isinstance(run_id, str) or not run_id.strip() or not isinstance(state, str):
                continue
            out = svc.observe_dispatch_lifecycle_event(
                dispatch_ref=run_id.strip(),
                state=state,
                stop_reason=item.get('stopReason'),
                error_message=item.get('errorMessage'),
                payload=item.get('payload') if isinstance(item.get('payload'), dict) else {},
            )
            applied.append(out)
        report['applied'] = applied
        OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        svc.close()

    print(OUT_PATH)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
