"""Mobile policy contracts and incident regressions; no live platform."""
import pytest
import store
pytestmark = [pytest.mark.android, pytest.mark.policy]

def test_real_required_travel_is_excluded_without_matching_vendor_tools_as_employer(case):
    from policy_rules import check_target
    policy = store.load_policy(case.root)
    policy['search'].update(businessTravel={'accepted': False}, largeCompanyExclusion={'enabled': True, 'companies': [{'name': '阿里巴巴', 'aliases': ['阿里云']}]})
    request = {'kind': 'greet', 'targetKey': 'boss:1', 'targetFacts': {**case.job, 'text': '熟练掌握Java或Python。7. 能够接受出差。'}}
    with pytest.raises(ValueError, match='required-business-travel'):
        check_target(policy, store.load_state(case.root), request)
    request['targetFacts']['text'] = '本岗位不需要出差。使用阿里云开发后端。'
    check_target(policy, store.load_state(case.root), request)
    request['targetFacts']['company'] = '阿里云'
    with pytest.raises(ValueError, match='large-company'):
        check_target(policy, store.load_state(case.root), request)
