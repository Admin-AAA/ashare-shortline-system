"""
Tushare 统一初始化模块 — 所有脚本引用此文件。

使用方式:
    from scripts.tushare_init import get_pro
    pro = get_pro()

注意: 必须通过代理服务器访问，不允许直连 Tushare API。
"""

import os
import tushare as ts

TUSHARE_TOKEN = "eqyrJ71eIKjLoM12OeDLQOwRMUTuETs2ItnXAJitOhjTE8sLIjm8UuihObjXEs3zOFDKUI1PMrTmMp1yO9Tjl19"
TUSHARE_PROXY = "https://fastapic.stockai888.top/"


def get_pro():
    """返回已配置代理的 Tushare Pro API 实例。"""
    pro = ts.pro_api(TUSHARE_TOKEN)
    pro._DataApi__http_url = TUSHARE_PROXY
    return pro


def get_pro_bar(ts_code: str, adj: str = "qfq", **kwargs):
    """通过代理调用 ts.pro_bar（前复权K线）。"""
    pro = get_pro()
    return ts.pro_bar(api=pro, ts_code=ts_code, adj=adj, **kwargs)
