import re
import subprocess
import sys
from pathlib import Path

# 项目凭据规则：.env 与任何凭据备份都不进 Git；新增文件里不得出现写死的密钥值。
ENV_PATH = re.compile(r"(^|/)\.env($|\.)")
ENV_ALLOW = {".env.example", ".env.template", ".env.sample"}
SECRET_ASSIGN = re.compile(
    r"""(?ix)
    (password|passwd|secret|api_key|apikey|access_token|auth_token|token)
    \b["']?\s*[:=]\s*                    # 前缀不加 \b：真实名字是 TUSHARE_TOKEN 这种带下划线的
    ["']([^"'\s]{16,})["']               # JSON 键在冒号前还有一个收尾引号
    """
)
# 占位值与环境变量引用不算泄漏
PLACEHOLDER = re.compile(r"(?i)^(\$\{|\$\(|<|your|xxx|changeme|placeholder|example|dummy|test|none|null|os\.environ)")
# 门禁自身的判定测试必须持有密钥形状的正样本，逐文件豁免（不做通配，新增豁免要显式登记）
SCAN_ALLOW = {"backend/tests/test_credential_guard.py"}


def staged() -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-status", "--diff-filter=ACMR"],
        capture_output=True, text=True, check=True,
    ).stdout
    rows = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        if path:
            rows.append((status.strip(), path))
    return rows


def scan_line(line: str) -> str | None:
    m = SECRET_ASSIGN.search(line)
    if m and not PLACEHOLDER.match(m.group(2)):
        return m.group(1)
    return None


def is_credential_path(path: str) -> bool:
    return bool(ENV_PATH.search(path)) and Path(path).name not in ENV_ALLOW


def main() -> int:
    problems: list[str] = []
    for status, path in staged():
        if is_credential_path(path):
            problems.append(f"{path}: 凭据文件不得提交（.env 及其备份一律排除）")
            continue
        if status != "A" or path in SCAN_ALLOW or not path.endswith((".py", ".json", ".yaml", ".yml", ".toml", ".sh", ".ts", ".tsx")):
            continue
        text = subprocess.run(
            ["git", "show", f":{path}"], capture_output=True, text=True, check=True,
        ).stdout
        for lineno, line in enumerate(text.splitlines(), 1):
            key = scan_line(line)
            if key:
                problems.append(f"{path}:{lineno}: 新增文件里出现写死的 {key} 值")
    if problems:
        print("凭据门禁未通过：")
        for p in problems:
            print(f"  {p}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())