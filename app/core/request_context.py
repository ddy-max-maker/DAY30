from contextvars import ContextVar
import uuid


request_id_context = ContextVar(
    "request_id",
    default=""
)


def generate_request_id():
    return str(uuid.uuid4())