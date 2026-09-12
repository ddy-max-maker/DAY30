"""带正确日志配置的启动脚本，用于缓存击穿测试"""
import logging

# ===== 先配好 root logger，让所有 logger.info 都打到控制台 =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)-30s | %(message)s",
    force=True,
)

# 避免 uvicorn 自己的日志太吵
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

# ===== 启动应用 =====
import uvicorn
uvicorn.run(
    "app.main:app",
    host="127.0.0.1",
    port=8001,
    log_level="info",
    log_config=None,   # 用我们上面 basicConfig 的配置
)