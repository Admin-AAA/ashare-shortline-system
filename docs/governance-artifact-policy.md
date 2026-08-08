# 治理证据产物策略

`.kiro/specs/production-evidence-risk-hardening/governance/store/` 是不可变、内容寻址的治理对象库。对象文件使用 `<kind>/<sha256>.json` 路径，`decision_index.jsonl` 和 `events.jsonl` 只是可重建的查询/审计投影。

策略如下：

- 已有治理对象保留，不能因为数量增长而删除或覆盖；它们是审计证据，不是临时构建产物。
- 新治理对象只能通过现有 governance store API 写入，不能手工提交 Markdown、截图、临时 JSON 或复制品作为权威来源。
- 每个对象必须是合法 JSON、64 位小写 SHA-256 文件名，单对象不超过 256 KiB；运行时 `.lock` 文件不入库。
- 证据检索使用对象 ID/哈希和 JSONL 投影，不依赖目录扫描顺序或 Markdown 副本。
- CI 运行 `backend/scripts/check_governance_store.py`，阻止临时文件、损坏对象和超大单对象进入主分支。

该策略治理“新增噪声”和可维护性，不删除现有可追溯证据。
