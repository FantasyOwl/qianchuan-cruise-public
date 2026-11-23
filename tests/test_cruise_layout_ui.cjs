'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch { ({ JSDOM } = require('../data/browser-control-test-deps/node_modules/jsdom')); }

const { CruiseConsole, inferAccountRole, resolveAccountProject } = require('../static/cruise.js');
const { Panel: HybridPanel } = require('../static/hybrid-chase.js');
const { PlanMetrics } = require('../static/plan-metrics.js');

function makeStorage() {
  const store = new Map();
  return {
    getItem(k) { return store.has(k) ? store.get(k) : null; },
    setItem(k, v) { store.set(String(k), String(v)); },
    removeItem(k) { store.delete(String(k)); },
    clear() { store.clear(); },
  };
}

function makeProfile(enabledFeatures = ['f1', 'f5']) {
  return {
    version: 1,
    enabled: true,
    main_interval_seconds: 200,
    auto_interval_seconds: 300,
    operation_start: '05:00',
    operation_end: '02:00',
    broadcast_protection: { enabled: false, start: '02:00', end: '05:00' },
    safety: { max_actions_per_round: 20, max_actions_per_day: 500, high_spend_no_orders: 500 },
    features: {
      f1: { enabled: enabledFeatures.includes('f1'), min_control_cost: 70, max_roi: 2.9 },
      f2: { enabled: false, rules: [] },
      f5: { enabled: enabledFeatures.includes('f5'), rules: [] },
      f6: { enabled: false, min_overall_roi: null },
    },
  };
}

function makeConsoleFixture(extra = {}) {
  const dom = new JSDOM(fs.readFileSync(require.resolve('../static/cruise.html'), 'utf8'), { url: 'http://127.0.0.1:8877/cruise' });
  const accounts = [
    { id: '1999999999999002', name: '示例分组A · 直播户', scene: 'live', tab_group: '示例分组A', delivery_entry: 'both', profile: makeProfile(), bindings: {}, revision: 'rev-1', target_roi: null, lanes: { main: { running: true }, auto: { running: true } } },
    { id: '1999999999999001', name: '示例分组A · 图文户', scene: 'product', tab_group: '示例分组A', delivery_entry: 'both', profile: makeProfile([]), bindings: {}, revision: 'rev-2', target_roi: null, lanes: { main: { running: false }, auto: { running: false } } },
    { id: '8531163845726695', name: '示例分组B直播户', scene: 'live', tab_group: '细肩带', delivery_entry: 'both', profile: makeProfile(), bindings: {}, revision: 'rev-3', target_roi: null, lanes: { main: { running: true }, auto: { running: false } } },
    { id: '8362438035614033', name: '示例分组B图文户', scene: 'product', tab_group: '细肩带', delivery_entry: 'both', profile: makeProfile([]), bindings: {}, revision: 'rev-4', target_roi: null, lanes: { main: { running: false }, auto: { running: false } } },
    { id: '8929253702525378', name: '示例分组C达人合作号', scene: 'live', tab_group: '示例分组C', delivery_entry: 'both', profile: makeProfile(), bindings: {}, revision: 'rev-5', target_roi: null, lanes: { main: { running: true }, auto: { running: true } } },
  ];
  const backend = {
    ok: true,
    csrf: 'csrf-1',
    accounts,
    feature_catalog: [
      { id: 'f1', name: '低支付ROI暂停', description: '任务规则', fields: [] },
      { id: 'f5', name: '自动化追投', description: '候选', fields: [] },
    ],
    logs: [],
    monitoring: {
      accounts: accounts.map(a => ({ account_id: a.id, backend: 'official_api', plans: [], data_connected: true })),
    },
    ego_executor: {
      provider: 'ego',
      enabled: true,
      armed: true,
      running: true,
      mode: 'account_parallel',
      max_parallel: 3,
      account_workers: [
        { account_id: '1999999999999002', name: '示例分组A · 直播户', current: true, message: '等待API', space_id: 3 },
      ],
    },
    api_rule_auto: [
      {
        account_id: '1999999999999002',
        plan_id: '8218616704149013',
        enabled: true,
        profile_revision: 'rev-1',
        diagnostics: [
          { feature_id: 'f1', reason: '未提交：本母计划已有待确认原单 45dd88' },
          { feature_id: 'f1', reason: '未提交：本母计划已有待确认原单 45dd88' },
          { feature_id: 'f1', reason: '未提交：本母计划已有待确认原单 45dd88' },
        ],
      },
    ],
    ...extra,
  };
  const fetcher = async (path) => {
    if (path === '/api/cruise?view=console') return { ok: true, json: async () => JSON.parse(JSON.stringify(backend)) };
    if (path.startsWith('/api/official-api/actions/history')) return { ok: true, json: async () => ({ account_id: backend.accounts[0].id, total: 0, page: 1, pages: 1, records: [] }) };
    return { ok: true, json: async () => ({ ok: true }) };
  };
  const app = new CruiseConsole(dom.window.document, fetcher, { poll: false });
  const $ = s => dom.window.document.querySelector(s);
  const $$ = s => [...dom.window.document.querySelectorAll(s)];
  return { dom, backend, app, $, $$ };
}

test('project grouping groups accounts by tab_group, supports role/search filtering, and persists local overrides', async () => {
  const prevLS = global.localStorage;
  global.localStorage = makeStorage();
  try {
    assert.equal(resolveAccountProject({ tab_group: '示例分组A' }), '示例分组A');
    assert.equal(resolveAccountProject({ tab_group: 'API账户' }), '默认项目');
    assert.equal(inferAccountRole({ name: '达人专号', scene: 'live' }), 'talent');
    assert.equal(inferAccountRole({ name: '示例分组A · 图文户', scene: 'product' }), 'product');
    assert.equal(inferAccountRole({ name: '示例分组B直播户', scene: 'live' }), 'live');

    const f = makeConsoleFixture();
    await f.app.start();
    assert.equal(f.$('#account-count').textContent, '5');

    const groups = f.$$('details.project-group');
    assert.equal(groups.length, 3);
    assert.equal(groups[0].dataset.project, '示例分组A');
    assert.equal(groups[0].querySelectorAll('.account-item').length, 2);
    assert.equal(groups[1].dataset.project, '细肩带');
    assert.equal(groups[2].dataset.project, '示例分组C');
    assert.equal(f.$('#account-project-tag').textContent, '示例分组A');

    // Role filter chips
    f.$('[data-rail-filter="product"]').click();
    assert.equal(f.$$('.account-item').length, 2);
    f.$('[data-rail-filter="talent"]').click();
    assert.equal(f.$$('.account-item').length, 1);
    assert.match(f.$('.account-item').textContent, /示例分组C达人合作号/);
    f.$('[data-rail-filter="all"]').click();
    assert.equal(f.$$('.account-item').length, 5);

    // Search filter
    const search = f.$('#account-search');
    search.value = '细肩带';
    search.dispatchEvent(new f.dom.window.Event('input', { bubbles: true }));
    assert.equal(f.$$('details.project-group').length, 1);
    assert.equal(f.$$('.account-item').length, 2);
    search.value = '';
    search.dispatchEvent(new f.dom.window.Event('input', { bubbles: true }));

    // Project group manager dialog save & reset
    f.$('#project-manage-btn').click();
    assert.equal(f.$('#project-group-dialog').open, true);
    f.$('[data-project-input="8929253702525378"]').value = '联合项目';
    f.$('[data-role-input="1999999999999002"]').value = 'talent';
    f.$('#project-group-form').dispatchEvent(new f.dom.window.Event('submit', { bubbles: true, cancelable: true }));
    assert.equal(f.$('#project-group-dialog').open, false);
    assert.ok(f.$$('details.project-group').some(g => g.dataset.project === '联合项目'));
    assert.match(f.$('[data-account="1999999999999002"] .role-badge').textContent, /达人/);

    f.$('#project-manage-btn').click();
    f.$('#project-group-reset').click();
    assert.equal(f.$$('details.project-group').some(g => g.dataset.project === '联合项目'), false);
    assert.match(f.$('[data-account="1999999999999002"] .role-badge').textContent, /直播/);
    f.app.stop();
  } finally {
    global.localStorage = prevLS;
  }
});

test('4-tab switching isolates view-chase, renders ego tab badge, and collapses guarantee drawer by default', async () => {
  const prevLS = global.localStorage;
  global.localStorage = makeStorage();
  try {
    const f = makeConsoleFixture();
    await f.app.start();

    assert.equal(f.$('#view-plans').hidden, false);
    assert.equal(f.$('#view-chase').hidden, true);
    assert.equal(f.$('#view-rules').hidden, true);
    assert.equal(f.$('#view-ledger').hidden, true);
    assert.equal(f.$('#guarantee-panel details.guarantee-drawer').open, false);
    assert.equal(f.$('#tab-chase-badge').hidden, false);
    assert.match(f.$('#tab-chase-badge').textContent, /1户运行/);

    f.$('[data-view="chase"]').click();
    assert.equal(f.$('#view-plans').hidden, true);
    assert.equal(f.$('#view-chase').hidden, false);
    assert.ok(f.$('#view-chase #hybrid-chase-panel'));

    f.$('[data-view="rules"]').click();
    assert.equal(f.$('#view-rules').hidden, false);
    const diagText = f.$('#feature-automation-state').textContent;
    assert.equal(diagText.split('未提交：本母计划已有待确认原单 45dd88').length - 1, 1);

    f.$('[data-view="ledger"]').click();
    assert.equal(f.$('#view-ledger').hidden, false);
    f.app.stop();
  } finally {
    global.localStorage = prevLS;
  }
});

test('hybrid-chase panel emits zero inline styles (CSP safe) and splits global vs account zones', () => {
  const dom = new JSDOM('<section id="hybrid-chase-panel"></section>', { url: 'http://127.0.0.1:8877/cruise' });
  const aid = '1999999999999002';
  const app = {
    accountId: aid,
    state: {
      accounts: [{ id: aid, name: '示例分组A · 直播户', scene: 'live', revision: 'rev-1', profile: {} }],
      ego_executor: {
        provider: 'ego',
        enabled: true,
        armed: true,
        running: true,
        mode: 'account_parallel',
        max_parallel: 3,
        account_workers: [{ account_id: aid, name: '示例分组A · 直播户', current: true, error: '测试报错', message: 'waiting', space_id: 3 }],
      },
      hybrid_chase: [{ account_id: aid, mode: 'api_data_browser', updated_at: '2026-11-19T00:00:00Z', bridge: { current: true, armed: true, version: '1.23.3' } }],
    },
    $: s => dom.window.document.querySelector(s),
    account() { return this.state.accounts[0]; },
  };
  const panel = new HybridPanel(app);
  panel.render();
  assert.ok(panel.root.querySelector('.chase-zone-global'));
  assert.ok(panel.root.querySelector('.chase-zone-account'));
  assert.ok(panel.root.querySelector('#ego-account-workers tr.is-current-account'));
  assert.equal(panel.root.querySelector('[style]'), null);
});

test('plan-metrics highlights active entry and dims quiet zero-spend entry', () => {
  const dom = new JSDOM('<div id="account-metrics"></div><div id="plan-table"></div><span id="connection-status"></span>', { url: 'http://127.0.0.1:8877/cruise' });
  const app = {
    doc: dom.window.document,
    accountId: '1999999999999002',
    state: {
      now: '2026-11-19T15:00:00+08:00',
      monitoring: {
        metric_catalog: [{ key: 'spend', label: '消耗', unit: '元' }, { key: 'roi', label: 'ROI' }],
        accounts: [{
          account_id: '1999999999999002',
          backend: 'official_api',
          data_connected: true,
          summary_by_entry: [
            { entry_label: '全域', metric_date: '2026-11-19', captured_at: '2026-11-19T15:00:00+08:00', read_fresh: true, metrics: { spend: 0, roi: 0 } },
            { entry_label: '乘方', metric_date: '2026-11-19', captured_at: '2026-11-19T15:00:00+08:00', read_fresh: true, metrics: { spend: 35086.2, roi: 3.39 } },
          ],
          plans: [],
        }],
      },
    },
  };
  const view = new PlanMetrics(app);
  view.summary();
  const entries = [...dom.window.document.querySelectorAll('.metric-entry')];
  assert.equal(entries.length, 2);
  assert.equal(entries[0].classList.contains('metric-entry--quiet'), true);
  assert.equal(entries[1].classList.contains('metric-entry--active'), true);
});
