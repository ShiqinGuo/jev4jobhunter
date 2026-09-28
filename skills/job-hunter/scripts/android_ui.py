"""Deterministic native navigation and filter verification; no recruiting HTTP calls."""
from pathlib import Path
import re

from android_device import PACKAGE
import store


def nodes(tree, resource, text=None):
    return [n for n in tree.iter('node') if n.get('resource-id') == PACKAGE + ':id/' + resource
            and (text is None or n.get('text') == text)]


def one(tree, resource, text=None):
    matches = nodes(tree, resource, text)
    if len(matches) != 1:
        raise store.StoreError('unique-native-control-required:' + resource + ':' + str(text))
    return matches[0]


def selected_options(tree, picture, device):
    """BOSS recommendation tiles omit selection accessibility state; inspect tile fill."""
    parents = {c: p for p in tree.iter() for c in p}
    selected = []
    for node in nodes(tree, 'tv_option'):
        left, top, right, bottom = device.bounds(parents[node])
        # Interior corner avoids text and the border of the default '全部' tile.
        inset = max(3, round((bottom - top) * .18))
        r, g, b = picture.getpixel((left + inset, top + inset))[:3]
        if g - r > 8 and b - r > 8:
            selected.append(node.get('text'))
    return selected


class NativeUI:
    def __init__(self, device, root, evidence):
        self.d, self.root, self.evidence = device, Path(root), Path(evidence)
        self.policy = store.load_policy(self.root)

    def home(self):
        for _ in range(7):
            tree = self.d.ui()
            if nodes(tree, 'tv_tab_1'):
                if not nodes(tree, 'magic_indicator'):
                    self.d.tap(one(tree, 'tv_tab_1'))
                    tree = self.d.ui()
                return tree
            self.d.back()
        raise store.StoreError('jobs-home-not-reached')

    def bind(self, workflow):
        tree = self.home()
        self.d.tap(one(tree, 'tv_tab_4'))
        result = workflow.bind()
        return result

    def city(self, city):
        allowed = self.policy.get('targets', {}).get('locations', [])
        if city not in allowed:
            raise store.StoreError('city-not-in-policy')
        tree = self.d.ui()
        current = [n for n in nodes(tree, 'tv_tab_label') if n.get('text') in [*allowed, '全国']]
        if len(current) != 1:
            raise store.StoreError('unique-city-tab-required')
        self.d.tap(current[0])
        self.d.tap(self.d.button('筛选城市'))
        tree = self.d.ui()
        matches = nodes(tree, 'btn_city', city)
        if not matches:
            spelling = self.d.config.get('cityInput', {}).get(city, '')
            if not re.fullmatch('[a-z]+', spelling):
                raise store.StoreError('city-pinyin-config-required:' + city)
            self.d.tap(one(tree, 'city_search_et'))
            self.d.adb('shell', 'input', 'text', spelling)
            tree = self.d.ui()
            matches = [n for n in tree.iter('node') if n.get('text') == city]
        if not matches:
            raise store.StoreError('city-choice-unavailable:' + city)
        # Current/history and popular city tiles can contain the same city.
        self.d.tap(matches[0])
        tree = self.d.ui()
        if not nodes(tree, 'tv_tab_label', city):
            raise store.StoreError('city-readback-mismatch')
        self.d.screenshot(self.evidence / 'city.png')
        return city

    def source(self, source, query):
        tree = self.home()
        if source == 'recommendation':
            titles = [n for n in tree.iter('node') if n.get('text') == query
                      and self.d.bounds(n)[3] < self.d.signature()['size'][1] * .12]
            if len(titles) != 1:
                raise store.StoreError('recommendation-intent-tab-required:' + query)
            self.d.tap(titles[0])
            return
        if query not in self.policy.get('search', {}).get('queries', []):
            raise store.StoreError('query-not-in-policy')
        icons = nodes(tree, 'img_icon')
        if not icons:
            raise store.StoreError('home-search-icon-unavailable')
        self.d.tap(max(icons, key=lambda n: self.d.bounds(n)[0]))
        tree = self.d.ui()
        matches = nodes(tree, 'tv_label', query) + nodes(tree, 'tv_suggest_word', query)
        if matches:
            self.d.tap(matches[0])
        elif re.fullmatch(r'[A-Za-z0-9 ]+', query):
            self.d.tap(one(tree, 'et_search'))
            self.d.adb('shell', 'input', 'keycombination', 113, 29)
            self.d.adb('shell', 'input', 'text', query.replace(' ', '%s'))
            self.d.tap(self.d.button('搜索'))
        else:
            raise store.StoreError('query-needs-existing-history-or-user-keyboard:' + query)
        if not nodes(self.d.ui(), 'tv_search_hint', query):
            raise store.StoreError('query-readback-mismatch')

    def filters(self, source, workflow, salary=None):
        config = self.policy.get('search', {}).get('androidFilters', {}).get(source)
        if not config:
            raise store.StoreError('source-filter-config-required')
        salaries = [salary] if salary else config['salaryLabels']
        if salary and salary not in config['salaryLabels']:
            raise store.StoreError('salary-not-in-policy')
        if source == 'recommendation' and len(salaries) != 1:
            raise store.StoreError('homepage-needs-one-native-salary-per-list')
        tree = self.d.ui()
        tabs = [n for n in nodes(tree, 'tv_tab_label') if n.get('text', '').startswith('筛选')]
        if len(tabs) != 1:
            raise store.StoreError('unique-filter-tab-required')
        self.d.tap(tabs[0])
        self.d.tap(self.d.button('清除'))
        tree = self.d.ui()
        wanted = [('经验', config['experienceLabels']), ('BOSS活跃', [config['bossActivity']]),
                  ('薪资', salaries)]
        if source == 'search':
            wanted = [wanted[2], wanted[0], wanted[1]]  # Top-to-bottom native sheet order.
        evidence = {}
        for category, labels in wanted:
            if source == 'recommendation':
                self.d.tap(one(tree, 'tv_filter', category))
                tree = self.d.ui()
            else:
                for _ in range(3):
                    if all(nodes(tree, 'keywords_view_text', label) for label in labels):
                        break
                    panels = [n for n in tree.iter('node') if n.get('scrollable') == 'true']
                    if len(panels) != 1:
                        raise store.StoreError('unique-filter-scroll-panel-required')
                    left, top, right, bottom = self.d.bounds(panels[0])
                    self.d.adb('shell', 'input', 'swipe', (left + right) // 2, round(top + (bottom-top)*.8),
                               (left + right) // 2, round(top + (bottom-top)*.35), 650)
                    tree = self.d.ui()
            for label in labels:
                self.d.tap(one(tree, 'tv_option' if source == 'recommendation' else 'keywords_view_text', label))
            tree = self.d.ui()
            path = self.evidence / (source + '-' + '-'.join(salaries) + '-' + category + '.png')
            picture = self.d.screenshot(path)
            selected = selected_options(tree, picture, self.d) if source == 'recommendation' else [
                n.get('text') for n in nodes(tree, 'keywords_view_text') if n.get('selected') == 'true']
            if not set(labels).issubset(selected):
                raise store.StoreError('filter-readback-mismatch:' + category)
            evidence[category] = {'selected': labels, 'screenshot': str(path)}
        workflow.batch['activeKeys'] = []
        workflow.arm_search()
        self.d.tap(self.d.button('确定', tree))
        workflow.read_batch()
        event = store.read_json(self.d.directory / (workflow.batch['lastList'] + '.json'))
        from android_capture import RECOMMEND, LIST
        if event.get('source') != (RECOMMEND if source == 'recommendation' else LIST):
            raise store.StoreError('list-response-source-mismatch')
        return evidence
