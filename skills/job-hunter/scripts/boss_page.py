"""Generate bounded BOSS DOM observations and one-step UI actions.

This module has no browser transport. The caller evaluates the returned IIFE and
parses its JSON string. Selectors are historical hints, never proof of a live
layout: missing/ambiguous controls fail closed. The caller owns authorization,
pending records, fresh review, receipt reconciliation, and any later retries.
"""
from __future__ import annotations

import json
import re


_DOM = r"""
const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const visible = el => {
  if (!el || !el.isConnected || !el.getClientRects().length) return false;
  for (let p = el; p && p.nodeType === 1; p = p.parentElement) {
    const style = getComputedStyle(p);
    if (p.hidden || p.getAttribute('aria-hidden') === 'true' ||
        style.display === 'none' || style.visibility === 'hidden' ||
        style.visibility === 'collapse' || style.opacity === '0') return false;
  }
  return true;
};
const text = el => el ? (el.innerText || '').trim() : '';
const flat = value => value.replace(/\s+/g, ' ').trim();
const unique = items => items.length === 1 ? items[0] : null;
const shown = (selector, root = document) => all(selector, root).filter(visible);
const enabled = el => visible(el) && !el.disabled &&
  el.getAttribute('aria-disabled') !== 'true' && !el.classList.contains('disabled');
const experienceLabel = value => /^(?:应届生|在校\/应届|在校生|1年以内|经验不限|不限|\d+[-–]\d+年|\d+年以上)$/.test(value);
const degreeLabel = value => /^(?:不限|学历不限|初中及以下|高中|中专\/中技|大专|本科|硕士|博士)$/.test(value);
const currentFilter = el => {
  const name = text(el);
  if (['工作经验', '经验'].includes(name)) return 'experience';
  if (['学历要求', '学历'].includes(name)) return 'degree';
  const root = el.closest('.condition-filter-select');
  const owners = root ? ['experience', 'degree'].filter(kind =>
    all(kind === 'experience' ? 'li[ka^="sel-job-rec-exp-"]' : 'li[ka^="sel-job-rec-degree-"]', root).length) : [];
  const kind = unique(owners);
  return kind && (kind === 'experience' ? experienceLabel(name) : degreeLabel(name)) ? kind : null;
};
const selectedState = el => {
  const states = [el.getAttribute('aria-selected'), el.getAttribute('aria-checked')];
  if (states.includes('false')) return false;
  return states.includes('true') || el.classList.contains('active');
};
const currentURL = new URL(location.href);
const bossOrigin = url => url.protocol === 'https:' &&
  ['www.zhipin.com', 'zhipin.com'].includes(url.hostname) && !url.username && !url.password;
const onBoss = bossOrigin(currentURL);
const safeURL = url => url.origin + url.pathname;
const jobURL = href => {
  try {
    const url = new URL(href, location.href);
    const match = /^\/job_detail\/([A-Za-z0-9_-]+)\.html$/.exec(url.pathname);
    return bossOrigin(url) && match ? {key: 'boss:' + match[1], url: safeURL(url)} : null;
  } catch (_) { return null; }
};
const cardRows = () => shown('.job-card-wrap').map(el => {
  const link = unique(shown('a.job-name[href]', el));
  const identity = link && jobURL(link.getAttribute('href'));
  if (!identity) return null;
  const value = text(el), lines = value.split(/\n+/).map(s => s.trim()).filter(Boolean);
  const exps = lines.filter(experienceLabel);
  const location = unique(shown('.job-card-footer .company-location', el));
  const cityMatch = location ? /^([\u4e00-\u9fff]{2,12})(?:·|$)/.exec(text(location)) : null;
  const namedCompany = unique(shown('.company-name, .job-card-footer a.boss-info span.boss-name', el));
  // The six-line card shape is present in the supplied captured DOM text.
  // Do not assume a company position if that complete shape no longer matches.
  const compactCompany = lines.length === 6 && experienceLabel(lines[2]) &&
    /^(?:本科|大专|硕士|博士|学历不限|高中|中专\/中技)$/.test(lines[3]) &&
    /^[^·\s]{2,12}·/.test(lines[5]) ? lines[4] : null;
  const company = namedCompany ? text(namedCompany) : compactCompany;
  return {el, link, company, jobTitle: text(link), key: identity.key,
    data: {...identity, text: value, company, city: cityMatch ? cityMatch[1] : null,
      experience: exps.length === 1 ? exps[0] : null,
      publisherType: shown('img[alt="猎头"]', el).length || /猎头顾问/.test(value) ? 'headhunter' : 'unknown'}};
}).filter(Boolean);
const detailRow = cards => {
  const panel = unique(shown('.job-detail-container'));
  if (!panel) return null;
  const value = text(panel);
  const active = unique(cards.filter(c => c.el.classList.contains('active')));
  let key = null;
  const linkedKeys = [...new Set(shown('a[href]', panel).map(el => jobURL(el.getAttribute('href'))?.key).filter(Boolean))];
  const ownKey = jobURL(location.href)?.key;
  const sameDescription = active ? cards.filter(c => c.company === active.company &&
    c.jobTitle === active.jobTitle) : [];
  // A stale panel with a new active card is not that card's detail. Require
  // the visible title AND company, and reject conflicting embedded job links.
  if (active && active.company && value.split(/\n/)[0].trim() === active.jobTitle &&
      value.includes(active.company) && ((linkedKeys.length === 1 && linkedKeys[0] === active.key) ||
        (!linkedKeys.length && sameDescription.length === 1))) key = active.key;
  else if (!active && ownKey && linkedKeys.length === 1 && linkedKeys[0] === ownKey) key = ownKey;
  const button = unique(shown('.job-detail-op .op-btn-chat', panel));
  return {el: panel, button, data: {key, text: value, button: button ? text(button) : null}};
};
const controlRows = () => {
  const rows = [];
  const add = (el, kind, label, filter = null, identity = '') => {
    if (!enabled(el) || !label) return;
    const id = [kind, filter || '', identity, label].map(encodeURIComponent).join(':');
    rows.push({el, data: {id, kind, label, ...(filter ? {filter} : {})}});
  };
  const inputs = shown('input[placeholder="搜索职位、公司"]')
    .filter(el => !el.readOnly && ['', 'text', 'search'].includes(el.type));
  for (const input of inputs) {
    add(input, 'keyword', input.getAttribute('placeholder'));
    const form = input.closest('form, .search-box, .search-form');
    if (form) for (const el of shown('button, a, [role="button"]', form))
      if (['搜索', '搜索职位'].includes(text(el))) add(el, 'search', text(el));
  }
  for (const el of shown('a.search-btn[ka="job_search_btn_click"]')) {
    if (['搜索', '搜索职位'].includes(text(el)) && !rows.some(row => row.el === el))
      add(el, 'search', text(el));
  }
  for (const el of shown('div.city-label[ka="switch_city_dialog_open"]')) {
    if (unique(shown('span.cur-city-label', el))) add(el, 'filter-menu', '城市', 'city');
  }
  for (const el of shown('ul.city-list-hot > li')) {
    const label = text(el);
    if (/^[\u4e00-\u9fff]{2,12}$/.test(label)) add(el, 'filter-option', label, 'city', 'city-list-hot');
  }
  // These exact div triggers were observed accepting a single page click.
  // Unknown placeholder spans remain unsupported as action targets.
  for (const el of shown('.condition-filter-select .current-select')) {
    const filter = currentFilter(el);
    if (filter) add(el, 'filter-menu', filter === 'degree' ? '学历要求' : '工作经验', filter);
  }
  for (const el of shown('button, [role="button"]')) {
    const name = el.getAttribute('aria-label') || text(el);
    if (el.getAttribute('aria-haspopup') && ['工作经验', '经验', '城市', '工作地点'].includes(name))
      add(el, 'filter-menu', name, ['城市', '工作地点'].includes(name) ? 'city' : 'experience');
  }
  for (const el of shown('li[ka^="sel-job-rec-exp-"]')) {
    const label = text(el);
    if (experienceLabel(label)) add(el, 'filter-option', label, 'experience', el.getAttribute('ka'));
  }
  for (const el of shown('li[ka^="sel-job-rec-degree-"]')) {
    const label = text(el);
    if (degreeLabel(label)) add(el, 'filter-option', label, 'degree', el.getAttribute('ka'));
  }
  // Accessible listboxes provide explicit filter ownership without guessing
  // the nesting or actions of an unknown city picker.
  for (const box of shown('[role="listbox"]')) {
    const name = box.getAttribute('aria-label');
    const filter = ['城市', '工作地点'].includes(name) ? 'city' :
      ['工作经验', '经验'].includes(name) ? 'experience' : null;
    if (!filter) continue;
    for (const option of shown('[role="option"]', box)) {
      if (filter === 'experience' && !experienceLabel(text(option))) continue;
      if (!option.matches('li[ka^="sel-job-rec-exp-"]'))
        add(option, 'filter-option', text(option), filter, name);
    }
  }
  // Duplicate IDs are deliberately unusable, never resolved by position.
  return rows.filter(r => rows.filter(other => other.data.id === r.data.id).length === 1);
};
const selectedExperience = () => {
  const selected = all('li[ka^="sel-job-rec-exp-"]').filter(selectedState);
  for (const box of all('[role="listbox"]')) {
    if (['工作经验', '经验'].includes(box.getAttribute('aria-label')))
      selected.push(...all('[role="option"]', box).filter(selectedState));
  }
  // Option text may be retained in a closed dropdown; its explicit selected
  // DOM state is evidence, its mere presence in that dropdown is not.
  return [...new Set(selected.map(el => (el.textContent || '').trim()).filter(experienceLabel))];
};
const selectedDegree = () => {
  const options = shown('li[ka^="sel-job-rec-degree-"]').filter(selectedState);
  const triggers = shown('.condition-filter-select .current-select').filter(el => currentFilter(el) === 'degree');
  if (options.length > 1 || triggers.length > 1) return [];
  const values = options.map(text).filter(degreeLabel);
  if (triggers.length && degreeLabel(text(triggers[0]))) values.push(text(triggers[0]));
  const distinct = [...new Set(values)];
  // This picker is single-choice; conflicting visible evidence stays unknown.
  return distinct.length === 1 ? distinct : [];
};
const listRoot = () => unique(shown('.job-list-container'));
const scrollTarget = () => {
  const root = listRoot();
  if (!root) return null;
  for (let el = root; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(el).overflowY) && el.clientHeight > 0 && el.scrollHeight > el.clientHeight)
      return el;
  }
  const el = document.scrollingElement;
  return el && el.clientHeight > 0 && el.scrollHeight > el.clientHeight ? el : null;
};
const accountLabel = () => {
  const labels = shown('.nav-figure .label, .nav-figure .username, .nav-figure .user-name, .nav-figure a[ka="header-username"] .label-text')
    .map(text).filter(s => s && !/登录|注册|消息|简历/.test(s));
  return unique([...new Set(labels)]);
};
const loading = () => {
  const root = listRoot();
  return !!(root && (root.getAttribute('aria-busy') === 'true' ||
    shown('[aria-busy="true"], .loading, .loading-text', root).some(el =>
      el.getAttribute('aria-busy') === 'true' || /加载/.test(text(el))))) ||
    /^加载中[，,]?\s*请稍候[.。…]*$/.test(text(document.body));
};
const endOfList = () => {
  const root = listRoot();
  return !!root && !loading() && shown('*', root).some(el => !el.closest('.job-card-wrap') &&
    /^(?:没有更多了|没有更多职位了|已加载全部职位|暂无符合条件的职位|没有找到相关职位)[。！!]*$/.test(text(el)));
};
const unsupported = reason => ({status: 'unsupported', operation, reason});
"""


def observation_script() -> str:
    """Return a DOM-only JSON observation; null/empty facts remain unknown."""
    return "(() => {\n" + _DOM + r"""
const cards = onBoss ? cardRows() : [];
const detail = onBoss ? detailRow(cards) : null;
const controls = onBoss ? controlRows() : [];
const keyword = unique(controls.filter(c => c.data.kind === 'keyword'));
const city = onBoss ? unique(shown('.cur-city-label')) : null;
const scroller = onBoss ? scrollTarget() : null;
const tail = cards.length ? cards[cards.length - 1].el : null;
const viewportBottom = scroller === document.scrollingElement ? innerHeight :
  (scroller ? Math.min(innerHeight, scroller.getBoundingClientRect().bottom) : 0);
const listTailBelowViewport = !!(scroller && tail &&
  scroller.scrollTop + scroller.clientHeight < scroller.scrollHeight - 1 &&
  tail.getBoundingClientRect().bottom > viewportBottom + 1);
return JSON.stringify({url: safeURL(currentURL), title: document.title,
  body: text(document.body), accountLabel: onBoss ? accountLabel() : null,
  filters: {city: city ? text(city) : null, keyword: keyword ? keyword.el.value : null,
    experience: onBoss ? selectedExperience() : [], degree: onBoss ? selectedDegree() : []},
  cards: cards.map(c => c.data), detail: detail ? detail.data : null,
  controls: controls.map(c => c.data), scrollable: onBoss && !!scrollTarget(),
  endOfList: onBoss && endOfList(), loading: onBoss && loading(), listTailBelowViewport});
})()"""


def action_script(operation: str, args: dict | None = None) -> str:
    """Generate exactly one eligible click, input fill, or container scroll.

    Unsupported operations/argument shapes raise ValueError before evaluation.
    A DOM mismatch returns ``unsupported`` without changing the page. Filling
    emits one native input event; it never clicks Search or sends Enter.
    """
    allowed = {"control": {"id", "value"}, "open-detail": {"key"},
               "scroll": set(), "submit": {"key"}, "dismiss-receipt": set()}
    if operation not in allowed or not isinstance(args if args is not None else {}, dict):
        raise ValueError("unsupported operation or argument type")
    args = {} if args is None else args
    if set(args) - allowed[operation]:
        raise ValueError("unsupported argument; selectors and executable code are not accepted")
    if operation == "control" and (not isinstance(args.get("id"), str) or not args["id"]):
        raise ValueError("control requires an observed id")
    if "value" in args and not isinstance(args["value"], str):
        raise ValueError("control value must be a string")
    if operation in {"open-detail", "submit"} and not re.fullmatch(r"boss:[A-Za-z0-9_-]+", str(args.get("key", ""))):
        raise ValueError("a stable boss job key is required")
    payload = json.dumps({"operation": operation, "args": args}, ensure_ascii=True)
    return "(() => {\nconst {operation, args} = " + payload + ";\n" + _DOM + r"""
const run = () => {
  if (!onBoss) return unsupported('not-boss-origin');
  if (!/^\/web\/geek\/jobs\/?$/.test(currentURL.pathname) && !jobURL(location.href))
    return unsupported('not-job-page');
  if (operation === 'control') {
    const control = unique(controlRows().filter(row => row.data.id === args.id));
    if (!control) return unsupported('control-missing-ambiguous-or-unsupported');
    if (control.data.kind === 'keyword') {
      if (!Object.prototype.hasOwnProperty.call(args, 'value')) return unsupported('keyword-value-required');
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      if (!setter || !enabled(control.el) || control.el.readOnly) return unsupported('input-unavailable');
      setter.call(control.el, args.value);
      control.el.dispatchEvent(new Event('input', {bubbles: true}));
      return {status: 'filled', operation, id: args.id};
    }
    if (Object.prototype.hasOwnProperty.call(args, 'value')) return unsupported('value-only-for-keyword');
    control.el.click();
    return {status: 'clicked', operation, id: args.id};
  }
  if (operation === 'open-detail') {
    const card = unique(cardRows().filter(row => row.key === args.key));
    if (!card || !enabled(card.link)) return unsupported('job-card-missing-or-ambiguous');
    card.link.click();
    return {status: 'clicked', operation, key: args.key};
  }
  if (operation === 'scroll') {
    const target = scrollTarget();
    if (!target || loading()) return unsupported('list-not-scrollable-or-loading');
    target.scrollBy({top: target.clientHeight, behavior: 'instant'});
    return {status: 'scrolled', operation};
  }
  if (operation === 'submit') {
    const cards = cardRows(), detail = detailRow(cards);
    if (!detail || detail.data.key !== args.key) return unsupported('detail-identity-not-proven');
    if (loading() || /登录查看完整内容|登录后查看|验证后继续/.test(detail.data.text))
      return unsupported('detail-incomplete-or-loading');
    if (!detail.button || !enabled(detail.button) || text(detail.button) !== '立即沟通')
      return unsupported('immediate-chat-button-unavailable');
    // Existing receipts/dialogs must be observed and reconciled separately.
    if (/已向BOSS发送消息/.test(text(document.body)) ||
        shown('[role="dialog"][aria-modal="true"]').length) return unsupported('dialog-or-receipt-present');
    detail.button.click();
    return {status: 'clicked', operation, key: args.key};
  }
  if (operation === 'dismiss-receipt') {
    if (!/已向BOSS发送消息/.test(text(document.body))) return unsupported('receipt-not-present');
    const button = unique(shown('a.default-btn.cancel-btn').filter(el => {
      const dialog = el.closest('[role="dialog"], .dialog-container, .dialog-wrap, .dialog, .greet-boss-container');
      return text(el) === '留在此页' && dialog && /已向BOSS发送消息/.test(text(dialog));
    }));
    if (!button || !enabled(button)) return unsupported('receipt-dismiss-missing-or-ambiguous');
    button.click();
    return {status: 'clicked', operation};
  }
  return unsupported('unsupported-operation');
};
return JSON.stringify(run());
})()"""
