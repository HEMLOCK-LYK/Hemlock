"""目标范围校验测试。"""

from hemlock.core.scope import check_scope


def test_no_scope_allows_all():
    """未配置范围时允许所有。"""
    assert check_scope("https://evil.com/hack", scope_patterns=[]) is True


def test_scope_match():
    """授权范围内允许。"""
    patterns = ["example.com", "*.target.com"]
    assert check_scope("https://example.com/api", scope_patterns=patterns) is True
    assert check_scope("https://sub.target.com/login", scope_patterns=patterns) is True


def test_scope_reject():
    """超出范围拒绝。"""
    patterns = ["example.com"]
    assert check_scope("https://evil.com/hack", scope_patterns=patterns) is False
    assert check_scope("https://other.com/data", scope_patterns=patterns) is False
