"""商品 & SKU 业务逻辑。"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.exceptions.errors import ProductNotFoundError, SKUNotFoundError
from app.models.inventory import Inventory
from app.models.product import Product, ProductStatus
from app.models.sku import SKU
from app.schemas.product import ProductCreate, ProductUpdate
from app.schemas.sku import SKUCreate, SKUUpdate


# ================ Product ================
def create_product(db: Session, data: ProductCreate) -> Product:
    product = Product(name=data.name, description=data.description)
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


def get_products_page(
    db: Session,
    page: int,
    page_size: int,
    only_on_sale: bool = False,
) -> tuple[list[Product], int]:
    """分页查询商品。

    返回 (当前页商品列表, 总记录数)。
    使用 LIMIT / OFFSET 在数据库层分页，禁止全量查询后 Python 切片。
    """
    base = select(Product)
    if only_on_sale:
        base = base.where(Product.status == ProductStatus.ON_SALE)

    # 总数
    total = db.scalar(select(func.count()).select_from(base.subquery()))

    # 当前页数据：按 id 升序，LIMIT / OFFSET
    offset = (page - 1) * page_size
    stmt = base.order_by(Product.id).limit(page_size).offset(offset)
    items = list(db.scalars(stmt).all())

    return items, total


def update_product(db: Session, product_id: int, data: ProductUpdate) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise ProductNotFoundError()

    if data.name is not None:
        product.name = data.name
    if data.description is not None:
        product.description = data.description

    db.commit()
    db.refresh(product)
    return product


def update_product_status(
    db: Session, product_id: int, status: ProductStatus
) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise ProductNotFoundError()

    product.status = status
    db.commit()
    db.refresh(product)
    return product


# ================ SKU ================
def create_sku(db: Session, product_id: int, data: SKUCreate) -> SKU:
    """创建 SKU 并同时创建对应的 Inventory 记录。"""

    product = db.get(Product, product_id)
    if product is None:
        raise ProductNotFoundError()

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


def update_sku(db: Session, sku_id: int, data: SKUUpdate) -> SKU:
    sku = db.get(SKU, sku_id)
    if sku is None:
        raise SKUNotFoundError()

    if data.name is not None:
        sku.name = data.name
    if data.price is not None:
        sku.price = data.price
    if data.status is not None:
        sku.status = data.status

    db.commit()
    db.refresh(sku)
    return sku
