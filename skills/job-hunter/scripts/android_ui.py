"""BOSS native selectors, bounded navigation and matching delivered-message evidence."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import time
import uuid
from android_device import PACKAGE
from android_domain import Evidence, Layout, LocatedJob, LocateStatus, Marker, NavigationError, TargetUnavailable
import store

def nodes(tree, resource: str, text: str | None = None):
    return [n for n in tree.iter('node') if n.get('resource-id') == PACKAGE + ':id/' + resource
            and (text is None or n.get('text') == text)]

def one(tree, resource: str, text: str | None = None):
    matches = nodes(tree, resource, text)
    if len(matches) != 1:
        raise NavigationError('unique-native-control-required:' + resource + ':' + str(text))
    return matches[0]

def display_matches(actual: str, expected: str) -> bool:
    """Native labels may be truncated; the detail response still must prove job ID."""
    actual, expected = (re.sub(r'\s+', '', value) for value in (actual, expected))
    if actual == expected:
        return True
    for short, full in ((actual, expected), (expected, actual)):
        prefix = re.sub(r'(?:\.{3}|…+)$', '', short)
        if prefix != short and len(prefix) >= 4 and full.startswith(prefix):
            return True
    return False

def selected_options(tree, picture, device):
    parents = {child: parent for parent in tree.iter() for child in parent}
    selected = []
    for node in nodes(tree, 'tv_option'):
        left, top, right, bottom = device.bounds(parents[node])
        inset = max(3, round((bottom - top) * .18))
        red, green, blue = picture.getpixel((left + inset, top + inset))[:3]
        if green - red > 8 and blue - red > 8:
            selected.append(node.get('text'))
    return selected

def conversation_company(tree, device) -> str:
    employers = nodes(tree, 'tv_company_name')
    if len(employers) == 1:
        return employers[0].get('text', '')
    height = device.bounds(tree[0])[3]
    headers = [n for n in nodes(tree, 'tv_sub_title') if device.bounds(n)[3] < height*.12 and ' · ' in n.get('text','')]
    if len(headers) != 1:
        raise NavigationError('unique-conversation-company-required')
    return headers[0].get('text').split(' · ',1)[0].strip()

class NativeUI:
    def __init__(self, device, feed, evidence: Path):
        self.device, self.feed, self.evidence = device, feed, evidence

    def home(self):
        for _ in range(7):
            tree = self.device.ui()
            tabs = nodes(tree, 'tv_tab_1')
            if tabs:
                if not nodes(tree, 'magic_indicator'):
                    self.device.tap(one(tree, 'tv_tab_1'))
                    tree = self.device.ui()
                return tree
            self.device.back()
        raise NavigationError('jobs-home-not-reached')

    def verify_account(self, expected: str):
        tree = self.home()
        self.device.tap(one(tree, 'tv_tab_4'))
        tree = self.device.ui()
        if not any(n.get('text') == expected or n.get('content-desc') == expected for n in tree.iter('node')):
            raise NavigationError('current-account-readback-mismatch')
        self.home()

    def source(self, source: str, query: str, policy: dict, layout: Layout):
        tree = self.home()
        if source == 'recommendation':
            titles = [n for n in tree.iter('node') if n.get('text') == query and self.device.bounds(n)[3] < layout.height * .12]
            if len(titles) != 1:
                raise NavigationError('recommendation-intent-tab-required:' + query)
            self.device.tap(titles[0])
            return
        if query not in policy.get('search', {}).get('queries', []):
            raise NavigationError('query-outside-current-policy')
        icons = nodes(tree, 'img_icon')
        if not icons:
            raise NavigationError('search-icon-unavailable')
        self.device.tap(max(icons, key=lambda n: self.device.bounds(n)[0]))
        tree = self.device.ui()
        existing = nodes(tree, 'tv_label', query) + nodes(tree, 'tv_suggest_word', query)
        if existing:
            self.device.tap(existing[0])
        else:
            self.device.tap(one(tree, 'et_search'))
            self.device.adb('shell', 'input', 'keycombination', 113, 29)
            self.device.input_ascii(query)
            self.device.tap(self.device.button('搜索'))
        if not nodes(self.device.ui(), 'tv_search_hint', query):
            raise NavigationError('query-readback-mismatch')

    def city(self, city: str, policy: dict):
        allowed = policy.get('targets', {}).get('locations', [])
        if city not in allowed:
            raise NavigationError('city-outside-current-policy')
        tree = self.device.ui()
        choices = [n for n in nodes(tree, 'tv_tab_label') if n.get('text') in [*allowed, '全国']]
        if len(choices) != 1:
            raise NavigationError('unique-city-tab-required')
        if choices[0].get('text') == city:
            return
        self.device.tap(choices[0])
        self.device.tap(self.device.button('筛选城市'))
        tree = self.device.ui()
        matches = nodes(tree, 'btn_city', city)
        if not matches:
            spelling = self.device.config.get('cityInput', {}).get(city)
            if not spelling or not re.fullmatch('[a-z]+', spelling):
                raise NavigationError('city-pinyin-config-required:' + city)
            self.device.tap(one(tree, 'city_search_et'))
            self.device.input_ascii(spelling)
            matches = [n for n in self.device.ui().iter('node') if n.get('text') == city]
        if not matches:
            raise NavigationError('city-choice-unavailable:' + city)
        self.device.tap(matches[0])
        if not nodes(self.device.ui(), 'tv_tab_label', city):
            raise NavigationError('city-readback-mismatch')

    def filters(self, source: str, policy: dict, salary: str | None, marker: Marker) -> dict:
        config = policy['search']['androidFilters'][source]
        salaries = [salary] if salary else config['salaryLabels']
        if any(label not in config['salaryLabels'] for label in salaries):
            raise NavigationError('salary-outside-policy')
        if source == 'recommendation' and len(salaries) != 1:
            raise NavigationError('native-recommendation-requires-one-salary')
        tree = self.device.ui()
        tabs = [n for n in nodes(tree, 'tv_tab_label') if n.get('text', '').startswith('筛选')]
        if len(tabs) != 1:
            raise NavigationError('unique-filter-tab-required')
        self.device.tap(tabs[0])
        self.device.tap(self.device.button('清除'))
        tree = self.device.ui()
        wanted = [('经验', config['experienceLabels']), ('BOSS活跃', [config['bossActivity']]), ('薪资', salaries)]
        if source == 'search':
            wanted = [wanted[2], wanted[0], wanted[1]]
        for category, labels in wanted:
            if source == 'recommendation':
                self.device.tap(one(tree, 'tv_filter', category))
                tree = self.device.ui()
            else:
                for _ in range(3):
                    if all(nodes(tree, 'keywords_view_text', label) for label in labels):
                        break
                    panel = [n for n in tree.iter('node') if n.get('scrollable') == 'true']
                    if len(panel) != 1:
                        raise NavigationError('unique-filter-scroll-panel-required')
                    left, top, right, bottom = self.device.bounds(panel[0])
                    self.device.adb('shell', 'input', 'swipe', (left+right)//2, round(top+(bottom-top)*.8),
                                    (left+right)//2, round(top+(bottom-top)*.35), 650)
                    tree = self.device.ui()
            for label in labels:
                self.device.tap(one(tree, 'tv_option' if source == 'recommendation' else 'keywords_view_text', label))
            tree = self.device.ui()
            picture = self.device.screenshot(self.evidence / f'{source}-{category}.png')
            selected = selected_options(tree, picture, self.device) if source == 'recommendation' else [
                n.get('text') for n in nodes(tree, 'keywords_view_text') if n.get('selected') == 'true']
            if not set(labels).issubset(selected):
                raise NavigationError('filter-readback-mismatch:' + category)
        self.feed.arm(marker)
        self.device.tap(self.device.button('确定', tree))
        event = self.feed.wait_material(marker, 'list')
        if event is None:
            raise NavigationError('filtered-list-response-missing:not-empty')
        from boss_protocol import LIST, RECOMMEND
        if event['source'] != (LIST if source == 'search' else RECOMMEND):
            raise NavigationError('filtered-list-source-mismatch')
        return event

    def cards(self, tree=None) -> list[dict]:
        tree = self.device.ui() if tree is None else tree
        parents = {child: parent for parent in tree.iter() for child in parent}
        cards = []
        for title in nodes(tree, 'tv_position_name'):
            parent = parents.get(title)
            while parent is not None:
                employers = nodes(parent, 'tv_company_name')
                if len(employers) == 1:
                    cards.append({'title': title.get('text', '').replace(' &@ ', '').strip(),
                                  'company': employers[0].get('text', ''), 'node': title})
                    break
                parent = parents.get(parent)
        return cards

    def locate_job(self, job: dict, jobs: dict, profile: dict, marker_factory) -> LocatedJob:
        profile = {**profile, 'endY': (profile['startY'] + profile['endY']) // 2}
        title, company = job.get('listTitle', job['title']), job.get('listCompany', job['company'])
        positions = {}
        for index, row in enumerate(jobs.values()):
            positions.setdefault((row.get('listTitle', row['title']), row.get('listCompany', row['company'])), []).append(index)
        visible = [positions[(c['title'], c['company'])][0] for c in self.cards()
                   if len(positions.get((c['title'], c['company']), [])) == 1]
        target = list(jobs).index(job['key'])
        first_reverse = bool(visible and target < min(visible))
        budget = max(8, min(40, len(jobs) // 2 + 4))
        for reverse in (first_reverse, not first_reverse):
            previous = None
            for _ in range(budget):
                cards = self.cards()
                matches = [c for c in cards if display_matches(c['title'], title) and display_matches(c['company'], company)]
                if len(matches) > 1:
                    return LocatedJob(LocateStatus.AMBIGUOUS)
                if matches:
                    marker = marker_factory('open-detail', job['key'])
                    self.feed.arm(marker)
                    self.device.tap(matches[0]['node'])
                    event = self.feed.wait_material(marker, 'detail')
                    if not event or not event['job']['complete']:
                        self.device.back()
                        return LocatedJob(LocateStatus.DETAIL_MISSING)
                    detail_tree = self.device.ui()
                    names = nodes(detail_tree, 'tv_boss_name')
                    return LocatedJob(LocateStatus.FOUND, {**event['job'], 'detailEvidence': event['id'],
                                     'recipientName': names[0].get('text') if len(names) == 1 else None,
                                     'listTitle': title, 'listCompany': company})
                fingerprint = [(c['title'], c['company'], c['node'].get('bounds')) for c in cards]
                if fingerprint == previous:
                    break
                previous = fingerprint
                self.feed.arm(marker_factory('locate-job', job['key']))
                self.device.swipe(profile, reverse)
        return LocatedJob(LocateStatus.NOT_VISIBLE)

    def return_to_list(self):
        for attempt in range(4):
            if self.cards():
                return
            if attempt < 3:
                self.device.back()
        raise NavigationError('job-list-not-restored')

    def delivered_message(self, action: dict, marker: Marker, layout: Layout) -> Evidence | None:
        tree = self.device.ui()
        if not nodes(tree, 'tv_content_text'):
            return None
        job = action['targetFacts']
        if conversation_company(tree, self.device) != job['company']:
            return None
        labels = [n.get('text', '') for n in tree.iter('node')]
        if not any(job['title'] == label or job['title'] in label for label in labels):
            # 14.170 omits the title in a collapsed chat card. Corroborate the job
            # through the exact detail response validated before this original click.
            identifier = job.get('detailEvidence', '')
            if not re.fullmatch(r'[\w-]+', identifier):
                return None
            path = self.feed.directory / (identifier + '.json')
            if not path.exists():
                return None
            detail = store.read_json(path).get('job', {})
            if (detail.get('key'), detail.get('company'), detail.get('title'), detail.get('complete')) != (
                    action['targetKey'], job['company'], job['title'], True):
                return None
        started = datetime.fromtimestamp(marker.at, timezone(timedelta(hours=8))).replace(second=0, microsecond=0)
        if action['kind'] == 'greet':
            contact = []
            for node in nodes(tree, 'tv_contact_time'):
                match = re.fullmatch(r'(\d+)月(\d+)日\s+(\d{2}):(\d{2})\s+由你发起的沟通', node.get('text', ''))
                if match:
                    month, day, hour, minute = map(int, match.groups())
                    observed = started.replace(month=month, day=day, hour=hour, minute=minute)
                    if started <= observed <= datetime.now(started.tzinfo) + timedelta(minutes=1):
                        contact.append(node.get('text'))
            if len(contact) != 1:
                return None
        content = action.get('content') or action.get('expectedGreeting', '')
        if not content:
            return None
        normalize = lambda text: re.sub(r'\s+', '', text)
        parents = {child: parent for parent in tree.iter() for child in parent}
        matches = []
        for node in nodes(tree, 'tv_content_text'):
            if normalize(node.get('text', '')) != normalize(content):
                continue
            parent = parents.get(node)
            while parent is not None and parent is not tree:
                avatars = nodes(parent, 'iv_avatar')
                if avatars:
                    if (len(avatars) == 1 and self.device.bounds(avatars[0])[0] > layout.width * .8
                            and nodes(parent, 'mMsgTvStatus', '送达')):
                        matches.append(node.get('text'))
                    break
                parent = parents.get(parent)
        if len(matches) != 1:
            return None
        # Text submission also requires a pre-click snapshot without this exact outbound bubble.
        if action['kind'] != 'greet' and normalize(content) in action.get('priorOutboundTexts', []):
            return None
        identifier = 'ui-delivery-' + uuid.uuid4().hex
        proof = Evidence(action['id'], marker.account_id, marker.account_label,
                         action['targetKey'], identifier, 'succeeded', matches[0],
                         {'company': job['company'], 'jobTitle': job['title'], 'detailEvidence': job.get('detailEvidence'), 'marker': marker.to_dict(),
                          'observedAt': store.stamp(), 'source': 'native-delivered-outbound-bubble'})
        return proof

    def conversation(self) -> dict:
        tree = self.device.ui()
        employer = conversation_company(tree, self.device)
        parents = {child: parent for parent in tree.iter() for child in parent}
        messages = []
        for node in nodes(tree, 'tv_content_text'):
            parent = parents.get(node)
            while parent is not None and parent is not tree:
                avatars = nodes(parent, 'iv_avatar')
                if avatars:
                    messages.append({'text': node.get('text', ''),
                        'outbound': self.device.bounds(avatars[0])[0] > self.device.bounds(tree[0])[2] * .8,
                        'delivered': bool(nodes(parent, 'mMsgTvStatus', '送达'))})
                    break
                parent = parents.get(parent)
        return {'company': employer, 'messages': messages}

    def message_rows(self, tree=None) -> list[dict]:
        tree = self.device.ui() if tree is None else tree
        parents = {child: parent for parent in tree.iter() for child in parent}
        rows = []
        for position in nodes(tree, 'tv_position'):
            parent = parents.get(position)
            while parent is not None:
                names = nodes(parent, 'tv_name')
                previews = nodes(parent, 'tv_msg')
                if len(names) == 1 and len(previews) == 1 and len(nodes(parent, 'tv_position')) == 1:
                    label = position.get('text', '')
                    rows.append({'name': names[0].get('text'), 'company': label.split('|', 1)[0].strip(),
                                 'position': label, 'preview': previews[0].get('text'),
                                 'outboundStatus': [n.get('text') for n in nodes(parent, 'iv_msg_status')],
                                 'node': names[0]})
                    break
                parent = parents.get(parent)
        return rows

    def message_home(self):
        tree = self.home()
        self.device.tap(one(tree, 'tv_tab_3', '消息'))
        tree = self.device.ui()
        previous = None
        for _ in range(9):
            if nodes(tree, 'et_input'):
                return tree
            visible = [(r['name'], r['position']) for r in self.message_rows(tree)]
            if visible == previous:
                break
            previous = visible
            left, top, right, bottom = self.device.bounds(tree[0])
            self.device.swipe({'x': (left+right)//2, 'startY': round(bottom*.8),
                              'endY': round(bottom*.3), 'durationMs': 650}, reverse=True)
            tree = self.device.ui()
        raise NavigationError('message-list-top-not-reached')

    def message_check(self, layout: Layout, pages=6) -> dict:
        self.message_home()
        collected = {}
        prior = None
        exhausted = False
        for page in range(pages):
            rows = self.message_rows()
            visible = [(r['name'], r['position'], r['preview']) for r in rows]
            if visible == prior:
                exhausted = True
                break
            prior = visible
            for row in rows:
                value = {k:v for k,v in row.items() if k != 'node'}
                collected[(row['name'], row['company'], row['position'])] = value
            if page + 1 < pages:
                self.device.swipe({'x': layout.width//2, 'startY': round(layout.height*.8),
                    'endY': round(layout.height*.3), 'durationMs': 650})
        return {'status': 'checked', 'rows': list(collected.values()), 'recentPages': page+1,
                'atEnd': exhausted, 'possibleIncoming': sum(not r['outboundStatus'] for r in collected.values())}

    def open_conversation(self, job: dict) -> dict:
        for attempt in range(2):
            tree = self.message_home()
            previous = None
            for _ in range(6):
                rows = self.message_rows(tree)
                matches = self.conversation_candidates(rows,job)
                if matches:
                    # Message rows reorder after drafts/read status refresh. Reacquire
                    # the intended row immediately before consuming its coordinates.
                    tree = self.device.ui()
                    fresh = self.conversation_candidates(self.message_rows(tree),job)
                    if not fresh:
                        continue
                    self.device.tap(fresh[0]['node'])
                    for settled in range(3):
                        snapshot = self.conversation()
                        if snapshot['company'] == job['company']:
                            return snapshot
                        time.sleep(.3)
                    break  # Reopen the message list and locate afresh once.
                visible = [(r['name'], r['position']) for r in rows]
                if visible == previous:
                    break
                previous = visible
                left, top, right, bottom = self.device.bounds(tree[0])
                self.device.swipe({'x': (left+right)//2, 'startY': round(bottom*.8),
                                  'endY': round(bottom*.3), 'durationMs': 650})
                tree = self.device.ui()
        raise TargetUnavailable('conversation-not-visible-in-recent-pages')

    @staticmethod
    def conversation_candidates(rows: list[dict], job: dict) -> list[dict]:
        matches = [row for row in rows if row['company'] == job['company']
                   and (not job.get('recipientName') or row['name'] == job['recipientName'])]
        if len(matches)>1:
            raise TargetUnavailable('ambiguous-company-recipient-conversation')
        return matches

    def prepare_text_session(self, text: str, action_id: str):
        from android_text import NativeTextSession
        tree = self.device.ui()
        fields = [n for n in tree.iter('node') if n.get('class') == 'android.widget.EditText']
        if len(fields) != 1:
            raise NavigationError('unique-message-input-required')
        return NativeTextSession(self.device, fields[0].get('resource-id', ''), text, action_id)
