"""CLI 入口:`python scripts/ingest.py`(§4.5)。

薄包装,把真正逻辑全部委托给 server.indexing.ingest。
默认扫 `INGEST_DATASET_DIR`(`.env` 配置,默认 `dataset/sample/`)。
失败的单商品 skip + log,不阻塞批量;再跑一次自动重试(详见 §4.5.6)。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 给"直接 python 跑"也能找到 server.*
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from server.indexing.ingest import ingest_all, print_summary  # noqa: E402


async def _main() -> int:
    summary = await ingest_all()
    print_summary(summary)
    return 0 if not summary.failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
