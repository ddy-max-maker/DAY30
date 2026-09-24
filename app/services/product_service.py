"""商品 & SKU 业务逻辑。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.exceptions.errors import (
    PermissionDeniedError,
    ProductNotFoundError,
    SKUNotFoundError,
)
from app.models.inventory import Inventory
from app.models.product import Product, ProductStatus
from app.models.sku import SKU
from app.schemas.product import ProductCreate, ProductUpdate
from app.schemas.sku import SKUCreate, SKUUpdate


# ================ 归属校验 ================
def _ensure_product_owner(product: Product, merchant_id: int) -> None:
    """资源归属校验：商品不属于该商家时抛 403。

    角色判断在 permissions.py 依赖里完成，这里只负责归属判断，
    两者缺一不可：MERCHANT 角色 + 资源归属才能操作商品。
    """
    if product.merchant_id != merchant_id:
        raise PermissionDeniedError(message="无权操作其他商家的商品")


def _ensure_sku_owner(sku: SKU, merchant_id: int) -> None:
    """资源归属校验：SKU 所属商品不属于该商家时抛 403。"""
    _ensure_product_owner(sku.product, merchant_id)


# ================ Product ================
def create_product(db: Session, data: ProductCreate, merchant_id: int) -> Product:
    """创建商品。merchant_id 由 Router 层传入（current_user.id），
    ProductCreate schema 中不存在该字段，客户端无法伪造归属。"""
    product = Product(
        name=data.name, description=data.description, merchant_id=merchant_id
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def get_product_by_id(db: Session, product_id: int) -> Product | None:
    return db.get(Product, product_id)


def get_products(db: Session, only_on_sale: bool = False) -> list[Product]:
    stmt = select(Product).order_by(Product.id)
    if only_on_sale:
        stmt = stmt.where(Product.status == ProductStatus.ON_SALE)
    return list(db.scalars(stmt).all())


def get_products_by_merchant(db: Session, merchant_id: int) -> list[Product]:
    """商家查看自己的商品列表（含下架商品）。"""
    stmt = (
        select(Product)
        .where(Product.merchant_id == merchant_id)
        .order_by(Product.id)
    )
    return list(db.scalars(stmt).all())


def update_product(
    db: Session, product_id: int, data: ProductUpdate, merchant_id: int | None = None
) -> Product:
    """更新商品。merchant_id 传 None 表示管理员操作（不校验归属），
    传具体值表示商家操作（必须与商品归属一致）。"""
    product = db.get(Product, product_id)
    if product is None:
        raise ProductNotFoundError()
    if merchant_id is not None:
        _ensure_product_owner(product, merchant_id)

    if data.name is not None:
        product.name = data.name
    if data.description is not None:
        product.description = data.description

    db.commit()
    db.refresh(product)
    return product


def update_product_status(
    db: Session, product_id: int, status: ProductStatus, merchant_id: int | None = None
) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise ProductNotFoundError()
    if merchant_id is not None:
        _ensure_product_owner(product, merchant_id)

    product.status = status
    db.commit()
    db.refresh(product)
    return product


# ================ SKU ================
def create_sku(
    db: Session, product_id: int, data: SKUCreate, merchant_id: int | None = None
) -> SKU:
    """创建 SKU 并同时创建对应的 Inventory 记录。

    merchant_id 非 None 时校验商品归属（商家只能给自己的商品建 SKU）。
    """

    product = db.get(Product, product_id)
    if product is None:
        raise ProductNotFoundError()
    if merchant_id is not None:
        _ensure_product_owner(product, merchant_id)

    sku = SKU(
        product_id=product_id,
        sku_code=data.sku_code,
        name=data.name,
        price=data.price,
        status=data.status,
    )
    inventory = Inventory(sku=sku, stock=data.stock)

    try:
        db.add(sku)
        db.add(inventory)
        db.commit()
        db.refresh(sku)
        db.refresh(inventory)
        return sku
    except IntegrityError:
        db.rollback()
        raise SKUNotFoundError(message=f"SKU 编码 {data.sku_code} 已存在") from None


def get_sku_by_id(db: Session, sku_id: int) -> SKU | None:
    return db.get(SKU, sku_id)


def update_sku(
    db: Session, sku_id: int, data: SKUUpdate, merchant_id: int | None = None
) -> SKU:
    sku = db.get(SKU, sku_id)
    if sku is None:
        raise SKUNotFoundError()
    if merchant_id is not None:
        _ensure_sku_owner(sku, merchant_id)

    if data.name is not None:
        sku.name = data.name
    if data.price is not None:
        sku.price = data.price
    if data.status is not None:
        sku.status = data.status

    db.commit()
    db.refresh(sku)
    return sku
