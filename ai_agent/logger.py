import logging

logging.basicConfig(
    level=logging.INFO,
    # format="[%(levelname)s]: %(message)s",
    format="%(message)s",
    force=True
)

# mcp降噪
for name in ["httpx", "requests", "qwen_agent", "mcp"]:
    logging.getLogger(name).setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


def log(text):
    logger.info(f'{text}')
