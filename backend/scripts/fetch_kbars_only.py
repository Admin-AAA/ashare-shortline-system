"""极简 K 线填充：通过 canonical Provider Router 拉取并写文件缓冲。"""

import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.tushare_client import clear_pro_cache, get_pro  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("fill")

OUT_DIR = Path("/tmp/ashare_kbars_batches")
BATCH_SIZE = 200
SLEEP_BETWEEN = 0.8


def fetch_one(pro, code: str) -> list[list[object]]:
    suffix = "SH" if code.startswith(("6", "9")) else "SZ"
    tsc = f"{code}.{suffix}"
    sd = (date.today() - timedelta(days=750)).strftime("%Y%m%d")
    ed = date.today().strftime("%Y%m%d")

    frame = pro.daily(ts_code=tsc, start_date=sd, end_date=ed)
    if frame is None or frame.empty:
        return []
    frame = frame.sort_values("trade_date").tail(250)
    serializable = frame.astype(object).where(frame.notna(), None)
    return serializable.values.tolist()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with open("/tmp/kbar_codes_needed.txt", encoding="utf-8") as handle:
        codes = [line.strip() for line in handle if line.strip()]
    logger.info("需处理: %d 只", len(codes))

    pro = get_pro()
    total = 0
    fail_streak = 0

    for batch_index in range(0, len(codes), BATCH_SIZE):
        batch = codes[batch_index : batch_index + BATCH_SIZE]
        started = time.monotonic()
        batch_data = []

        for code in batch:
            ok = False
            for attempt in range(3):
                try:
                    items = fetch_one(pro, code)
                    if items:
                        batch_data.append({"code": code, "items": items})
                    ok = True
                    fail_streak = 0
                    break
                except Exception as exc:  # noqa: BLE001 - batch continues per code
                    if attempt == 2:
                        logger.warning("  %s fail: %s", code, exc)
                        fail_streak += 1
                    else:
                        time.sleep(1 + attempt)

            if ok:
                total += 1
            time.sleep(SLEEP_BETWEEN)

        if batch_data:
            filename = OUT_DIR / f"batch_{batch_index // BATCH_SIZE:04d}.json"
            filename.write_text(json.dumps(batch_data, ensure_ascii=False), encoding="utf-8")

        elapsed = time.monotonic() - started
        logger.info(
            "批次 %d/%d: %d stocks in %.1fs, 累计 %d",
            batch_index // BATCH_SIZE + 1,
            (len(codes) + BATCH_SIZE - 1) // BATCH_SIZE,
            len(batch_data),
            elapsed,
            total,
        )

        if fail_streak > 5:
            logger.warning("连续失败太多，重建 Provider Session")
            clear_pro_cache()
            time.sleep(3)
            pro = get_pro()
            fail_streak = 0

    clear_pro_cache()
    logger.info("完成！共拉取 %d 只股票，存于 %s", total, OUT_DIR)


if __name__ == "__main__":
    main()
