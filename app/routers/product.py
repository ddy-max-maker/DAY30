"""普通用户商品浏览接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.dependencies.auth import get_current_user
from app.exceptions.errors import ProductNotFoundError
from app.models.product import ProductStatus
from app.models.sku import SKUStatus
from app.models.user import User
from app.schemas.common import ResponseModel
from app.schemas.product import (
    ProductDetailResponse,
    ProductResponse,
    SKUBriefResponse,
)
from app.services import product_service

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("", response_model=ResponseModel[list[ProductResponse]])
def list_products(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ResponseModel[list[ProductResponse]]:
    """浏览在售商品列表（不含下架商品）。"""
    products = product_service.get_products(db, only_on_sale=True)
    return ResponseModel(
        data=[ProductResponse.model_validate(p) for p in products]
    )


@router.get("/{product_id}", response_model=ResponseModel[ProductDetailResponse])
def get_product(
    product_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ResponseModel[ProductDetailResponse]:
    """查看商品详情（含在售 SKU）。"""
    product = product_service.get_product_by_id(db, product_id)

    if product is None or product.status != ProductStatus.ON_SALE:
        raise ProductNotFoundError()

    active_skus = [
        SKUBriefResponse.model_validate(sku)
        for sku in product.skus
        if sku.status == SKUStatus.ACTIVE
    ]

    return ResponseModel(
        data=ProductDetailResponse(
            id=product.id,
            name=product.name,
            description=product.description,
            status=product.status,
            skus=active_skus,
        )
    )
