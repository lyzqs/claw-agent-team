#!/usr/bin/env node

const http = require('http');
const os = require('os');
const { opentelemetry } = require('/usr/lib/node_modules/openclaw/node_modules/@opentelemetry/otlp-transformer/build/src/generated/root.js');

const ExportMetricsServiceRequest = opentelemetry.proto.collector.metrics.v1.ExportMetricsServiceRequest;
const ExportMetricsServiceResponse = opentelemetry.proto.collector.metrics.v1.ExportMetricsServiceResponse;
const ExportTraceServiceRequest = opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest;
const ExportTraceServiceResponse = opentelemetry.proto.collector.trace.v1.ExportTraceServiceResponse;

const ATTRIBUTE_MAP = {
  'openclaw.channel': 'channel',
  'openclaw.provider': 'provider',
  'openclaw.model': 'model',
  'openclaw.outcome': 'outcome',
  'openclaw.source': 'source',
  'openclaw.lane': 'lane',
  'openclaw.state': 'state',
  'openclaw.reason': 'reason',
  'openclaw.attempt': 'attempt',
  'openclaw.webhook': 'webhook',
  'openclaw.context': 'context',
  'openclaw.token': 'token_type',
};

const HIGH_CARDINALITY_LABELS = new Set([
  'sessionkey',
  'sessionid',
  'chatid',
  'messageid',
  'error',
  'traceid',
  'spanid',
]);

function parseArgs(argv) {
  const config = {
    listenHost: '127.0.0.1',
    listenPort: 19160,
    env: 'local',
    project: 'agent-team-grafana',
    system: 'openclaw',
    service: 'openclaw-gateway',
    job: 'openclaw-otel-bridge',
    instance: os.hostname(),
  };
  for (let i = 0; i < argv.length; i += 1) {
    const value = argv[i];
    const next = argv[i + 1];
    switch (value) {
      case '--listen-host':
        config.listenHost = next;
        i += 1;
        break;
      case '--listen-port':
        config.listenPort = Number(next);
        i += 1;
        break;
      case '--env':
        config.env = next;
        i += 1;
        break;
      case '--project':
        config.project = next;
        i += 1;
        break;
      case '--system':
        config.system = next;
        i += 1;
        break;
      case '--service':
        config.service = next;
        i += 1;
        break;
      case '--job':
        config.job = next;
        i += 1;
        break;
      case '--instance':
        config.instance = next;
        i += 1;
        break;
      default:
        break;
    }
  }
  return config;
}

function normalizeMetricName(name) {
  return String(name || '')
    .replace(/[^a-zA-Z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/_+/g, '_')
    .toLowerCase();
}

function normalizeLabelName(name) {
  return String(name || '')
    .replace(/[^a-zA-Z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/_+/g, '_')
    .toLowerCase();
}

function numberFromUnknown(value) {
  if (typeof value === 'number') return value;
  if (typeof value === 'string') return Number(value);
  if (typeof value === 'bigint') return Number(value);
  if (value && typeof value.toNumber === 'function') return value.toNumber();
  if (value && typeof value.low === 'number' && typeof value.high === 'number') {
    return Number(BigInt(value.low >>> 0) + (BigInt(value.high >>> 0) << 32n));
  }
  return 0;
}

function anyValueToString(value) {
  if (!value || typeof value !== 'object') return '';
  if (value.stringValue != null) return String(value.stringValue);
  if (value.boolValue != null) return String(value.boolValue);
  if (value.intValue != null) return String(numberFromUnknown(value.intValue));
  if (value.doubleValue != null) return String(value.doubleValue);
  if (value.arrayValue?.values) return JSON.stringify(value.arrayValue.values.map(anyValueToString));
  if (value.kvlistValue?.values) {
    return JSON.stringify(
      Object.fromEntries(
        value.kvlistValue.values.map((entry) => [entry.key, anyValueToString(entry.value)]),
      ),
    );
  }
  if (value.bytesValue != null) return Buffer.from(value.bytesValue).toString('base64');
  return '';
}

function attributeKeyToLabel(key) {
  if (ATTRIBUTE_MAP[key]) return ATTRIBUTE_MAP[key];
  if (key === 'service.name' || key.startsWith('telemetry.sdk.')) return null;
  if (key.startsWith('openclaw.')) return normalizeLabelName(key.slice('openclaw.'.length));
  if (key === 'host.name') return 'otel_host';
  return normalizeLabelName(key);
}

function labelsFromAttributes(attributes) {
  const labels = {};
  for (const attr of attributes || []) {
    const label = attributeKeyToLabel(attr.key);
    if (!label || HIGH_CARDINALITY_LABELS.has(label)) continue;
    const raw = anyValueToString(attr.value);
    if (!raw) continue;
    labels[label] = raw;
  }
  return labels;
}

function attributesMap(attributes) {
  const mapped = {};
  for (const attr of attributes || []) {
    const key = String(attr.key || '');
    if (!key) continue;
    mapped[key] = anyValueToString(attr.value);
  }
  return mapped;
}

function resolveAgentIdFromSessionKey(sessionKey) {
  const raw = String(sessionKey || '').trim().toLowerCase();
  const match = raw.match(/^agent:([^:]+):/);
  if (match && match[1]) return match[1];
  if (raw === 'main') return 'main';
  return raw ? 'unknown' : 'unknown';
}

function mergeLabels(base, extra) {
  return Object.assign({}, base, extra);
}

function layerForMetric(metricName) {
  if (/^openclaw\.(tokens|cost\.usd|run\.duration_ms|context\.tokens|message\.|webhook\.)$/.test(metricName)) {
    return 'L3';
  }
  if (/^openclaw\.agent\.(tokens|message\.processed)$/.test(metricName)) {
    return 'L3';
  }
  return 'L2';
}

function makeBaseLabels(config, metricName) {
  return {
    env: config.env,
    project: config.project,
    system: config.system,
    service: config.service,
    job: config.job,
    instance: config.instance,
    layer: layerForMetric(metricName),
  };
}

function labelsKey(labels) {
  return JSON.stringify(Object.entries(labels).sort(([a], [b]) => a.localeCompare(b)));
}

function escapeHelp(text) {
  return String(text || '').replace(/\\/g, '\\\\').replace(/\n/g, '\\n');
}

function escapeLabelValue(text) {
  return String(text).replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/"/g, '\\"');
}

function formatLabels(labels) {
  const entries = Object.entries(labels || {}).sort(([a], [b]) => a.localeCompare(b));
  if (!entries.length) return '';
  return `{${entries.map(([key, value]) => `${key}="${escapeLabelValue(value)}"`).join(',')}}`;
}

class MetricsStore {
  constructor(config) {
    this.config = config;
    this.simpleMetrics = new Map();
    this.histograms = new Map();
    this.ingestRequests = 0;
    this.ingestErrors = 0;
    this.pointsProcessed = 0;
    this.lastExportTimestampSeconds = 0;
    // upstream NewAPI metrics: counters per label-set
    this.upstreamCounters = new Map(); // "name,labelsKey" -> {labels, value}
    // upstream histogram buckets (in-memory accumulation)
    this.upstreamHistograms = new Map(); // "labelsKey" -> {sum, count, buckets: {le -> running}}
    // upstream event timestamps for rate calculation (ring buffer per channel)
    this.upstreamEventTimes = new Map(); // channel -> sorted array of timestamps (ms)
    this.upstreamEventsReceived = 0;
    this.upstreamEventErrors = 0;
  }

  // Canonical error_type classification per spec
  classifyErrorType(httpStatusCode, isNetworkError, isTimeout) {
    if (isTimeout) return 'timeout';
    if (isNetworkError) return 'network_error';
    const code = Number(httpStatusCode);
    if (code === 0) return 'unknown';
    if (code === 401 || code === 403) return 'auth_failure';
    if (code === 429) return 'rate_limit';
    if (code >= 500 && code <= 599) return 'server_error';
    if ([400, 404, 422].includes(code)) return 'client_error';
    return 'unknown';
  }

  recordUpstreamEvent(event) {
    const channel = String(event.channel || 'unknown');
    const httpStatusCode = Number(event.http_status_code || 0);
    const isError = event.status_family === 'error' || event.is_error === true;
    const isTimeout = event.is_timeout === true || event.error_type === 'timeout';
    const isNetworkError = event.is_network_error === true || event.error_type === 'network_error';
    const durationMs = Number(event.duration_ms || 0);
    const tokensTotal = Number(event.tokens_total || 0);
    const tokensInput = Number(event.tokens_input || 0);
    const tokensOutput = Number(event.tokens_output || 0);
    const errorType = this.classifyErrorType(httpStatusCode, isNetworkError, isTimeout);
    const statusFamily = isError ? 'error' : 'success';

    // 1. openclaw_upstream_newapi_requests_total
    this._addCounter('openclaw_upstream_newapi_requests_total',
      'OpenClaw outbound requests to NewAPI',
      { channel, status_family: statusFamily, http_status_code: String(httpStatusCode) }, 1);

    // 2. openclaw_upstream_newapi_errors_total (only if error)
    if (isError) {
      this._addCounter('openclaw_upstream_newapi_errors_total',
        'OpenClaw outbound error requests to NewAPI',
        { channel, error_type: errorType, http_status_code: String(httpStatusCode) }, 1);
    }

    // 3. openclaw_upstream_newapi_tokens_total
    const totalTokens = tokensTotal || (tokensInput + tokensOutput);
    if (totalTokens > 0) {
      this._addCounter('openclaw_upstream_newapi_tokens_total',
        'Token consumption for OpenClaw outbound requests to NewAPI',
        { channel }, totalTokens);
      if (tokensInput > 0) {
        this._addCounter('openclaw_upstream_newapi_tokens_total',
          'Token consumption for OpenClaw outbound requests to NewAPI',
          { channel, token_type: 'input' }, tokensInput);
        this._addCounter('openclaw_upstream_newapi_tokens_total',
          'Token consumption for OpenClaw outbound requests to NewAPI',
          { channel, token_type: 'output' }, tokensOutput);
      }
    }

    // 4. openclaw_upstream_newapi_request_duration_ms (histogram)
    // Use pre-defined bucket boundaries matching spec: 10, 50, 100, 250, 500, 1000, 2500, 5000, 10000ms
    const BOUNDS = [10, 50, 100, 250, 500, 1000, 2500, 5000, 10000];
    const bucketSamples = [];
    const bucketLabelsBase = { channel, status_family: statusFamily };
    let running = 0;
    for (let i = 0; i < BOUNDS.length; i++) {
      running += durationMs <= BOUNDS[i] ? 1 : 0;
      const bucketLabels = Object.assign({}, bucketLabelsBase, { le: String(BOUNDS[i]) });
      bucketSamples.push([bucketLabels, running]);
    }
    // overflow bucket
    bucketSamples.push([Object.assign({}, bucketLabelsBase, { le: '+Inf' }), 1]);
    this._setHistogram('openclaw_upstream_newapi_request_duration_ms',
      'Request duration (ms) for OpenClaw outbound calls to NewAPI',
      bucketLabelsBase, bucketSamples, durationMs, 1);

    // 5. Track timestamps for request_rate gauge
    const now = Date.now();
    if (!this.upstreamEventTimes.has(channel)) {
      this.upstreamEventTimes.set(channel, []);
    }
    this.upstreamEventTimes.get(channel).push(now);
    this.upstreamEventsReceived += 1;
  }

  _labelsKey(labels) {
    return labelsKey(labels);
  }

  _addCounter(name, help, labels, value) {
    const key = this._labelsKey(labels);
    const fullKey = `${name}||${key}`;
    const existing = this.upstreamCounters.get(fullKey);
    const nextValue = (existing ? existing.value : 0) + Number(value || 0);
    this.upstreamCounters.set(fullKey, { name, type: 'counter', help, labels, value: nextValue });
  }

  _setHistogram(baseName, help, labels, bucketSamples, sumValue, countValue) {
    const key = this._labelsKey(labels);
    const base = this.upstreamHistograms.get(key) || { help, sum: 0, count: 0, buckets: {}, labels };
    base.help = help;
    base.sum += Number(sumValue || 0);
    base.count += Number(countValue || 0);
    for (const [bucketLabels, runningTotal] of bucketSamples) {
      const bk = this._labelsKey(bucketLabels);
      // Accumulate: each event contributes to cumulative bucket counts
      base.buckets[bk] = (base.buckets[bk] || 0) + runningTotal;
    }
    this.upstreamHistograms.set(key, base);
  }

  _computeRequestRates() {
    const now = Date.now();
    const WINDOW_MS = 60000; // 1-minute window
    const rates = {};
    for (const [channel, times] of this.upstreamEventTimes) {
      const cutoff = now - WINDOW_MS;
      const recent = times.filter(t => t > cutoff);
      this.upstreamEventTimes.set(channel, recent);
      if (recent.length > 0) {
        const windowSec = (recent[recent.length - 1] - recent[0]) / 1000;
        const rate = windowSec > 0 ? recent.length / windowSec : 0;
        rates[channel] = rate;
      }
    }
    return rates;
  }

  setSimple(name, type, help, labels, value) {
    if (!this.simpleMetrics.has(name)) {
      this.simpleMetrics.set(name, { type, help, samples: new Map() });
    }
    const metric = this.simpleMetrics.get(name);
    metric.type = type;
    metric.help = help;
    metric.samples.set(labelsKey(labels), { labels, value });
  }

  addSimple(name, type, help, labels, value) {
    if (!this.simpleMetrics.has(name)) {
      this.simpleMetrics.set(name, { type, help, samples: new Map() });
    }
    const metric = this.simpleMetrics.get(name);
    metric.type = type;
    metric.help = help;
    const key = labelsKey(labels);
    const existing = metric.samples.get(key);
    const nextValue = (existing ? Number(existing.value) : 0) + Number(value || 0);
    metric.samples.set(key, { labels, value: nextValue });
  }

  setHistogram(baseName, help, labels, bucketSamples, sumValue, countValue) {
    if (!this.histograms.has(baseName)) {
      this.histograms.set(baseName, { help, samples: new Map() });
    }
    const metric = this.histograms.get(baseName);
    metric.help = help;
    for (const [bucketLabels, bucketValue] of bucketSamples) {
      metric.samples.set(`bucket:${labelsKey(bucketLabels)}`, { suffix: 'bucket', labels: bucketLabels, value: bucketValue });
    }
    metric.samples.set(`sum:${labelsKey(labels)}`, { suffix: 'sum', labels, value: sumValue });
    metric.samples.set(`count:${labelsKey(labels)}`, { suffix: 'count', labels, value: countValue });
  }

  updateBridgeMetrics() {
    const labels = {
      env: this.config.env,
      project: this.config.project,
      system: this.config.system,
      service: this.config.service,
      job: this.config.job,
      instance: this.config.instance,
      layer: 'L0',
    };
    this.setSimple('openclaw_otel_bridge_requests_total', 'counter', 'Successful OTLP metric export requests received by the OpenClaw bridge', labels, this.ingestRequests);
    this.setSimple('openclaw_otel_bridge_decode_errors_total', 'counter', 'Failed OTLP metric export decode attempts', labels, this.ingestErrors);
    this.setSimple('openclaw_otel_bridge_last_export_timestamp_seconds', 'gauge', 'Unix timestamp of the last successful OTLP metric export', labels, this.lastExportTimestampSeconds);
    this.setSimple('openclaw_otel_bridge_points_total', 'counter', 'Total OTLP metric points processed by the OpenClaw bridge', labels, this.pointsProcessed);

    // upstream NewAPI bridge-level health
    this.setSimple('openclaw_upstream_events_received_total', 'counter', 'Total upstream NewAPI events received by the OpenClaw bridge', labels, this.upstreamEventsReceived);
    this.setSimple('openclaw_upstream_events_decode_errors_total', 'counter', 'Total upstream NewAPI event decode errors', labels, this.upstreamEventErrors);
  }

  renderPrometheus() {
    this.updateBridgeMetrics();
    const lines = [];

    // Emit accumulated upstream NewAPI counters
    const counterEntries = [];
    this.upstreamCounters.forEach((metric) => { counterEntries.push(metric); });
    counterEntries.sort((a, b) => a.name.localeCompare(b.name) || labelsKey(a.labels).localeCompare(labelsKey(b.labels)));
    for (const metric of counterEntries) {
      lines.push(`# HELP ${metric.name} ${escapeHelp(metric.help || metric.name)}`);
      lines.push(`# TYPE ${metric.name} ${metric.type}`);
      lines.push(`${metric.name}${formatLabels(metric.labels)} ${metric.value}`);
    }

    // Emit upstream histograms
    const BOUNDS_ORDER = ['+Inf', '10', '50', '100', '250', '500', '1000', '2500', '5000', '10000'];
    const histEntries = [];
    this.upstreamHistograms.forEach((metric) => { histEntries.push(metric); });
    histEntries.sort((a, b) => labelsKey(a.labels).localeCompare(labelsKey(b.labels)));
    for (const metric of histEntries) {
      const baseName = 'openclaw_upstream_newapi_request_duration_ms';
      lines.push(`# HELP ${baseName} ${escapeHelp(metric.help || baseName)}`);
      lines.push(`# TYPE ${baseName} histogram`);
      const bucketEntries = Object.entries(metric.buckets)
        .filter(([k]) => k.includes('le:'))
        .sort((a, b) => {
          const getLe = (k) => { const m = k.match(/le:"?([^"]+)/); return m ? m[1] : ''; };
          const ai = BOUNDS_ORDER.indexOf(getLe(a[0]));
          const bi = BOUNDS_ORDER.indexOf(getLe(b[0]));
          return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
        });
      for (const [bk, count] of bucketEntries) {
        const m = bk.match(/le:"?([^"]+)/);
        if (!m) continue;
        const le = m[1];
        const labelPart = bk.replace(/,le:"?[^"]+"?/, '').replace(/^\{/, '').replace(/\}$/, '');
        const fullLabels = `{${labelPart}}`;
        lines.push(`${baseName}_bucket${fullLabels} ${count}`);
      }
      const labelPart2 = Object.entries(metric.labels).map(([k, v]) => `${k}="${escapeLabelValue(v)}"`).join(',');
      lines.push(`${baseName}_sum{${labelPart2}} ${metric.sum}`);
      lines.push(`${baseName}_count{${labelPart2}} ${metric.count}`);
    }

    // Emit upstream request rate gauges
    const rates = this._computeRequestRates();
    if (Object.keys(rates).length > 0) {
      lines.push(`# HELP openclaw_upstream_newapi_request_rate OpenClaw outbound request rate to NewAPI (requests/sec per channel)`);
      lines.push(`# TYPE openclaw_upstream_newapi_request_rate gauge`);
      for (const [channel, rate] of Object.entries(rates)) {
        lines.push(`openclaw_upstream_newapi_request_rate{channel="${escapeLabelValue(channel)}"} ${rate.toFixed(6)}`);
      }
    }

    // Emit bridge-level metrics
    const simpleEntries = [];
    this.simpleMetrics.forEach((metric, name) => { simpleEntries.push([name, metric]); });
    simpleEntries.sort(([a], [b]) => a.localeCompare(b));
    for (const [name, metric] of simpleEntries) {
      lines.push(`# HELP ${name} ${escapeHelp(metric.help || name)}`);
      lines.push(`# TYPE ${name} ${metric.type}`);
      const sampleEntries = [];
      metric.samples.forEach((sample) => { sampleEntries.push(sample); });
      sampleEntries.sort((a, b) => labelsKey(a.labels).localeCompare(labelsKey(b.labels)));
      for (const sample of sampleEntries) {
        lines.push(`${name}${formatLabels(sample.labels)} ${sample.value}`);
      }
    }
    return `${lines.join('\n')}\n`;
  }
}

function pointValue(point) {
  if (point.asDouble != null) return Number(point.asDouble);
  if (point.asInt != null) return numberFromUnknown(point.asInt);
  return 0;
}

function normalizeCounterName(metricName) {
  const normalized = normalizeMetricName(metricName);
  return normalized.endsWith('_total') ? normalized : `${normalized}_total`;
}

function processHistogram(store, config, metric, points, resourceLabels) {
  const baseName = normalizeMetricName(metric.name);
  for (const point of points || []) {
    const labels = mergeLabels(makeBaseLabels(config, metric.name), mergeLabels(resourceLabels, labelsFromAttributes(point.attributes)));
    const bounds = point.explicitBounds || [];
    const counts = point.bucketCounts || [];
    const bucketSamples = [];
    let running = 0;
    for (let index = 0; index < counts.length; index += 1) {
      running += numberFromUnknown(counts[index]);
      const bucketLabels = Object.assign({}, labels, { le: index < bounds.length ? String(bounds[index]) : '+Inf' });
      bucketSamples.push([bucketLabels, running]);
    }
    store.setHistogram(baseName, metric.description || metric.name, labels, bucketSamples, Number(point.sum || 0), numberFromUnknown(point.count || 0));
    store.pointsProcessed += 1;
  }
}

function processMetric(store, config, metric, resourceLabels) {
  if (metric.gauge) {
    for (const point of metric.gauge.dataPoints || []) {
      const labels = mergeLabels(makeBaseLabels(config, metric.name), mergeLabels(resourceLabels, labelsFromAttributes(point.attributes)));
      store.setSimple(normalizeMetricName(metric.name), 'gauge', metric.description || metric.name, labels, pointValue(point));
      store.pointsProcessed += 1;
    }
    return;
  }
  if (metric.sum) {
    const metricName = metric.sum.isMonotonic ? normalizeCounterName(metric.name) : normalizeMetricName(metric.name);
    const type = metric.sum.isMonotonic ? 'counter' : 'gauge';
    for (const point of metric.sum.dataPoints || []) {
      const labels = mergeLabels(makeBaseLabels(config, metric.name), mergeLabels(resourceLabels, labelsFromAttributes(point.attributes)));
      store.setSimple(metricName, type, metric.description || metric.name, labels, pointValue(point));
      store.pointsProcessed += 1;
    }
    return;
  }
  if (metric.histogram) {
    processHistogram(store, config, metric, metric.histogram.dataPoints, resourceLabels);
  }
}

function aggregateAgentMetricsFromTrace(store, config, span, resourceLabels) {
  const attrs = attributesMap(span.attributes || []);
  const sessionKey = attrs['openclaw.sessionKey'] || '';
  const agent = resolveAgentIdFromSessionKey(sessionKey);
  if (!agent || agent === 'unknown') return;

  const baseLabels = mergeLabels(makeBaseLabels(config, 'openclaw.agent.message.processed'), resourceLabels);
  const commonLabels = Object.assign({}, baseLabels, { agent });

  if (span.name === 'openclaw.message.processed') {
    const outcome = attrs['openclaw.outcome'] || 'unknown';
    const channel = attrs['openclaw.channel'] || 'unknown';
    store.addSimple(
      'openclaw_agent_message_processed_total',
      'counter',
      'Messages processed aggregated by agent from OpenClaw trace spans',
      Object.assign({}, commonLabels, { outcome, channel }),
      1,
    );
  }

  if (span.name === 'openclaw.model.usage') {
    const provider = attrs['openclaw.provider'] || 'unknown';
    const model = attrs['openclaw.model'] || 'unknown';
    const tokenAttrs = [
      ['input', attrs['openclaw.tokens.input']],
      ['output', attrs['openclaw.tokens.output']],
      ['cache_read', attrs['openclaw.tokens.cache_read']],
      ['cache_write', attrs['openclaw.tokens.cache_write']],
      ['total', attrs['openclaw.tokens.total']],
    ];
    for (const [tokenType, rawValue] of tokenAttrs) {
      const value = Number(rawValue || 0);
      if (!Number.isFinite(value) || value === 0) continue;
      store.addSimple(
        'openclaw_agent_tokens_total',
        'counter',
        'Token usage aggregated by agent from OpenClaw model usage spans',
        Object.assign({}, commonLabels, { provider, model, token_type: tokenType }),
        value,
      );
    }
  }
}

function handleExport(store, config, buffer) {
  const decoded = ExportMetricsServiceRequest.decode(buffer);
  for (const resourceMetric of decoded.resourceMetrics || []) {
    const resourceLabels = labelsFromAttributes(resourceMetric.resource?.attributes || []);
    for (const scopeMetric of resourceMetric.scopeMetrics || []) {
      for (const metric of scopeMetric.metrics || []) {
        processMetric(store, config, metric, resourceLabels);
      }
    }
  }
  store.ingestRequests += 1;
  store.lastExportTimestampSeconds = Date.now() / 1000;
}

function handleTraceExport(store, config, buffer) {
  const decoded = ExportTraceServiceRequest.decode(buffer);
  for (const resourceSpan of decoded.resourceSpans || []) {
    const resourceLabels = labelsFromAttributes(resourceSpan.resource?.attributes || []);
    for (const scopeSpan of resourceSpan.scopeSpans || []) {
      for (const span of scopeSpan.spans || []) {
        aggregateAgentMetricsFromTrace(store, config, span, resourceLabels);
      }
    }
  }
}

function main() {
  const config = parseArgs(process.argv.slice(2));
  const store = new MetricsStore(config);
  const metricsResponseBuffer = Buffer.from(ExportMetricsServiceResponse.encode(ExportMetricsServiceResponse.create({})).finish());
  const tracesResponseBuffer = Buffer.from(ExportTraceServiceResponse.encode(ExportTraceServiceResponse.create({})).finish());

  const server = http.createServer((req, res) => {
    if (req.method === 'GET' && req.url === '/metrics') {
      const payload = Buffer.from(store.renderPrometheus(), 'utf-8');
      res.writeHead(200, {
        'Content-Type': 'text/plain; version=0.0.4; charset=utf-8',
        'Content-Length': payload.length,
      });
      res.end(payload);
      return;
    }

    if (req.method === 'GET' && req.url === '/health') {
      const payload = Buffer.from(JSON.stringify({ status: 'ok', lastExportTimestampSeconds: store.lastExportTimestampSeconds }));
      res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': payload.length });
      res.end(payload);
      return;
    }

    if (req.method === 'POST' && req.url.startsWith('/v1/upstream')) {
      const chunks = [];
      req.on('data', (chunk) => chunks.push(chunk));
      req.on('end', () => {
        try {
          const body = Buffer.concat(chunks);
          const contentType = req.headers['content-type'] || '';
          let eventList = [];
          if (contentType.includes('application/json') || contentType.includes('text/plain')) {
            const text = body.toString('utf-8');
            try { eventList = JSON.parse(text); } catch { eventList = [JSON.parse(text)]; }
            if (!Array.isArray(eventList)) eventList = [eventList];
          } else {
            res.writeHead(400, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'unsupported content-type, use application/json' }));
            return;
          }
          for (const event of eventList) {
            if (event && typeof event === 'object') {
              store.recordUpstreamEvent(event);
            }
          }
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ status: 'ok', events: eventList.length }));
        } catch (error) {
          store.upstreamEventErrors += 1;
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: String(error && error.message ? error.message : error) }));
        }
      });
      return;
    }

    if (req.method === 'POST' && (req.url === '/v1/metrics' || req.url === '/v1/traces')) {
      const chunks = [];
      req.on('data', (chunk) => chunks.push(chunk));
      req.on('end', () => {
        try {
          const body = Buffer.concat(chunks);
          if (req.url === '/v1/metrics') {
            handleExport(store, config, body);
            res.writeHead(200, {
              'Content-Type': 'application/x-protobuf',
              'Content-Length': metricsResponseBuffer.length,
            });
            res.end(metricsResponseBuffer);
            return;
          }
          handleTraceExport(store, config, body);
          res.writeHead(200, {
            'Content-Type': 'application/x-protobuf',
            'Content-Length': tracesResponseBuffer.length,
          });
          res.end(tracesResponseBuffer);
        } catch (error) {
          store.ingestErrors += 1;
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: String(error && error.message ? error.message : error) }));
        }
      });
      return;
    }

    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('not found');
  });

  server.listen(config.listenPort, config.listenHost, () => {
    process.stdout.write(`openclaw-otel-bridge listening on http://${config.listenHost}:${config.listenPort}\n`);
  });
}

main();
