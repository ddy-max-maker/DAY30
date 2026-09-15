from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ResponseModel(BaseModel, Generic[T]):
    code: int = 0

    message: str = "success"

    data: T | None = None


class PageResponse(BaseModel, Generic[T]):
    """统一分页响应模型。

    items: 当前页数据
    page: 当前页码（从 1 开始）
    page_size: 每页条数
    total: 总记录数
    total_pages: 总页数（total=0 时为 0，不能整除时向上取整）
    """

    items: list[T]
    page: int
    page_size: int
    total: int
    total_pages: int
