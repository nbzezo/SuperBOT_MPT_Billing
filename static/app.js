// app.js — Frontend logic cho MPT SuperBot

const API = '';

// ===== UTILS =====
function $(id) { return document.getElementById(id); }

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function relativeTime(ts) {
  const diff = Math.max(0, Date.now() - ts);
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s} giây trước`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} phút trước`;
  const h = Math.floor(m / 60);
  return `${h} giờ trước`;
}

// ===== THEME (sáng/tối) =====
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = $('btnTheme');
  if (btn) btn.textContent = theme === 'dark' ? '☀' : '☾';
}
function initTheme() {
  const saved = localStorage.getItem('theme');
  const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  applyTheme(saved || (prefersDark ? 'dark' : 'light'));
  $('btnTheme')?.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('theme', next);
    applyTheme(next);
  });
}

// ===== DIRTY STATE (cảnh báo thay đổi chưa lưu) =====
let isDirty = false;
function markDirty() {
  isDirty = true;
  $('dirtyBadge')?.removeAttribute('hidden');
}
function clearDirty() {
  isDirty = false;
  $('dirtyBadge')?.setAttribute('hidden', '');
}
window.addEventListener('beforeunload', (e) => {
  if (isDirty) { e.preventDefault(); e.returnValue = ''; }
});

// ===== SECRET REVEAL (hiện/ẩn token) =====
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.btn-reveal');
  if (!btn) return;
  const inp = $(btn.dataset.reveal) || btn.previousElementSibling;
  if (!inp) return;
  inp.type = inp.type === 'password' ? 'text' : 'password';
  btn.textContent = inp.type === 'password' ? '👁' : '🙈';
});

function toast(msg, type='info') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  $('toast-container').appendChild(el);
  setTimeout(() => { el.style.animation='slideOut .2s forwards'; setTimeout(()=>el.remove(),200); }, 3000);
}

async function api(path, method='GET', body=null) {
  const opts = { method, headers: {'Content-Type':'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(API+path, opts);
  return r.json();
}

// ===== TABS =====
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    // Nhắc nếu rời tab Config khi có thay đổi chưa lưu
    const leavingConfig = document.querySelector('.tab-btn.active')?.dataset.tab === 'tab-mpt-config';
    if (leavingConfig && btn.dataset.tab !== 'tab-mpt-config' && isDirty) {
      toast('Bạn có thay đổi chưa lưu trong Cấu hình', 'info');
    }
    document.querySelectorAll('.tab-btn').forEach(b=>{ b.classList.remove('active'); b.setAttribute('aria-selected','false'); });
    document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
    btn.classList.add('active');
    btn.setAttribute('aria-selected','true');
    $(btn.dataset.tab)?.classList.add('active');
    if (btn.dataset.tab === 'tab-dashboard') { loadDashboard(); loadDashboardCharts(); }
    if (btn.dataset.tab === 'tab-billing') { bmLoadGroups(); }
    if (btn.dataset.tab === 'tab-meeting') loadMeetingHistory();
    if (btn.dataset.tab === 'tab-log') loadLogs();
  });
});

// ===== CONFIG =====
let config = {};
let currentTargetIdx = 0;

async function loadConfig() {
  config = await api('/api/config');
  renderTargetSelect();
  renderBotList();
  loadGlobalCfg();
  updateSummary();
  $('nospamToken').value = config.nospam?.bot_token || '';
  $('nospamChatId').value = config.nospam?.chat_id || '';
  $('meetingToken').value = config.meeting?.bot_token || '';
  $('meetingGeminiKey').value = config.meeting?.gemini_api_key || '';
  $('meetingPrompt').value = config.meeting?.summary_prompt || '';
  $('meetingModel').value = config.meeting?.model || 'gemini-2.5-flash';
  $('meetingStyle').value = config.meeting?.summary_style || 'structured';
  $('meetingLang').value = config.meeting?.language || 'vi';
  $('meetingTranscript').checked = !!config.meeting?.include_transcript;
  $('meetingAllowed').value = (config.meeting?.allowed_chats || []).join(', ');
  populateTargetSelects();
}

function loadGlobalCfg() {
  const g = config.global || {};
  $('dropThreshold').value = g.drop_threshold ?? 70;
  $('reportHour').value = g.report_hour ?? 20;
  $('workStartHour').value = g.work_start_hour ?? 7;
  $('workEndHour').value = g.work_end_hour ?? 21;
  $('onlyOnChange').checked = g.only_on_change !== false;
}

function renderTargetSelect() {
  const sel = $('targetSelect');
  const targets = config.mpt?.targets || [];
  sel.innerHTML = targets.length
    ? targets.map((t,i)=>`<option value="${i}">${t.label||t.id}</option>`).join('')
    : '<option value="">-- Chưa có target --</option>';
  if (targets.length) loadTargetForm(0);
}

function loadTargetForm(idx) {
  const t = (config.mpt?.targets||[])[idx];
  if (!t) return;
  currentTargetIdx = idx;
  $('targetLabel').value = t.label||'';
  $('alertName').value = t.alertName||'';
  $('targetEnabled').checked = t.enabled!==false;
  $('loginUrl').value = t.login_url||'';
  $('monitoringUrl').value = t.monitoring_url||'';
  $('mptUsername').value = t.username||'';
  $('mptPassword').value = t.password||'';
  $('threshold').value = t.threshold??0;
  $('thresholdViettel').value = t.threshold_viettel??'';
  $('thresholdMobi').value = t.threshold_mobi??'';
  $('thresholdVina').value = t.threshold_vina??'';
  $('intervalValue').value = t.intervalValue??5;
  $('intervalUnit').value = t.intervalUnit||'minute';
}

$('targetSelect').addEventListener('change', e => {
  // Lưu sửa đổi target hiện tại vào RAM trước khi chuyển → tránh mất dữ liệu
  saveCurrentTarget();
  loadTargetForm(+e.target.value);
});

$('btnAddTarget').addEventListener('click', () => {
  if (!config.mpt) config.mpt = {targets:[], bots:[]};
  if (!config.mpt.targets) config.mpt.targets = [];
  saveCurrentTarget();
  const id = 'target_' + Date.now();
  config.mpt.targets.push({id, label:'Target mới', alertName:'NEW', enabled:true,
    login_url:'', monitoring_url:'', username:'', password:'',
    threshold:0, threshold_viettel:null, threshold_mobi:null, threshold_vina:null,
    intervalValue:5, intervalUnit:'minute', intervalMs:300000});
  renderTargetSelect();
  $('targetSelect').value = config.mpt.targets.length - 1;
  loadTargetForm(config.mpt.targets.length - 1);
  markDirty();
});

$('btnDeleteTarget').addEventListener('click', () => {
  const targets = config.mpt?.targets||[];
  if (!targets.length) return;
  const t = targets[currentTargetIdx];
  if (!confirm(`Xóa target "${t?.label || t?.id || ''}"? Không thể hoàn tác.`)) return;
  targets.splice(currentTargetIdx, 1);
  currentTargetIdx = 0;
  renderTargetSelect();
  markDirty();
  toast('Đã xóa target (nhớ Lưu cấu hình)', 'info');
});

// Đánh dấu "chưa lưu" khi sửa bất kỳ field nào trong tab Config
$('tab-mpt-config').addEventListener('input', () => markDirty());

function saveCurrentTarget() {
  const targets = config.mpt?.targets||[];
  if (!targets[currentTargetIdx]) return;
  const t = targets[currentTargetIdx];
  t.label = $('targetLabel').value;
  t.alertName = $('alertName').value;
  t.enabled = $('targetEnabled').checked;
  t.login_url = $('loginUrl').value;
  t.monitoring_url = $('monitoringUrl').value;
  t.username = $('mptUsername').value;
  t.password = $('mptPassword').value;
  t.threshold = +$('threshold').value||0;
  t.threshold_viettel = $('thresholdViettel').value!=='' ? +$('thresholdViettel').value : null;
  t.threshold_mobi = $('thresholdMobi').value!=='' ? +$('thresholdMobi').value : null;
  t.threshold_vina = $('thresholdVina').value!=='' ? +$('thresholdVina').value : null;
  t.intervalValue = +$('intervalValue').value||5;
  t.intervalUnit = $('intervalUnit').value;
  const mult = t.intervalUnit==='hour' ? 3600000 : 60000;
  t.intervalMs = t.intervalValue * mult;
}

$('btnSaveConfig').addEventListener('click', async () => {
  saveCurrentTarget();
  if (!config.global) config.global={};
  config.global.drop_threshold = +$('dropThreshold').value||70;
  config.global.report_hour = +$('reportHour').value||20;
  config.global.work_start_hour = +$('workStartHour').value||7;
  config.global.work_end_hour = +$('workEndHour').value||21;
  config.global.only_on_change = $('onlyOnChange').checked;
  await api('/api/config', 'POST', {config});
  clearDirty();
  toast('Đã lưu cấu hình!', 'success');
  renderTargetSelect();
  populateTargetSelects();
  updateSummary();
});

$('btnPause').addEventListener('click', async () => {
  await api('/api/mpt/pause','POST',{paused:true});
  toast('⏸ Đã tạm dừng quét','info');
  $('scanStatus').textContent='⏸ Paused';
});
$('btnResume').addEventListener('click', async () => {
  await api('/api/mpt/pause','POST',{paused:false});
  toast('▶ Đã tiếp tục quét','success');
  $('scanStatus').textContent='▶ Running';
});

// ===== SUMMARY =====
async function updateSummary() {
  const targets = config.mpt?.targets||[];
  const on = targets.filter(t=>t.enabled).length;
  $('sumTargetsOn').textContent = on;
  try {
    const stats = await api('/api/mpt/stats');
    let total = 0;
    Object.values(stats).forEach(s=>{ total += (s.drops||0)+(s.bads||0); });
    const el = $('sumAlerts');
    el.textContent = total;
    el.className = 'summary-card-value ' + (total>0?'bad':'good');
  } catch(e) {}
  try {
    const st = await api('/api/mpt/status');
    $('sumStatus').textContent = st.paused ? '⏸ Paused' : '▶ Running';
    $('sumStatus').className = 'summary-card-value ' + (st.paused?'warn':'good');
  } catch(e) {}
}

// ===== BOT MPT LIST =====
function renderBotList() {
  const bots = config.mpt?.bots||[];
  const container = $('botList');
  if (!bots.length) { container.innerHTML='<div class="muted">Chưa có bot nào.</div>'; return; }
  container.innerHTML = bots.map((b,i)=>`
    <div class="bot-list-item">
      <div class="bot-list-header">
        <span>${b.label||'Bot '+(i+1)}</span>
        <label class="toggle" style="margin:0">
          <input type="checkbox" class="bot-enabled-chk" data-idx="${i}" ${b.enabled?'checked':''}>
          <span class="slider"></span><span class="toggle-label" style="font-size:9px;">Bật</span>
        </label>
      </div>
      <div class="bot-list-body">
        <div class="row">
          <div class="col">
            <label>Tên Bot</label>
            <input type="text" class="bot-label-inp" data-idx="${i}" value="${b.label||''}" />
          </div>
          <div class="col-2">
            <label>Token</label>
            <div class="secret-wrap">
              <input type="password" class="bot-token-inp" data-idx="${i}" value="${escapeHtml(b.token||'')}" />
              <button type="button" class="btn-reveal" aria-label="Hiện/ẩn token">👁</button>
            </div>
          </div>
          <div class="col">
            <label>Chat ID</label>
            <input type="text" class="bot-chatid-inp" data-idx="${i}" value="${b.chatid||''}" />
          </div>
          <div class="col flex-align-end">
            <button class="danger bot-del-btn control-sm" data-idx="${i}">✕</button>
          </div>
        </div>
      </div>
    </div>`).join('');

  container.querySelectorAll('.bot-del-btn').forEach(btn=>btn.addEventListener('click',()=>{
    const i = +btn.dataset.idx;
    const b = config.mpt.bots[i];
    if (!confirm(`Xóa bot "${b?.label || 'Bot '+(i+1)}"?`)) return;
    config.mpt.bots.splice(i,1);
    renderBotList();
    markDirty();
  }));

  // Ghi sửa đổi vào config (RAM) ngay khi gõ → tránh mất khi chưa "Lưu danh sách Bot"
  const writeBot = (cls, key, prop='value') => container.querySelectorAll(cls).forEach(el=>{
    el.addEventListener('input', ()=>{ config.mpt.bots[+el.dataset.idx][key] = el[prop]; markDirty(); });
  });
  writeBot('.bot-label-inp', 'label');
  writeBot('.bot-token-inp', 'token');
  writeBot('.bot-chatid-inp', 'chatid');
  container.querySelectorAll('.bot-enabled-chk').forEach(el=>{
    el.addEventListener('change', ()=>{ config.mpt.bots[+el.dataset.idx].enabled = el.checked; markDirty(); });
  });
}

$('btnAddBot').addEventListener('click',()=>{
  if(!config.mpt) config.mpt={targets:[],bots:[]};
  if(!config.mpt.bots) config.mpt.bots=[];
  config.mpt.bots.push({label:'Bot mới',token:'',chatid:'',enabled:true});
  renderBotList();
  markDirty();
});

$('btnSaveBots').addEventListener('click', async ()=>{
  const bots = config.mpt?.bots||[];
  document.querySelectorAll('.bot-label-inp').forEach(el=>{ bots[+el.dataset.idx].label=el.value; });
  document.querySelectorAll('.bot-token-inp').forEach(el=>{ bots[+el.dataset.idx].token=el.value; });
  document.querySelectorAll('.bot-chatid-inp').forEach(el=>{ bots[+el.dataset.idx].chatid=el.value; });
  document.querySelectorAll('.bot-enabled-chk').forEach(el=>{ bots[+el.dataset.idx].enabled=el.checked; });
  await api('/api/config','POST',{config});
  clearDirty();
  toast('Đã lưu danh sách Bot MPT','success');
});

// Bot MPT start/stop
$('btnStartMptBot').addEventListener('click', async ()=>{
  const r = await api('/api/mpt/bot','POST',{action:'start'});
  toast(r.status==='started'?'▶ MPT Bot đã khởi động':'Bot đang chạy','success');
  setTimeout(checkBotStatuses, 500);
});
$('btnStopMptBot').addEventListener('click', async ()=>{
  await api('/api/mpt/bot','POST',{action:'stop'});
  toast('⏹ MPT Bot đã dừng','info');
  setTimeout(checkBotStatuses, 500);
});

// ===== DASHBOARD =====
async function loadDashboard() {
  $('dashboardContent').textContent = 'Đang tải...';
  try {
    const snaps = await api('/api/mpt/snapshots');
    const filter = $('routingFilter').value.toLowerCase();
    const selTarget = $('dashTargetSelect').value;
    if (!Object.keys(snaps).length) {
      $('dashboardContent').innerHTML = '<div class="muted">Chưa có dữ liệu snapshot. Chờ chu kỳ quét đầu tiên...</div>';
      return;
    }
    let html = '';
    for (const [tid, snap] of Object.entries(snaps)) {
      if (selTarget && selTarget !== tid) continue;
      const target = (config.mpt?.targets||[]).find(t=>t.id===tid);
      const name = target?.alertName || tid;
      const ts = new Date(snap.ts).toLocaleTimeString('vi-VN');
      const rows = (snap.rows||[]).filter(r=>!filter||r.cluster.toLowerCase().includes(filter));
      html += `<div class="target-card">
        <div class="target-card-header"><span>${name}</span><span class="ts-badge">${ts} · ${relativeTime(snap.ts)}</span></div>
        <table style="width:100%;border-collapse:collapse;font-size:11px;">
          <thead><tr style="background:var(--border);color:var(--bg);">
            <th style="padding:5px 8px;text-align:left;">Routing</th>
            <th style="padding:5px 8px;text-align:center;">Viettel</th>
            <th style="padding:5px 8px;text-align:center;">Mobi</th>
            <th style="padding:5px 8px;text-align:center;">Vina</th>
            <th style="padding:5px 8px;text-align:center;">Other</th>
          </tr></thead><tbody>
          ${rows.map(r=>{
            const bad = r.viettel===0||r.mobi===0||r.vina===0;
            return `<tr style="${bad?'background:rgba(220,50,47,.12);':''}">
              <td style="padding:4px 8px;border-bottom:1px solid var(--bg-alt);">${r.cluster}</td>
              <td style="padding:4px 8px;border-bottom:1px solid var(--bg-alt);text-align:center;${r.viettel===0?'color:var(--red);font-weight:700;':''}">${r.viettel}</td>
              <td style="padding:4px 8px;border-bottom:1px solid var(--bg-alt);text-align:center;${r.mobi===0?'color:var(--red);font-weight:700;':''}">${r.mobi}</td>
              <td style="padding:4px 8px;border-bottom:1px solid var(--bg-alt);text-align:center;${r.vina===0?'color:var(--red);font-weight:700;':''}">${r.vina}</td>
              <td style="padding:4px 8px;border-bottom:1px solid var(--bg-alt);text-align:center;">${r.other}</td>
            </tr>`;
          }).join('')}
        </tbody></table></div>`;
    }
    $('dashboardContent').innerHTML = html || '<div class="muted">Không có dữ liệu phù hợp.</div>';
  } catch(e) {
    $('dashboardContent').innerHTML = `<div class="muted">Lỗi: ${e.message}</div>`;
  }
}

// ===== DASHBOARD CHARTS (SVG) =====
function localISO(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function lastNDates(n) {
  const out = [];
  for (let i = n - 1; i >= 0; i--) { const d = new Date(); d.setDate(d.getDate() - i); out.push(localISO(d)); }
  return out;
}
const ddmm = iso => iso.slice(8, 10) + '/' + iso.slice(5, 7);

function loadDashboardCharts() {
  loadAlertChart();
  loadBillingTrend();
}

async function loadAlertChart() {
  const el = $('chartAlerts');
  try {
    const days = +($('alertRangeSel').value || 14);
    const selTarget = $('dashTargetSelect').value;
    const data = await api('/api/mpt/stats/range?days=' + days);
    const dates = lastNDates(days);
    const labels = dates.map(ddmm);
    const rows = dates.map(iso => {
      const day = data[iso] || {};
      let drops = 0, bads = 0;
      Object.entries(day).forEach(([tid, s]) => { if (selTarget && selTarget !== tid) return; drops += s.drops || 0; bads += s.bads || 0; });
      return [drops, bads];
    });
    const series = [{ name: 'Giảm sâu', color: CX_ALERT.drops }, { name: 'Cạn số', color: CX_ALERT.bads }];
    const total = rows.reduce((s, r) => s + r[0] + r[1], 0);
    el.className = 'chart-body';
    el.innerHTML = total ? (svgStackedBars({ labels, series, rows }) + chartLegend(series))
                         : '<div class="muted small">Không có cảnh báo trong khoảng này ✓</div>';
  } catch (e) { el.innerHTML = '<div class="muted small">Lỗi: ' + escapeHtml(e.message) + '</div>'; }
}

async function loadBillingTrend() {
  const el = $('chartBillingTrend');
  try {
    const days = 15;
    const selTarget = $('dashTargetSelect').value;
    const scope = $('trendScope').value;
    const data = await api('/api/mpt/billing/history?days=' + days);
    const dates = lastNDates(days);
    const labels = dates.map(ddmm);
    const targets = (config.mpt?.targets || []).filter(t => t.enabled && (!selTarget || selTarget === t.id));
    el.className = 'chart-body';

    if (!Object.keys(data).length) {
      el.innerHTML = '<div class="muted small">Chưa có dữ liệu cache billing. Bấm "Backfill 15 ngày" để lấy lịch sử, hoặc đợi job 22:00.</div>';
      return;
    }

    if (scope === 'ps') {
      const series = targets.map((t, idx) => ({
        name: t.alertName || t.id,
        color: CX_PALETTE[idx % CX_PALETTE.length],
        values: dates.map(iso => { const c = data[iso] && data[iso][t.id]; return (c && c.rows) ? c.rows.reduce((s, r) => s + (r.duration || 0), 0) : null; })
      }));
      const hasAny = series.some(s => s.values.some(v => v != null));
      el.innerHTML = hasAny ? (svgComboBars({ labels, series }) + chartLegend(series))
                            : '<div class="muted small">Chưa đủ dữ liệu trong 15 ngày.</div>';
      return;
    }

    // scope === 'account': gom per-account → top-5 theo tổng sản lượng 15 ngày
    const showPS = !selTarget;
    const acc = {};
    targets.forEach(t => {
      dates.forEach((iso, di) => {
        const rows = (data[iso] && data[iso][t.id] && data[iso][t.id].rows) || [];
        rows.forEach(r => {
          const key = t.id + '' + r.name;
          if (!acc[key]) acc[key] = { name: r.name, ps: t.alertName || t.id, values: Array(dates.length).fill(null) };
          acc[key].values[di] = (acc[key].values[di] || 0) + (r.duration || 0);
        });
      });
    });
    const list = Object.values(acc)
      .map(a => ({ ...a, total: a.values.reduce((s, v) => s + (v || 0), 0) }))
      .filter(a => a.total > 0)
      .sort((a, b) => b.total - a.total)
      .slice(0, 5);
    if (!list.length) { el.innerHTML = '<div class="muted small">Chưa đủ dữ liệu account trong 15 ngày.</div>'; return; }

    el.innerHTML = `<div class="cx-mini-grid">${list.map((a, idx) => {
      const last = [...a.values].reverse().find(v => v != null);
      const first = a.values.find(v => v != null);
      const delta = (last != null && first != null) ? last - first : 0;
      const pct = first ? (delta / first * 100) : 0;
      const up = delta >= 0;
      const color = CX_PALETTE[idx % CX_PALETTE.length];
      return `<div class="cx-mini">
        <div class="cx-mini-head"><span title="${escapeHtml(a.name)}">${escapeHtml(cxTrunc(a.name, 16))}</span><span class="cx-mini-val">${cxNum(last ?? 0)}</span></div>
        ${svgMiniCombo({ values: a.values, color })}
        <div class="cx-mini-foot ${up ? 'cx-up' : 'cx-down'}">${up ? '▲' : '▼'} ${Math.abs(pct).toFixed(0)}%${showPS ? ' · ' + escapeHtml(a.ps) : ''}</div>
      </div>`;
    }).join('')}</div>`;
  } catch (e) { el.innerHTML = '<div class="muted small">Lỗi: ' + escapeHtml(e.message) + '</div>'; }
}

let _trendPoll = null;
async function triggerBackfill(days) {
  const st = $('trendStatus');
  st.textContent = 'bắt đầu...';
  await api('/api/mpt/billing/backfill', 'POST', { days });
  if (_trendPoll) clearInterval(_trendPoll);
  _trendPoll = setInterval(async () => {
    try {
      const s = await api('/api/mpt/billing/cache-status');
      if (s.running) { st.textContent = `cache ${s.done}/${s.total}...`; }
      else { st.textContent = ''; clearInterval(_trendPoll); _trendPoll = null; loadBillingTrend(); }
    } catch (e) { st.textContent = ''; clearInterval(_trendPoll); _trendPoll = null; }
  }, 3000);
}

async function loadBillingAccountsChart() {
  const el = $('chartBillingAccounts');
  const day = $('billChartDate').value || localISO(new Date());
  const selTarget = $('dashTargetSelect').value;
  const targets = (config.mpt?.targets || []).filter(t => t.enabled && (!selTarget || selTarget === t.id));
  if (!targets.length) { el.innerHTML = '<div class="muted small">Không có target phù hợp.</div>'; return; }
  $('billChartLoading').style.display = 'inline';
  el.className = 'chart-body'; el.innerHTML = '';
  try {
    const accs = [];
    for (const t of targets) {
      const r = await fetch(`/api/mpt/billing?target_id=${encodeURIComponent(t.id)}&day=${encodeURIComponent(day)}`).then(r => r.json());
      (r.rows || []).forEach(row => accs.push({ label: row.name, value: row.duration || 0 }));
    }
    if (!accs.length) { el.innerHTML = '<div class="muted small">Không có dữ liệu billing ngày ' + escapeHtml(day) + '.</div>'; return; }
    accs.sort((a, b) => b.value - a.value);
    const top = accs.slice(0, 15);
    const segs = accs.slice(0, 6).map((a, i) => ({ label: cxTrunc(a.label, 14), value: a.value, color: CX_PALETTE[i % CX_PALETTE.length] }));
    const other = accs.slice(6).reduce((s, a) => s + a.value, 0);
    if (other > 0) segs.push({ label: 'Khác', value: other, color: 'var(--text-faint)' });
    el.innerHTML = `<div class="cx-split">
      <div><div class="cx-sub">Top ${top.length} account · ${escapeHtml(day)}</div>${svgHBars({ items: top, color: CX_CARRIER.vt })}</div>
      <div><div class="cx-sub">Tỉ trọng sản lượng</div>${svgDonut({ segments: segs })}${chartLegend(segs)}</div>
    </div>`;
  } catch (e) { el.innerHTML = '<div class="muted small">Lỗi: ' + escapeHtml(e.message) + '</div>'; }
  finally { $('billChartLoading').style.display = 'none'; }
}

$('btnRefreshDash').addEventListener('click', () => { loadDashboard(); loadDashboardCharts(); });
$('routingFilter').addEventListener('input', loadDashboard);
$('dashTargetSelect').addEventListener('change', () => { loadDashboard(); loadDashboardCharts(); });
$('alertRangeSel').addEventListener('change', loadAlertChart);
$('btnLoadBillChart').addEventListener('click', loadBillingAccountsChart);
$('billChartDate').value = localISO(new Date());
$('trendScope').addEventListener('change', loadBillingTrend);
$('btnBackfill').addEventListener('click', () => triggerBackfill(15));
$('btnCacheToday').addEventListener('click', () => triggerBackfill(1));

// ===== POPULATE TARGET SELECTS =====
function populateTargetSelects() {
  const targets = config.mpt?.targets||[];
  ['dashTargetSelect','billingTargetSelect','findTargetSelect'].forEach(id=>{
    const sel = $(id);
    if (!sel) return;
    const first = sel.options[0].outerHTML;
    sel.innerHTML = first + targets.map(t=>`<option value="${t.id}">${t.label||t.id}</option>`).join('');
  });
}

// ===== NOSPAM (config only - bot control) =====
$('btnSaveNospam').addEventListener('click', async ()=>{
  if(!config.nospam) config.nospam={};
  config.nospam.bot_token = $('nospamToken').value;
  config.nospam.chat_id   = $('nospamChatId').value;
  await api('/api/config','POST',{config});
  toast('Đã lưu cấu hình Nospam','success');
});

$('btnTestNospamBot').addEventListener('click', async ()=>{
  const el = $('nospamTestStatus');
  el.textContent = '...';
  const r = await api('/api/telegram/test','POST',{token: $('nospamToken').value});
  el.textContent = r.ok ? '✓ '+r.msg : '✗ '+r.msg;
  el.className = 'test-status ' + (r.ok?'test-ok':'test-err');
});

$('btnStartNospamBot').addEventListener('click', async ()=>{
  const r = await api('/api/nospam/bot','POST',{action:'start'});
  toast(r.status==='started'?'▶ Nospam Bot đã khởi động':'Bot đang chạy','success');
  setTimeout(checkBotStatuses, 500);
});
$('btnStopNospamBot').addEventListener('click', async ()=>{
  await api('/api/nospam/bot','POST',{action:'stop'});
  toast('⏹ Nospam Bot đã dừng','info');
  setTimeout(checkBotStatuses, 500);
});

// ===== TRA CỨU HỢP NHẤT: MPT Portal + Nospam =====
$('btnFindCall').addEventListener('click', async ()=>{
  const phone = $('findPhone').value.trim();
  if (!phone) { toast('Nhập số điện thoại','info'); return; }

  $('nospamQueryStatus').textContent = '⏳ Đang tra cứu cả hai nguồn...';
  $('findResults').innerHTML = '<div class="muted">⏳ Đang tìm trên portal MPT...</div>';
  $('nospamResult').innerHTML = '<div class="muted">⏳ Đang kiểm tra spam...</div>';

  // Chạy song song 2 truy vấn
  const [nospamResult, mptResult] = await Promise.allSettled([
    api('/api/nospam/query','POST',{phone}),
    api('/api/mpt/find','POST',{phone, target_id: $('findTargetSelect').value})
  ]);

  // Hiển thị kết quả Nospam
  const nr = nospamResult.status==='fulfilled' ? nospamResult.value : null;
  if (!nr || nr.error) {
    $('nospamResult').innerHTML = `<div style="color:var(--red);font-weight:700;">❌ Lỗi: ${nr?.error||'Không thể kết nối'}</div>`;
  } else {
    $('nospamResult').textContent = nr.result || '(Không có kết quả từ nospam.vncert.vn)';
  }

  // Hiển thị kết quả MPT Portal
  const mr = mptResult.status==='fulfilled' ? mptResult.value : null;
  if (!mr || mr.error) {
    $('findResults').innerHTML = `<div style="color:var(--red);font-weight:700;">❌ Lỗi MPT: ${mr?.error||'Lỗi kết nối'}</div>`;
  } else {
    const data = mr.data || [];
    if (data.length === 0) {
      $('findResults').innerHTML = `<div class="muted">Không có hệ thống nào đang bật để tra cứu.</div>`;
    } else {
      let html = '';
      data.forEach(d => {
        html += `<div class="call-block">`;
        if (d.result.error) {
          html += `<div class="call-head">${escapeHtml(d.target)}</div>`;
          html += `<div class="call-empty" style="color:var(--red);">Lỗi: ${escapeHtml(d.result.error)}</div>`;
        } else if (!d.result.rows || d.result.rows.length === 0) {
          html += `<div class="call-head">${escapeHtml(d.target)}</div>`;
          html += `<div class="call-empty">Không có cuộc gọi.</div>`;
        } else {
          const rows = d.result.rows;
          html += `<div class="call-head">${escapeHtml(d.target)} <span class="muted">· ${rows.length} cuộc gọi</span></div>`;
          html += `<table class="call-table"><thead><tr>`+
                  `<th>Account</th><th>Thời gian</th><th class="c-dur">Giây</th><th>Kết quả</th>`+
                  `</tr></thead><tbody>`;
          rows.slice(0, 10).forEach(r => {
            const ok = /NORMAL|ANSWER/i.test(r.cause || '');
            html += `<tr>`+
              `<td class="c-acc">${escapeHtml(r.account)}</td>`+
              `<td class="c-time">${escapeHtml(r.startTime)}</td>`+
              `<td class="c-dur">${escapeHtml(r.billsec)}</td>`+
              `<td class="c-cause ${ok ? '' : 'call-cause-bad'}">${escapeHtml(r.cause)}</td>`+
              `</tr>`;
          });
          html += `</tbody></table>`;
          if (rows.length > 10) {
            html += `<div class="call-more">…và ${rows.length - 10} cuộc gọi khác</div>`;
          }
        }
        html += `</div>`;
      });
      $('findResults').innerHTML = html;
    }
  }

  $('nospamQueryStatus').textContent = '';
});

// Enter key trigger
$('findPhone').addEventListener('keydown', e => {
  if (e.key === 'Enter') $('btnFindCall').click();
});

// ===== MEETING =====
$('btnSaveMeeting').addEventListener('click', async ()=>{
  if(!config.meeting) config.meeting={};
  config.meeting.bot_token     = $('meetingToken').value;
  config.meeting.gemini_api_key = $('meetingGeminiKey').value;
  await api('/api/config','POST',{config});
  toast('Đã lưu cấu hình Meeting','success');
});

$('btnTestGemini').addEventListener('click', async ()=>{
  const el = $('geminiTestStatus');
  el.textContent = '...';
  const r = await api('/api/meeting/test-gemini','POST',{api_key: $('meetingGeminiKey').value});
  el.textContent = r.ok ? '✓ '+r.msg : '✗ '+r.msg;
  el.className = 'test-status ' + (r.ok?'test-ok':'test-err');
});

$('btnSaveMeetingPrompt').addEventListener('click', async ()=>{
  await api('/api/meeting/prompt','POST',{prompt: $('meetingPrompt').value});
  toast('Đã lưu Prompt AI','success');
});

$('btnSaveMeetingOpts').addEventListener('click', async ()=>{
  if(!config.meeting) config.meeting={};
  config.meeting.model = $('meetingModel').value;
  config.meeting.summary_style = $('meetingStyle').value;
  config.meeting.language = $('meetingLang').value;
  config.meeting.include_transcript = $('meetingTranscript').checked;
  config.meeting.allowed_chats = $('meetingAllowed').value.split(',').map(s=>s.trim()).filter(Boolean);
  await api('/api/config','POST',{config});
  toast('Đã lưu tuỳ chọn tóm tắt','success');
});

$('btnMeetingUpload').addEventListener('click', async ()=>{
  const inp = $('meetingUploadFile');
  const file = inp.files[0];
  if(!file){ toast('Chọn file audio/video trước','info'); return; }
  const st = $('meetingUploadStatus'), out = $('meetingUploadResult');
  st.textContent = `Đang tải lên & tóm tắt (${(file.size/1024/1024).toFixed(1)}MB)... có thể vài phút`;
  out.textContent = '';
  try {
    const fd = new FormData(); fd.append('file', file);
    const r = await fetch('/api/meeting/upload', {method:'POST', body:fd}).then(r=>r.json());
    if(!r.ok){ st.textContent=''; out.textContent = '❌ Lỗi: ' + (r.error||'không rõ'); return; }
    st.textContent = '✓ Xong: ' + r.filename;
    out.textContent = r.content || '';
    loadMeetingHistory();
  } catch(e){ st.textContent=''; out.textContent = '❌ Lỗi: ' + e.message; }
});

$('btnStartMeetingBot').addEventListener('click', async ()=>{
  const r = await api('/api/meeting/bot','POST',{action:'start'});
  toast(r.status==='started'?'▶ Meeting Bot đã khởi động':'Bot đang chạy','success');
  setTimeout(checkBotStatuses, 500);
});
$('btnStopMeetingBot').addEventListener('click', async ()=>{
  await api('/api/meeting/bot','POST',{action:'stop'});
  toast('⏹ Meeting Bot đã dừng','info');
  setTimeout(checkBotStatuses, 500);
});

async function loadMeetingHistory() {
  const q = ($('meetingSearch')?.value || '').trim();
  const r = await api('/api/meeting/history' + (q ? ('?q=' + encodeURIComponent(q)) : ''));
  const files = r.files||[];
  const el = $('meetingHistory');
  if (!files.length) { el.innerHTML='<div class="muted">Không có file phù hợp.</div>'; return; }
  const linkStyle = 'font-size:11px;font-weight:700;text-decoration:none;border:2px solid var(--border);padding:2px 8px;cursor:pointer;';
  el.innerHTML = files.map(f=>{
    const enc = encodeURIComponent(f);
    const isNote = f.startsWith('Meeting_Note_');
    return `
    <div class="meeting-file-item">
      <span class="meeting-file-name">📄 ${f}</span>
      <span class="hstack-xs">
        <a href="/api/meeting/download/${enc}" download="${f}" class="ghost" style="${linkStyle}color:var(--blue);">⬇ Tải</a>
        <a href="/api/meeting/export/${enc}" target="_blank" class="ghost" style="${linkStyle}color:var(--blue);">🖨 PDF</a>
        ${isNote ? `<button class="ghost btn-resummarize" data-file="${f}" style="${linkStyle}color:#7c3aed;">↻ Tóm tắt lại</button>` : ''}
        <button class="danger btn-delnote" data-file="${f}" style="${linkStyle}">🗑 Xoá</button>
      </span>
    </div>`;
  }).join('');

  el.querySelectorAll('.btn-delnote').forEach(b=>b.addEventListener('click', async ()=>{
    const f = b.dataset.file;
    if(!confirm('Xoá note "'+f+'"?')) return;
    await api('/api/meeting/note/'+encodeURIComponent(f),'DELETE');
    toast('Đã xoá '+f,'info');
    loadMeetingHistory();
  }));
  el.querySelectorAll('.btn-resummarize').forEach(b=>b.addEventListener('click', async ()=>{
    const f = b.dataset.file;
    b.textContent = '⏳...'; b.disabled = true;
    const r = await api('/api/meeting/resummarize','POST',{
      filename: f, style: $('meetingStyle').value,
      model: $('meetingModel').value, language: $('meetingLang').value });
    b.textContent = '↻ Tóm tắt lại'; b.disabled = false;
    if(!r.ok){ toast('Lỗi: '+(r.error||'không rõ'),'error'); return; }
    toast('Đã tạo: '+r.filename,'success');
    loadMeetingHistory();
  }));
}

$('btnRefreshMeetingHistory').addEventListener('click', loadMeetingHistory);
$('meetingSearch')?.addEventListener('keydown', e=>{ if(e.key==='Enter') loadMeetingHistory(); });
$('meetingSearch')?.addEventListener('input', ()=>{ clearTimeout(window._mtSearchT); window._mtSearchT = setTimeout(loadMeetingHistory, 350); });

// ===== LOG (Websocket + REST) =====
function logClass(l) {
  const c = `${l.code||''} ${l.level||''}`.toUpperCase();
  if (/ERR|FAIL|ERROR/.test(c)) return 'log-err';
  if (/WARN|SKIP|COOLDOWN|PAUSE/.test(c)) return 'log-warn';
  return '';
}

function renderLogLines(logs, el) {
  if (!logs.length) { el.innerHTML = '<span class="log-line log-muted">(Chưa có log)</span>'; return; }
  el.innerHTML = logs.map(l=>{
    const ts = new Date(l.ts).toLocaleTimeString('vi-VN');
    const code = l.code ? `[${l.code}]` : '';
    const tid  = l.target_id ? `[${l.target_id}]` : '';
    const line = escapeHtml(`[${ts}]${code}${tid} ${l.msg}`);
    return `<span class="log-line ${logClass(l)}">${line}</span>`;
  }).join('');
  if ($('logAutoScroll').checked) el.scrollTop = el.scrollHeight;
}

async function loadLogs() {
  const r = await api('/api/logs?limit=200');
  renderLogLines(r.logs||[], $('log'));
}

$('btnClearLog').addEventListener('click', async ()=>{
  if (!confirm('Xóa toàn bộ log?')) return;
  await api('/api/logs','DELETE');
  $('log').innerHTML = '<span class="log-line log-muted">(Log đã được xóa)</span>';
  toast('Đã xóa log','info');
});

// WebSocket live streaming
let logWs = null;
function connectLogWs() {
  if (logWs) return;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  logWs = new WebSocket(`${protocol}//${window.location.host}/ws/logs`);
  logWs.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.logs && document.querySelector('.tab-btn[data-tab="tab-log"]').classList.contains('active')) {
        renderLogLines(data.logs, $('log'));
      }
    } catch (e) {}
  };
  logWs.onclose = () => {
    logWs = null;
    setTimeout(connectLogWs, 5000); // Reconnect after 5s
  };
}


// ===== BOT STATUS POLLING =====
function updateBotStatusUI(name, running) {
  const map = {nospam:'nospamBotStatus', meeting:'meetingBotStatus', mpt:'mptBotStatus'};
  const el = $(map[name]);
  if (!el) return;
  el.className = 'bot-status ' + (running?'running':'stopped');
  el.innerHTML = `<span class="dot"></span> ${running?'● RUNNING':'⬛ Stopped'}`;
}

async function checkBotStatuses() {
  try {
    const [ns, mt, mpt] = await Promise.all([
      api('/api/nospam/bot/status'),
      api('/api/meeting/bot/status'),
      api('/api/mpt/bot/status')
    ]);
    updateBotStatusUI('nospam',  ns.running);
    updateBotStatusUI('meeting', mt.running);
    updateBotStatusUI('mpt',     mpt.running);
    const anyRunning = ns.running||mt.running||mpt.running;
    $('globalDot').className = 'dot ' + (anyRunning?'dot-running':'dot-idle');
    $('globalStatusText').textContent = anyRunning?'Đang hoạt động':'Chờ';
  } catch(e) {}
}

// ===== BILLING =====
$('billingDateMode').addEventListener('change', e=>{
  $('billingCustomDate').style.display = e.target.value==='custom'?'':'none';
});

$('btnGetBilling').addEventListener('click', async ()=>{
  const mode = $('billingDateMode').value;
  let day;
  if (mode==='today') { const d=new Date(); day=d.toISOString().slice(0,10); }
  else if (mode==='yesterday') { const d=new Date(); d.setDate(d.getDate()-1); day=d.toISOString().slice(0,10); }
  else day = $('billingCustomDate').value;
  if (!day) { toast('Chọn ngày','info'); return; }

  $('billingLoading').style.display='';

  const targets = (config.mpt?.targets||[]).filter(t=>t.enabled);
  const selTarget = $('billingTargetSelect').value;
  const toFetch = selTarget==='all' ? targets : targets.filter(t=>t.id===selTarget);

  const container = $('billing-result');
  if (!toFetch.length) {
    container.innerHTML = '<div class="billing-empty">Không có target nào đang bật.</div>';
    $('billingLoading').style.display='none';
    return;
  }
  // Render tăng dần: từng target hiện ngay khi có kết quả
  container.innerHTML = '';
  for (const t of toFetch) {
    const block = document.createElement('div');
    block.className = 'billing-target-block';
    const label = escapeHtml(t.alertName||t.label||t.id);
    block.innerHTML = `<div class="billing-target-header"><span>${label} — ${day}</span><span><span class="spinner"></span></span></div>`;
    container.appendChild(block);
    try {
      const r = await fetch(`/api/mpt/billing?target_id=${encodeURIComponent(t.id)}&day=${encodeURIComponent(day)}`).then(r=>r.json());
      if (r.error) {
        block.innerHTML = `<div class="billing-target-header">${label}</div><div class="billing-empty">❌ ${escapeHtml(r.error)}</div>`;
        continue;
      }
      const rows = r.rows||[];
      const total = rows.reduce((s,r)=>s+r.duration,0);
      block.innerHTML = `
        <div class="billing-target-header"><span>${label} — ${day}</span><span>${total.toFixed(2)} phút</span></div>
        <table class="billing-table">
          <thead><tr><th>Account</th><th style="text-align:right;">Duration (phút)</th></tr></thead>
          <tbody>${rows.map(r=>`<tr><td>${escapeHtml(r.name)}</td><td style="text-align:right;font-weight:700;color:var(--blue);">${r.duration.toFixed(2)}</td></tr>`).join('')}
          <tr class="total-row"><td>TỔNG</td><td style="text-align:right;">${total.toFixed(2)}</td></tr>
          </tbody></table>`;
    } catch(e) { block.innerHTML = `<div class="billing-empty">Lỗi: ${escapeHtml(e.message)}</div>`; }
  }
  $('billingLoading').style.display='none';
});

// ===== INIT =====
function dashboardActive() {
  return document.querySelector('.tab-btn[data-tab="tab-dashboard"]')?.classList.contains('active');
}

(async () => {
  initTheme();
  await loadConfig();
  await loadDashboard();            // tab mặc định là Dashboard → có dữ liệu ngay
  loadDashboardCharts();
  await checkBotStatuses();
  connectLogWs();
  setInterval(checkBotStatuses, 5000);
  setInterval(updateSummary, 30000);
  setInterval(() => { if (dashboardActive()) loadDashboard(); }, 15000);  // auto-refresh dashboard
})();

// ===== BILLING MANAGER (BM) =====

let _bmClusters = {}; // {target_id: [cluster_name...]}

async function bmLoadGroups() {
  const container = document.getElementById('bm-group-list');
  if (!container) return;
  try {
    const res = await fetch('/api/billing/groups');
    const data = await res.json();
    const groups = data.groups || [];
    if (!groups.length) {
      container.innerHTML = '<div class="muted" style="text-align:center;padding:20px;">Chưa có nhóm nào. Bấm "+ Tạo nhóm mới" để bắt đầu.</div>';
      return;
    }
    container.innerHTML = groups.map(g => {
      const bal = g.balance || 0;
      const balColor = bal < 0 ? 'color:#ef4444' : (g.warn_low > 0 && bal < g.warn_low ? 'color:#f59e0b' : 'color:#22c55e');
      const balIcon = bal < 0 ? '🔴' : (g.warn_low > 0 && bal < g.warn_low ? '⚠️' : '✅');
      return `
      <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;">
        <div class="hstack" style="justify-content:space-between;flex-wrap:wrap;gap:6px;">
          <div>
            <strong style="font-size:0.95rem;">${g.name}</strong>
            <span class="muted" style="font-size:0.8rem;margin-left:8px;">${g.target_id.toUpperCase()}</span>
            ${!g.enabled ? '<span style="font-size:0.75rem;background:#374151;padding:2px 6px;border-radius:4px;margin-left:6px;">Tắt</span>' : ''}
          </div>
          <div class="hstack" style="gap:6px;">
            <button class="control-sm" onclick="bmOpenEdit(${g.id})" title="Sửa">✏️</button>
            <button class="control-sm" onclick="bmOpenTopup(${g.id},'${g.name}')" title="Nạp tiền">💳</button>
            <button class="control-sm" style="color:#ef4444;" onclick="bmDeleteGroup(${g.id},'${g.name}')" title="Xóa">🗑</button>
          </div>
        </div>
        <div style="margin-top:8px;display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:6px;font-size:0.82rem;">
          <div>Giá: <strong>${(g.price_per_min||0).toLocaleString('vi-VN')} đ/phút</strong></div>
          <div>Block: <strong>${g.block_factor}</strong></div>
          <div>Số dư: <strong style="${balColor}">${balIcon} ${(bal).toLocaleString('vi-VN')} đ</strong></div>
          <div>Cảnh báo thấp: <strong>${(g.warn_low||0).toLocaleString('vi-VN')} đ</strong></div>
        </div>
        <div style="margin-top:6px;font-size:0.8rem;color:var(--text-muted);">
          Clusters: <span style="color:var(--text);">${(g.clusters||[]).join(', ') || '(chưa có)'}</span>
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    container.innerHTML = `<div class="muted">Lỗi: ${e.message}</div>`;
  }
}

async function bmLoadClusters() {
  const tid = document.getElementById('bmTarget').value;
  const list = document.getElementById('bmClusterList');
  list.innerHTML = '<span class="muted">Đang tải...</span>';
  if (!_bmClusters[tid]) {
    const res = await fetch(`/api/billing/clusters/available?target_id=${tid}`);
    const data = await res.json();
    _bmClusters = { ..._bmClusters, ...data.clusters };
  }
  const clusters = _bmClusters[tid] || [];
  if (!clusters.length) { list.innerHTML = '<span class="muted">Không có cluster nào.</span>'; return; }
  list.innerHTML = clusters.map(c =>
    `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:0.85rem;font-weight:bold;color:var(--text);">
      <input type="checkbox" class="bm-cluster-cb" value="${c}" style="transform:scale(1.2);"> ${c}
    </label>`
  ).join('');
}

function bmOpenCreate() {
  document.getElementById('bmGroupId').value = '';
  document.getElementById('bmModalTitle').textContent = 'Tạo nhóm mới';
  document.getElementById('bmName').value = '';
  document.getElementById('bmTarget').value = 'ps3';
  document.getElementById('bmPrice').value = '';
  document.getElementById('bmBlockFactor').value = '1.02';
  document.getElementById('bmWarnLow').value = '';
  document.getElementById('bmWarnNeg').value = '';
  document.getElementById('bmNotes').value = '';
  bmLoadClusters();
  const modal = document.getElementById('bmModal');
  modal.style.display = 'flex';
}

async function bmOpenEdit(id) {
  const res = await fetch('/api/billing/groups');
  const data = await res.json();
  const g = (data.groups || []).find(x => x.id === id);
  if (!g) return;
  document.getElementById('bmGroupId').value = g.id;
  document.getElementById('bmModalTitle').textContent = 'Sửa nhóm: ' + g.name;
  document.getElementById('bmName').value = g.name;
  document.getElementById('bmTarget').value = g.target_id;
  document.getElementById('bmPrice').value = g.price_per_min;
  document.getElementById('bmBlockFactor').value = g.block_factor;
  document.getElementById('bmWarnLow').value = g.warn_low || '';
  document.getElementById('bmWarnNeg').value = g.warn_neg || '';
  document.getElementById('bmNotes').value = g.notes || '';
  await bmLoadClusters();
  // Tick các cluster đã chọn
  const selected = g.clusters || [];
  document.querySelectorAll('.bm-cluster-cb').forEach(cb => {
    cb.checked = selected.includes(cb.value);
  });
  document.getElementById('bmModal').style.display = 'flex';
}

function bmCloseModal() { document.getElementById('bmModal').style.display = 'none'; }

async function bmSaveGroup() {
  const id = document.getElementById('bmGroupId').value;
  const clusters = [...document.querySelectorAll('.bm-cluster-cb:checked')].map(cb => cb.value);
  const body = {
    name: document.getElementById('bmName').value.trim(),
    target_id: document.getElementById('bmTarget').value,
    price_per_min: parseFloat(document.getElementById('bmPrice').value) || 0,
    block_factor: parseFloat(document.getElementById('bmBlockFactor').value) || 1.02,
    warn_low: parseFloat(document.getElementById('bmWarnLow').value) || 0,
    warn_neg: parseFloat(document.getElementById('bmWarnNeg').value) || 0,
    notes: document.getElementById('bmNotes').value,
    clusters
  };
  if (!body.name || !body.price_per_min) { alert('Vui lòng điền tên nhóm và giá/phút'); return; }
  const url = id ? `/api/billing/groups/${id}` : '/api/billing/groups';
  const method = id ? 'PUT' : 'POST';
  const res = await fetch(url, { method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
  const data = await res.json();
  if (data.ok) { bmCloseModal(); bmLoadGroups(); }
  else alert('Lỗi: ' + JSON.stringify(data));
}

async function bmDeleteGroup(id, name) {
  if (!confirm(`Xóa nhóm "${name}"? Không thể hoàn tác!`)) return;
  const res = await fetch(`/api/billing/groups/${id}`, { method: 'DELETE' });
  const data = await res.json();
  if (data.ok) bmLoadGroups();
}

function bmOpenTopup(id, name) {
  document.getElementById('bmTopupGroupId').value = id;
  document.getElementById('bmTopupTitle').textContent = 'Nạp tiền cho: ' + name;
  document.getElementById('bmTopupAmount').value = '';
  document.getElementById('bmTopupNote').value = '';
  document.getElementById('bmTopupModal').style.display = 'flex';
}

function bmCloseTopup() { document.getElementById('bmTopupModal').style.display = 'none'; }

async function bmDoTopup() {
  const id = document.getElementById('bmTopupGroupId').value;
  const amount = parseFloat(document.getElementById('bmTopupAmount').value);
  const note = document.getElementById('bmTopupNote').value;
  if (!amount || amount <= 0) { alert('Số tiền phải > 0'); return; }
  const res = await fetch(`/api/billing/groups/${id}/topup`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ amount, note })
  });
  const data = await res.json();
  if (data.ok) {
    alert(`Nạp thành công! Số dư mới: ${data.new_balance.toLocaleString('vi-VN')} đ`);
    bmCloseTopup(); bmLoadGroups();
  } else alert('Lỗi: ' + JSON.stringify(data));
}

// Remove DOMContentLoaded listener to fix firing issue
