/* A separate in-memory edit set per restaurant; all writes are explicit. */
'use strict';
const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="menu-csrf"]').content;
const states = new Map();
let restaurant = $('restaurant').value, busy = false, generation = 0;
const state = () => states.get(restaurant);
const dirty = () => !!state()?.updates.size;
function message(text, error = false) { $('message').textContent = text; $('message').className = error ? 'error' : ''; }
function controls() {
  $('save').disabled = busy || !dirty(); $('validate').disabled = busy || !state();
  $('restaurant').disabled = busy; $('initialise').disabled = busy;
  $('save-state').textContent = busy ? 'Working…' : !state() ? 'No draft loaded' : dirty() ? 'Unsaved changes' : 'Draft saved';
  $('products').querySelectorAll('input,textarea').forEach(input => { input.disabled = busy; });
}
async function api(action, method = 'GET', body) {
  const res = await fetch(`/admin/menu-api/${encodeURIComponent(restaurant)}/${action}`, {
    method, headers: { 'Content-Type': 'application/json', 'X-Menu-CSRF': csrf },
    ...(body ? { body: JSON.stringify(body) } : {})
  });
  const data = await res.json();
  if (!res.ok) { const err = new Error(data.error || 'Request failed.'); err.status = res.status; throw err; }
  return data;
}
function summary(v, provisional = false) {
  $('total').textContent = v.item_count; $('missing').textContent = v.missing_prices;
  $('warnings').textContent = v.missing_descriptions;
  $('ready').textContent = provisional ? 'Check changes' : v.ready_to_publish ? 'Ready to publish' : 'Needs review';
  $('issues').replaceChildren();
  if (v.extraction_review_issue_count) {
    const note = document.createElement('p');
    note.textContent = `${v.extraction_review_issue_count} extraction review issues remain recorded. Choices, relationships and source evidence are preserved; they require separate review.`;
    $('issues').append(note);
  }
  if (v.blocking_issues.length && !provisional) {
    const intro = document.createElement('p'); intro.textContent = `${v.blocking_issue_count} issue(s) to review before publishing:`;
    const list = document.createElement('ul');
    v.blocking_issues.forEach(issue => { const li = document.createElement('li'); li.textContent = `${issue.item || issue.category || 'Menu'}: ${issue.message || issue.reason || issue.type.replaceAll('_', ' ')}`; list.append(li); });
    $('issues').append(intro, list);
  }
}
function field(labelText, fieldName, item, category, card) {
  const label = document.createElement('label'); label.textContent = labelText;
  const input = document.createElement(fieldName === 'description' ? 'textarea' : 'input');
  if (fieldName === 'price') { input.type = 'text'; input.inputMode = 'decimal'; input.placeholder = 'Missing price'; }
  else { input.maxLength = fieldName === 'name' ? 300 : 5000; }
  input.value = fieldName === 'price' && typeof item.price === 'number' && Number.isFinite(item.price) ? item.price.toFixed(2) : item[fieldName] ?? ''; label.append(input);
  const hint = document.createElement('span'); hint.className = 'hint';
  const updateHint = () => { hint.textContent = fieldName === 'price' && item.price == null ? 'Price required before publishing' : fieldName === 'description' && !item.description?.trim() ? 'Optional: description missing' : ''; };
  updateHint(); label.append(hint);
  if (fieldName === 'description') label.className = 'description';
  input.addEventListener('input', () => {
    item[fieldName] = fieldName === 'price' && input.value === '' ? null : input.value;
    updateHint();
    const key = `${category.key}:${item.id}`;
    state().updates.set(key, {...state().updates.get(key), category: category.key, item_id: item.id, [fieldName]: item[fieldName]});
    card.classList.toggle('missing', item.price === null);
    const validPrice = item.price === null || /^\d+(\.\d{1,2})?$/.test(String(item.price));
    card.classList.toggle('invalid', !validPrice || !item.name.trim());
    input.setCustomValidity(fieldName === 'price' && !validPrice ? 'Enter a non-negative GBP price, such as 6.99.' : fieldName === 'name' && !item.name.trim() ? 'Product name is required.' : '');
    const items = state().data.categories.flatMap(c => c.items);
    summary({...state().data.validation, missing_prices: items.filter(i => i.price == null).length, missing_descriptions: items.filter(i => !i.description?.trim()).length}, true);
    controls();
  });
  return label;
}
function render() {
  const data = state().data; $('workspace').hidden = false; $('empty').hidden = true;
  $('restaurant-name').textContent = data.restaurant || $('restaurant').selectedOptions[0].textContent;
  $('currency-notice').hidden = data.currency === 'GBP';
  $('categories').replaceChildren(); $('products').replaceChildren();
  summary(data.validation, dirty());
  data.categories.forEach((category, index) => {
    const link = document.createElement('a'); link.href = `#category-${index}`; link.textContent = `${category.display_name ?? category.name} (${category.items.length})`; $('categories').append(link);
    const section = document.createElement('section'); section.className = 'category'; section.id = `category-${index}`;
    const heading = document.createElement('h3'); heading.textContent = category.display_name ?? category.name; section.append(heading);
    category.items.forEach(item => {
      const card = document.createElement('article'); card.className = 'product'; card.classList.toggle('missing', item.price == null);
      card.classList.toggle('invalid', data.validation.blocking_issues.some(i => i.item_id === item.id && i.type !== 'missing_price'));
      card.append(field('Product name', 'name', item, category, card), field('Price (£ GBP)', 'price', item, category, card), field('Description', 'description', item, category, card));
      section.append(card);
    });
    $('products').append(section);
  });
  controls();
}
async function load() {
  const requestGeneration = ++generation;
  $('workspace').hidden = true; $('empty').hidden = true; message(''); busy = true; controls();
  try {
    if (!state()) states.set(restaurant, {data: await api('draft'), updates: new Map()});
    if (requestGeneration === generation) render();
  } catch (err) { if (err.status === 404 && err.message.startsWith('No draft')) $('empty').hidden = false; else message(err.message, true); }
  finally { busy = false; controls(); }
}
$('restaurant').addEventListener('change', async () => {
  const next = $('restaurant').value;
  if (dirty()) {
    const dialog = $('switch-dialog'); dialog.returnValue = 'cancel'; dialog.showModal();
    await new Promise(resolve => dialog.addEventListener('close', resolve, {once: true}));
    if (dialog.returnValue !== 'switch') { $('restaurant').value = restaurant; return; }
  }
  restaurant = next; load();
});
$('initialise').addEventListener('click', async () => {
  busy = true; controls();
  try { states.set(restaurant, {data: await api('initialise', 'POST'), updates: new Map()}); render(); message('Draft initialised. Review your products below.'); }
  catch (err) { message(err.message, true); } finally { busy = false; controls(); }
});
async function submit(save) {
  for (const input of $('products').querySelectorAll('input,textarea')) if (!input.reportValidity()) return;
  busy = true; controls();
  try {
    const data = await api(save ? 'draft' : 'validate', save ? 'PUT' : 'POST', {revision: state().data.revision, updates: [...state().updates.values()]});
    if (save) { states.set(restaurant, {data, updates: new Map()}); render(); message('Draft saved successfully.'); }
    else { summary(data.validation); message(data.validation.ready_to_publish ? 'Menu is ready to publish. Publishing is not available in v1.' : 'Review the highlighted issues before publishing.'); }
  } catch (err) { message(err.message, true); } finally { busy = false; controls(); }
}
$('save').addEventListener('click', () => submit(true)); $('validate').addEventListener('click', () => submit(false));
window.addEventListener('beforeunload', event => { if ([...states.values()].some(s => s.updates.size)) { event.preventDefault(); event.returnValue = ''; } });
load();
