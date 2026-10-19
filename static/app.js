// Inside the Match - client. All text from the server is inserted with textContent (never as HTML).
const $ = (s, el = document) => el.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

let ws, meta = null, teams = {}, homeId, awayId, playing = false, matchEnd = 5800;
const panes = [];
const timeline = new Map();
const ICON = { goal: '⚽', big_chance: '🎯', card: '🟨', substitution: '🔁', momentum: '⚡', rhythm: '🌀',
  pressure_spell: '🔥', possession_streak: '🔗', sprint_record: '🏃', shot_speed_record: '💥', distance_milestone: '📏' };
const BADGE = { llm: '✦ AI · grounded', template: '● LIVE', fallback: '● AUTO' };
const STATS = [['possession_pct', 'Possession %'], ['shots', 'Shots'], ['shots_on_target', 'On target'], ['xg', 'xG'],
  ['big_chances', 'Big chances'], ['passes', 'Passes'], ['pass_accuracy_pct', 'Pass acc. %'],
  ['avg_pass_difficulty', 'Pass difficulty'], ['final_third_passes', 'Final-third passes'], ['tackles_won', 'Tackles won'],
  ['interceptions', 'Interceptions'], ['pressures', 'Pressures'], ['fouls', 'Fouls'], ['corners', 'Corners'],
  ['fastest_shot_kmh', 'Fastest shot km/h'], ['avg_ball_speed_kmh', 'Ball speed km/h']];

function send(m) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); }
function teamColor(id) { return id === homeId ? 'var(--home)' : id === awayId ? 'var(--away)' : 'var(--accent)'; }
function minuteOf(clock) { return parseInt(clock, 10) || 0; }

// ---------- panes & profiles ----------
function addPane(profile) {
  const node = $('#paneTpl').content.firstElementChild.cloneNode(true);
  const pane = { node, profile: { ...profile }, pid: null, shown: new Map() };
  const sel = (c) => $('.' + c, node);
  const opt = (s, v, t) => s.append(new Option(t, v));

  opt(sel('preset'), '', 'Custom viewer');
  meta.presets.forEach((p, i) => opt(sel('preset'), i, p.label));
  Object.entries(meta.options.languages).forEach(([k, v]) => opt(sel('language'), k, v));
  opt(sel('club'), '', 'No favourite club');
  [homeId, awayId].forEach((t) => opt(sel('club'), t, '♥ ' + teams[t].name));
  opt(sel('player'), '', 'No player focus');
  [homeId, awayId].forEach((t) => teams[t].players.forEach((p) => opt(sel('player'), p.player_id, `★ ${p.name} (${teams[t].short_name})`)));
  opt(sel('metric'), '', 'No metric');
  Object.entries(meta.options.team_metrics).forEach(([k, v]) => opt(sel('metric'), k, 'Metric: ' + v));
  Object.entries(meta.options.player_metrics).forEach(([k, v]) => opt(sel('metric'), k, 'Player: ' + v));

  const fill = () => ['mode', 'language', 'club', 'player', 'metric'].forEach((k) => { sel(k).value = pane.profile[k] || ''; });
  fill();
  sel('preset').value = meta.presets.findIndex((p) => JSON.stringify(p.profile) === JSON.stringify(profile));
  sel('preset').onchange = (e) => { if (e.target.value !== '') { pane.profile = { ...meta.presets[e.target.value].profile }; fill(); pushProfiles(); } };
  ['mode', 'language', 'club', 'player', 'metric'].forEach((k) => sel(k).onchange = (e) => {
    pane.profile[k] = e.target.value || null; sel('preset').value = ''; pushProfiles();
  });
  $('#panes').append(node);
  panes.push(pane);
}

function pushProfiles() {
  panes.forEach((p) => clearOverlays(p));
  send({ cmd: 'set_profiles', profiles: panes.map((p) => p.profile) });
}

function setCompare(on) {
  $('#compareBtn').classList.toggle('on', on);
  $('#panes').classList.toggle('compare', on);
  if (on && panes.length === 1) addPane(meta.presets[1].profile);
  if (!on && panes.length === 2) { panes.pop().node.remove(); }
  pushProfiles();
}

// ---------- overlays ----------
function clearOverlays(pane) {
  pane.shown.forEach((v) => clearTimeout(v.timer));
  pane.shown.clear();
  pane.node.querySelectorAll('.layer').forEach((l) => l.replaceChildren());
}

function buildOverlay(o, slot) {
  const card = el('div', 'ov ' + slot);
  card.style.setProperty('--team', teamColor(o.team));
  if (o.language === 'ar') card.dir = 'rtl';
  if (o.highlight && o.highlight.your_club) card.classList.add('club');
  const h = el('div', 'h');
  const star = o.highlight && o.highlight.your_player ? '★ ' : '';
  h.append(el('span', null, `${star}${ICON[o.alert_type] || ''} ${o.headline}`));
  const b = el('span', 'badge ' + o.source, BADGE[o.source] || o.source);
  b.title = o.source === 'llm' ? 'Explained by AI agents; every number verified against match data'
    : o.source === 'fallback' ? `Template fallback (${o.fallback_reason || 'n/a'})` : 'Instant template; AI explanation on its way';
  h.append(b);
  card.append(h);
  if (o.text && o.text !== o.headline) card.append(el('div', 't', o.text));
  if (o.why && o.why !== o.text) card.append(el('div', 'w', o.why));
  if (o.evidence && o.evidence.length) {
    const ev = el('div', 'ev');
    o.evidence.forEach((e) => { const s = el('span', null, e.label + ' '); s.append(el('b', null, String(e.value))); ev.append(s); });
    card.append(ev);
  }
  if (o.metric) card.append(el('div', 'm', `${o.metric.label}: ` + Object.entries(o.metric.values).map(([k, v]) => `${k} ${v}`).join(' · ')));
  return card;
}

function showOverlay(pane, o) {
  if (o.kind === 'player_tracker' || o.kind === 'metric_ticker') {
    $('.ticker-label', pane.node).textContent = o.headline;
    $('.ticker-text', pane.node).textContent = o.text;
    return;
  }
  if (o.kind === 'recap') return showRecap(pane, o);
  const slot = { full_banner: 'banner', lower_third: 'lower', side_panel: 'side' }[o.display.slot] || 'side';
  const layer = $('.layer.' + slot, pane.node);
  const prev = pane.shown.get(o.overlay_id);
  if (prev && prev.version > o.version) return;               // never downgrade AI -> template
  const card = buildOverlay(o, slot);
  if (prev && prev.node.isConnected) {
    prev.node.replaceWith(card);
    if (o.version > prev.version) card.classList.add('upgraded');
    clearTimeout(prev.timer);
  } else if (slot === 'side') {
    layer.prepend(card);
    while (layer.children.length > 3) layer.lastChild.remove();
  } else {
    layer.replaceChildren(card);
  }
  const secs = Math.max(5, o.display.duration_s) + (o.version > 1 ? 3 : 0);
  const timer = setTimeout(() => card.remove(), secs * 1000);
  pane.shown.set(o.overlay_id, { node: card, version: o.version, timer });
}

function showRecap(pane, o) {
  const card = el('div', 'ov recap');
  const x = el('span', 'x', '✕'); x.onclick = () => card.remove();
  card.append(x, el('span', 'badge ' + o.source, BADGE[o.source] || o.source), el('h2', null, o.headline), el('div', 't', o.text));
  const ul = el('ul');
  (o.key_moments || []).forEach((k) => ul.append(el('li', null, k)));
  card.append(ul);
  if (o.player_of_the_period) card.append(el('div', 'm', `★ ${o.player_of_the_period.name}: ${o.player_of_the_period.reason}`));
  $('.layer.full', pane.node).replaceChildren(card);
  setTimeout(() => card.remove(), 25000);
}

// ---------- pitch, stats, timeline ----------
function onEvent(e) {
  $('#clock').textContent = e.clock;
  if (e.x == null) return;
  const toX = e.end_x ?? e.x, toY = e.end_y ?? e.y;
  panes.forEach((p) => {
    const ball = $('.ball', p.node), line = $('.action', p.node);
    ball.setAttribute('cx', toX); ball.setAttribute('cy', toY);
    if (e.type === 'pass' || e.type === 'shot') {
      line.setAttribute('x1', e.x); line.setAttribute('y1', e.y); line.setAttribute('x2', toX); line.setAttribute('y2', toY);
      line.classList.toggle('shot', e.type === 'shot');
    }
  });
  $('#progress').style.width = Math.min(100, 100 * e.elapsed_s / matchEnd) + '%';
}

function onSnapshot(s) {
  $('#clock').textContent = s.clock;
  $('#score').textContent = `${s.score[homeId]} – ${s.score[awayId]}`;
  const box = $('#stats'); box.replaceChildren();
  STATS.forEach(([k, label]) => {
    const h = s.teams[homeId][k], a = s.teams[awayId][k];
    const row = el('div', 'stat');
    const mid = el('div', 'lbl', label);
    const hn = parseFloat(h) || 0, an = parseFloat(a) || 0;
    if (hn + an > 0) {
      const bars = el('div', 'bars');
      const bh = el('i'); bh.style.width = (100 * hn / (hn + an)) + '%';
      const ba = el('i'); ba.style.width = (100 * an / (hn + an)) + '%';
      bars.append(bh, ba); mid.append(bars);
    }
    row.append(el('span', null, h ?? '–'), mid, el('span', 'r', a ?? '–'));
    box.append(row);
  });
  const share = s.momentum.threat_share_pct, hs = share[homeId] ?? 50;
  $('#momHome').style.width = hs + '%'; $('#momAway').style.width = (100 - hs) + '%';
  panes.forEach((p) => { $('.mh', p.node).style.width = hs + '%'; $('.ma', p.node).style.width = (100 - hs) + '%'; });
  const top = s.momentum.on_top;
  $('#momLabel').textContent = top ? `${teams[top].short_name} on top` : 'Even contest';
  const r = $('#rhythm'); r.className = 'chip ' + s.rhythm.state;
  r.textContent = `${s.rhythm.state}${s.rhythm.turnovers_per_min != null ? ' · ' + s.rhythm.turnovers_per_min + ' turnovers/min' : ''}`;
  const ag = s.agents, calls = Object.values(ag.llm_calls).reduce((x, y) => x + y, 0);
  const fb = Object.values(ag.fallbacks).reduce((x, y) => x + y, 0);
  const rows = [['LLM calls', calls], ...Object.entries(ag.llm_calls).map(([k, v]) => ['  · ' + k, v]),
    ['Fallbacks', fb], ['Instant overlay p95', ag.instant_latency_p95_ms + ' ms'], ['Overlays sent', ag.overlays]];
  $('#agentStats').replaceChildren(...rows.map(([k, v]) => { const d = el('div', 'kv'); d.append(el('span', null, k), el('span', null, String(v))); return d; }));
}

function renderTimeline() {
  const items = [...timeline.values()].sort((a, b) => a.elapsed_s - b.elapsed_s);
  $('#dots').replaceChildren(...items.map((o) => {
    const d = el('i', o.alert_type === 'goal' ? 'goal' : (o.tags || []).includes('turning_point') ? 'turning_point' : '');
    d.style.left = (100 * o.elapsed_s / matchEnd) + '%'; d.title = `${o.clock} ${o.headline}`; return d;
  }));
  $('#moments').replaceChildren(...items.filter((o) => o.importance >= 45).reverse().map((o) => {
    const b = el('button', null, `${o.clock} ${ICON[o.alert_type] || ''} ${o.headline}`);
    b.style.setProperty('--team', teamColor(o.team));
    b.onclick = () => send({ cmd: 'seek', minute: minuteOf(o.clock) });
    return b;
  }));
}

// ---------- connection ----------
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (msg) => {
    const m = JSON.parse(msg.data);
    switch (m.kind) {
      case 'meta': {
        meta = m; homeId = m.teams.home.team_id; awayId = m.teams.away.team_id;
        teams = { [homeId]: m.teams.home, [awayId]: m.teams.away };
        $('#homeTeam b').textContent = m.teams.home.name; $('#awayTeam b').textContent = m.teams.away.name;
        $('#sHome').textContent = m.teams.home.short_name; $('#sAway').textContent = m.teams.away.short_name;
        $('#provider').textContent = `model: ${m.llm_provider === 'foundry' ? 'Microsoft Foundry' : 'offline mock'}`;
        matchEnd = m.match.periods.reduce((s, p) => s + p.duration_s, 0);
        if (!panes.length) addPane(m.presets[0].profile);
        if (new URLSearchParams(location.search).has('compare') && panes.length === 1) setCompare(true);
        else pushProfiles();
        break;
      }
      case 'profiles': m.ids.forEach((id, i) => { if (panes[i]) panes[i].pid = id; }); break;
      case 'event': onEvent(m.event); break;
      case 'snapshot': onSnapshot(m); break;
      case 'overlays': m.items.forEach((o) => panes.forEach((p) => { if (p.pid === o.profile_id) showOverlay(p, o); })); break;
      case 'timeline': timeline.clear(); m.items.forEach((o) => timeline.set(o.overlay_id, o)); renderTimeline(); break;
      case 'timeline_item': timeline.set(m.item.overlay_id, m.item); renderTimeline(); break;
      case 'reset': panes.forEach(clearOverlays); break;
      case 'status':
        playing = m.playing; $('#playBtn').textContent = playing ? '⏸' : '▶';
        $('#speed').value = String(m.speed); break;
      case 'end': playing = false; $('#playBtn').textContent = '▶'; break;
    }
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

$('#playBtn').onclick = () => send({ cmd: playing ? 'pause' : 'play' });
$('#speed').onchange = (e) => send({ cmd: 'speed', value: +e.target.value });
$('#jumpBtn').onclick = () => send({ cmd: 'seek', minute: +$('#jumpMin').value });
$('#restartBtn').onclick = () => send({ cmd: 'restart' });
$('#compareBtn').onclick = () => setCompare(!$('#compareBtn').classList.contains('on'));
$('#track').onclick = (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  send({ cmd: 'seek', minute: Math.floor(((e.clientX - r.left) / r.width) * matchEnd / 60) });
};
connect();
