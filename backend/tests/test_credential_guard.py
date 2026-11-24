"""凭据门禁的正负样本校验：必须拦住真实泄漏，也必须放过正常代码。

2026-11-24 体检发现 backend/.env.bak.<时间戳> 未被 gitignore，`git add -A` 会把
4 个 API Key 一起提交。门禁本身没被验证过就等于没有门禁，这里用真实形状的字符串跑判定。
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_no_credentials.py"
_spec = importlib.util.spec_from_file_location("check_no_credentials", SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def test_credential_paths_blocked():
    for path in (
        "backend/.env",
        ".env",
        "backend/.env.bak.20261115172248",
        "backend/.env.local",
        "config/.env.production",
    ):
        assert guard.is_credential_path(path), path


def test_template_paths_allowed():
    for path in ("backend/.env.example", ".env.template", "docs/.env.sample"):
        assert not guard.is_credential_path(path), path


def test_hardcoded_secrets_detected():
    for line in (
        'TUSHARE_TOKEN = "8f3c1d9e7b2a4f6c8d0e1b3a5c7d9e2f"',
        "api_key: 'sk-live-abcdef0123456789abcdef'",
        'password="Hunter2Hunter2Hunter2"',
        '{"access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"}',
    ):
        assert guard.scan_line(line) is not None, line


def test_benign_lines_pass():
    for line in (
        'password = os.environ["ASHARE_DB_PASSWORD"]',
        'token = "${ASHARE_DB_PASSWORD}"',
        'api_key = "changeme"',
        "def login(password: str) -> None:",
        'SECRET_KEY = "test"',
        "logger.info('token refreshed')",
        'api_key=""',
    ):
        assert guard.scan_line(line) is None, line