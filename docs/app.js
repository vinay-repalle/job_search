'use strict';

/* ---------------- state & storage ---------------- */
const KEYS = { filters: 'qaj.filters.v1', tracker: 'qaj.tracker.v1', install: 'qaj.install.dismissed.v1' };
const DEFAULTS = { age: '7', groups: [], modes: [], exp: '99', trust: 'all', hideShift: false, showHidden: false };
const FALLBACK_GROUPS = { qa: 'QA / Testing', support: 'App Support', validation: 'CSV / Validation' };
const APPLIED_STATES = ['applied', 'interview', 'offer', 'rejected'];
const STATE_LABEL = { applied: 'Applied', interview: 'Interview', offer: 'Offer 🎉', rejected: 'Not selected' };
const TABS = ['jobs', 'saved', 'applied'];

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: keep in memory */ }
  },
};

let data = null;
let jobs = [];
let loadedAt = 0;
let tab = 'jobs';
let query = '';
let pendingApply = null;
let filters = { ...DEFAULTS, ...store.get(KEYS.filters, {}) };
let tracker = store.get(KEYS.tracker, {});
let mySkills = new Set();

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : '#');
const groupLabel = (g) => (data?.groups?.[g]) || FALLBACK_GROUPS[g] || g;
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/* ---------------- dates ---------------- */
const DAY = 86400000;
const refDate = () => data?.run?.date || localISO();
const daysBetween = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / DAY);
function localISO(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function yesterdayISO() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return localISO(d);
}
const isoToDate = (iso) => new Date(`${iso}T00:00:00`);
function ago(iso) {
  if (!iso) return '';
  const d = Math.floor((Date.now() - Date.parse(iso)) / DAY);
  if (d <= 0) return 'Today';
  if (d === 1) return 'Yesterday';
  return `${d} days ago`;
}
function fmtDate(ms) {
  return new Date(ms).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}
function dayName(iso) {
  if (iso === localISO()) return 'Today';
  if (iso === yesterdayISO()) return 'Yesterday';
  return isoToDate(iso).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' });
}
const shortDay = (iso) => isoToDate(iso).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });

/* ---------------- load ---------------- */
async function load() {
  try {
    const res = await fetch('data/jobs.json', { cache: 'no-cache' });
    if (res.status === 404) {
      data = { jobs: [], run: {}, updated_at: null };   // site is live but the first daily run hasn't happened yet
    } else {
      if (!res.ok) throw new Error(String(res.status));
      data = await res.json();
    }
  } catch {
    if (!data) data = null;
  }
  jobs = (data?.jobs ?? []).slice().sort((a, b) =>
    (b.first_seen || '').localeCompare(a.first_seen || '') || (b.score || 0) - (a.score || 0));
  mySkills = new Set(data?.my_skills ?? []);
  loadedAt = Date.now();
  renderHeader();
  render();
}

function renderHeader() {
  const el = $('#updated');
  if (!data) { el.textContent = 'Couldn’t load jobs — check your connection'; return; }
  if (!data.updated_at) { el.textContent = 'First jobs arrive after the first daily run'; return; }
  const when = new Date(data.updated_at);
  const sameDay = when.toDateString() === new Date().toDateString();
  const time = when.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  const newCount = data.run?.new_ids?.length ?? 0;
  el.textContent = `Updated ${sameDay ? 'today' : fmtDate(when)} ${time} · ${newCount} new`;
}

/* ---------------- filtering ---------------- */
function passes(j) {
  const t = tracker[j.id];
  if (t?.status === 'hidden' && !filters.showHidden) return false;
  const age = Number(filters.age);
  if (age > 0 && daysBetween(j.first_seen, refDate()) >= age) return false;
  if (filters.groups.length && !filters.groups.includes(j.group)) return false;
  if (filters.modes.length && !filters.modes.includes(j.mode)) return false;
  const exp = Number(filters.exp);
  if (exp < 99 && j.exp_min != null && j.exp_min > exp) return false;
  if (filters.trust === 'company' && j.trust !== 'company') return false;
  if (filters.trust === 'trusted' && j.trust === 'other') return false;
  if (filters.hideShift && j.shift) return false;
  return matchesQuery(j);
}

function matchesQuery(j) {
  if (!query) return true;
  const hay = `${j.title} ${j.company} ${(j.skills || []).join(' ')} ${j.location} ${j.source} ${j.mode}`.toLowerCase();
  return query.split(/\s+/).every((w) => hay.includes(w));
}

function activeFilterCount() {
  let n = 0;
  if (filters.age !== DEFAULTS.age) n++;
  if (filters.groups.length) n++;
  if (filters.modes.length) n++;
  if (filters.exp !== DEFAULTS.exp) n++;
  if (filters.trust !== DEFAULTS.trust) n++;
  if (filters.hideShift) n++;
  if (filters.showHidden) n++;
  return n;
}

/* ---------------- rendering ---------------- */
const ICON = {
  pin: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21Z" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="9.5" r="2.5" fill="currentColor"/></svg>',
  mode: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M3 11.5 12 4l9 7.5M5.5 10v10h13V10" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  exp: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><rect x="3" y="7" width="18" height="13" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8.5 7V5h7v2" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  money: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M7 5h10M7 9.5h10M7 5c5 0 6 9-1 9l7 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

function sourceTag(j) {
  if (j.trust === 'company') return '<span class="tag company">✓ Company site</span>';
  if (j.trust === 'trusted') return `<span class="tag trusted">${esc(j.source)}</span>`;
  return `<span class="tag">${esc(j.source)}</span>`;
}

function card(j, { mode = 'jobs' } = {}) {
  const t = tracker[j.id] || {};
  const saved = t.status === 'saved';
  const applied = APPLIED_STATES.includes(t.status);
  const hidden = t.status === 'hidden';
  const skills = (j.skills || []).map((s) => `<span class="skill${mySkills.has(s) ? ' mine' : ''}">${esc(s)}</span>`).join('');
  const others = (j.links || []).slice(1).map((l) => `<a href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener">${esc(l.name)} ↗</a>`).join('');

  let actions;
  if (mode === 'applied') {
    const opts = APPLIED_STATES.map((s) => `<option value="${s}"${t.status === s ? ' selected' : ''}>${STATE_LABEL[s]}</option>`).join('');
    actions = `
      <div class="actions">
        <a class="btn ghost small" href="${esc(safeUrl(j.apply_url))}" target="_blank" rel="noopener" data-act="open" data-id="${esc(j.id)}">Open posting ↗</a>
        <button class="btn ghost small x" data-act="remove" data-id="${esc(j.id)}">Remove</button>
      </div>
      <div class="status-row">
        <label>Status <select data-act="status" data-id="${esc(j.id)}">${opts}</select></label>
        <span class="muted">Applied ${t.applied_at ? fmtDate(t.applied_at) : ''}</span>
      </div>`;
  } else {
    actions = `
      <div class="actions">
        <a class="btn primary" href="${esc(safeUrl(j.apply_url))}" target="_blank" rel="noopener" data-act="apply" data-id="${esc(j.id)}">Apply ↗</a>
        <button class="btn small${saved ? ' on' : ''}" data-act="save" data-id="${esc(j.id)}" aria-pressed="${saved}">${saved ? '★ Saved' : '☆ Save'}</button>
        <button class="btn small${applied ? ' on' : ''}" data-act="applied" data-id="${esc(j.id)}" aria-pressed="${applied}">${applied ? '✓ ' + STATE_LABEL[t.status] : 'Mark applied'}</button>
      </div>`;
  }
  const hideBtn = mode !== 'jobs' ? ''
    : hidden
      ? `<button class="icon-only unhide" data-act="unhide" data-id="${esc(j.id)}">Unhide</button>`
      : `<button class="icon-only" data-act="hide" data-id="${esc(j.id)}" aria-label="Not interested" title="Not interested">✕</button>`;

  return `
  <article class="card" data-id="${esc(j.id)}">
    <div class="card-top">
      ${sourceTag(j)}
      <span class="tag">${esc(groupLabel(j.group))}</span>
      ${j.shift ? '<span class="tag warn">⚠ Shifts</span>' : ''}
      <span class="age">${esc(ago(j.posted_at) || ('Found ' + fmtDate(Date.parse(j.first_seen))))}</span>
      ${hideBtn}
    </div>
    <h3>${esc(j.title)}</h3>
    <p class="company">${esc(j.company)}</p>
    <ul class="meta">
      <li>${ICON.pin}${esc(j.location)}</li>
      <li>${ICON.mode}${esc(j.mode === 'Not stated' ? 'Mode not stated' : j.mode)}</li>
      ${j.exp_text ? `<li>${ICON.exp}${esc(j.exp_text)}</li>` : ''}
      ${j.salary ? `<li>${ICON.money}${esc(j.salary)}</li>` : ''}
    </ul>
    ${skills ? `<div class="skills">${skills}</div>` : ''}
    ${(j.snippet || others) ? `
    <details>
      <summary>Details</summary>
      ${j.snippet ? `<p class="snippet">${esc(j.snippet)}</p>` : ''}
      ${others ? `<div class="links"><span class="muted">Also on:</span> ${others}</div>` : ''}
    </details>` : ''}
    <div class="card-foot">${actions}</div>
  </article>`;
}

/* Jobs tab: one section per day the jobs were found, newest day first, so each
   morning's arrivals sit on top under their own heading and older ones stay below. */
function daySections(items) {
  const latest = data?.run?.date;
  const byDay = new Map();
  for (const j of items) {
    if (!byDay.has(j.first_seen)) byDay.set(j.first_seen, []);
    byDay.get(j.first_seen).push(j);
  }
  return [...byDay.entries()].map(([day, list]) => {
    const isLatest = day === latest;
    const name = dayName(day);
    const relative = name === 'Today' || name === 'Yesterday';
    return `
    <section class="day ${isLatest ? 'latest' : 'earlier'}" aria-label="${esc(name)}">
      <h2 class="day-h">
        ${isLatest ? '<span class="new-pill">NEW</span>' : ''}
        <span>${esc(isLatest && name === 'Today' ? 'New today' : name)}</span>
        <span class="day-n">${plural(list.length, 'job')}</span>
        ${relative ? `<span class="day-date">${esc(shortDay(day))}</span>` : ''}
      </h2>
      <div class="grid">${list.map((j) => card(j)).join('')}</div>
    </section>`;
  }).join('');
}

function renderNotice(visible) {
  const el = $('#notice');
  const latest = data?.run?.date;
  let html = '';
  if (tab === 'jobs' && data?.updated_at && latest) {
    const newAll = jobs.filter((j) => j.first_seen === latest && tracker[j.id]?.status !== 'hidden').length;
    const newShown = visible.filter((j) => j.first_seen === latest).length;
    if (latest !== localISO()) {
      html = `<span>No new jobs yet today — last check was ${esc(shortDay(latest))}. New jobs arrive every morning around 8 AM.</span>`;
    } else if (!newAll) {
      html = '<span>No new jobs found today. Earlier jobs are below.</span>';
    } else if (newShown < newAll) {
      const missing = newAll - newShown;
      html = `<span>${plural(missing, 'new job')} from today ${missing === 1 ? 'is' : 'are'} hidden by your filters or search.</span>
        <button class="btn small" data-act="reset">Show all</button>`;
    }
  }
  el.innerHTML = html;
  el.hidden = !html;
}

function trackedJobs(states) {
  return Object.entries(tracker)
    .filter(([, t]) => states.includes(t.status))
    .sort((a, b) => (b[1].at || 0) - (a[1].at || 0))
    .map(([id, t]) => jobs.find((j) => j.id === id) || t.job)
    .filter(Boolean)
    .filter(matchesQuery);
}

function render() {
  disarm();
  const list = $('#list');
  const n = activeFilterCount();
  const fc = $('#filterCount');
  fc.hidden = n === 0;
  fc.textContent = n;

  const visible = jobs.filter(passes);
  const saved = trackedJobs(['saved']);
  const applied = trackedJobs(APPLIED_STATES);
  $('#cJobs').textContent = visible.length;
  $('#cSaved').textContent = saved.length || '';
  $('#cApplied').textContent = applied.length || '';
  $('#doneFilters').textContent = visible.length ? `Show ${plural(visible.length, 'job')}` : 'No jobs match — try fewer filters';

  let summary = '';
  if (tab === 'jobs') {
    summary = data ? `Showing ${visible.length} of ${jobs.length} jobs` + (n ? ' · filters on' : '') : '';
  } else if (tab === 'saved') {
    summary = 'Jobs you starred to apply later.';
  } else {
    summary = 'Update the status as things move along.';
  }
  $('#summary').textContent = summary;
  renderNotice(visible);

  if (!data && tab === 'jobs') {
    list.innerHTML = '<div class="empty"><h2>No connection</h2><p class="muted">Open the app again when you’re online.</p></div>';
    return;
  }
  const items = tab === 'jobs' ? visible : tab === 'saved' ? saved : applied;
  if (!items.length) {
    const msg = {
      jobs: ['No jobs match', n ? 'Try fewer filters.' : 'New jobs arrive every morning at 8 AM.', n],
      saved: ['Nothing saved yet', 'Tap “☆ Save” on a job to keep it here.', 0],
      applied: ['No applications yet', 'Tap “Mark applied” after you apply.', 0],
    }[tab];
    list.innerHTML = `<div class="empty"><h2>${msg[0]}</h2><p class="muted">${msg[1]}</p>${msg[2] ? '<button class="btn" data-act="reset">Reset filters</button>' : ''}</div>`;
    return;
  }
  list.innerHTML = tab === 'jobs'
    ? daySections(items)
    : `<div class="grid">${items.map((j) => card(j, { mode: tab === 'applied' ? 'applied' : 'saved' })).join('')}</div>`;
}

/* ---------------- tap-twice confirm (only for things that can't easily be undone) ---------------- */
let armed = null;
let armTimer;
function disarm() {
  clearTimeout(armTimer);
  if (armed?.isConnected) {
    armed.innerHTML = armed.dataset.label;
    armed.classList.remove('armed');
  }
  armed = null;
}
// First tap turns the button red and asks again; returns true when this tap is the confirming one.
function confirmed(el, ask) {
  if (armed === el) { disarm(); return true; }
  disarm();
  armed = el;
  el.dataset.label = el.innerHTML;
  el.textContent = ask;
  el.classList.add('armed');
  armTimer = setTimeout(disarm, 4000);
  return false;
}
document.addEventListener('click', (e) => {
  if (armed && !armed.contains(e.target)) disarm();
}, true);

/* ---------------- tracker actions ---------------- */
function findJob(id) {
  return jobs.find((j) => j.id === id) || tracker[id]?.job;
}

function setStatus(id, status) {
  const job = findJob(id);
  if (!status) {
    delete tracker[id];
  } else {
    const prev = tracker[id] || {};
    tracker[id] = {
      status,
      at: Date.now(),
      applied_at: APPLIED_STATES.includes(status) ? (prev.applied_at || Date.now()) : prev.applied_at,
      job: job ? { ...job, snippet: (job.snippet || '').slice(0, 300) } : prev.job,
    };
  }
  store.set(KEYS.tracker, tracker);
  render();
}

let toastTimer;
function toast(msg, actionLabel, action) {
  const el = $('#toast');
  el.innerHTML = `<span>${esc(msg)}</span>` + (actionLabel ? ` <button class="btn small" style="margin-left:8px">${esc(actionLabel)}</button>` : '');
  el.hidden = false;
  if (actionLabel) $('button', el).onclick = () => { action(); el.hidden = true; };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, actionLabel ? 7000 : 2500);
}

function onListClick(e) {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const id = el.dataset.id;
  const t = tracker[id];
  switch (el.dataset.act) {
    case 'apply':
      pendingApply = id;            // ask "did you apply?" when she comes back
      break;
    case 'save':
      if (APPLIED_STATES.includes(t?.status)) return;
      setStatus(id, t?.status === 'saved' ? null : 'saved');
      break;
    case 'applied':
      if (APPLIED_STATES.includes(t?.status)) {
        // Un-marking loses the applied date and any Interview/Offer status.
        if (!confirmed(el, 'Tap again to unmark')) return;
        const prev = { ...t };
        setStatus(id, null);
        toast('Removed from Applied', 'Undo', () => { tracker[id] = prev; store.set(KEYS.tracker, tracker); render(); });
      } else {
        setStatus(id, 'applied');
        toast('Added to Applied ✓');
      }
      break;
    case 'hide': {
      const prev = t?.status || null;
      setStatus(id, 'hidden');
      toast('Hidden', 'Undo', () => setStatus(id, prev));
      break;
    }
    case 'unhide':
      setStatus(id, null);
      break;
    case 'remove': {
      if (!confirmed(el, 'Tap again to remove')) return;
      const prev = { ...t };
      delete tracker[id];
      store.set(KEYS.tracker, tracker);
      render();
      toast('Removed', 'Undo', () => { tracker[id] = prev; store.set(KEYS.tracker, tracker); render(); });
      break;
    }
    case 'reset':
      resetFilters({ clearSearch: true });
      break;
  }
}
$('#list').addEventListener('click', onListClick);
$('#notice').addEventListener('click', onListClick);

$('#list').addEventListener('change', (e) => {
  const el = e.target.closest('select[data-act="status"]');
  if (el) setStatus(el.dataset.id, el.value);
});

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  if (pendingApply) {
    const id = pendingApply;
    pendingApply = null;
    if (!APPLIED_STATES.includes(tracker[id]?.status)) {
      setTimeout(() => toast('Did you apply?', 'Mark applied', () => setStatus(id, 'applied')), 400);
    }
  }
  if (Date.now() - loadedAt > 30 * 60 * 1000) load();
});

/* ---------------- tabs ---------------- */
function selectTab(name) {
  tab = name;
  document.body.dataset.tab = name;
  $$('.tabs button').forEach((x) => x.setAttribute('aria-selected', String(x.dataset.tab === name)));
  render();
}
$$('.tabs button').forEach((b) => b.addEventListener('click', () => {
  if (tab === b.dataset.tab) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
  selectTab(b.dataset.tab);
  window.scrollTo({ top: 0 });
}));

/* ---------------- filters UI ---------------- */
function syncFilterUI() {
  $$('.chips').forEach((box) => {
    const key = box.dataset.filter;
    const single = box.hasAttribute('data-single');
    $$('button', box).forEach((b) => {
      const on = single ? filters[key] === b.dataset.v : filters[key].includes(b.dataset.v);
      b.setAttribute('aria-pressed', String(on));
    });
  });
  $('#hideShift').checked = filters.hideShift;
  $('#showHidden').checked = filters.showHidden;
}

function saveFilters() {
  store.set(KEYS.filters, filters);
  syncFilterUI();
  render();
}

function resetFilters({ clearSearch = false } = {}) {
  filters = { ...DEFAULTS, groups: [], modes: [] };
  if (clearSearch) { query = ''; $('#q').value = ''; }
  saveFilters();
}

$$('.chips').forEach((box) => box.addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  const key = box.dataset.filter;
  if (box.hasAttribute('data-single')) {
    filters[key] = b.dataset.v;
  } else {
    const set = new Set(filters[key]);
    set.has(b.dataset.v) ? set.delete(b.dataset.v) : set.add(b.dataset.v);
    filters[key] = [...set];
  }
  saveFilters();
}));
$('#hideShift').addEventListener('change', (e) => { filters.hideShift = e.target.checked; saveFilters(); });
$('#showHidden').addEventListener('change', (e) => { filters.showHidden = e.target.checked; saveFilters(); });
$('#resetFilters').addEventListener('click', () => resetFilters());

/* Phones/tablets: filters open as a bottom sheet. It closes on the ✕, on "Show N jobs",
   on a tap outside, on a swipe down, on Esc, and on the phone's Back button. */
const sheet = $('#filters');
const wideScreen = window.matchMedia('(min-width: 1024px)');
let sheetOpen = false;

function setSheetA11y() {
  if (wideScreen.matches) {
    sheet.removeAttribute('role');
    sheet.removeAttribute('aria-modal');
    sheet.removeAttribute('inert');
  } else {
    sheet.setAttribute('role', 'dialog');
    sheet.setAttribute('aria-modal', 'true');
    sheet.toggleAttribute('inert', !sheetOpen);
  }
}

function openSheet() {
  if (sheetOpen || wideScreen.matches) return;
  sheetOpen = true;
  document.body.classList.add('sheet-open');
  $('#filterBtn').setAttribute('aria-expanded', 'true');
  setSheetA11y();
  history.pushState({ sheet: true }, '');
  setTimeout(() => $('#closeFilters').focus({ preventScroll: true }), 50);
}

function hideSheet() {
  if (!sheetOpen) return;
  sheetOpen = false;
  document.body.classList.remove('sheet-open');
  $('#filterBtn').setAttribute('aria-expanded', 'false');
  setSheetA11y();
  $('#filterBtn').focus({ preventScroll: true });
}

function closeSheet() {
  if (!sheetOpen) return;
  if (history.state?.sheet) history.back();   // popstate below does the hiding
  else hideSheet();
}

window.addEventListener('popstate', () => { if (sheetOpen) hideSheet(); });
$('#filterBtn').addEventListener('click', () => (sheetOpen ? closeSheet() : openSheet()));
$('#closeFilters').addEventListener('click', closeSheet);
$('#backdrop').addEventListener('click', closeSheet);
$('#doneFilters').addEventListener('click', () => {
  closeSheet();
  if (tab !== 'jobs') selectTab('jobs');
  window.scrollTo({ top: 0, behavior: 'smooth' });
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && sheetOpen) closeSheet(); });
wideScreen.addEventListener('change', () => {
  if (wideScreen.matches && sheetOpen) closeSheet();
  setSheetA11y();
});

// Swipe the sheet's top bar down to close it.
let dragStart = null;
const head = $('#sheetHead');
head.addEventListener('touchstart', (e) => {
  if (!sheetOpen) return;
  dragStart = e.touches[0].clientY;
  sheet.style.transition = 'none';
}, { passive: true });
head.addEventListener('touchmove', (e) => {
  if (dragStart == null) return;
  const dy = Math.max(0, e.touches[0].clientY - dragStart);
  sheet.style.transform = `translateY(${dy}px)`;
}, { passive: true });
function endDrag(e) {
  if (dragStart == null) return;
  const dy = (e.changedTouches?.[0]?.clientY ?? dragStart) - dragStart;
  dragStart = null;
  sheet.style.transition = '';
  sheet.style.transform = '';
  if (dy > 70) closeSheet();
}
head.addEventListener('touchend', endDrag);
head.addEventListener('touchcancel', endDrag);

let searchTimer;
$('#q').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { query = e.target.value.trim().toLowerCase(); render(); }, 120);
});
$('#q').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.blur(); });

/* ---------------- refresh ---------------- */
$('#refreshBtn').addEventListener('click', async () => {
  const btn = $('#refreshBtn');
  if (btn.classList.contains('spin')) return;
  btn.classList.add('spin');
  const before = data?.updated_at;
  await Promise.all([load(), new Promise((r) => setTimeout(r, 500))]);
  btn.classList.remove('spin');
  if (!data) toast('No connection — showing your last list');
  else if (data.updated_at !== before) toast('New jobs loaded');
  else toast('You’re up to date');
});

/* ---------------- backup / restore ---------------- */
$('#exportBtn').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify({ version: 1, exported_at: new Date().toISOString(), tracker }, null, 1)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `qa-jobs-backup-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
});

$('#importFile').addEventListener('change', async (e) => {
  const file = e.target.files?.[0];
  e.target.value = '';
  if (!file) return;
  try {
    const incoming = JSON.parse(await file.text()).tracker || {};
    let added = 0;
    for (const [id, t] of Object.entries(incoming)) {
      if (!t || typeof t !== 'object' || typeof t.status !== 'string') continue;
      if (!tracker[id] || (t.at || 0) > (tracker[id].at || 0)) { tracker[id] = t; added++; }
    }
    store.set(KEYS.tracker, tracker);
    render();
    toast(`Restored ${added} item${added === 1 ? '' : 's'}`);
  } catch {
    toast('That file isn’t a QA Jobs backup');
  }
});

/* ---------------- install prompt ---------------- */
let deferredPrompt = null;
const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;

function showInstall() {
  if (isStandalone() || store.get(KEYS.install, false)) return;
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  $('#installText').textContent = deferredPrompt
    ? 'Add it to your home screen — it opens like a normal app.'
    : isIOS
      ? 'In Safari tap Share ⎋, then “Add to Home Screen”.'
      : 'Open the browser menu ⋮, then “Install app” or “Add to Home screen”.';
  $('#installBtn').hidden = !deferredPrompt;
  $('#install').hidden = false;
}

window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  showInstall();
});
$('#installBtn').addEventListener('click', async () => {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  await deferredPrompt.userChoice.catch(() => null);
  deferredPrompt = null;
  $('#install').hidden = true;
});
$('#installClose').addEventListener('click', () => {
  store.set(KEYS.install, true);
  $('#install').hidden = true;
});
window.addEventListener('appinstalled', () => { $('#install').hidden = true; });

/* ---------------- start ---------------- */
// Keep the laptop filter sidebar just below the sticky header.
const topBar = $('#top');
const setTopH = () => document.documentElement.style.setProperty('--top-h', `${topBar.offsetHeight}px`);
setTopH();
if ('ResizeObserver' in window) new ResizeObserver(setTopH).observe(topBar);

// The filter sheet adds a Back-button history entry; don't let going back jump the scroll.
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
if (history.state?.sheet) history.replaceState(null, '');

setSheetA11y();
syncFilterUI();
const startTab = new URLSearchParams(location.search).get('tab');   // home-screen shortcuts
if (TABS.includes(startTab)) selectTab(startTab);
load().then(() => setTimeout(showInstall, 1200));

if ('serviceWorker' in navigator) {
  // When a new version of the app is deployed, the new service worker takes over and we
  // reload once so she sees it straight away (not on the next launch).
  const hadController = Boolean(navigator.serviceWorker.controller);
  let reloading = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!hadController || reloading) return;
    reloading = true;
    location.reload();
  });
  window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}
