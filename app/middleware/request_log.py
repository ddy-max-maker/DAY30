import time

from fastapi import Request

from app.core.logger import logger
from app.core.request_context import (
    generate_request_id,
    request_id_context
)


async def request_log_middleware(
    request: Request,
    call_next
):

    request_id = generate_request_id()


    request_id_context.set(
        request_id
    )


    start_time = time.time()


    response = await call_next(request)


    process_time = (
        time.time() - start_time
    )


    logger.info(
        f"{request.method} "
        f"{request.url.path} "
        f"{response.status_code} "
        f"{process_time:.4f}s"
    )


    response.headers[
        "X-Request-ID"
    ] = request_id


    return response