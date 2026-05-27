#!/usr/bin/env python3
"""精简单日补缺：limit_up_board + index_daily for a target date."""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
import pandas as pd
from datetime import datetime
from scripts.tushare_init import get_pro

pro = get_pro()
NOW = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
TARGET = sys.argv[1] if len(sys.argv) > 1 else '20260526'

def sql_esc(v):
    if v is None: return 'NULL'
    if isinstance(v, str): return "'" + v.replace("'", "''") + "'"
    if isinstance(v, float) and math.isnan(v): return 'NULL'
    return str(v)

print(f"=== Backfill {TARGET} ===")

# Part 1: limit_up_board
df = pro.limit_list_d(trade_date=TARGET, limit='5000')
print(f"Tushare limit_list_d: {len(df)} rows")

if len(df) > 0:
    values = []
    for _, r in df.iterrows():
        code = r['ts_code'].split('.')[0]
        ft = None if pd.isna(r['first_time']) else str(r['first_time'])
        lst = None if pd.isna(r['last_time']) else str(r['last_time'])
        us = None if pd.isna(r['up_stat']) else str(r['up_stat'])
        to = round(float(r['turnover_ratio']), 2) if not pd.isna(r['turnover_ratio']) else None
        fd_yi = round(float(r['fd_amount']) / 1e8, 4) if not pd.isna(r['fd_amount']) else None
        ind = None if pd.isna(r['industry']) else str(r['industry'])
        la_yi_val = float(r['limit_amount']) / 1e8
        la_yi = round(la_yi_val, 4) if not pd.isna(r['limit_amount']) else None
        lt = int(r['limit_times']) if not pd.isna(r['limit_times']) else 0
        ot = int(r['open_times']) if not pd.isna(r['open_times']) else 0
        
        v = f"({sql_esc(code)},{sql_esc(TARGET)},{sql_esc(r['name'])},{round(float(r['pct_chg']),2)},"
        v += f"{sql_esc(r['limit'])},{lt},{sql_esc(ft)},{sql_esc(lst)},{ot},{sql_esc(us)},"
        v += f"{sql_esc(la_yi)},{sql_esc(to)},{sql_esc(fd_yi)},{sql_esc(ind)},{sql_esc(NOW)})"
        values.append(v)
    
    cols = "code,trade_date,name,pct_chg,limit_type,limit_times,first_time,last_time,open_times,up_stat,limit_amount_yi,turnover_ratio,fd_amount_yi,industry,created_at"
    for i in range(0, len(values), 50):
        batch_sql = f"INSERT INTO limit_up_board ({cols}) VALUES {','.join(values[i:i+50])} ON DUPLICATE KEY UPDATE id=id"
        print(batch_sql)

# Part 2: index_daily
print("\n--INDEX_DAILY--")
IDX = ['000001.SH','399001.SZ','399006.SZ','000688.SH','000016.SH','399005.SZ']
idx_vals = []
for code in IDX:
    try:
        df_i = pro.index_daily(ts_code=code, trade_date=TARGET)
        if len(df_i) > 0:
            r = df_i.iloc[0]
            v = f"({sql_esc(code)},{sql_esc(TARGET)},{r['close']},{r['pct_chg']},{r['open']},{r['high']},{r['low']},{r['vol']},{sql_esc(NOW)})"
            idx_vals.append(v)
    except Exception as e:
        print(f"-- SKIP {code}: {e}")

if idx_vals:
    cols_i = "ts_code,trade_date,close,pct_chg,open,high,low,volume,created_at"
    sql_i = f"INSERT INTO index_daily ({cols_i}) VALUES {','.join(idx_vals)} ON DUPLICATE KEY UPDATE id=id"
    print(sql_i)

print("\n--DONE--")
