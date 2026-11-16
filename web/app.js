(() => {
  'use strict';

  const BASE = (location.pathname.replace(/\/[^/]*$/, '/') || '/').replace(/\/?$/, '/');
  const API = (p) => BASE + 'api/' + p.replace(/^\//, '');
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));

  let SESSION = localStorage.getItem('yw_session') || '';
  let state = { version: '1.4.0', page: 'dashboard', overview: null, config: null, nav: [], logs: [], chat: [] };
  let pollTimer = null, logTimer = null, sse = null;

  /* ---------------- 基础 ---------------- */
  function toast(msg, type = '') {
    const el = $('#toast');
    el.textContent = msg;
    el.className = 'toast show ' + type;
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.className = 'toast ' + type; }, 3200);
  }

  async function api(path, opts = {}) {
    const o = Object.assign({ method: 'GET', headers: {} }, opts);
    o.headers = Object.assign({ Authorization: 'Bearer ' + SESSION }, o.headers);
    if (o.body && !(o.body instanceof FormData)) {
      o.headers['Content-Type'] = 'application/json';
      o.body = JSON.stringify(o.body);
    }
    const r = await fetch(API(path), o);
    if (r.status === 401) { logout(true); throw new Error('登录已失效'); }
    let data = {};
    try { data = await r.json(); } catch (e) {}
    if (!r.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + r.status));
    return data;
  }

  function openModal(html, wide) {
    $('#modal').innerHTML = '<div class="modal-card' + (wide ? ' wide' : '') + '">' + html + '</div>';
    $('#modal').classList.remove('hidden');
    $('#mask').classList.remove('hidden');
    return $('.modal-card', $('#modal'));
  }
  function closeModal() {
    $('#modal').classList.add('hidden');
    $('#mask').classList.add('hidden');
    $('#modal').innerHTML = '';
  }
  function confirmBox(title, text, okText = '确认', danger = false) {
    return new Promise((resolve) => {
      const card = openModal(
        '<div class="modal-head"><h3>' + esc(title) + '</h3></div>' +
        '<div class="modal-body"><p class="muted" style="font-size:13px;line-height:1.8">' + text + '</p></div>' +
        '<div class="modal-foot"><button class="btn ghost" data-no>取消</button>' +
        '<button class="btn ' + (danger ? 'danger' : 'primary') + '" data-yes>' + esc(okText) + '</button></div>');
      $('[data-no]', card).onclick = () => { closeModal(); resolve(false); };
      $('[data-yes]', card).onclick = () => { closeModal(); resolve(true); };
    });
  }

  function fmtUptime(s) {
    s = Math.max(0, parseInt(s || 0, 10));
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d) return d + ' 天 ' + h + ' 时';
    if (h) return h + ' 时 ' + m + ' 分';
    return m + ' 分 ' + (s % 60) + ' 秒';
  }
  function fmtNum(n) {
    n = Number(n || 0);
    if (n >= 10000) return (n / 10000).toFixed(1) + ' 万';
    return String(n);
  }
  function timeText(ts) {
    if (!ts) return '-';
    const d = new Date(Number(ts) * 1000);
    return d.toLocaleString('zh-CN', { hour12: false });
  }
  function kindMeta(k) {
    return {
      qq: { title: 'QQ 官方机器人', cls: 'qq', note: '开放平台网关' }
    }[k] || { title: 'QQ 官方机器人', cls: 'qq', note: '开放平台网关' };
  }

  /* ---------------- 主题 ---------------- */
  function applyTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('yw_theme', t);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', t === 'light' ? '#f3f5fb' : '#080a12');
  }
  function toggleTheme() {
    applyTheme(document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light');
  }

  /* ---------------- 认证 ---------------- */
  function tokenOk(v) { return /^[A-Za-z0-9]{30}$/.test(String(v || '').trim()); }

  function showAuth() {
    $('#auth').classList.remove('hidden');
    $('#shell').classList.add('hidden');
    $('#boot').classList.add('hidden');
    if (pollTimer) clearInterval(pollTimer);
    if (sse) { sse.close(); sse = null; }
  }
  function showShell() {
    $('#auth').classList.add('hidden');
    $('#shell').classList.remove('hidden');
    $('#boot').classList.add('hidden');
  }

  async function bootAuth() {
    let st = {};
    try { st = await api('auth/state'); } catch (e) {}
    if (st.version) {
      state.version = st.version;
      ['brandVer', 'verChip', 'footVer', 'artVer'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.textContent = 'v' + st.version;
      });
    }
    if (!st.initialized) return renderSetup();
    if (SESSION) {
      try {
        await enterApp();
        return;
      } catch (e) { SESSION = ''; localStorage.removeItem('yw_session'); }
    }
    renderLogin();
  }

  function authShell(inner) {
    $('#authCard').innerHTML = inner;
    showAuth();
  }

  function tokenInput(id, ph) {
    return '<div class="field"><label class="label" for="' + id + '">访问令牌</label>' +
      '<div style="display:flex;gap:8px">' +
      '<input class="input" id="' + id + '" placeholder="' + ph + '" autocomplete="off" spellcheck="false" style="font-family:ui-monospace,Menlo,Consolas,monospace;letter-spacing:.06em">' +
      '<button type="button" class="btn ghost" id="' + id + 'Eye" style="flex:0 0 auto;padding:0 12px">显示</button></div>' +
      '<div class="hint">30 位字母与数字，可在服务器上执行 <code>./start.sh token</code> 查看</div></div>';
  }

  function bindEye(id) {
    const inp = $('#' + id), btn = $('#' + id + 'Eye');
    if (!inp || !btn) return;
    btn.onclick = () => {
      const on = inp.type === 'text';
      inp.type = on ? 'password' : 'text';
      btn.textContent = on ? '显示' : '隐藏';
    };
  }

  function renderSetup() {
    authShell(
      '<h2>第一次启动</h2>' +
      '<p class="sub">控制台用一串 30 位访问令牌进门，不再需要账号和密码。令牌在服务启动时已经自动生成。</p>' +
      '<div class="auth-form">' +
      '<div class="auth-note" style="margin:0"><b>令牌在哪？</b><br>登录服务器进入安装目录，执行 <code>./start.sh token</code> 即可显示；也可以直接查看 <code>data/TOKEN.txt</code>。</div>' +
      '<div id="tkOut" class="hidden"><div class="field"><label class="label">新令牌（只显示这一次）</label>' +
      '<input class="input" id="tkVal" readonly style="font-family:ui-monospace,Menlo,Consolas,monospace"></div>' +
      '<button class="btn block" id="tkCopy">复制令牌</button></div>' +
      '<p class="err" id="suErr"></p>' +
      '<button class="btn primary block" id="genBtn">生成并进入控制台</button>' +
      '</div>' +
      '<div class="auth-extra"><span>会话有效期 24 小时</span><a href="javascript:void(0)" id="helpTip">怎么查看令牌？</a></div>'
    );
    $('#helpTip').onclick = tokenHelp;
    $('#genBtn').onclick = async () => {
      const btn = $('#genBtn'); btn.disabled = true; btn.classList.add('loading');
      try {
        const r = await api('auth/setup', { method: 'POST', body: {} });
        SESSION = r.session; localStorage.setItem('yw_session', SESSION);
        if ($('#tkVal')) $('#tkVal').value = r.token || '';
        if ($('#tkOut')) $('#tkOut').classList.remove('hidden');
        toast('令牌已生成，请先复制保存', 'ok');
        setTimeout(() => { enterApp(); }, 2600);
      } catch (ex) { $('#suErr').textContent = ex.message; }
      finally { btn.disabled = false; btn.classList.remove('loading'); }
    };
    $('#tkCopy').onclick = () => {
      const v = $('#tkVal').value;
      if (navigator.clipboard) navigator.clipboard.writeText(v).then(() => toast('已复制', 'ok')).catch(() => {});
      else { $('#tkVal').select(); document.execCommand('copy'); toast('已复制', 'ok'); }
    };
  }

  function tokenHelp() {
    openModal(
      '<div class="modal-head"><h3>查看访问令牌</h3></div>' +
      '<div class="modal-body"><p class="muted" style="font-size:13px;line-height:1.9">登录服务器，进入机器人安装目录，任选一种：</p>' +
      '<pre class="plat-url" style="padding:14px">./start.sh token</pre>' +
      '<pre class="plat-url" style="padding:14px">cat data/TOKEN.txt</pre>' +
      '<p class="muted" style="font-size:12.5px;line-height:1.9">令牌为 30 位字母与数字，可随时重置：</p>' +
      '<pre class="plat-url" style="padding:14px">./start.sh token --reset</pre></div>' +
      '<div class="modal-foot"><button class="btn primary" onclick="this.closest(\'.modal\').classList.add(\'hidden\');document.getElementById(\'mask\').classList.add(\'hidden\')">知道了</button></div>', false);
  }

  function renderLogin() {
    authShell(
      '<h2>进入控制台</h2>' +
      '<p class="sub">粘贴访问令牌，30 位字母与数字。</p>' +
      '<form class="auth-form" id="loginForm" autocomplete="off">' +
      tokenInput('lgTok', '例如 7fK2mQ9xR4tY6uP1aS3dF5gH8jL0') +
      '<p class="err" id="lgErr"></p>' +
      '<button class="btn primary block" id="lgBtn" type="submit">进入控制台</button>' +
      '</form>' +
      '<div class="auth-extra"><span>会话有效期 24 小时</span><a href="javascript:void(0)" id="forgotTip">令牌丢了？</a></div>' +
      '<div class="auth-note"><b>提示</b><br>令牌保存在服务器 <code>data/TOKEN.txt</code>，执行 <code>./start.sh token</code> 可随时查看或重置。</div>'
    );
    bindEye('lgTok');
    $('#forgotTip').onclick = tokenHelp;
    $('#loginForm').onsubmit = async (e) => {
      e.preventDefault();
      const t = $('#lgTok').value.trim(), err = $('#lgErr');
      if (!t) return err.textContent = '请填写访问令牌';
      if (!tokenOk(t)) return err.textContent = '令牌应为 30 位字母与数字';
      const btn = $('#lgBtn'); btn.disabled = true; btn.classList.add('loading');
      try {
        const r = await api('auth/login', { method: 'POST', body: { token: t } });
        SESSION = r.session; localStorage.setItem('yw_session', SESSION);
        await enterApp();
      } catch (ex) {
        err.textContent = ex.message;
      } finally { btn.disabled = false; btn.classList.remove('loading'); }
    };
    setTimeout(() => { const el = $('#lgTok'); if (el) el.focus(); }, 120);
  }

  function logout(silent) {
    const s = SESSION;
    SESSION = '';
    localStorage.removeItem('yw_session');
    if (pollTimer) clearInterval(pollTimer);
    if (sse) { sse.close(); sse = null; }
    if (!silent) {
      fetch(API('auth/logout'), { method: 'POST', headers: { Authorization: 'Bearer ' + s } }).catch(() => {});
      toast('已退出登录');
    }
    bootAuth();
  }

  /* ---------------- 导航 ---------------- */
  const ICONS = {
    dashboard: '<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h4A1.5 1.5 0 0 1 11 5.5v4A1.5 1.5 0 0 1 9.5 11h-4A1.5 1.5 0 0 1 4 9.5v-4Zm9 0A1.5 1.5 0 0 1 14.5 4h4A1.5 1.5 0 0 1 20 5.5v4a1.5 1.5 0 0 1-1.5 1.5h-4A1.5 1.5 0 0 1 13 9.5v-4ZM4 14.5A1.5 1.5 0 0 1 5.5 13h4a1.5 1.5 0 0 1 1.5 1.5v4A1.5 1.5 0 0 1 9.5 20h-4A1.5 1.5 0 0 1 4 18.5v-4Zm9 0a1.5 1.5 0 0 1 1.5-1.5h4a1.5 1.5 0 0 1 1.5 1.5v4a1.5 1.5 0 0 1-1.5 1.5h-4a1.5 1.5 0 0 1-1.5-1.5v-4Z"/>',
    chat: '<path d="M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4V5Z"/><path d="M8 8h8M8 12h5"/>',
    model: '<path d="M12 2 4 6v6c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V6l-8-4Z"/><path d="M9 12h6M12 9v6"/>',
    platform: '<circle cx="7" cy="11" r="3.4"/><circle cx="17" cy="13" r="3.4"/><circle cx="12" cy="4.6" r="2.4"/><path d="M9.5 8.6 11 6.6M14.8 10.4 15.6 7M10 13.4l4 1"/>',
    plugins: '<path d="M10 3a2 2 0 0 1 2 2v1.1A5 5 0 0 1 15.9 8H17a2 2 0 1 1 0 4h-1.1A5 5 0 0 1 12 15.9V17a2 2 0 1 1-4 0v-1.1A5 5 0 0 1 5.1 12H4a2 2 0 1 1 0-4h1.1A5 5 0 0 1 8 5.1V5a2 2 0 0 1 2-2Z"/><circle cx="10" cy="10" r="2.4"/>',
    market: '<path d="M4 7h16l-1.2 12.2A2 2 0 0 1 16.8 21H7.2a2 2 0 0 1-2-1.8L4 7Z"/><path d="M8 7V5a4 4 0 0 1 8 0v2"/>',
    logs: '<path d="M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z"/><path d="M8 9h8M8 13h8M8 17h5"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 9 19.4a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3.05V3a2 2 0 1 1 4 0v.09A1.7 1.7 0 0 0 15 4.6a1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9c.12.62.68 1.05 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1Z"/>'
  };
  const NAV = [
    { g: '工作台', items: [['dashboard', '概览'], ['chat', '试聊']] },
    { g: '配置', items: [['model', '模型'], ['platform', '平台连接']] },
    { g: '扩展', items: [['plugins', '插件'], ['market', '插件市场']] },
    { g: '系统', items: [['logs', '日志'], ['settings', '设置']] }
  ];
  const TITLES = { dashboard: '概览', chat: '试聊', model: '模型', platform: '平台连接', plugins: '插件', market: '插件市场', logs: '日志', settings: '设置' };

  function renderNav() {
    $('#nav').innerHTML = NAV.map((g) => '<div class="nav-group">' + g.g + '</div>' +
      g.items.map(([id, label]) => '<a class="nav-item" href="#' + id + '" data-nav="' + id + '">' +
        '<svg viewBox="0 0 24 24">' + (ICONS[id] || '') + '</svg><span class="n-label">' + label + '</span>' +
        (id === 'settings' ? '<span class="n-badge hidden" id="navUpdate">新</span>' : '') + '</a>').join('')).join('');
    $$('#nav .nav-item').forEach((a) => {
      a.onclick = (e) => { e.preventDefault(); go(a.dataset.nav); };
    });
  }

  function go(page) {
    if (!TITLES[page]) page = 'dashboard';
    state.page = page;
    if (location.hash !== '#' + page) history.replaceState(null, '', '#' + page);
    $('#pageTitle').textContent = TITLES[page];
    $$('#nav .nav-item').forEach((a) => a.classList.toggle('on', a.dataset.nav === page));
    document.body.classList.remove('nav-open');
    $('#sidebar').classList.remove('open');
    $('#mask').classList.add('hidden');
    window.scrollTo({ top: 0, behavior: 'instant' });
    const fn = { dashboard: pageDashboard, chat: pageChat, model: pageModel, platform: pagePlatform,
      plugins: pagePlugins, market: pageMarket, logs: pageLogs, settings: pageSettings }[page];
    Promise.resolve(fn()).catch((e) => {
      $('#view').innerHTML = '<div class="card"><div class="empty"><b>页面加载失败</b>' + esc(e.message) + '</div></div>';
    });
  }

  function paintTop(o) {
    if (!o) return;
    $('#memChip').textContent = (o.rss_mb || 0) + ' MB';
    $('#verChip').textContent = 'v' + o.version;
    $('#footVer').textContent = 'v' + o.version;
    $('#brandVer').textContent = 'v' + o.version;
    const dot = $('#sideDot');
    dot.className = 'pulse' + (o.online ? ' on' : '');
    $('#sideState').textContent = o.online ? ((o.nickname || '已连接') + (o.self_id ? ' · ' + o.self_id : '')) : '等待平台接入';
    const u = o.update || {};
    const chip = $('#updateChip');
    if (u.need) {
      chip.hidden = false;
      chip.textContent = '可更新 v' + (u.latest || '');
      chip.onclick = () => go('settings');
      const nb = $('#navUpdate'); if (nb) nb.classList.remove('hidden');
    } else {
      chip.hidden = true;
    }
    const q = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    q('stUptime', fmtUptime(o.uptime));
    q('stRss', (o.rss_mb || 0) + ' MB');
    q('stPlugins', (o.plugins && o.plugins.loaded || 0) + ' / ' + (o.plugins && o.plugins.total || 0));
    q('stRecv', fmtNum(o.stats && o.stats.recv));
    q('stSent', fmtNum(o.stats && o.stats.sent));
    q('stLlm', fmtNum(o.stats && o.stats.llm));
    q('stErr', fmtNum(o.stats && o.stats.err));
  }

  async function refreshTop() {
    try {
      const o = await api('overview');
      state.overview = o;
      paintTop(o);
      return o;
    } catch (e) { return null; }
  }

  function startPoll() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(refreshTop, 8000);
  }

  async function enterApp() {
    const o = await api('overview');
    state.overview = o;
    state.version = o.version;
    state.onboardDone = !!o.onboard_done;
    $('#userName').textContent = o.auth_user || 'admin';
    $('#userAvatar').textContent = (o.auth_user || 'A').slice(0, 1).toUpperCase();
    renderNav();
    showShell();
    paintTop(o);
    go((location.hash || '#dashboard').slice(1));
    startPoll();
  }

  /* ---------------- 概览 ---------------- */
  async function pageDashboard() {
    const o = state.overview || await api('overview');
    state.overview = o;
    paintTop(o);
    const u = o.update || {};
    const plats = o.platforms || [];
    const online = plats.filter((p) => p.online).length;
    const updBanner = u.need ? (
      '<div class="card" style="border-color:rgba(251,191,36,.3);background:rgba(251,191,36,.07)">' +
      '<div class="card-body between" style="flex-wrap:wrap;gap:14px">' +
      '<div class="row"><span class="badge amber"><span class="dot"></span>新版本</span>' +
      '<div><b style="font-size:13.6px">YueWuBot v' + esc(u.latest) + ' 已发布</b>' +
      '<div class="hint">当前 v' + esc(o.version) + '，更新后会自动重启。</div></div></div>' +
      '<button class="btn primary sm" id="goUpdate">查看更新</button></div></div>'
    ) : '';

    $('#view').innerHTML =
      '<div class="page">' +
      onboardCard(o) +
      '<div class="hero">' +
      '<div class="hero-avatar">' + esc((o.nickname || 'Y').slice(0, 1)) + '</div>' +
      '<div class="hero-copy"><h2>' + esc(o.nickname || 'YueWuBot') +
      '<span class="badge ' + (o.online ? 'green' : 'gray') + '"><span class="dot"></span>' + (o.online ? '在线' : '未连接') + '</span></h2>' +
      '<p>' + (o.online ? '已连接 ' + esc(o.platform_name || '平台') + (o.self_id ? ' · ' + esc(o.self_id) : '') : '还没有平台接入，去「平台连接」添加一条。') + '</p>' +
      '<div class="hero-meta"><span class="chip soft">' + esc(o.os || '') + '</span><span class="chip soft">负载 ' + esc(o.load || '-') + '</span>' +
      '<span class="chip soft">' + online + ' / ' + plats.length + ' 条连接在线</span></div></div>' +
      '<div class="hero-actions"><button class="btn ghost" id="heroChat">试聊一下</button><button class="btn primary" id="heroRefresh">刷新状态</button></div>' +
      '</div>' +
      updBanner +
      '<div class="grid-4">' +
      '<div class="stat"><div class="ic"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.4"/><path d="M12 7.5V12l3 2"/></svg></div>' +
      '<div class="k">运行时间</div><div class="v" id="stUptime">' + fmtUptime(o.uptime) + '</div><div class="trail">本次进程运行时长</div></div>' +
      '<div class="stat"><div class="ic c2"><svg viewBox="0 0 24 24"><path d="M5 19V9m5 10V5m5 14v-7m5 7V8"/></svg></div>' +
      '<div class="k">内存占用</div><div class="v" id="stRss">' + (o.rss_mb || 0) + ' MB</div><div class="trail">常驻内存占用</div></div>' +
      '<div class="stat"><div class="ic c3"><svg viewBox="0 0 24 24"><path d="M10 3a2 2 0 0 1 2 2v1.1A5 5 0 0 1 15.9 8H17a2 2 0 1 1 0 4h-1.1A5 5 0 0 1 12 15.9V17a2 2 0 1 1-4 0v-1.1A5 5 0 0 1 5.1 12H4a2 2 0 1 1 0-4h1.1A5 5 0 0 1 8 5.1V5a2 2 0 0 1 2-2Z"/></svg></div>' +
      '<div class="k">插件</div><div class="v" id="stPlugins">' + (o.plugins.loaded || 0) + ' / ' + (o.plugins.total || 0) + '</div><div class="trail">已加载 / 已安装</div></div>' +
      '<div class="stat"><div class="ic c4"><svg viewBox="0 0 24 24"><path d="M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9l-5 4V5Z"/></svg></div>' +
      '<div class="k">收到消息</div><div class="v" id="stRecv">' + fmtNum(o.stats && o.stats.recv) + '</div><div class="trail">已发 ' + fmtNum(o.stats && o.stats.sent) + ' · 模型 ' + fmtNum(o.stats && o.stats.llm) + '</div></div>' +
      '</div>' +
      '<div class="grid-2">' +
      '<div class="card"><div class="card-head"><div><h3>平台连接</h3><div class="sub">每个连接独立重连，互不影响</div></div>' +
      '<div class="right"><button class="btn sm ghost" id="toPlatform">管理</button></div></div>' +
      '<div class="card-body"><div class="list">' + (plats.length ? plats.map((p) => {
        const m = kindMeta(p.kind);
        return '<div class="list-item"><div class="ic"><svg viewBox="0 0 24 24"><path d="M7 7.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7Zm10 2a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7Z"/></svg></div>' +
          '<div class="txt"><b>' + esc(p.name) + '</b><small>' + esc(m.title) + (p.self_id ? ' · ' + esc(p.self_id) : '') + '</small></div>' +
          '<span class="badge ' + (p.online ? 'green' : 'gray') + '"><span class="dot"></span>' + (p.online ? '在线' : '离线') + '</span></div>';
      }).join('') : '<div class="empty"><b>还没有接入机器人</b>填入 AppID 与 AppSecret 后，这里会显示在线状态。</div>') + '</div></div></div>' +
      '<div class="card"><div class="card-head"><div><h3>模型与余额</h3><div class="sub">' + esc(o.model || '未配置') + '</div></div>' +
      '<div class="right"><button class="btn sm ghost" id="toModel">设置</button></div></div>' +
      '<div class="card-body"><div class="kv"><span>协议</span><b>' + esc(o.protocol || 'openai') + '</b></div>' +
      '<div class="kv"><span>API Key</span><b>' + (o.has_key ? '已配置' : '未配置') + '</b></div>' +
      '<div class="kv"><span>最近响应</span><b>' + (o.llm_ms || 0) + ' ms</b></div>' +
      '<div id="balBox" class="hint" style="margin-top:10px">正在查询余额…</div></div></div>' +
      '</div>' +
      '<div class="card"><div class="card-head"><div><h3>快捷操作</h3><div class="sub">常用入口</div></div></div>' +
      '<div class="card-body row" style="flex-wrap:wrap">' +
      '<button class="btn" id="qChat">试聊</button><button class="btn" id="qMarket">逛插件市场</button>' +
      '<button class="btn" id="qCheck">检查更新</button><button class="btn" id="qLogs">看日志</button>' +
      '<button class="btn danger" id="qRestart">重启机器人</button></div></div>' +
      '</div>';

    $('#heroChat').onclick = () => go('chat');
    $('#heroRefresh').onclick = async () => { await refreshTop(); toast('已刷新', 'ok'); go('dashboard'); };
    if ($('#goUpdate')) $('#goUpdate').onclick = () => go('settings');
    bindOnboard(o);
    $('#toPlatform').onclick = () => go('platform');
    $('#toModel').onclick = () => go('model');
    $('#qChat').onclick = () => go('chat');
    $('#qMarket').onclick = () => go('market');
    $('#qLogs').onclick = () => go('logs');
    $('#qCheck').onclick = async () => {
      toast('正在检查更新…');
      try { const r = await api('update', { method: 'POST', body: { check: true } }); toast(r.update.need ? ('发现 v' + r.update.latest) : '已是最新版本', 'ok'); refreshTop(); }
      catch (e) { toast(e.message, 'err'); }
    };
    $('#qRestart').onclick = () => doRestart();
    loadBalance($('#balBox'));
  }

  function onboardCard(o) {
    if (state.onboardDone) return '';
    const pl = o.platforms || [];
    const steps = [
      { k: '填写模型 API Key', d: '选服务商，填 Key 与模型名，先让机器人会说话', go: 'model', ok: !!o.has_key },
      { k: '接入聊天平台', d: 'QQ 官方机器人填 AppID 与 AppSecret，保存后扫码或上线', go: 'platform', ok: pl.length > 0 },
      { k: '打开需要的插件', d: '签到、点歌、图库、天气，按需启用即可', go: 'plugins', ok: (o.plugins || {}).loaded > 0 },
      { k: '发一句话试试', d: '私聊机器人或在群里 @ 它，看是否正常回复', go: 'chat', ok: !!o.online }
    ];
    const done = steps.filter((x) => x.ok).length;
    if (done >= steps.length) {
      state.onboardDone = true;
      api('onboard', { method: 'POST', body: { done: true } }).catch(() => {});
      return '';
    }
    return '<div class="card ob-card">' +
      '<div class="card-head"><div><h3>新手引导</h3><div class="sub">四步跑通，大约三分钟</div></div>' +
      '<div class="right"><span class="chip soft">' + done + ' / ' + steps.length + '</span>' +
      '<button class="btn sm ghost" id="obSkip" style="margin-left:8px">跳过</button></div></div>' +
      '<div class="card-body" style="display:grid;gap:8px">' +
      steps.map((x, i) => '<button class="ob-step' + (x.ok ? ' ok' : '') + '" data-ob="' + x.go + '">' +
        '<span class="ob-idx">' + (x.ok ? '✓' : (i + 1)) + '</span>' +
        '<span class="ob-copy"><b>' + x.k + '</b><small>' + x.d + '</small></span>' +
        '<span class="ob-go">' + (x.ok ? '已完成' : '去设置') + '</span></button>').join('') +
      '</div></div>';
  }

  function bindOnboard(o) {
    $$('#view [data-ob]').forEach((b) => b.onclick = () => go(b.dataset.ob));
    const skip = $('#obSkip');
    if (skip) skip.onclick = async () => {
      state.onboardDone = true;
      try { await api('onboard', { method: 'POST', body: { done: true } }); } catch (e) {}
      toast('已跳过引导，可在设置里重新打开');
      go('dashboard');
    };
  }

  async function loadBalance(box) {
    if (!box) return;
    try {
      const r = await api('balance');
      const b = r.balance || {};
      if (b.supported) {
        box.innerHTML = '<div class="row" style="gap:10px"><span style="font-size:22px;font-weight:730;letter-spacing:-.02em">' +
          (Number(b.total || 0).toFixed(2)) + '</span><span class="badge blue">' + esc(b.currency || 'CNY') + '</span>' +
          '<span class="faint small" style="margin-left:auto">' + (b.checked_at ? timeText(b.checked_at) : '') + '</span></div>';
      } else {
        const msg = String(b.message || '该平台不支持余额查询');
        const brief = /401/.test(msg) ? '接口鉴权失败（401）' : /404/.test(msg) ? '接口不存在（404）' : (msg.length > 26 ? msg.slice(0, 26) + '…' : msg);
        box.innerHTML = '<span class="faint" title="' + esc(msg) + '">' + esc(brief) + '</span>';
      }
    } catch (e) { box.innerHTML = '<span class="faint">余额查询失败</span>'; }
  }

  /* ---------------- 试聊 ---------------- */
  async function pageChat() {
    $('#view').innerHTML =
      '<div class="page"><div class="card"><div class="card-head"><div><h3>试聊</h3>' +
      '<div class="sub">直接调用当前模型，不经过平台与插件</div></div>' +
      '<div class="right"><button class="btn sm ghost" id="clearChat">清空</button></div></div>' +
      '<div class="card-body" style="padding-top:14px"><div class="chat-box">' +
      '<div class="chat-list" id="chatList"></div>' +
      '<div class="chat-quick"><button class="btn sm ghost" data-q="你好，介绍一下你自己">自我介绍</button>' +
      '<button class="btn sm ghost" data-q="用三句话说明你能做什么">能做什么</button>' +
      '<button class="btn sm ghost" data-q="讲一个冷笑话">讲个笑话</button></div>' +
      '<div class="chat-input"><textarea class="textarea" id="chatInput" rows="1" placeholder="输入消息，Enter 发送，Shift+Enter 换行"></textarea>' +
      '<button class="btn primary" id="chatSend">发送</button></div></div></div></div></div>';
    const list = $('#chatList'), input = $('#chatInput');
    const paint = () => {
      list.innerHTML = state.chat.length ? state.chat.map((m) =>
        '<div class="msg ' + (m.role === 'user' ? 'me' : '') + '"><span class="who">' + (m.role === 'user' ? '你' : 'AI') + '</span>' +
        '<div><div class="bubble">' + esc(m.content) + '</div>' + (m.ms ? '<div class="meta">' + m.ms + ' ms</div>' : '') + '</div></div>').join('')
        : '<div class="empty"><b>开始对话</b>下面的输入框会直接发给模型。</div>';
      list.scrollTop = list.scrollHeight;
    };
    paint();
    $('#clearChat').onclick = () => { state.chat = []; paint(); };
    $$('[data-q]').forEach((b) => b.onclick = () => { input.value = b.dataset.q; input.focus(); });
    const send = async () => {
      const text = input.value.trim();
      if (!text) return;
      state.chat.push({ role: 'user', content: text });
      input.value = ''; paint();
      const btn = $('#chatSend'); btn.disabled = true; btn.classList.add('loading');
      const t0 = Date.now();
      try {
        const r = await api('chat', { method: 'POST', body: { messages: state.chat.slice(-12) } });
        state.chat.push({ role: 'assistant', content: r.reply, ms: r.ms || (Date.now() - t0) });
      } catch (e) {
        state.chat.push({ role: 'assistant', content: '（调用失败：' + e.message + '）' });
      }
      btn.disabled = false; btn.classList.remove('loading'); paint();
    };
    $('#chatSend').onclick = send;
    input.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } };
    input.focus();
  }

  /* ---------------- 模型 ---------------- */
  async function pageModel() {
    const c = (await api('config')).config;
    state.config = c;
    const llm = c.llm || {}, rep = c.reply || {};
    const hasKey = !!llm.api_key;
    $('#view').innerHTML =
      '<div class="page"><div class="grid-2" style="align-items:start">' +
      '<div class="card"><div class="card-head"><div><h3>模型接入</h3><div class="sub">OpenAI 兼容或 Anthropic Messages 协议</div></div></div>' +
      '<div class="card-body" style="display:grid;gap:15px">' +
      '<div class="field"><span class="label">协议</span><div class="seg" id="protoSeg">' +
      '<button data-v="openai" class="' + ((llm.protocol || 'openai') !== 'anthropic' ? 'on' : '') + '">OpenAI 兼容</button>' +
      '<button data-v="anthropic" class="' + (llm.protocol === 'anthropic' ? 'on' : '') + '">Anthropic</button></div></div>' +
      '<div class="field"><label class="label" for="mBase">接口地址</label><input class="input" id="mBase" value="' + esc(llm.base_url || '') + '" placeholder="https://api.deepseek.com"></div>' +
      '<div class="field"><label class="label" for="mKey">API Key <span class="opt">留空表示不修改</span></label>' +
      '<div class="input-row"><input class="input" id="mKey" type="password" placeholder="' + (hasKey ? '已保存，留空不修改' : 'sk-...') + '">' +
      '<button class="icon-btn" id="keyEye" style="border:1px solid var(--line-2);width:42px;height:auto;border-radius:11px"><svg viewBox="0 0 24 24"><path d="M12 5c5 0 8.7 3.5 10 7-1.3 3.5-5 7-10 7S3.3 15.5 2 12c1.3-3.5 5-7 10-7Z"/><circle cx="12" cy="12" r="2.8"/></svg></button></div></div>' +
      '<div class="field"><label class="label" for="mModel">模型名称</label><input class="input" id="mModel" value="' + esc(llm.model || '') + '" placeholder="deepseek-flash"></div>' +
      '<div class="field"><label class="label">温度 <span class="opt">越低越稳定</span></label><div class="row"><input class="range" id="mTemp" type="range" min="0" max="2" step="0.05" value="' + (llm.temperature == null ? 0.85 : llm.temperature) + '"><span class="range-val" id="mTempVal">' + (llm.temperature == null ? 0.85 : llm.temperature) + '</span></div></div>' +
      '<div class="grid-2" style="gap:12px"><div class="field"><label class="label" for="mMax">最大输出 tokens</label><input class="input" id="mMax" type="number" min="0" value="' + (llm.max_tokens || 0) + '"></div>' +
      '<div class="field"><label class="label" for="mTimeout">超时（秒）</label><input class="input" id="mTimeout" type="number" min="3" value="' + (llm.timeout || 8) + '"></div></div>' +
      '<div class="grid-2" style="gap:12px"><div class="field"><label class="label" for="mRetry">重试次数</label><input class="input" id="mRetry" type="number" min="1" max="5" value="' + (llm.retries || 3) + '"></div>' +
      '<div class="field"><label class="label" for="mBal">余额接口 <span class="opt">可留空</span></label><input class="input" id="mBal" value="' + esc(llm.balance_url || '') + '" placeholder="自动识别"></div></div>' +
      '<div class="between" style="padding:11px 13px;border:1px solid var(--line);border-radius:12px"><div><b style="font-size:12.9px">接收图片</b><div class="hint">用户发图时把图片一起交给模型</div></div>' +
      '<label class="switch"><input type="checkbox" id="mVision" ' + (llm.vision !== false ? 'checked' : '') + '><i></i></label></div>' +
      '<div class="between" style="padding:11px 13px;border:1px solid var(--line);border-radius:12px"><div><b style="font-size:12.9px">自动发图</b><div class="hint">回复里的图片链接会作为图片发送</div></div>' +
      '<label class="switch"><input type="checkbox" id="mAutoImg" ' + (rep.auto_image !== false ? 'checked' : '') + '><i></i></label></div>' +
      '<button class="btn primary block" id="saveModel">保存模型设置</button>' +
      '</div></div>' +
      '<div style="display:grid;gap:18px">' +
      '<div class="card"><div class="card-head"><div><h3>系统提示词</h3><div class="sub">留空则使用内置人设</div></div></div>' +
      '<div class="card-body"><textarea class="textarea" id="mSystem" style="min-height:220px">' + esc(llm.system_prompt || '') + '</textarea>' +
      '<button class="btn block" id="saveSystem" style="margin-top:12px">保存提示词</button></div></div>' +
      '<div class="card"><div class="card-head"><div><h3>连通性测试</h3><div class="sub">发送一条真实请求</div></div></div>' +
      '<div class="card-body"><div class="input-row"><input class="input" id="testIn" placeholder="ping"><button class="btn primary" id="testBtn">测试</button></div>' +
      '<pre class="plat-url" id="testOut" style="margin-top:12px;display:none;white-space:pre-wrap"></pre>' +
      '<div style="margin-top:12px;padding-top:12px;border-top:1px dashed var(--line)"><div class="between"><span class="hint">账户余额</span><button class="btn sm ghost" id="balRefresh">查询</button></div>' +
      '<div id="balBox2" class="hint" style="margin-top:8px">点击查询</div></div></div></div>' +
      '</div></div></div>';

    let proto = llm.protocol === 'anthropic' ? 'anthropic' : 'openai';
    $$('#protoSeg button').forEach((b) => b.onclick = () => {
      proto = b.dataset.v;
      $$('#protoSeg button').forEach((x) => x.classList.toggle('on', x === b));
    });
    $('#mTemp').oninput = (e) => { $('#mTempVal').textContent = e.target.value; };
    $('#keyEye').onclick = () => { const k = $('#mKey'); k.type = k.type === 'password' ? 'text' : 'password'; };

    const save = async (patch, btn) => {
      btn.disabled = true; btn.classList.add('loading');
      try {
        await api('config', { method: 'POST', body: patch });
        toast('已保存', 'ok');
      } catch (e) { toast(e.message, 'err'); }
      finally { btn.disabled = false; btn.classList.remove('loading'); }
    };
    $('#saveModel').onclick = () => {
      const key = $('#mKey').value.trim();
      const body = {
        llm: {
          protocol: proto, base_url: $('#mBase').value.trim(), model: $('#mModel').value.trim(),
          temperature: Number($('#mTemp').value), max_tokens: Number($('#mMax').value) || 0,
          timeout: Number($('#mTimeout').value) || 8, retries: Number($('#mRetry').value) || 3,
          balance_url: $('#mBal').value.trim(), vision: $('#mVision').checked,
          api_key: key || '__KEEP__'
        },
        reply: { auto_image: $('#mAutoImg').checked }
      };
      save(body, $('#saveModel'));
    };
    $('#saveSystem').onclick = () => save({ llm: { system_prompt: $('#mSystem').value } }, $('#saveSystem'));
    $('#testBtn').onclick = async () => {
      const btn = $('#testBtn'), out = $('#testOut');
      const text = $('#testIn').value.trim() || 'ping';
      btn.disabled = true; btn.classList.add('loading');
      try {
        const r = await api('chat', { method: 'POST', body: { messages: [{ role: 'user', content: text }] } });
        out.style.display = 'block';
        out.textContent = (r.reply || '(空回复)') + '\n\n· ' + (r.ms || 0) + ' ms';
      } catch (e) {
        out.style.display = 'block'; out.textContent = '失败：' + e.message;
      }
      btn.disabled = false; btn.classList.remove('loading');
    };
    $('#balRefresh').onclick = () => loadBalance($('#balBox2'));
  }

  /* ---------------- 平台 ---------------- */
  async function pagePlatform() {
    const r = await api('platforms');
    const list = r.connections || [];
    $('#view').innerHTML =
      '<div class="page"><div class="card"><div class="card-head"><div><h3>平台连接</h3>' +
      '<div class="sub">只接 QQ 官方机器人，AppID + AppSecret 就能上线</div></div>' +
      '<div class="right"><button class="btn primary sm" id="addPlat">新增连接</button></div></div>' +
      '<div class="card-body">' + (list.length ? '<div class="grid-2">' + list.map(platCard).join('') + '</div>'
        : '<div class="empty"><b>还没有连接</b>点右上角新增一条。</div>') + '</div></div>' +
      '<div class="card"><div class="card-head"><div><h3>接入说明</h3><div class="sub">三步把机器人接上 QQ</div></div></div>' +
      '<div class="card-body" style="display:grid;gap:10px">' +
      '<div class="kv"><span>第 1 步</span><b>QQ 开放平台创建机器人，拿到 AppID 与 AppSecret</b></div>' +
      '<div class="kv"><span>第 2 步</span><b>在开放平台把机器人加入群，或先用单聊调试</b></div>' +
      '<div class="kv"><span>第 3 步</span><b>填进这里保存，网关会自动连接并订阅群聊与单聊事件</b></div>' +
      '<div class="kv"><span>默认订阅</span><b>群 @ 消息、群消息、单聊消息</b></div>' +
      '</div></div></div>';

    $('#addPlat').onclick = () => platModal(null, list);
    $$('[data-edit]').forEach((b) => b.onclick = () => {
      const p = list.find((x) => x.id === b.dataset.edit);
      if (p) platModal(p, list);
    });
    $$('[data-del]').forEach((b) => b.onclick = async () => {
      const p = list.find((x) => x.id === b.dataset.del);
      if (!p) return;
      if (!await confirmBox('删除连接', '确定删除 <b>' + esc(p.name) + '</b> 吗？该操作不可撤销。', '删除', true)) return;
      try {
        await savePlatforms(list.filter((x) => x.id !== p.id));
        toast('已删除', 'ok'); go('platform');
      } catch (e) { toast(e.message, 'err'); }
    });
    $$('[data-toggle]').forEach((b) => b.onchange = async () => {
      const p = list.find((x) => x.id === b.dataset.toggle);
      if (!p) return;
      p.enabled = b.checked;
      try { await savePlatforms(list); toast(b.checked ? '已启用' : '已停用', 'ok'); go('platform'); }
      catch (e) { toast(e.message, 'err'); b.checked = !b.checked; }
    });
    $('#reconnectAll') && ($('#reconnectAll').onclick = async () => {
      try { await api('platforms/reconnect', { method: 'POST' }); toast('已重连', 'ok'); go('platform'); }
      catch (e) { toast(e.message, 'err'); }
    });
  }

  function platCard(p) {
    const m = kindMeta(p.kind);
    return '<div class="plat' + (p.enabled ? '' : ' plat-off') + '">' +
      '<div class="plat-head"><div class="plat-ic ' + m.cls + '"><svg viewBox="0 0 24 24"><path d="M7 7.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7Zm10 2a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7Z"/></svg></div>' +
      '<div class="plat-title"><b>' + esc(p.name) + '</b><small>' + esc(m.title) + ' · ' + esc(m.note) + '</small></div>' +
      '<span class="badge ' + (p.online ? 'green' : 'gray') + '"><span class="dot"></span>' + (p.online ? '在线' : '离线') + '</span></div>' +
      '<div class="plat-meta"><span class="badge">' + (p.enabled ? '已启用' : '已停用') + '</span>' +
      (p.self_id ? '<span class="badge cyan">' + esc(p.self_id) + '</span>' : '') +
      (p.nickname ? '<span class="badge">' + esc(p.nickname) + '</span>' : '') +
      (p.app_id && p.last_error ? '<span class="badge red" title="' + esc(p.last_error) + '">异常</span>' : '') +
      (p.app_id ? '' : '<span class="badge gray">待配置</span>') + '</div>' +
      (p.app_id ? '<div class="plat-url">AppID ' + esc(p.app_id) + '</div>' : '') +
      '<div class="plat-actions"><button class="btn sm ghost" data-edit="' + esc(p.id) + '">编辑</button>' +
      '<button class="btn sm danger" data-del="' + esc(p.id) + '">删除</button>' +
      '<label class="switch" style="margin-left:auto" title="启用"><input type="checkbox" data-toggle="' + esc(p.id) + '" ' + (p.enabled ? 'checked' : '') + '><i></i></label></div>' +
      '</div>';
  }

  async function savePlatforms(items) {
    const connections = items.map((x) => ({
      id: x.id, name: x.name, kind: 'qq',
      app_id: x.app_id === '******' ? '__KEEP__' : (x.app_id || ''),
      app_secret: x.app_secret === '******' ? '__KEEP__' : (x.app_secret || ''),
      enabled: !!x.enabled, self_id: x.self_id || '', intents: x.intents || 0
    }));
    await api('platforms', { method: 'POST', body: { connections } });
  }

  function platModal(p, list) {
    const editing = !!p;
    const cur = p || { kind: 'qq', direction: 'client', name: '', enabled: true, intents: 1 << 25 };
    const card = openModal(
      '<div class="modal-head"><h3>' + (editing ? '编辑机器人' : '接入 QQ 机器人') + '</h3><button class="icon-btn" data-close><svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div>' +
      '<div class="modal-body">' +
      '<div class="field"><label class="label">名称</label><input class="input" id="pName" value="' + esc(cur.name || '') + '" placeholder="给这个机器人起个名字"></div>' +
      '<div class="grid-2" style="gap:12px"><div class="field"><label class="label">AppID</label>' +
      '<input class="input" id="pAppId" value="' + esc(cur.app_id === '******' ? '' : (cur.app_id || '')) + '" placeholder="机器人 AppID"></div>' +
      '<div class="field"><label class="label">AppSecret</label>' +
      '<input class="input" id="pSecret" type="password" value="" placeholder="' + (editing ? '留空不修改' : 'AppSecret') + '"></div></div>' +
      '<div class="hint">在 QQ 开放平台「机器人」页面获取。保存后自动连接网关，无需填地址或端口。</div>' +
      '<div class="between" style="padding:10px 12px;border:1px solid var(--line);border-radius:11px"><span style="font-size:12.7px">启用这个机器人</span>' +
      '<label class="switch"><input type="checkbox" id="pEnabled" ' + (cur.enabled ? 'checked' : '') + '><i></i></label></div>' +
      '<p class="err" id="pErr"></p></div>' +
      '<div class="modal-foot"><button class="btn ghost" data-close>取消</button><button class="btn primary" id="pSave">保存</button></div>', true);
    $$('[data-close]', card).forEach((b) => b.onclick = closeModal);
    $('#pSave').onclick = async () => {
      const name = $('#pName').value.trim();
      const appId = $('#pAppId').value.trim() || (editing ? '__KEEP__' : '');
      const secret = $('#pSecret').value.trim() || (editing ? '__KEEP__' : '');
      if (!name) return $('#pErr').textContent = '请填写名称';
      if (!appId || !secret) return $('#pErr').textContent = '请填写 AppID 与 AppSecret';
      const item = Object.assign({}, cur, {
        name, kind: 'qq', direction: 'client', enabled: $('#pEnabled').checked,
        app_id: appId, app_secret: secret
      });
      const next = list.slice();
      const idx = next.findIndex((x) => x.id === item.id);
      if (idx >= 0) next[idx] = item; else next.push(item);
      try {
        await savePlatforms(next);
        closeModal(); toast('已保存', 'ok'); go('platform');
      } catch (e) { $('#pErr').textContent = e.message; }
    };
  }

  /* ---------------- 插件 ---------------- */
  async function pagePlugins() {
    const r = await api('plugins');
    const list = r.plugins || [];
    const admins = r.admins || [];
    state.plugins = list;
    $('#view').innerHTML =
      '<div class="page"><div class="card"><div class="card-head"><div><h3>已安装插件</h3>' +
      '<div class="sub">' + list.length + ' 个插件 · 点击开关启用或停用</div></div>' +
      '<div class="right"><button class="btn sm ghost" id="upBtn">上传 zip</button><input type="file" id="upFile" accept=".zip" hidden></div></div>' +
      '<div class="card-body"><div class="plugin-grid" id="pluginGrid">' +
      (list.length ? list.map(pluginCard).join('') : '<div class="empty" style="grid-column:1/-1"><b>还没有插件</b>去插件市场装一个，或者上传 zip。</div>') +
      '</div></div></div>' +
      '<div class="card"><div class="card-head"><div><h3>管理员</h3><div class="sub">只有这些 QQ 能使用受限插件</div></div>' +
      '<div class="right"><button class="btn sm ghost" id="saveAdmins">保存</button></div></div>' +
      '<div class="card-body"><div class="input-row"><input class="input" id="adminInput" placeholder="输入 QQ 号，回车添加">' +
      '<button class="btn" id="addAdmin">添加</button></div><div class="row" id="adminTags" style="flex-wrap:wrap;margin-top:12px"></div></div></div></div>';

    let cur = admins.slice();
    const paintAdmins = () => {
      $('#adminTags').innerHTML = cur.length ? cur.map((a) =>
        '<span class="badge blue" style="padding:5px 9px">' + esc(a) + ' <button data-rm="' + esc(a) + '" style="color:inherit;opacity:.7">×</button></span>').join('')
        : '<span class="faint small">还没有设置管理员</span>';
      $$('[data-rm]').forEach((b) => b.onclick = () => { cur = cur.filter((x) => x !== b.dataset.rm); paintAdmins(); });
    };
    paintAdmins();
    $('#addAdmin').onclick = () => {
      const v = $('#adminInput').value.trim();
      if (!/^\d{4,16}$/.test(v)) return toast('请输入 4-16 位 QQ 号', 'err');
      if (!cur.includes(v)) cur.push(v);
      $('#adminInput').value = ''; paintAdmins();
    };
    $('#adminInput').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); $('#addAdmin').click(); } };
    $('#saveAdmins').onclick = async () => {
      try { await api('admins', { method: 'POST', body: { admins: cur } }); toast('管理员已保存', 'ok'); }
      catch (e) { toast(e.message, 'err'); }
    };

    $('#upBtn').onclick = () => $('#upFile').click();
    $('#upFile').onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      const fd = new FormData(); fd.append('file', f);
      toast('正在安装…');
      try {
        const res = await api('plugins/upload', { method: 'POST', body: fd });
        toast('已安装 ' + (res.title || res.name), 'ok');
        if (res.deps && !res.deps.ok && !res.deps.skipped) toast('依赖安装失败：' + (res.deps.error || ''), 'err');
        go('plugins');
      } catch (ex) { toast(ex.message, 'err'); }
      e.target.value = '';
    };

    $$('[data-ptoggle]').forEach((el) => el.onchange = async () => {
      try {
        await api('plugins/' + encodeURIComponent(el.dataset.ptoggle) + '/toggle',
          { method: 'POST', body: { enabled: el.checked } });
        toast(el.checked ? '已启用' : '已停用', 'ok');
        go('plugins');
      } catch (e) { toast(e.message, 'err'); el.checked = !el.checked; }
    });
    $$('[data-pcfg]').forEach((b) => b.onclick = () => pluginConfigModal(list.find((x) => x.name === b.dataset.pcfg)));
    $$('[data-pdel]').forEach((b) => b.onclick = async () => {
      const p = list.find((x) => x.name === b.dataset.pdel);
      if (!p) return;
      if (!await confirmBox('删除插件', '确定删除 <b>' + esc(p.title) + '</b> 吗？插件数据会一起删除。', '删除', true)) return;
      try { await api('plugins/' + encodeURIComponent(p.name), { method: 'DELETE' }); toast('已删除', 'ok'); go('plugins'); }
      catch (e) { toast(e.message, 'err'); }
    });
  }

  function pluginCard(p) {
    return '<div class="plugin"><div class="plugin-top">' +
      '<div class="plugin-ic">' + (p.icon ? '<img src="' + esc(p.icon) + '" alt="">' : esc((p.title || p.name).slice(0, 1))) + '</div>' +
      '<div class="plugin-name"><b>' + esc(p.title || p.name) + '</b><small>v' + esc(p.version) + ' · ' + esc(p.author || 'unknown') + '</small></div>' +
      '<label class="switch" title="启用"><input type="checkbox" data-ptoggle="' + esc(p.name) + '" ' + (p.enabled ? 'checked' : '') + '><i></i></label></div>' +
      '<div class="plugin-desc">' + esc(p.description || '没有描述') + '</div>' +
      '<div class="plugin-foot"><span class="plugin-cmds">' + (p.commands || []).length + ' 条指令 · ' + esc(p.size || '') + '</span>' +
      '<div class="plugin-acts"><button class="icon-btn" data-pcfg="' + esc(p.name) + '" title="设置"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/></svg></button>' +
      '<button class="icon-btn" data-pdel="' + esc(p.name) + '" title="删除"><svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/></svg></button></div></div></div>';
  }

  async function pluginConfigModal(p) {
    if (!p) return;
    let cfg = { schema: {}, values: {} }, deps = { requirements: [], record: {} };
    try {
      const [a, b] = await Promise.all([api('plugins/' + encodeURIComponent(p.name) + '/config'), api('plugins/' + encodeURIComponent(p.name) + '/deps')]);
      cfg = a; deps = b;
    } catch (e) {}
    const schema = cfg.schema || {};
    const vals = cfg.values || {};
    const fields = Object.keys(schema).map((k) => {
      const s = schema[k] || {};
      const v = vals[k] != null ? vals[k] : s.default;
      const label = esc(s.label || k);
      if (s.type === 'bool' || typeof s.default === 'boolean') {
        return '<div class="between" style="padding:10px 12px;border:1px solid var(--line);border-radius:11px"><span style="font-size:12.7px">' + label + '</span>' +
          '<label class="switch"><input type="checkbox" data-cfg="' + esc(k) + '" ' + (v ? 'checked' : '') + '><i></i></label></div>';
      }
      if (s.type === 'number') {
        return '<div class="field"><label class="label">' + label + '</label><input class="input" type="number" data-cfg="' + esc(k) + '" value="' + esc(v == null ? '' : v) + '"></div>';
      }
      if (Array.isArray(s.options) && s.options.length) {
        return '<div class="field"><label class="label">' + label + '</label><select class="select" data-cfg="' + esc(k) + '">' +
          s.options.map((o) => { const ov = typeof o === 'object' ? o.value : o; const ot = typeof o === 'object' ? (o.label || o.value) : o; return '<option value="' + esc(ov) + '" ' + (String(ov) === String(v) ? 'selected' : '') + '>' + esc(ot) + '</option>'; }).join('') + '</select></div>';
      }
      return '<div class="field"><label class="label">' + label + '</label><input class="input" data-cfg="' + esc(k) + '" value="' + esc(v == null ? '' : v) + '"></div>';
    }).join('');
    const reqs = deps.requirements || [];
    const rec = deps.record || {};
    const depBox = reqs.length ? (
      '<div class="field"><span class="label">插件依赖</span><div class="plat-url">' + reqs.map(esc).join('<br>') + '</div>' +
      '<div class="row" style="margin-top:8px"><button class="btn sm" id="depBtn">重新安装</button>' +
      '<span class="hint">' + (rec.ok ? '上次安装成功' : rec.error ? '上次失败：' + esc(rec.error) : '尚未安装') + '</span></div></div>'
    ) : '<div class="hint">该插件没有声明额外依赖。</div>';

    const card = openModal(
      '<div class="modal-head"><h3>' + esc(p.title || p.name) + '</h3><button class="icon-btn" data-close><svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div>' +
      '<div class="modal-body">' +
      '<div class="between" style="padding:10px 12px;border:1px solid var(--line);border-radius:11px"><span style="font-size:12.7px">仅管理员可用</span>' +
      '<label class="switch"><input type="checkbox" id="pAdminOnly" ' + (p.admin_only ? 'checked' : '') + '><i></i></label></div>' +
      (fields || '<div class="hint">该插件没有可配置项。</div>') + depBox +
      '</div><div class="modal-foot"><button class="btn ghost" data-close>取消</button><button class="btn primary" id="cfgSave">保存</button></div>', true);
    $$('[data-close]', card).forEach((b) => b.onclick = closeModal);
    if ($('#depBtn')) $('#depBtn').onclick = async () => {
      const btn = $('#depBtn'); btn.disabled = true; btn.classList.add('loading');
      try {
        const r = await api('plugins/' + encodeURIComponent(p.name) + '/deps', { method: 'POST', body: { force: true } });
        toast(r.result && r.result.ok ? '依赖安装完成' : ('失败：' + ((r.result && r.result.error) || '未知')), r.result && r.result.ok ? 'ok' : 'err');
      } catch (e) { toast(e.message, 'err'); }
      btn.disabled = false; btn.classList.remove('loading');
    };
    $('#cfgSave').onclick = async () => {
      const values = {};
      $$('[data-cfg]', card).forEach((el) => {
        const k = el.dataset.cfg;
        const s = schema[k] || {};
        if (el.type === 'checkbox') values[k] = el.checked;
        else if (s.type === 'number') values[k] = Number(el.value) || 0;
        else values[k] = el.value;
      });
      try {
        await api('plugins/' + encodeURIComponent(p.name) + '/config', { method: 'POST', body: { values } });
        await api('plugins/' + encodeURIComponent(p.name) + '/admin', { method: 'POST', body: { admin_only: $('#pAdminOnly').checked } });
        closeModal(); toast('已保存', 'ok');
      } catch (e) { toast(e.message, 'err'); }
    };
  }

  /* ---------------- 市场 ---------------- */
  async function pageMarket() {
    $('#view').innerHTML =
      '<div class="page"><div class="card"><div class="card-head"><div><h3>插件市场</h3>' +
      '<div class="sub" id="mkSub">来自官方索引，安装后立即生效</div></div>' +
      '<div class="right"><div class="input-row"><input class="input" id="mkQ" placeholder="搜索插件" style="width:200px"><button class="btn sm primary" id="mkGo">搜索</button></div></div></div>' +
      '<div class="card-body"><div class="mk-grid" id="mkGrid"><div class="empty" style="grid-column:1/-1">正在加载…</div></div></div></div></div>';
    let q = '';
    const load = async () => {
      $('#mkGrid').innerHTML = '<div class="empty" style="grid-column:1/-1">正在加载…</div>';
      try {
        const r = await api('market?q=' + encodeURIComponent(q));
        const items = r.items || [];
        $('#mkSub').textContent = (r.source || '插件索引') + ' · ' + items.length + ' 个结果';
        $('#mkGrid').innerHTML = items.length ? items.map((p) =>
          '<div class="mk"><div class="mk-top"><div class="mk-ic">' + (p.icon ? '<img src="' + esc(p.icon) + '" alt="">' : esc((p.title || p.name).slice(0, 1))) + '</div>' +
          '<div class="mk-name"><b>' + esc(p.title || p.name) + '</b><small>v' + esc(p.version) + ' · ' + esc(p.author) + '</small></div>' +
          (p.installed ? '<span class="badge green">已安装</span>' : '') + '</div>' +
          '<p>' + esc(p.desc || '没有描述') + '</p>' +
          '<div class="mk-tags">' + (p.tags || []).map((t) => '<span>' + esc(t) + '</span>').join('') + '</div>' +
          '<div class="mk-foot"><span class="hint" style="font-size:11px">' + esc(p.id) + '</span>' +
          '<button class="btn sm ' + (p.installed ? '' : 'primary') + '" style="margin-left:auto" data-install="' + esc(p.id) + '" ' + (p.installed ? 'disabled' : '') + '>' + (p.installed ? '已安装' : '安装') + '</button></div></div>').join('')
          : '<div class="empty" style="grid-column:1/-1"><b>没有结果</b>换个关键词试试。</div>';
        $$('[data-install]').forEach((b) => b.onclick = () => install(items.find((x) => x.id === b.dataset.install), b));
      } catch (e) {
        $('#mkGrid').innerHTML = '<div class="empty" style="grid-column:1/-1"><b>加载失败</b>' + esc(e.message) + '</div>';
      }
    };
    const install = async (item, btn) => {
      if (!item) return;
      btn.disabled = true; btn.classList.add('loading');
      try {
        const r = await api('market/install', { method: 'POST', body: { item } });
        toast('已安装 ' + (r.name || ''), 'ok');
        if (r.deps && !r.deps.ok && !r.deps.skipped) toast('依赖安装失败：' + (r.deps.error || ''), 'err');
        load();
      } catch (e) { toast(e.message, 'err'); btn.disabled = false; btn.classList.remove('loading'); }
    };
    $('#mkGo').onclick = () => { q = $('#mkQ').value.trim(); load(); };
    $('#mkQ').onkeydown = (e) => { if (e.key === 'Enter') { q = $('#mkQ').value.trim(); load(); } };
    load();
  }

  /* ---------------- 日志 ---------------- */
  async function pageLogs() {
    $('#view').innerHTML =
      '<div class="page"><div class="card"><div class="card-head"><div><h3>运行日志</h3>' +
      '<div class="sub">实时推送，保留最近 800 条</div></div><div class="right">' +
      '<select class="select" id="logLevel" style="width:auto"><option value="">全部级别</option><option value="info">info</option><option value="ok">ok</option><option value="warn">warn</option><option value="error">error</option></select>' +
      '<input class="input" id="logQ" placeholder="搜索" style="width:150px">' +
      '<button class="btn sm ghost" id="logPause">暂停</button><button class="btn sm ghost" id="logClear">清空视图</button></div></div>' +
      '<div class="card-body"><div class="log-wrap" id="logWrap"></div></div></div></div>';
    let paused = false, level = '', q = '';
    const items = [];
    const paint = () => {
      const shown = items.filter((x) => (!level || x.level === level) && (!q || (x.msg || '').toLowerCase().includes(q)));
      const wrap = $('#logWrap');
      wrap.innerHTML = shown.length ? shown.map((x) =>
        '<div class="log-item ' + (x.level === 'error' ? 'err' : x.level === 'warn' ? 'warn' : '') + '">' +
        '<time>' + esc((x.t || '').split(' ')[1] || x.t || '') + '</time>' +
        '<span class="lv lv-' + esc(x.level) + '">' + esc(x.level) + '</span>' +
        '<span class="msg">' + esc(x.msg) + '</span></div>').join('') : '<div class="empty"><b>暂无日志</b>机器人运行后这里会实时出现内容。</div>';
      wrap.scrollTop = wrap.scrollHeight;
    };
    try {
      const r = await api('logs');
      items.push(...(r.items || []));
      state.logSeq = r.seq || 0;
    } catch (e) {}
    paint();
    $('#logLevel').onchange = (e) => { level = e.target.value; paint(); };
    $('#logQ').oninput = (e) => { q = e.target.value.toLowerCase(); paint(); };
    $('#logPause').onclick = (e) => { paused = !paused; e.target.textContent = paused ? '继续' : '暂停'; };
    $('#logClear').onclick = () => { items.length = 0; paint(); };
    if (sse) sse.close();
    try {
      sse = new EventSource(API('logs/stream') + '?t=' + encodeURIComponent(SESSION));
      sse.onmessage = (e) => {
        if (paused) return;
        try {
          const x = JSON.parse(e.data);
          items.push(x);
          if (items.length > 900) items.splice(0, items.length - 900);
          paint();
        } catch (err) {}
      };
    } catch (e) {}
  }

  /* ---------------- 设置 ---------------- */
  async function pageSettings() {
    const o = state.overview || await api('overview');
    let cfg = state.config || (await api('config')).config;
    state.config = cfg;
    const rt = cfg.runtime || {}, upd = cfg.update || {}, u = o.update || {};
    $('#view').innerHTML =
      '<div class="page"><div class="grid-2" style="align-items:start">' +
      '<div style="display:grid;gap:18px">' +
      '<div class="card"><div class="card-head"><div><h3>访问令牌</h3><div class="sub">控制台唯一凭据，30 位字母与数字</div></div></div>' +
      '<div class="card-body"><div class="kv"><span>当前身份</span><b>' + esc(o.auth_user || 'admin') + '</b></div>' +
      '<div class="kv"><span>令牌</span><b id="tkMask" style="font-family:ui-monospace,Menlo,Consolas,monospace">••••••••••••••••••••</b></div>' +
      '<div class="kv"><span>会话</span><b>24 小时有效</b></div>' +
      '<div class="kv"><span>存放位置</span><b>data/TOKEN.txt</b></div>' +
      '<div class="row" style="gap:8px;margin-top:14px"><button class="btn" id="tkShow" style="flex:1">查看</button>' +
      '<button class="btn" id="tkCopy" style="flex:1">复制</button></div>' +
      '<button class="btn block" id="tkBtn" style="margin-top:8px">重新生成令牌</button></div></div>' +
      '<div class="card"><div class="card-head"><div><h3>运行时</h3><div class="sub">影响内存与插件行为</div></div></div>' +
      '<div class="card-body" style="display:grid;gap:14px">' +
      '<div class="grid-2" style="gap:12px"><div class="field"><label class="label">插件闲置回收（分钟）</label><input class="input" id="rtIdle" type="number" value="' + (rt.plugin_idle_minutes || 10) + '"></div>' +
      '<div class="field"><label class="label">图片保留（天）</label><input class="input" id="rtImg" type="number" value="' + (rt.image_keep_days || 7) + '"></div></div>' +
      '<div class="field"><label class="label">内存软上限（MB）</label><input class="input" id="rtMem" type="number" value="' + (rt.memory_soft_mb || 120) + '"></div>' +
      '<div class="between" style="padding:10px 12px;border:1px solid var(--line);border-radius:11px"><div><b style="font-size:12.8px">插件自动装依赖</b><div class="hint">加载前读取 requirements.txt</div></div>' +
      '<label class="switch"><input type="checkbox" id="rtDeps" ' + (rt.auto_deps !== false ? 'checked' : '') + '><i></i></label></div>' +
      '<div class="field"><label class="label">pip 镜像 <span class="opt">留空用默认</span></label><input class="input" id="rtMirror" value="' + esc(rt.pip_mirror || '') + '" placeholder="https://pypi.tuna.tsinghua.edu.cn/simple"></div>' +
      '<button class="btn primary block" id="rtSave">保存运行时</button></div></div>' +
      '</div>' +
      '<div style="display:grid;gap:18px">' +
      '<div class="card"><div class="card-head"><div><h3>版本更新</h3><div class="sub">从 GitHub Pages 读取版本信息</div></div>' +
      '<div class="right"><button class="btn sm ghost" id="updCheck">检查</button></div></div>' +
      '<div class="card-body"><div class="update-box"><div class="ic"><svg viewBox="0 0 24 24"><path d="M12 3v12m-4-4 4 4 4-4M5 20h14"/></svg></div>' +
      '<div style="flex:1"><b>当前 v' + esc(o.version) + ' · 最新 ' + esc(u.latest || '未知') + '</b>' +
      '<small>' + (u.error ? '检测失败：' + esc(u.error) : u.need ? '有新版本可用' : u.latest ? '已是最新版本' : '尚未检测') +
      (u.checked_at ? ' · ' + timeText(u.checked_at) : '') + '</small></div>' +
      (u.need ? '<button class="btn primary sm" id="updApply">一键更新</button>' : '') + '</div>' +
      (u.notes && u.notes.length ? '<ul class="update-notes">' + u.notes.map((n) => '<li>' + esc(n) + '</li>').join('') + '</ul>' : '') +
      '<div class="field" style="margin-top:14px"><label class="label">更新检查地址</label><input class="input" id="updUrl" value="' + esc(upd.url || '') + '"></div>' +
      '<div class="between" style="margin-top:10px"><span class="hint">自动检查：每 3 小时</span><label class="switch"><input type="checkbox" id="updAuto" ' + (upd.auto_check !== false ? 'checked' : '') + '><i></i></label></div>' +
      '<button class="btn block" id="updSave" style="margin-top:10px">保存更新设置</button></div></div>' +
      '<div class="card danger-zone"><div class="card-head"><div><h3>危险操作</h3><div class="sub">请确认当前没有正在进行的重要任务</div></div></div>' +
      '<div class="card-body" style="display:grid;gap:9px"><button class="btn danger block" id="restartBtn">重启机器人</button>' +
      '<button class="btn ghost block" id="logoutBtn2">退出登录</button></div></div>' +
      '</div></div></div>';

    $('#tkShow').onclick = async () => {
      try {
        const r = await api('auth/token');
        $('#tkMask').textContent = r.token || '-';
        state.token = r.token || '';
      } catch (e) { toast(e.message, 'err'); }
    };
    $('#tkCopy').onclick = async () => {
      let t = state.token;
      if (!t) { try { t = (await api('auth/token')).token; } catch (e) { return toast(e.message, 'err'); } }
      state.token = t;
      $('#tkMask').textContent = t;
      if (navigator.clipboard) navigator.clipboard.writeText(t).then(() => toast('已复制', 'ok')).catch(() => {});
      else toast('请手动选中复制', 'err');
    };
    $('#tkBtn').onclick = () => tokenRotateModal();
    $('#rtSave').onclick = async () => {
      try {
        await api('config', { method: 'POST', body: { runtime: {
          plugin_idle_minutes: Number($('#rtIdle').value) || 10,
          image_keep_days: Number($('#rtImg').value) || 0,
          memory_soft_mb: Number($('#rtMem').value) || 120,
          auto_deps: $('#rtDeps').checked,
          pip_mirror: $('#rtMirror').value.trim()
        } } });
        toast('已保存', 'ok');
      } catch (e) { toast(e.message, 'err'); }
    };
    $('#updCheck').onclick = async (e) => {
      const b = e.target; b.disabled = true; b.classList.add('loading');
      try { const r = await api('update', { method: 'POST', body: { check: true } }); toast(r.update.need ? '发现 v' + r.update.latest : '已是最新', 'ok'); state.config = null; go('settings'); }
      catch (ex) { toast(ex.message, 'err'); b.disabled = false; b.classList.remove('loading'); }
    };
    if ($('#updApply')) $('#updApply').onclick = async () => {
      if (!await confirmBox('一键更新', '将下载 v' + esc(u.latest) + ' 并覆盖程序文件，<b>data/ 与 plugins/ 会保留</b>，完成后自动重启。', '开始更新', false)) return;
      toast('正在下载更新…');
      try {
        await api('update/apply', { method: 'POST' });
        toast('更新完成，正在重启…', 'ok');
        setTimeout(() => location.reload(), 6000);
      } catch (e) { toast(e.message, 'err'); }
    };
    $('#updSave').onclick = async () => {
      try {
        await api('config', { method: 'POST', body: { update: {
          url: $('#updUrl').value.trim(), auto_check: $('#updAuto').checked
        } } });
        toast('已保存', 'ok');
      } catch (e) { toast(e.message, 'err'); }
    };
    $('#restartBtn').onclick = () => doRestart();
    $('#logoutBtn2').onclick = () => logout();
  }

  function tokenRotateModal() {
    const card = openModal(
      '<div class="modal-head"><h3>重新生成令牌</h3><button class="icon-btn" data-close><svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div>' +
      '<div class="modal-body"><p class="muted" style="font-size:13px;line-height:1.9">生成后旧令牌立即失效，已登录的会话也会全部退出，需要拿新令牌重新登录。</p>' +
      '<div class="field" style="margin-top:12px"><label class="label">指定新令牌 <span class="opt">可留空，自动生成</span></label>' +
      '<input class="input" id="tkNew" placeholder="30 位字母与数字" autocomplete="off" spellcheck="false"></div>' +
      '<p class="err" id="tkErr"></p></div>' +
      '<div class="modal-foot"><button class="btn ghost" data-close>取消</button><button class="btn primary" id="tkSave">生成</button></div>');
    $$('[data-close]', card).forEach((b) => b.onclick = closeModal);
    $('#tkSave').onclick = async () => {
      const v = $('#tkNew').value.trim(), err = $('#tkErr');
      if (v && !tokenOk(v)) return err.textContent = '令牌应为 30 位字母与数字';
      try {
        const r = await api('auth/token', { method: 'POST', body: { token: v } });
        SESSION = r.session; localStorage.setItem('yw_session', SESSION);
        state.token = r.token;
        closeModal();
        openModal(
          '<div class="modal-head"><h3>新令牌已生成</h3></div>' +
          '<div class="modal-body"><p class="muted" style="font-size:13px">请立即复制保存，关闭后仍可在 data/TOKEN.txt 查看。</p>' +
          '<pre class="plat-url" style="padding:14px;word-break:break-all;white-space:pre-wrap">' + esc(r.token) + '</pre></div>' +
          '<div class="modal-foot"><button class="btn primary" data-close>我已保存</button></div>');
        $$('[data-close]').forEach((b) => b.onclick = closeModal);
        if (state.page === 'settings') go('settings');
      } catch (e) { err.textContent = e.message; }
    };
  }

  async function doRestart() {
    if (!await confirmBox('重启机器人', '服务会在 2 秒后退出并由守护进程拉起，期间控制台短暂不可用。', '重启', true)) return;
    try {
      await api('restart', { method: 'POST' });
      toast('正在重启…', 'ok');
      setTimeout(() => location.reload(), 5000);
    } catch (e) { toast(e.message, 'err'); }
  }

  /* ---------------- 启动 ---------------- */
  function bindChrome() {
    $('#themeBtn').onclick = toggleTheme;
    $('#collapseBtn').onclick = () => {
      $('#shell').classList.toggle('collapsed');
      localStorage.setItem('yw_collapsed', $('#shell').classList.contains('collapsed') ? '1' : '0');
    };
    $('#menuBtn').onclick = () => {
      $('#sidebar').classList.add('open');
      $('#mask').classList.remove('hidden');
    };
    $('#mask').onclick = () => {
      $('#sidebar').classList.remove('open');
      $('#mask').classList.add('hidden');
    };
    $('#refreshBtn').onclick = async () => {
      await refreshTop();
      toast('已刷新', 'ok');
      go(state.page);
    };
    $('#modal').onclick = (e) => { if (e.target.id === 'modal') closeModal(); };
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeModal();
    });
    if (localStorage.getItem('yw_collapsed') === '1') $('#shell').classList.add('collapsed');
  }

  async function boot() {
    applyTheme(localStorage.getItem('yw_theme') || 'dark');
    bindChrome();
    window.addEventListener('hashchange', () => {
      const p = (location.hash || '#dashboard').slice(1);
      if (p && p !== state.page && !$('#shell').classList.contains('hidden')) go(p);
    });
    try {
      await bootAuth();
    } catch (e) {
      $('#boot').classList.add('hidden');
      toast('初始化失败：' + e.message, 'err');
    } finally {
      setTimeout(() => $('#boot').classList.add('hidden'), 260);
    }
  }

  boot();
})();
