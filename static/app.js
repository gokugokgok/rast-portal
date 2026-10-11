'use strict';
const output = document.querySelector('#results');
const search = document.querySelector('#search');
const queryInput = document.querySelector('#q');
const categoryInput = document.querySelector('#category');
const safeInput = document.querySelector('#safesearch');
const settings = document.querySelector('#settings');
let pending = null;
let wikiPending = null;
let lastOverview = null;
let currentQuery = queryInput.value;
document.documentElement.classList.add('js-enabled');
async function loadOverview(query) {
  const panel = output.querySelector('#wiki-content');
  if (!panel) return;
  if (lastOverview && lastOverview.query === query) {
    panel.innerHTML = lastOverview.markup;
    return;
  }
  const controller = new AbortController();
  wikiPending = controller;
  const data = new FormData(); data.set('q', query);
  try {
    const response = await fetch('/overview', {method: 'POST', body: data,
      cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer', signal: controller.signal});
    const markup = await response.text();
    if (wikiPending !== controller || currentQuery !== query || !panel.isConnected) return;
    panel.innerHTML = markup;
    if (response.ok) lastOverview = {query, markup};
  } catch (error) {
    if (error.name !== 'AbortError' && wikiPending === controller && panel.isConnected) {
      panel.querySelector('.wiki-loading').textContent = '概要を取得できませんでした。';
    }
  } finally {
    if (wikiPending === controller) wikiPending = null;
  }
}
function selectCategory(category) {
  categoryInput.value = category;
  document.querySelectorAll('.home-chip').forEach(button => {
    const active = button.dataset.category === category;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}
selectCategory(categoryInput.value);
async function submit(form) {
  if (pending) pending.abort();
  if (wikiPending) wikiPending.abort();
  wikiPending = null;
  const controller = new AbortController();
  pending = controller;
  const data = new FormData(form);
  if (form === search) data.set('category', categoryInput.value);
  const query = String(data.get('q') || '').trim();
  if (!query) { pending = null; queryInput.focus(); return; }
  if (lastOverview && lastOverview.query !== query) lastOverview = null;
  currentQuery = query;
  queryInput.value = query;
  selectCategory(String(data.get('category') || 'all'));
  safeInput.value = String(data.get('safesearch') || '1');
  settings.open = false;
  document.body.classList.add('has-results');
  output.setAttribute('aria-busy', 'true');
  search.classList.add('loading');
  try {
    const response = await fetch('/', {method: 'POST', body: data,
      headers: {'X-Portal-Request': '1'}, cache: 'no-store', credentials: 'omit',
      referrerPolicy: 'no-referrer', signal: controller.signal});
    const markup = await response.text();
    if (pending !== controller) return;
    // All text/attributes are escaped server-side. Only this site's fragments.
    output.innerHTML = markup;
    loadOverview(query);
    queryInput.blur();
    output.querySelectorAll('img').forEach(img => {
      img.addEventListener('error', () => { img.hidden = true; img.parentElement.classList.add('image-unavailable'); });
    });
    window.scrollTo({top: 0, behavior: 'instant'});
  } catch (error) {
    if (error.name !== 'AbortError' && pending === controller) output.textContent = '接続に失敗しました。もう一度お試しください。';
  } finally {
    if (pending === controller) {
      pending = null; output.setAttribute('aria-busy', 'false'); search.classList.remove('loading');
    }
  }
}
document.addEventListener('submit', event => {
  if (event.target.matches('#search, .page-form, .tab-form')) {
    event.preventDefault(); submit(event.target);
  }
});
document.querySelectorAll('.home-chip').forEach(button => button.addEventListener('click', () => {
  selectCategory(button.dataset.category);
  if (queryInput.value.trim()) submit(search); else queryInput.focus();
}));
safeInput.addEventListener('change', () => {
  if (currentQuery) {
    queryInput.value = currentQuery;
    submit(search);
  }
});
document.querySelector('#theme').addEventListener('change', event => {
  document.documentElement.dataset.theme = event.target.value;
});
document.querySelector('#input-clear').addEventListener('click', () => { queryInput.value = ''; queryInput.focus(); });
function clearScreen() {
  if (pending) pending.abort();
  if (wikiPending) wikiPending.abort();
  wikiPending = null;
  lastOverview = null;
  pending = null;
  currentQuery = '';
  queryInput.value = '';
  queryInput.defaultValue = '';
  queryInput.removeAttribute('value');
  selectCategory('all');
  output.replaceChildren();
  output.setAttribute('aria-busy', 'false');
  search.classList.remove('loading');
  document.body.classList.remove('has-results');
  document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
  const img = document.querySelector('#preview-image');
  img.removeAttribute('src'); img.alt = ''; img.dataset.fallback = '';
  document.querySelector('#preview-title').textContent = '画像';
  document.querySelector('#preview-host').textContent = '';
  document.querySelector('#preview-link').removeAttribute('href');
}
document.querySelector('#clear').addEventListener('click', clearScreen);
document.querySelector('#privacy-open').addEventListener('click', () => document.querySelector('#privacy-dialog').showModal());
document.querySelectorAll('.close-dialog').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
document.addEventListener('click', event => {
  const button = event.target.closest('.image-open');
  if (button) {
    const dialog = document.querySelector('#image-dialog');
    const img = document.querySelector('#preview-image');
    img.hidden = false;
    img.parentElement.classList.remove('image-unavailable');
    img.dataset.fallback = button.dataset.fallback;
    img.src = button.dataset.preview; img.alt = button.dataset.title;
    document.querySelector('#preview-title').textContent = button.dataset.title;
    document.querySelector('#preview-host').textContent = button.dataset.host;
    document.querySelector('#preview-link').href = button.dataset.url;
    dialog.showModal();
  }
  if (!settings.contains(event.target)) settings.open = false;
});
document.querySelector('#preview-image').addEventListener('error', event => {
  const img = event.target;
  const fallback = img.dataset.fallback;
  if (fallback && new URL(fallback, location.href).href !== img.src) {
    img.dataset.fallback = '';
    img.src = fallback;
  } else {
    img.hidden = true;
    img.parentElement.classList.add('image-unavailable');
  }
});
document.querySelectorAll('dialog').forEach(dialog => {
  dialog.addEventListener('click', event => { if (event.target === dialog) {
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
  }});
});
document.addEventListener('keydown', event => {
  if (event.key === '/' && !event.target.matches('input,select,textarea') && !document.querySelector('dialog[open]')) {
    event.preventDefault(); queryInput.focus();
  }
});
window.addEventListener('pagehide', clearScreen);
window.addEventListener('pageshow', event => { if (event.persisted) clearScreen(); });
if (currentQuery) loadOverview(currentQuery);
