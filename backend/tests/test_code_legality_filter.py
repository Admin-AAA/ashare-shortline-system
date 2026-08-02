"""测试推荐写入层的股票合法性校验（P1.2 假数据防护）。

背景（2026-08-02 诊断）：库里存在 code=977483/967711、name=diag/diag2 的
假 formal 推荐被结算为 success。此测试锁定 is_legal_stock_code 的判定：
- 合法 A 股代码段：0/3（深）、6（沪）、8/4（北交）、92（北交新段）
- 非法：7/9 开头（如 977483/967711）、长度非 6、名称含 diag/诊断/test 标记
"""
import pytest

from app.services.runs.persistence import is_legal_stock_code


@pytest.mark.parametrize(
    ("code", "name", "expected"),
    [
        # 合法
        ("000001", "平安银行", True),
        ("600519", "贵州茅台", True),
        ("300750", "宁德时代", True),
        ("688981", "中芯国际", True),
        ("830799", "艾融软件", True),   # 北交 8 开头
        ("430047", "诺思兰德", True),   # 北交 4 开头
        ("920002", "万达轴承", True),   # 北交 92 新段
        ("002594", "比亚迪", True),
        # 非法 code
        ("977483", "diag", False),      # 历史假数据 code
        ("967711", "diag2", False),     # 历史假数据 code
        ("12345", "测试", False),        # 5 位
        ("1234567", "股票", False),      # 7 位
        ("ABC123", "股票", False),       # 非数字
        ("700001", "股票", False),       # 7 开头非法段
        # 非法名称
        ("000001", "diag", False),
        ("000001", "诊断测试", False),
        ("000001", "test_001", True),     # 名称 marker 已收紧，不再拦截 test/测试
        ("000001", "平安银行测试", True),  # 正常名称含"测试"字样不拦截业务名
        (None, "股票", False),
        ("", "股票", False),
    ],
)
def test_is_legal_stock_code(code, name, expected):
    assert is_legal_stock_code(code, name) is expected


def test_is_legal_stock_code_omits_name():
    # name 缺省时不因名称拒绝（兼容旧调用）
    assert is_legal_stock_code("000001") is True
    assert is_legal_stock_code("977483") is False
