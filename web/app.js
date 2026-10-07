'use strict';
const $ = id => document.getElementById(id);
const state = {status: null, decks: [], deck: null, run: null, slide: 1, busy: false, tab: 'overview'};
const client = new LocalApiClient();
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function notice(text) { $('notice').textContent = text || ''; $('notice').hidden = !text; }
async function api(path, options = {}) {
  return client.request(path, options);
}
function setTab(name) {
  state.tab = name;
  for (const tab of ['overview', 'slides', 'context']) $(tab).hidden = tab !== name;
  document.querySelectorAll('[data-tab]').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
}
function updateControls() {
  const active = state.status?.active_run;
  $('analyze').disabled = state.busy || !!active || !state.deck || !$('model').value || !state.status?.lm.connected || !!state.status?.importing;
  $('upload-button').disabled = state.busy || !!active || !!state.status?.importing;
}
async function loadStatus() {
  const status = await api('/api/status');
  state.status = status;
  const lm = status.lm;
  $('connection').classList.toggle('connected', lm.connected && lm.models.length > 0);
  $('connection-label').textContent = lm.connected ? (lm.models.length ? 'LM Studio connected' : 'Load a Qwen vision model') : 'LM Studio offline';
  const selected = $('model').value;
  $('model').replaceChildren();
  for (const model of lm.models) {
    const opt = el('option', model); opt.value = model; $('model').append(opt);
  }
  if (!lm.models.length) {
    const opt = el('option', 'Connect a local Qwen vision model'); opt.value = ''; $('model').append(opt);
  } else if (lm.models.includes(selected)) $('model').value = selected;
  if (status.setup_error) notice(status.setup_error);
  else if (status.importing) notice('Importing the default deck: extracting text and rendering slides locally…');
  else if (!lm.connected) notice('Start LM Studio’s local server on port 1234 and load your Qwen vision model. Click the connection indicator to retry.');
  else if (!lm.models.length) notice('LM Studio is connected. Load a Qwen model with vision support, then retry the connection.');
  else if (!state.busy) notice('');
  updateControls();
}
function renderDecks() {
  $('deck-list').replaceChildren();
  for (const deck of state.decks) {
    const button = el('button', deck.name.replace(/\.pdf$/i, ''), 'deck-item');
    button.classList.toggle('selected', deck.id === state.deck?.id);
    button.append(el('small', `${deck.slide_count} slides · PDF pitch deck`));
    button.addEventListener('click', () => selectDeck(deck.id).catch(e => notice(e.message)));
    $('deck-list').append(button);
  }
}
async function loadDecks() {
  state.decks = await api('/api/decks');
  renderDecks();
  if (!state.deck && state.decks.length) await selectDeck(state.decks[0].id);
}
async function selectDeck(id) {
  const data = await api(`/api/decks/${id}`);
  state.deck = data.deck;
  state.run = null;
  state.slide = 1;
  $('deck-title').textContent = data.deck.name.replace(/\.pdf$/i, '').replace(/_Deck.*$/i, '').replaceAll('_', ' ');
  $('deck-breadcrumb').textContent = data.deck.name;
  $('slide-count').textContent = data.deck.slide_count;
  renderDecks();
  const previous = document.querySelector('.previous-runs');
  if (previous) previous.remove();
  if (data.runs.length) {
    const history = el('div', undefined, 'previous-runs');
    const label = el('label', 'SAVED ANALYSES'); label.htmlFor = 'run-history';
    const select = el('select'); select.id = 'run-history';
    for (const run of data.runs) {
      const opt = el('option', `${new Date(run.created).toLocaleString()} · ${run.status}`); opt.value = run.id; select.append(opt);
    }
    select.addEventListener('change', () => loadRun(select.value).catch(e => notice(e.message)));
    history.append(label, select); $('run-bar').before(history);
    await loadRun(data.runs[0].id);
  } else renderRun();
  updateControls();
}
async function loadRun(id) {
  const run = await api(`/api/runs/${id}`);
  if (state.deck?.id !== run.deck_id) return;
  if (state.run?.id === run.id && state.run.updated === run.updated) return;
  state.run = run;
  renderRun();
}
function renderRun() {
  const run = state.run;
  const slides = run?.payload.slides || [];
  const done = slides.filter(s => s.interpretation).length;
  $('completed-count').textContent = done;
  $('run-bar').hidden = !run;
  if (run) {
    $('stage').textContent = run.stage;
    $('progress-detail').textContent = `${done} of ${run.payload.deck.slide_count} slides understood · ${run.status}`;
    const history = $('run-history');
    if (history) {
      const option = [...history.options].find(o => o.value === run.id);
      if (option) option.textContent = `${new Date(run.created).toLocaleString()} · ${run.status}`;
    }
    $('stop').hidden = !['running', 'queued'].includes(run.status);
    $('resume').hidden = !['failed', 'cancelled', 'interrupted'].includes(run.status);
    if (run.payload.error) notice(run.payload.error);
  }
  const report = run?.payload.report;
  $('empty-state').hidden = !!report;
  $('report').hidden = !report;
  if (report) renderReport(report);
  $('exports').hidden = !run;
  if (run) {
    $('export-md').href = `/api/runs/${run.id}/markdown`;
    $('export-json').href = `/api/runs/${run.id}/json`;
  }
  renderSlideNav();
  renderContext();
  if (state.tab === 'slides') showSlide(state.slide).catch(e => notice(e.message));
  updateControls();
}
const titles = {business_overview: 'The business, in plain English', problem_and_customer: 'Problem & customer', product_and_how_it_works: 'Product & how it works', business_model: 'Business model', market_and_positioning: 'Market & positioning', traction_and_stage: 'Traction & stage', team_and_funding: 'Team & funding', not_explained_in_deck: 'What the deck leaves unexplained'};
function renderReport(report) {
  $('report').replaceChildren(el('p', 'This explanation reflects the deck’s claims. Claims, forecasts, and illustrations have not been independently verified.', 'report-note'));
  for (const [key, values] of Object.entries(report)) {
    const section = el('section', undefined, 'report-section');
    section.append(el('h2', titles[key] || key));
    const items = Array.isArray(values) ? values : [values];
    if (!items.length) section.append(el('p', 'Not explained in the deck.'));
    for (const item of items) {
      const p = el('p', typeof item === 'string' ? item : item.text);
      if (typeof item !== 'string') {
        const citations = el('span', undefined, 'citations');
        for (const n of item.slide_numbers) {
          const ref = el('button', `Slide ${n}`);
          ref.addEventListener('click', () => { setTab('slides'); showSlide(n).catch(e => notice(e.message)); });
          citations.append(ref);
        }
        p.append(citations);
      }
      section.append(p);
    }
    $('report').append(section);
  }
}
function renderSlideNav() {
  $('slide-nav').replaceChildren();
  if (!state.deck) return;
  for (let number = 1; number <= state.deck.slide_count; number++) {
    const item = state.run?.payload.slides.find(s => s.number === number);
    const button = el('button', `Slide ${String(number).padStart(2, '0')}`);
    button.classList.toggle('selected', state.slide === number);
    button.append(el('small', item?.interpretation ? 'Understood ✓' : item?.vision ? 'Vision saved' : 'Awaiting analysis'));
    button.addEventListener('click', () => showSlide(number).catch(e => notice(e.message)));
    $('slide-nav').append(button);
  }
}
async function showSlide(number) {
  if (!state.deck) return;
  state.slide = number;
  const did = state.deck.id;
  const source = await api(`/api/decks/${did}/slides/${number}`);
  if (state.deck?.id !== did || state.slide !== number) return;
  const item = state.run?.payload.slides.find(s => s.number === number);
  const box = $('slide-detail'); box.replaceChildren();
  const img = el('img'); img.src = `/api/decks/${did}/images/${number}`; img.alt = `Pitch deck slide ${number}`;
  box.append(img, el('h2', item?.interpretation?.title || `Slide ${number}`));
  if (item?.interpretation) {
    box.append(el('h3', 'Combined understanding'), el('p', item.interpretation.explanation));
    const list = el('ul'); item.interpretation.claims.forEach(c => list.append(el('li', c))); box.append(list);
    if (item.interpretation.uncertainties.length) {
      box.append(el('h3', 'Unclear or unresolved'));
      item.interpretation.uncertainties.forEach(c => box.append(el('p', c)));
    }
  } else box.append(el('p', 'The local model has not completed this slide yet.'));
  const text = el('details'); text.append(el('summary', 'PDF-extracted text'), el('pre', source.extracted_text || 'This slide has no embedded text. Qwen vision reads the visible text from the rendered image.')); box.append(text);
  if (item?.vision) {
    const vision = el('details'); vision.append(el('summary', 'Independent Qwen vision observations'));
    vision.append(el('h3', 'Visible text'), el('pre', item.vision.visible_text), el('h3', 'Visual description'), el('p', item.vision.visual_description));
    item.vision.charts_and_diagrams.forEach(c => vision.append(el('p', c)));
    item.vision.uncertainties.forEach(c => vision.append(el('p', `Uncertainty: ${c}`)));
    box.append(vision);
  }
  renderSlideNav();
}
function renderContext() {
  $('context-details').replaceChildren(); $('activity').replaceChildren();
  const p = state.run?.payload;
  if (!p) return;
  const grid = el('div', undefined, 'context-grid');
  for (const [label, value] of [['Prompt version', p.prompt_version], ['Saved slide interpretations', p.slides.filter(s => s.interpretation).length], ['Local tool executions', p.trace.filter(t => t.phase === 'tool').length]]) {
    const cell = el('div'); cell.append(el('small', label), el('strong', value)); grid.append(cell);
  }
  $('context-details').append(grid, el('p', `Source SHA-256: ${p.deck.sha256}`, 'report-note'));
  const header = el('div', undefined, 'activity-row'); header.append(el('strong', 'STAGE'), el('strong', 'INPUT / OUTPUT TOKENS'), el('strong', 'ELAPSED')); $('activity').append(header);
  for (const t of [...p.trace].reverse()) {
    const row = el('div', undefined, 'activity-row');
    row.append(el('span', t.phase === 'tool' ? `Tool: ${t.name} · ${t.ok ? 'OK' : 'rejected'}` : t.phase), el('span', t.usage ? `${t.usage.prompt_tokens ?? '—'} / ${t.usage.completion_tokens ?? '—'}` : '—'), el('span', t.seconds !== undefined ? `${t.seconds}s` : '—'));
    $('activity').append(row);
  }
}
document.querySelectorAll('[data-tab]').forEach(b => b.addEventListener('click', () => { setTab(b.dataset.tab); if (state.tab === 'slides') showSlide(state.slide).catch(e => notice(e.message)); }));
$('model').addEventListener('change', updateControls);
$('upload-button').addEventListener('click', () => $('upload').click());
$('upload').addEventListener('change', async () => {
  const file = $('upload').files[0]; if (!file) return;
  state.busy = true; updateControls(); notice('Importing PDF: rendering slides and extracting text locally…');
  try {
    const deck = await api('/api/decks', {method: 'POST', headers: {'Content-Type': 'application/pdf', 'X-Filename': encodeURIComponent(file.name)}, body: file});
    await loadDecks(); await selectDeck(deck.id); notice('');
  } catch (e) { notice(e.message); }
  finally { state.busy = false; $('upload').value = ''; updateControls(); }
});
$('analyze').addEventListener('click', async () => {
  state.busy = true; updateControls(); notice('Starting the local slide-by-slide analysis…');
  try {
    const result = await api('/api/runs', {method: 'POST', body: JSON.stringify({deck_id: state.deck.id, model: $('model').value})});
    await loadStatus(); await selectDeck(state.deck.id); await loadRun(result.id); notice('');
  } catch (e) { notice(e.message); }
  finally { state.busy = false; updateControls(); }
});
$('resume').addEventListener('click', async () => {
  state.busy = true; updateControls();
  try { const r = await api(`/api/runs/${state.run.id}/resume`, {method: 'POST', body: '{}'}); await loadStatus(); await loadRun(r.id); notice(''); }
  catch (e) { notice(e.message); }
  finally { state.busy = false; updateControls(); }
});
$('stop').addEventListener('click', async () => {
  try { const r = await api(`/api/runs/${state.run.id}/cancel`, {method: 'POST', body: '{}'}); notice(r.message); }
  catch (e) { notice(e.message); }
});
$('connection').addEventListener('click', () => loadStatus().catch(e => notice(e.message)));
let pollBusy = false;
async function poll() {
  if (pollBusy) return;
  pollBusy = true;
  try {
    if (state.run && ['running', 'queued'].includes(state.run.status)) await loadRun(state.run.id);
    if (state.status?.importing) { await loadStatus(); await loadDecks(); }
    else if (state.status?.active_run && state.run && !['running', 'queued'].includes(state.run.status)) await loadStatus();
  } catch (e) { notice(e.message); }
  finally { pollBusy = false; }
}
async function init() {
  try { await loadStatus(); await loadDecks(); }
  catch (e) { notice(e.message); }
  setInterval(poll, 3500);
}
init();
