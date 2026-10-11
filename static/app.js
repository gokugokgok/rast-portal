'use strict';
const output = document.querySelector('#results');
const search = document.querySelector('#search');
const queryInput = document.querySelector('#q');
const categoryInput = document.querySelector('#category');
const settings = document.querySelector('#settings');
const searchOptions = ['safesearch', 'per_page', 'region', 'mode', 'match'].map(name => document.querySelector(`[name="${name}"][form="search"]`));
let pending = null;
let fillPending = null;
let sequence = 0;
let wikiPending = null;
let lastOverview = null;
let currentQuery = queryInput.value;
let mediaController = new AbortController();
let previewController = null;
let imageObserver = null;
let iconObserver = null;
const assets = new Map();
const imageJobs = [];
let imageActive = 0;
const iconJobs = [];
let iconActive = 0;
const pageCache = new Map();
function resetMedia() {
  mediaController.abort(); mediaController = new AbortController();
  if (previewController) previewController.abort();
  if (imageObserver) imageObserver.disconnect();
  imageObserver = null;
  if (iconObserver) iconObserver.disconnect();
  iconObserver = null;
  imageJobs.length = 0;
  iconJobs.length = 0;
  assets.forEach(entry => { if (entry.url) URL.revokeObjectURL(entry.url); });
  assets.clear();
  const dialog = document.querySelector('#image-dialog');
  if (dialog.open) dialog.close();
  document.querySelector('#preview-image').removeAttribute('src');
  document.querySelector('#preview-status').textContent = '';
}
function imageAsset(src, signal) {
  const existing = assets.get(src);
  if (existing && (existing.url || !existing.signal.aborted)) return existing.promise;
  const entry = {url: null, signal};
  entry.promise = (async () => {
    for (let attempt = 0; attempt < 3; attempt++) {
      const response = await fetch(src, {cache: 'no-store', credentials: 'omit',
        referrerPolicy: 'no-referrer', signal});
      if ((response.status === 503 || response.status === 429) && attempt < 2) {
        await new Promise(resolve => setTimeout(resolve, 500));
        if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
        continue;
      }
      if (!response.ok) throw new Error('Image unavailable');
      const blob = await response.blob();
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
      if (!/^image\/(png|jpeg|gif|webp|avif|x-icon|vnd\.microsoft\.icon)$/.test(blob.type)) throw new Error('Unsupported image');
      entry.url = URL.createObjectURL(blob);
      return entry.url;
    }
  })().catch(error => {
    if (assets.get(src) === entry) assets.delete(src);
    throw error;
  });
  assets.set(src, entry);
  return entry.promise;
}
function pumpImages() {
  while (imageActive < 3 && imageJobs.length) {
    const {img, signal} = imageJobs.shift();
    if (signal.aborted || !img.isConnected) continue;
    imageActive++;
    imageAsset(img.dataset.src, signal).then(async src => {
      if (signal.aborted || !img.isConnected) return;
      img.src = src;
      await img.decode();
      if (signal.aborted || !img.isConnected) return;
      img.hidden = false;
      img.parentElement.classList.remove('image-unavailable');
    }).catch(error => {
      if (error.name !== 'AbortError' && !signal.aborted && img.isConnected) {
        img.hidden = true; img.parentElement.classList.add('image-unavailable');
      }
    }).finally(() => { imageActive--; pumpImages(); });
  }
}
function attachImages(root = output) {
  const signal = mediaController.signal;
  const enqueue = img => { imageJobs.push({img, signal}); pumpImages(); };
  if ('IntersectionObserver' in window && !imageObserver) {
    // Observe the visible tile, since hidden images have no intersection box.
    imageObserver = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) {
        imageObserver.unobserve(entry.target);
        const img = entry.target.querySelector('img[data-src]');
        if (img) enqueue(img);
      }
    }), {rootMargin: '300px'});
  }
  root.querySelectorAll('img[data-src]:not([data-queued])').forEach(img => {
    img.dataset.queued = 'true';
    if (imageObserver) imageObserver.observe(img.parentElement); else enqueue(img);
  });
}
function pumpIcons() {
  while (iconActive < 2 && iconJobs.length) {
    const {img, signal} = iconJobs.shift();
    if (signal.aborted || !img.isConnected) continue;
    iconActive++;
    imageAsset(img.dataset.icon, signal).then(async src => {
      if (signal.aborted || !img.isConnected) return;
      img.src = src; await img.decode();
      if (signal.aborted || !img.isConnected) return;
      img.hidden = false; img.parentElement.classList.add('has-icon');
    }).catch(() => {}).finally(() => { iconActive--; pumpIcons(); });
  }
}
function attachIcons(root = output) {
  const signal = mediaController.signal;
  if ('IntersectionObserver' in window && !iconObserver) {
    iconObserver = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) {
        iconObserver.unobserve(entry.target);
        const img = entry.target.querySelector('img[data-icon]');
        if (img) { iconJobs.push({img, signal}); pumpIcons(); }
      }
    }), {rootMargin: '200px'});
  }
  root.querySelectorAll('img[data-icon]:not([data-queued])').forEach(img => {
    img.dataset.queued = 'true';
    if (iconObserver) iconObserver.observe(img.parentElement);
    else iconJobs.push({img, signal});
  });
  pumpIcons();
}
async function openPreview(button) {
  if (previewController) previewController.abort();
  const controller = previewController = new AbortController();
  const dialog = document.querySelector('#image-dialog');
  const img = document.querySelector('#preview-image');
  const status = document.querySelector('#preview-status');
  const tile = button.querySelector('img[data-src]');
  const thumbnail = tile && tile.naturalWidth > 0 ? tile.src : null;
  let displayed = Boolean(thumbnail);
  img.hidden = !displayed;
  img.parentElement.classList.remove('image-unavailable');
  if (thumbnail) img.src = thumbnail; else img.removeAttribute('src');
  img.alt = button.dataset.title;
  document.querySelector('#preview-title').textContent = button.dataset.title;
  document.querySelector('#preview-host').textContent = button.dataset.host;
  document.querySelector('#preview-link').href = button.dataset.url;
  status.textContent = displayed ? '大きい画像を読み込み中…' : '画像を読み込み中…';
  if (!dialog.open) dialog.showModal();
  const apply = async src => {
    const probe = new Image(); probe.src = src; await probe.decode();
    if (controller.signal.aborted || !dialog.open) return false;
    img.src = src; img.hidden = false; displayed = true; return true;
  };
  try {
    const src = await imageAsset(button.dataset.preview, controller.signal);
    if (await apply(src)) status.textContent = '';
  } catch (error) {
    if (controller.signal.aborted) return;
    if (!displayed && button.dataset.fallback) {
      try {
        const src = await imageAsset(button.dataset.fallback, mediaController.signal);
        await apply(src);
      } catch (_) {}
    }
    if (controller.signal.aborted || !dialog.open) return;
    status.textContent = displayed ? 'サムネイルで表示しています。' : '画像元から取得できませんでした。掲載サイトで確認できます。';
    if (!displayed) img.parentElement.classList.add('image-unavailable');
  }
}
document.documentElement.classList.add('js-enabled');
async function loadOverview(query) {
  const panel = output.querySelector('#wiki-content');
  if (!panel) return;
  if (lastOverview && lastOverview.query === query) {
    panel.innerHTML = lastOverview.markup;
    attachIcons(panel);
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
    attachIcons(panel);
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
function parseMarkup(markup) {
  const parsed = document.createElement('template');
  parsed.innerHTML = markup;
  return parsed;
}
function rememberPage(key, markup, fragment, page) {
  if (!fragment || fragment.dataset.limited === '1' || fragment.dataset.page !== page) return;
  const assetCount = new Set([...markup.matchAll(/\/(?:media|icon)\/[A-Za-z0-9_-]{32}/g)].map(match => match[0])).size;
  pageCache.delete(key);
  while (pageCache.size && (pageCache.size >= 12 || [...pageCache.values()].reduce((sum, entry) => sum + entry.assetCount, 0) + assetCount > 400)) {
    pageCache.delete(pageCache.keys().next().value);
  }
  if (assetCount <= 400) pageCache.set(key, {markup, assetCount, expires: Date.now() + 45000});
}
async function fillResults(data, key, id) {
  const controller = new AbortController();
  fillPending = controller;
  data.set('progressive', 'fill');
  try {
    const response = await fetch('/', {method: 'POST', body: data,
      headers: {'X-Portal-Request': '1'}, cache: 'no-store', credentials: 'omit',
      referrerPolicy: 'no-referrer', signal: controller.signal});
    const markup = await response.text();
    if (sequence !== id || fillPending !== controller) return;
    const parsed = parseMarkup(markup);
    const fragment = parsed.content.querySelector('.search-state');
    const current = output.querySelector('.search-state');
    if (!response.ok || !fragment || !current) throw new Error('Additional results unavailable');
    const oldContent = current.querySelector('.results-content');
    const newContent = fragment.querySelector('.results-content');
    const focused = document.activeElement;
    const oldList = oldContent.querySelector('.web-list, .image-grid, .video-list');
    const newList = newContent.querySelector('.web-list, .image-grid, .video-list');
    const identity = card => JSON.stringify([
      card.querySelector('h2 a')?.getAttribute('href'), card.querySelector('h2')?.textContent,
      card.querySelector('img[data-src]')?.dataset.src,
      card.querySelector('.image-open')?.dataset.preview,
      card.querySelector('img[data-icon]')?.dataset.icon,
    ]);
    if (oldList && newList) {
      const oldCards = [...oldList.children], newCards = [...newList.children];
      oldCards.forEach((card, index) => {
        if (newCards[index] && identity(card) === identity(newCards[index])) newCards[index].replaceWith(card);
      });
    }
    oldContent.querySelectorAll('img[data-src]').forEach(img => { if (imageObserver) imageObserver.unobserve(img.parentElement); });
    oldContent.querySelectorAll('img[data-icon]').forEach(img => { if (iconObserver) iconObserver.unobserve(img.parentElement); });
    oldContent.replaceWith(newContent);
    if (focused && newContent.contains(focused)) focused.focus({preventScroll: true});
    Object.assign(current.dataset, fragment.dataset);
    // Keep the encyclopedia panel and already downloaded image blobs. Only
    // the result list changes; no second Wikipedia request or image reset.
    rememberPage(key, markup, fragment, String(data.get('page') || '0'));
    attachImages(newContent);
    attachIcons(newContent);
  } catch (error) {
    if (error.name !== 'AbortError' && sequence === id) {
      const status = output.querySelector('.fill-status');
      if (status) status.textContent = '追加の結果を取得できませんでした。表示済みの結果はそのまま利用できます。';
    }
  } finally {
    if (fillPending === controller) fillPending = null;
  }
}
async function submit(form) {
  const id = ++sequence;
  if (pending) pending.abort();
  if (fillPending) fillPending.abort();
  fillPending = null;
  if (wikiPending) wikiPending.abort();
  wikiPending = null;
  const controller = new AbortController();
  pending = controller;
  const data = new FormData(form);
  if (form === search) data.set('category', categoryInput.value);
  data.set('progressive', 'initial');
  const query = String(data.get('q') || '').trim();
  if (!query) { pending = null; queryInput.focus(); return; }
  resetMedia();
  if (currentQuery !== query) { lastOverview = null; pageCache.clear(); }
  currentQuery = query;
  queryInput.value = query;
  selectCategory(String(data.get('category') || 'all'));
  searchOptions.forEach(input => { if (data.has(input.name)) input.value = String(data.get(input.name)); });
  settings.open = false;
  document.body.classList.add('has-results');
  output.setAttribute('aria-busy', 'true');
  search.classList.add('loading');
  try {
    const cacheKey = JSON.stringify([data.get('category'), ...searchOptions.map(input => data.get(input.name)), data.get('page') || '0']);
    const cached = pageCache.get(cacheKey);
    let markup, ok = true;
    if (cached && cached.expires > Date.now()) markup = cached.markup;
    else {
      const response = await fetch('/', {method: 'POST', body: data,
        headers: {'X-Portal-Request': '1'}, cache: 'no-store', credentials: 'omit',
        referrerPolicy: 'no-referrer', signal: controller.signal});
      markup = await response.text();
      ok = response.ok;
    }
    if (pending !== controller) return;
    // Parse once into an inert template, then move its nodes to the page.
    const parsed = parseMarkup(markup);
    const fragment = parsed.content.querySelector('.search-state');
    if (ok) rememberPage(cacheKey, markup, fragment, String(data.get('page') || '0'));
    const completing = fragment && fragment.dataset.completing === '1';
    output.replaceChildren(parsed.content);
    loadOverview(query);
    queryInput.blur();
    attachImages();
    attachIcons();
    window.scrollTo({top: 0, behavior: 'instant'});
    if (ok && completing) fillResults(data, cacheKey, id);
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
searchOptions.forEach(input => input.addEventListener('change', () => {
  if (currentQuery) {
    queryInput.value = currentQuery;
    submit(search);
  }
}));
document.querySelector('#theme').addEventListener('change', event => {
  document.documentElement.dataset.theme = event.target.value;
});
document.querySelector('#input-clear').addEventListener('click', () => { queryInput.value = ''; queryInput.focus(); });
function clearScreen() {
  sequence++;
  if (fillPending) fillPending.abort();
  fillPending = null;
  resetMedia();
  if (pending) pending.abort();
  if (wikiPending) wikiPending.abort();
  wikiPending = null;
  lastOverview = null;
  pageCache.clear();
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
    openPreview(button);
  }
  if (!settings.contains(event.target)) settings.open = false;
});
document.querySelector('#image-dialog').addEventListener('close', () => {
  if (previewController) previewController.abort();
  const img = document.querySelector('#preview-image');
  const src = img.src;
  const usedByTile = [...output.querySelectorAll('img[data-src]')].some(tile => tile.src === src);
  if (!usedByTile) assets.forEach((entry, key) => {
    if (entry.url === src) { URL.revokeObjectURL(entry.url); assets.delete(key); }
  });
  img.removeAttribute('src');
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
attachImages();
attachIcons();
