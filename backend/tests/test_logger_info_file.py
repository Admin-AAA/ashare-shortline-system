import logging
from types import SimpleNamespace

import app.core.logger as lg


def test_app_log_gets_info_and_noisy_libs_quiet(tmp_path, monkeypatch):
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    monkeypatch.setattr(lg, "_logging_initialized", False)
    monkeypatch.setattr(lg, "get_settings", lambda: SimpleNamespace(
        log_dir=str(tmp_path), log_level="INFO", log_max_bytes=1024 * 1024, log_backup_count=2))
    try:
        lg.setup_logging()
        logging.getLogger("app.services.x").info("hello-info")
        logging.getLogger("httpx").info("noisy")
        for h in root.handlers:
            h.flush()
        text = (tmp_path / "app.log").read_text()
        assert "hello-info" in text and "[pid " in text
        assert "noisy" not in text
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
        root.setLevel(level)


def test_flows_call_setup_logging():
    import inspect
    import app.orchestration.flows as f
    src = inspect.getsource(f)
    assert src.count("_ensure_app_logging()") >= src.count("@flow(") + 0
