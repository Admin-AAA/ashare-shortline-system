"""因子执行计划的纯领域值对象。

本模块不依赖 ``services.factors``：Registry 负责把已验证的因子图投影为这里的
不可变条目，执行器只消费其稳定顺序和 fingerprint。这样回测 / 运行版本可以记录
同一个 plan fingerprint，而不必持有可变 Registry。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


FACTOR_EXECUTION_PLAN_CONTRACT_VERSION = "factor-execution-plan/v1"


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


@dataclass(frozen=True)
class FactorPlanEntry:
    """一个已验证 Factor 在执行计划中的不可变描述。"""

    factor_id: str
    output_fields: tuple[str, ...]
    depends_on: tuple[str, ...]
    implementation_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_fields", tuple(self.output_fields))
        object.__setattr__(self, "depends_on", tuple(self.depends_on))


@dataclass(frozen=True)
class FactorExecutionPlan:
    """冻结后的确定性 Factor 拓扑计划及其内容指纹。"""

    entries: tuple[FactorPlanEntry, ...]
    contract_version: str = FACTOR_EXECUTION_PLAN_CONTRACT_VERSION
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(
            self,
            "fingerprint",
            hashlib.sha256(self.to_canonical_json().encode("utf-8")).hexdigest(),
        )

    @property
    def factor_ids(self) -> tuple[str, ...]:
        """按实际执行顺序返回 Factor id。"""
        return tuple(entry.factor_id for entry in self.entries)

    def to_canonical_json(self) -> str:
        """返回用于 fingerprint / 审计持久化的稳定 JSON 表示。"""
        return _canonical_json(
            {
                "contract_version": self.contract_version,
                "entries": [
                    {
                        "factor_id": entry.factor_id,
                        "output_fields": list(entry.output_fields),
                        "depends_on": list(entry.depends_on),
                        "implementation_hash": entry.implementation_hash,
                    }
                    for entry in self.entries
                ],
            }
        )
