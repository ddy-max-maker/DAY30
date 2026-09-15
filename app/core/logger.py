"""统一日志初始化。

设计要点：
1. 两个 handler：console（stdout，docker logs 可见）+ file（轮转）。
2. RequestIdFilter 把 request_id 注入到每条 record，无 request_id 时填 "-"。
3. `if not logger.handlers` 防止 import 多次导致重复 handler（一条日志打两遍）。
4. 日志目录不存在时自动创建（os.makedirs exist_ok=True）。
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from app.core.request_context import request_id_context

LOG_DIR = "logs"

os.makedirs(LOG_DIR, exist_ok=True)


logger = logging.getLogger("backend")

logger.setLevel(logging.INFO)


class RequestIdFilter(logging.Filter):
    """把 ContextVar 里的 request_id 注入到每条 LogRecord。

    无 request_id 时（启动期/请求外日志）填 "-"，
    避免出现 "request_id=" 这样的空字段。
    """

    def filter(self, record):
        record.request_id = request_id_context.get() or "-"
        return True


formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | request_id=%(request_id)s | %(message)s"
)

if not logger.handlers:
    # 控制台输出到 stdout，方便 docker logs / 终端查看
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.addFilter(RequestIdFilter())
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件轮转：单文件 10MB，保留 5 个
    file_handler = RotatingFileHandler(
        "logs/app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.addFilter(RequestIdFilter())
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 防止 root logger 重复打印（logger.propagate=True 时会冒泡到 root）
    logger.propagate = False
