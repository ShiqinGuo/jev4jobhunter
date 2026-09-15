"""Execute generated JS against offline rendered DOM, without visiting BOSS."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('boss_page', ROOT / 'skills/job-hunter/scripts/boss_page.py')
boss_page = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boss_page)

HARNESS = r"""
const fs = require('fs');
const {chromium} = require(process.argv[1]);
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext({offline: true});
    await context.route('**/*', route => route.abort());
    const page = await context.newPage();
    const network = [];
    page.on('request', req => network.push(req.url()));
    await page.setContent(request.html);
    await page.evaluate(() => {
      window.fixtureCalls = [];
      document.addEventListener('click', event => {
        event.preventDefault();
        window.fixtureCalls.push({kind: 'click', id: event.target.id});
      }, true);
      document.addEventListener('input', event =>
        window.fixtureCalls.push({kind: 'input', id: event.target.id, value: event.target.value}), true);
      const original = Element.prototype.scrollBy;
      Element.prototype.scrollBy = function(options) {
        window.fixtureCalls.push({kind: 'scroll', id: this.id, top: options.top});
        return original.call(this, options);
      };
    });
    if (request.before) await page.evaluate(request.before);
    const values = [];
    for (const source of request.scripts) {
      // Only the location argument is substituted; queries/layout/events use a
      // real offline Chromium DOM. No navigation or BOSS connection takes place.
      values.push(await page.evaluate(({source, href}) =>
        JSON.parse(new Function('location', 'return (' + source + ')')({href})),
        {source, href: request.url || 'https://www.zhipin.com/web/geek/jobs?securityId=do-not-export'}));
    }
    const calls = await page.evaluate(() => window.fixtureCalls);
    process.stdout.write(JSON.stringify({values, calls, network}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
"""


def fixture(extra='', company='示例科技', title='Python工程师'):
    return f'''<!doctype html><html><head><title>离线岗位页</title><style>
    .job-list-container {{height: 110px; overflow-y: auto; width: 350px;}}
    .job-card-wrap {{height: 190px;}} .job-detail-container {{height: 180px; overflow:auto;}}
    .hidden {{display:none;}}</style></head><body>
    <header><div class="nav-figure"><a class="label">测试候选人</a></div></header>
    <div class="search-box"><span class="cur-city-label">杭州</span>
      <input id="keyword" placeholder="搜索职位、公司" value="Python">
      <button id="search">搜索</button></div>
    <button id="experience-menu" aria-label="工作经验" aria-haspopup="listbox">工作经验</button>
    <ul role="listbox" aria-label="工作经验">
      <li id="exp-grad" ka="sel-job-rec-exp-102" class="active">应届生</li>
      <li id="exp-under" ka="sel-job-rec-exp-103">1年以内</li>
      <li id="exp-one" ka="sel-job-rec-exp-104" class="active">1-3年</li>
      <li ka="sel-job-rec-exp-105">3-5年</li></ul>
    <div class="job-list-container" id="jobs"><div class="job-card-wrap active" id="card-one">
      <a id="job-one" class="job-name" href="/job_detail/job_one.html?securityId=secret">{title}</a>
      <div>13-18K</div><div>1-3年</div><div>本科</div>
      <div class="company-name">{company}</div><div class="job-card-footer"><span class="company-location">杭州·西湖区</span></div></div>
      <div class="job-card-wrap" id="card-two"><a id="job-two" class="job-name" href="/job_detail/job_two.html">Django研发</a>
      <div>15-20K</div><div>1年以内</div><div>本科</div><div class="company-name">另一家公司</div>
      <div class="job-card-footer"><span class="company-location">杭州·滨江区</span></div><img alt="猎头"></div></div>
    <div class="job-detail-container" id="detail">{title}<div>13-18K</div><div>杭州</div>
      <div>{company} · 招聘者</div><div>职位描述</div><p>业务接口与数据库开发。</p>
      <div class="job-detail-op"><a id="submit" class="op-btn-chat">立即沟通</a></div></div>
    <div class="hidden">hidden-sensitive-marker</div><script type="application/json">hidden-script-marker</script>
    {extra}</body></html>'''


class GeneratorValidationTests(unittest.TestCase):
    def test_rejects_selector_code_and_invalid_shapes(self):
        for operation, args in [('execute', {}), ('control', {'selector': 'button'}),
                                ('submit', {'key': 'boss:a', 'js': 'anything'}),
                                ('open-detail', {'key': 'boss:a;alert(1)'}),
                                ('scroll', {'times': 5}), ('control', {'id': 'a', 'value': 3})]:
            with self.subTest(operation=operation, args=args), self.assertRaises(ValueError):
                boss_page.action_script(operation, args)

    def test_generated_code_is_transport_free_iife(self):
        for source in [boss_page.observation_script(), boss_page.action_script('submit', {'key': 'boss:abc'})]:
            self.assertTrue(source.startswith('(() => {'))
            self.assertTrue(source.endswith('})()'))
            for forbidden in ('fetch(', 'XMLHttpRequest', 'document.cookie', 'localStorage',
                              'sessionStorage', '__vue__', '__NEXT_DATA__', 'WebSocket', 'CDP', 'setTimeout'):
                self.assertNotIn(forbidden, source)


class OfflineDOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = os.environ.get('BOSS_PAGE_NODE') or shutil.which('node')
        if not cls.node:
            if os.environ.get('CI'):
                raise RuntimeError('CI requires Node for offline DOM tests')
            raise unittest.SkipTest('Node is required for offline DOM behavior tests')
        candidates = [os.environ.get('BOSS_PAGE_PLAYWRIGHT', ''), 'playwright',
                      str(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright')]
        cls.playwright = None
        for candidate in filter(None, candidates):
            check = subprocess.run([cls.node, '-e',
                "try{const p=require(process.argv[1]);process.exit(require('fs').existsSync(p.chromium.executablePath())?0:1)}catch(e){process.exit(1)}",
                candidate], capture_output=True, timeout=10)
            if check.returncode == 0:
                cls.playwright = candidate
                break
        if cls.playwright is None:
            if os.environ.get('CI'):
                raise RuntimeError('CI requires Playwright and Chromium for offline DOM tests')
            raise unittest.SkipTest('Installed Playwright and Chromium required; no download is attempted')

    def run_dom(self, scripts, *, html=None, before=None, url=None):
        result = subprocess.run([self.node, '-e', HARNESS, self.playwright], input=json.dumps({
            'html': html or fixture(), 'scripts': scripts, 'before': before, 'url': url}),
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['network'], [], 'fixture must make no network requests')
        return output

    def test_observation_keeps_all_body_and_only_selected_experience(self):
        output = self.run_dom([boss_page.observation_script()], html=fixture('<p>' + '长正文' * 5000 + '末尾证据</p>'))
        value = output['values'][0]
        self.assertEqual(value['filters'], {'city': '杭州', 'keyword': 'Python', 'experience': ['应届生', '1-3年'], 'degree': []})
        self.assertEqual(value['accountLabel'], '测试候选人')
        self.assertIn('末尾证据', value['body'])
        self.assertNotIn('hidden-sensitive-marker', value['body'])
        self.assertNotIn('hidden-script-marker', value['body'])
        self.assertEqual(len(value['cards']), 2)
        self.assertEqual([card['company'] for card in value['cards']], ['示例科技', '另一家公司'])
        self.assertEqual(value['cards'][0]['experience'], '1-3年')
        self.assertEqual(value['cards'][0]['city'], '杭州')
        self.assertEqual(value['cards'][1]['publisherType'], 'headhunter')
        self.assertNotIn('securityId', value['url'] + value['cards'][0]['url'])
        self.assertEqual(value['detail']['key'], 'boss:job_one')
        self.assertEqual(output['calls'], [])

    def test_home_header_label_text_is_visible_unique_and_ignores_hidden_profile(self):
        header = '''<li class="nav-figure">
          <a href="https://www.zhipin.com/web/geek/recommend" ka="header-username">
            <span class="label-text">示例用户</span><img alt="头像"></a>
          <div class="hidden"><a ka="header-username"><span class="label-text">隐藏个人中心</span></a></div>
          </li>'''
        html = fixture().replace('<div class="nav-figure"><a class="label">测试候选人</a></div>', header)
        output = self.run_dom([boss_page.observation_script()], html=html, url='https://www.zhipin.com/')
        self.assertEqual(output['values'][0]['accountLabel'], '示例用户')
        self.assertEqual(output['calls'], [])
        ambiguous = self.run_dom([boss_page.observation_script()], html=html,
            before="document.querySelector('.nav-figure').insertAdjacentHTML('beforeend', '<a ka=\"header-username\"><span class=\"label-text\">另一账号</span></a>')")
        self.assertIsNone(ambiguous['values'][0]['accountLabel'])

    def test_closed_options_still_report_explicit_selection_but_cannot_be_clicked(self):
        output = self.run_dom([boss_page.observation_script()], before="document.querySelector('ul').style.display='none'")
        value = output['values'][0]
        self.assertEqual(value['filters']['experience'], ['应届生', '1-3年'])
        self.assertFalse(any(c['kind'] == 'filter-option' for c in value['controls']))

    def test_explicit_false_selection_is_not_overridden_by_stale_active_class(self):
        result = self.run_dom([boss_page.observation_script()],
            before="document.querySelector('#exp-grad').setAttribute('aria-selected','false')")
        self.assertEqual(result['values'][0]['filters']['experience'], ['1-3年'])

    def test_keyword_fill_is_one_fill_and_no_search(self):
        observed = self.run_dom([boss_page.observation_script()])['values'][0]
        control = next(c for c in observed['controls'] if c['kind'] == 'keyword')
        value = 'Django "; throw Error(1); //'
        result = self.run_dom([boss_page.action_script('control', {'id': control['id'], 'value': value}), boss_page.observation_script()])
        self.assertEqual(result['calls'], [{'kind': 'input', 'id': 'keyword', 'value': value}])
        self.assertEqual(result['values'][0]['status'], 'filled')
        self.assertEqual(result['values'][1]['filters']['keyword'], value)

    def test_search_and_option_each_click_only_the_observed_control(self):
        observed = self.run_dom([boss_page.observation_script()])['values'][0]
        for kind, label, target in [('search', '搜索', 'search'), ('filter-option', '1年以内', 'exp-under')]:
            control = next(c for c in observed['controls'] if c['kind'] == kind and c['label'] == label)
            with self.subTest(kind=kind):
                result = self.run_dom([boss_page.action_script('control', {'id': control['id']})])
                self.assertEqual(result['calls'], [{'kind': 'click', 'id': target}])

    def test_live_search_anchor_and_city_picker_each_use_one_observed_control(self):
        extra = '''<a id="live-search" class="search-btn" ka="job_search_btn_click">搜索</a>
          <ul class="city-list-hot"><li id="city-beijing">北京</li><li id="city-chengdu">成都</li></ul>
          <ul class="city-list-hot hidden"><li>成都</li></ul>'''
        html = fixture(extra).replace('<button id="search">搜索</button>', '').replace(
            '<span class="cur-city-label">杭州</span>',
            '<div id="city-trigger" class="city-label" ka="switch_city_dialog_open"><span class="cur-city-label">杭州</span></div>')
        observed = self.run_dom([boss_page.observation_script()], html=html)['values'][0]
        for kind, label, target in [('search', '搜索', 'live-search'), ('filter-menu', '城市', 'city-trigger'),
                                     ('filter-option', '成都', 'city-chengdu')]:
            with self.subTest(kind=kind):
                control = next(c for c in observed['controls'] if c['kind'] == kind and c['label'] == label)
                result = self.run_dom([boss_page.action_script('control', {'id': control['id']})], html=html)
                self.assertEqual(result['calls'], [{'kind': 'click', 'id': target}])
        city = next(c for c in observed['controls'] if c['kind'] == 'filter-option' and c['label'] == '成都')
        duplicated = self.run_dom([boss_page.action_script('control', {'id': city['id']})], html=html,
            before="document.querySelector('.city-list-hot').insertAdjacentHTML('beforeend','<li>成都</li>')")
        self.assertEqual(duplicated['values'][0]['status'], 'unsupported')
        self.assertEqual(duplicated['calls'], [])

    def test_live_footer_company_name_is_scoped_to_its_card(self):
        html = fixture('<span class="boss-name">页面外无关公司</span>').replace(
            '<div class="company-name">示例科技</div>',
            '<div class="job-card-footer"><a class="boss-info"><span class="boss-name">实际公司甲</span></a><p>招聘者附加行</p></div>').replace(
            '<div class="company-name">另一家公司</div>',
            '<div class="job-card-footer"><a class="boss-info"><span class="boss-name">实际公司乙</span></a><p>招聘者附加行</p></div>')
        result = self.run_dom([boss_page.observation_script()], html=html)
        self.assertEqual([card['company'] for card in result['values'][0]['cards']], ['实际公司甲', '实际公司乙'])
        self.assertEqual(result['calls'], [])

    def test_card_city_uses_unique_footer_location_never_salary_middle_dot(self):
        html = fixture().replace('<div>13-18K</div><div>1-3年</div>',
            '<div>\ue038-\ue032\ue033K·13薪</div><div>1-3年</div>').replace('杭州·西湖区', '成都·双流区·航空港')
        observed = self.run_dom([boss_page.observation_script()], html=html)
        self.assertEqual(observed['values'][0]['cards'][0]['city'], '成都')
        self.assertEqual(observed['calls'], [])
        for before in [
            "document.querySelector('#card-one .company-location').remove()",
            "const p=document.querySelector('#card-one .company-location');p.parentElement.append(p.cloneNode(true))",
            "document.querySelector('#card-one .company-location').textContent='8-13K·13薪'"
        ]:
            with self.subTest(before=before):
                result = self.run_dom([boss_page.observation_script()], html=html, before=before)
                self.assertIsNone(result['values'][0]['cards'][0]['city'])
        hidden = self.run_dom([boss_page.observation_script()], html=html,
            before="document.querySelector('#card-one .job-card-footer').insertAdjacentHTML('beforeend','<span class=\"company-location hidden\">上海·浦东新区</span>')")
        self.assertEqual(hidden['values'][0]['cards'][0]['city'], '成都')

    def test_live_div_filter_triggers_and_degree_option_are_single_visible_clicks(self):
        extra = '''<div class="condition-filter-select"><div id="live-exp-menu" class="current-select">工作经验</div></div>
          <div class="condition-filter-select"><div id="live-degree-menu" class="current-select">学历要求</div>
          <ul id="degree-options"><li id="degree-any" ka="sel-job-rec-degree-0">不限</li>
          <li id="degree-college" ka="sel-job-rec-degree-202" class="active">大专</li>
          <li ka="sel-job-rec-degree-203">本科</li></ul></div>
          <div class="condition-filter-select hidden"><div class="current-select">学历要求</div>
          <li ka="sel-job-rec-degree-202" class="active">大专</li></div>'''
        html = fixture(extra).replace('<button id="experience-menu" aria-label="工作经验" aria-haspopup="listbox">工作经验</button>', '')
        observed = self.run_dom([boss_page.observation_script()], html=html)['values'][0]
        self.assertEqual(observed['filters']['degree'], ['大专'])
        self.assertEqual(observed['filters']['experience'], ['应届生', '1-3年'])
        for kind, label, filter_name, target in [('filter-menu', '工作经验', 'experience', 'live-exp-menu'),
            ('filter-menu', '学历要求', 'degree', 'live-degree-menu'), ('filter-option', '大专', 'degree', 'degree-college')]:
            with self.subTest(label=label):
                control = next(c for c in observed['controls'] if c['kind'] == kind and c['label'] == label and c['filter'] == filter_name)
                result = self.run_dom([boss_page.action_script('control', {'id': control['id']})], html=html)
                self.assertEqual(result['calls'], [{'kind': 'click', 'id': target}])
        closed = self.run_dom([boss_page.observation_script()], html=html,
            before="document.querySelector('#degree-options').hidden=true;document.querySelector('#live-degree-menu').textContent='大专'")
        self.assertEqual(closed['values'][0]['filters']['degree'], ['大专'])
        self.assertFalse(any(c['kind'] == 'filter-option' and c.get('filter') == 'degree' for c in closed['values'][0]['controls']))
        hidden = self.run_dom([boss_page.observation_script()], html=html,
            before="document.querySelector('#degree-options').hidden=true")
        self.assertEqual(hidden['values'][0]['filters']['degree'], [])

    def test_degree_control_duplicates_and_conflicting_readback_are_rejected(self):
        extra = '''<div class="condition-filter-select"><div id="degree-menu" class="current-select">大专</div>
          <li id="college" ka="sel-job-rec-degree-202" class="active">大专</li></div>'''
        html = fixture(extra)
        observed = self.run_dom([boss_page.observation_script()], html=html)['values'][0]
        option = next(c for c in observed['controls'] if c.get('filter') == 'degree' and c['kind'] == 'filter-option')
        duplicate = self.run_dom([boss_page.action_script('control', {'id': option['id']}), boss_page.observation_script()], html=html,
            before="document.querySelector('#college').parentElement.append(document.querySelector('#college').cloneNode(true))")
        self.assertEqual(duplicate['values'][0]['status'], 'unsupported')
        self.assertEqual(duplicate['values'][1]['filters']['degree'], [])
        self.assertEqual(duplicate['calls'], [])
        mismatch = self.run_dom([boss_page.observation_script()], html=html,
            before="document.querySelector('#degree-menu').textContent='本科'")
        self.assertEqual(mismatch['values'][0]['filters']['degree'], [])

    def test_duplicate_or_stale_control_id_never_clicks(self):
        observed = self.run_dom([boss_page.observation_script()])['values'][0]
        control = next(c for c in observed['controls'] if c['kind'] == 'search')
        for before in ["document.querySelector('.search-box').append(document.querySelector('#search').cloneNode(true))",
                       "document.querySelector('#search').textContent='购买服务'"]:
            result = self.run_dom([boss_page.action_script('control', {'id': control['id']})], before=before)
            self.assertEqual(result['values'][0]['status'], 'unsupported')
            self.assertEqual(result['calls'], [])

    def test_open_detail_exact_key_and_duplicate_rejection(self):
        result = self.run_dom([boss_page.action_script('open-detail', {'key': 'boss:job_two'})])
        self.assertEqual(result['calls'], [{'kind': 'click', 'id': 'job-two'}])
        duplicate = self.run_dom([boss_page.action_script('open-detail', {'key': 'boss:job_one'})],
            before="document.querySelector('#jobs').append(document.querySelector('#card-one').cloneNode(true))")
        self.assertEqual(duplicate['values'][0]['status'], 'unsupported')
        self.assertEqual(duplicate['calls'], [])

    def test_submit_clicked_is_not_success_and_stale_company_is_rejected(self):
        result = self.run_dom([boss_page.action_script('submit', {'key': 'boss:job_one'})])
        self.assertEqual(result['values'][0]['status'], 'clicked')
        self.assertEqual(result['calls'], [{'kind': 'click', 'id': 'submit'}])
        stale = self.run_dom([boss_page.action_script('submit', {'key': 'boss:job_one'})],
            before="document.querySelector('#detail').innerHTML=document.querySelector('#detail').innerHTML.replace('示例科技','旧公司')")
        self.assertEqual(stale['values'][0]['status'], 'unsupported')
        self.assertEqual(stale['calls'], [])

    def test_submit_rejects_wrong_key_missing_button_and_partial_detail(self):
        scenarios = [({'key': 'boss:job_two'}, None), ({'key': 'boss:job_one'}, "document.querySelector('#submit').remove()"),
                     ({'key': 'boss:job_one'}, "document.querySelector('#detail').append('登录查看完整内容')"),
                     ({'key': 'boss:job_one'}, "document.querySelector('#submit').textContent='继续沟通'")]
        for args, before in scenarios:
            result = self.run_dom([boss_page.action_script('submit', args)], before=before)
            self.assertEqual(result['values'][0]['status'], 'unsupported')
            self.assertEqual(result['calls'], [])

    def test_same_company_same_title_requires_detail_job_link_to_disambiguate(self):
        duplicate = """const card=document.querySelector('#card-one').cloneNode(true);
        card.classList.remove('active');card.querySelector('a').setAttribute('href','/job_detail/other_job.html');
        document.querySelector('#jobs').append(card);"""
        result = self.run_dom([boss_page.observation_script(), boss_page.action_script('submit', {'key': 'boss:job_one'})], before=duplicate)
        self.assertIsNone(result['values'][0]['detail']['key'])
        self.assertEqual(result['values'][1]['status'], 'unsupported')
        self.assertEqual(result['calls'], [])
        linked = self.run_dom([boss_page.action_script('submit', {'key': 'boss:job_one'})],
            before=duplicate + "document.querySelector('#detail').insertAdjacentHTML('beforeend','<a href=\"/job_detail/job_one.html\">查看职位</a>')")
        self.assertEqual(linked['calls'], [{'kind': 'click', 'id': 'submit'}])

    def test_scroll_targets_list_once_never_detail_and_does_not_claim_end(self):
        result = self.run_dom([boss_page.action_script('scroll'), boss_page.observation_script()])
        self.assertEqual(result['calls'], [{'kind': 'scroll', 'id': 'jobs', 'top': 110}])
        self.assertFalse(result['values'][1]['endOfList'])
        loading = self.run_dom([boss_page.action_script('scroll')], before="document.querySelector('#jobs').setAttribute('aria-busy','true')")
        self.assertEqual(loading['values'][0]['status'], 'unsupported')
        self.assertEqual(loading['calls'], [])

    def test_explicit_terminal_and_receipt_dismissal(self):
        receipt = '<div role="dialog">已向BOSS发送消息<a id="stay" class="default-btn cancel-btn">留在此页</a></div>'
        result = self.run_dom([boss_page.action_script('dismiss-receipt')], html=fixture(receipt))
        self.assertEqual(result['calls'], [{'kind': 'click', 'id': 'stay'}])
        absent = self.run_dom([boss_page.action_script('dismiss-receipt')])
        self.assertEqual(absent['calls'], [])
        self.assertEqual(absent['values'][0]['status'], 'unsupported')
        terminal = self.run_dom([boss_page.observation_script()], before="document.querySelector('#jobs').insertAdjacentHTML('beforeend','<div>没有更多职位了</div>')")
        self.assertTrue(terminal['values'][0]['endOfList'])
        unrelated = '<p>已向BOSS发送消息</p><div role="dialog"><a id="stay" class="default-btn cancel-btn">留在此页</a></div>'
        wrong_dialog = self.run_dom([boss_page.action_script('dismiss-receipt')], html=fixture(unrelated))
        self.assertEqual(wrong_dialog['calls'], [])

    def test_unknown_layout_origin_and_hover_only_menu_fail_closed(self):
        value = self.run_dom([boss_page.observation_script(), boss_page.action_script('scroll')],
            html='<body><span class="placeholder-text">工作经验</span><p>未知结构</p></body>')
        self.assertEqual(value['values'][0]['controls'], [])
        self.assertIsNone(value['values'][0]['accountLabel'])
        self.assertEqual(value['values'][1]['status'], 'unsupported')
        outside = self.run_dom([boss_page.action_script('submit', {'key': 'boss:job_one'})], url='https://example.com/web/geek/jobs')
        self.assertEqual(outside['values'][0]['status'], 'unsupported')
        self.assertEqual(outside['calls'], [])


if __name__ == '__main__':
    unittest.main()
