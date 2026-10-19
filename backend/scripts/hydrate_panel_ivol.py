"""给研究回测面板（entry_panel_WX.json）里缺失的 ivol20_pct / ivol_dn20_pct 补值（只补 None，已有值不动）。

背景：面板是回放导出态，卡片的 ivol 分位在导出当时从 daily_kbars 附上；导出时 daily_kbars.ivol20/ivol_dn20
是 NULL 的交易日，卡片缺字段，每次回测 L-093/L-109/L-121 否决都会打"否决未评估"警告。DB 回补后用本脚本
按生产同一函数（risk_features.load_ivol*_pct_map，当日全市场横截面分位）补到面板卡片上。

- 只读 DB，不请求上游；DB 仍为 NULL 的日子（如基准 000905.SH 本地缺失的 2023-06 以前）保持缺失。
- 首次改写前备份为 <file>.pre_ivol_hydrate；原子替换；panel 顶层记 `_ivol_hydrated` 元数据，可重复运行。

用法：
  python scripts/hydrate_panel_ivol.py               # 默认：research_stability_gates.panels() 的 4 策略 × 各窗口
  python scripts/hydrate_panel_ivol.py --dry-run
  python scripts/hydrate_panel_ivol.py path/to/entry_panel_WX.json ...
"""
from __future__ import annotations
import argparse, datetime as _dt, json, os, shutil, sys
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
from app.db.session import SessionLocal  # noqa: E402
from app.services.risk_features import load_ivol20_pct_map, load_ivol_dn20_pct_map  # noqa: E402

FIELDS = {"ivol20_pct": load_ivol20_pct_map, "ivol_dn20_pct": load_ivol_dn20_pct_map}
SIDS = ("chao-gu-yang-jia-skill", "chen-xiaoqun-skill", "oversold-capital-reversal", "sector-relative-strength-leader")


def default_panels() -> list[Path]:
    from scripts.research_stability_gates import panels
    out: list[Path] = []
    for sid in SIDS:
        out.extend(panels(sid).values())
    return out


def hydrate_panel(p: dict, db) -> dict:
    """就地补 p['days'][*]['cards'][*] 的缺失分位；返回 {field: {missing, filled}}。"""
    stats = {}
    for field, loader in FIELDS.items():
        dates = sorted({d["signal_date"] for d in p["days"] for c in d["cards"] if c.get(field) is None})
        m = loader(db, dates) if dates else {}
        miss = filled = 0
        for d in p["days"]:
            for c in d["cards"]:
                if c.get(field) is not None:
                    continue
                miss += 1
                v = m.get((str(d["signal_date"]), str(c.get("code") or "")))
                if v is not None:
                    c[field] = v
                    filled += 1
        stats[field] = {"missing": miss, "filled": filled}
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    files = [Path(f) for f in a.files] or default_panels()
    with SessionLocal() as db:
        for f in files:
            p = json.loads(f.read_text())
            st = hydrate_panel(p, db)
            n = sum(v["filled"] for v in st.values())
            print(f"{f.relative_to(B) if f.is_relative_to(B) else f}: {st}", flush=True)
            if a.dry_run or not n:
                del p
                continue
            bak = f.with_name(f.name + ".pre_ivol_hydrate")
            if not bak.exists():
                shutil.copy2(f, bak)
            p.setdefault("_ivol_hydrated", []).append(
                {"at": _dt.datetime.now().isoformat(timespec="seconds"), "source": "daily_kbars", "stats": st})
            tmp = f.with_name(f.name + ".tmp")
            tmp.write_text(json.dumps(p, ensure_ascii=False))
            os.replace(tmp, f)
            del p


if __name__ == "__main__":
    main()
