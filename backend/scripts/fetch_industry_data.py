#!/usr/bin/env python3
"""
从 Sina Finance API 获取申万一级行业分类数据，更新到 MySQL 中的 unified_quotes 表。

用法:
    source .venv/bin/activate
    python scripts/fetch_industry_data.py
"""

import json
import time
from datetime import datetime
import urllib.request
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.tables import UnifiedQuote

SINA_NODES_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/Market_Center.getHQNodes"
)
SINA_NODE_DATA_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/Market_Center.getHQNodeData"
)

BACKEND_DIR = Path(__file__).resolve().parents[1]


def http_get(url: str, timeout: float = 15.0) -> dict | list:
    """发送 HTTP GET 请求并返回解析后的 JSON。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    # Sina API 可能返回带有 BOM 或编码问题的 JSON
    body = body.lstrip("\ufeff")
    # 某些返回可能是 text/html 错误页
    if body.startswith("<!") or body.startswith("<html"):
        raise ValueError(f"HTML response instead of JSON: {body[:200]}")
    return json.loads(body)


def fetch_shenwan_industries() -> list[tuple[str, str]]:
    """
    从 Sina API 获取申万一级行业列表。

    Returns:
        [(industry_name, node_id), ...]
    """
    data = http_get(SINA_NODES_URL)

    # 找到 A股 → 申万一级
    for item in data:
        if not isinstance(item, list):
            continue
        for section in item:
            if not isinstance(section, list) or len(section) < 2:
                continue
            if section[0] != "A股":
                continue
            groups = section[1]
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, list) or len(group) < 2:
                    continue
                if group[0] == "申万一级":
                    industries = group[1]
                    if not isinstance(industries, list):
                        continue
                    result = []
                    for ind in industries:
                        if isinstance(ind, list) and len(ind) >= 3:
                            name = str(ind[0]).strip()
                            node_id = str(ind[2]).strip()
                            if name and node_id:
                                result.append((name, node_id))
                    return result
    return []


def fetch_industry_stocks(node_id: str) -> list[str]:
    """
    分页获取某个申万行业下的所有股票代码。

    Args:
        node_id: 申万行业节点 ID（如 sw1_730000）

    Returns:
        股票代码列表 (6 位数字字符串)
    """
    all_codes: list[str] = []
    page = 1
    per_page = 80

    while True:
        url = (
            f"{SINA_NODE_DATA_URL}"
            f"?page={page}&num={per_page}&sort=symbol&asc=1&node={node_id}"
        )
        try:
            stocks = http_get(url)
        except Exception as e:
            print(f"    Error fetching page {page}: {e}")
            break

        if not isinstance(stocks, list) or len(stocks) == 0:
            break

        for stock in stocks:
            if isinstance(stock, dict):
                code = str(stock.get("code", "")).strip()
                if code and len(code) == 6:
                    all_codes.append(code)

        if len(stocks) < per_page:
            break
        page += 1
        time.sleep(0.15)  # 礼貌性延迟

    return all_codes


def build_code_industry_map() -> dict[str, str]:
    """
    构建 股票代码 → 申万行业名称 的映射。

    Returns:
        {code: industry_name}
    """
    print("Fetching Shenwan industry list...")
    industries = fetch_shenwan_industries()
    print(f"Found {len(industries)} Shenwan first-level industries")

    code_to_industry: dict[str, str] = {}
    total_stocks = 0

    for name, node_id in industries:
        print(f"  Fetching stocks for: {name} ({node_id})...", end=" ", flush=True)
        codes = fetch_industry_stocks(node_id)
        print(f"{len(codes)} stocks")
        for code in codes:
            if code not in code_to_industry:
                code_to_industry[code] = name
        total_stocks += len(codes)
        time.sleep(0.2)

    print(f"\nTotal: {len(code_to_industry)} unique stocks mapped to {len(industries)} industries")
    return code_to_industry


def update_database(code_to_industry: dict[str, str]) -> int:
    """
    更新 unified_quotes 表的 industry 字段。

    对于 quote_json 是 dict 且有 industry 字段的记录，
    更新 industry 为申万行业名称。

    Returns:
        更新的记录数
    """
    session = SessionLocal()
    try:
        rows = session.scalars(select(UnifiedQuote)).all()
        db_codes = {row.code for row in rows if row.code}
        print(f"\nDatabase has {len(db_codes)} distinct stock codes")

        matched = db_codes & set(code_to_industry.keys())
        unmatched = db_codes - set(code_to_industry.keys())
        print(f"Matched: {len(matched)}, Unmatched: {len(unmatched)}")

        if unmatched:
            print(f"Sample unmatched codes: {list(unmatched)[:20]}")

        updated = 0
        now = datetime.utcnow()
        for row in rows:
            industry = code_to_industry.get(row.code)
            if not industry:
                continue
            payload = json.loads(row.quote_json or "{}")
            if "industry" not in payload:
                continue
            if payload.get("industry") == industry:
                continue
            payload["industry"] = industry
            row.quote_json = json.dumps(payload, ensure_ascii=False)
            row.updated_at = now
            updated += 1

        session.commit()
        print(f"Updated {updated} records")
        return updated
    finally:
        session.close()


def main():
    # Step 1: Build mapping
    code_to_industry = build_code_industry_map()

    # Step 2: Show sample
    print("\nSample mappings:")
    for code, ind in list(code_to_industry.items())[:10]:
        print(f"  {code} → {ind}")

    # Step 3: Update database
    print("\nUpdating database...")
    update_database(code_to_industry)

    print("\nDone!")


if __name__ == "__main__":
    main()
