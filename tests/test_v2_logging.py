import logging
import stat

from whatsapp_digest.logging_setup import setup_logging


def test_logging_creates_private_file_and_writes(tmp_path):
    path = tmp_path / "logs" / "digest.log"
    setup_logging(path, 7)
    logger = logging.getLogger("whatsapp_digest.test")
    logger.info("safe operational test")
    for handler in logging.getLogger().handlers:
        flush = getattr(handler, "flush", None)
        if flush:
            flush()
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    assert "safe operational test" in path.read_text(encoding="utf-8")
