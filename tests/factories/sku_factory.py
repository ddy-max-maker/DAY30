"""SKU 测试工厂。

只负责创建 SKU 测试对象（含对应 inventory），不包含业务断言。
"""

from decimal import Decimal

from app.models.inventory import Inventory
from app.models.sku import SKU, SKUStatus


def create_test_sku(
    db,
    product_id: int,
    sku_code: str = "TEST-SKU-001",
    name: str = "Test SKU",
    price: str = "99.00",
    stock: int = 100,
    status: SKUStatus = SKUStatus.ACTIVE,
) -> SKU:
    """创建一个 SKU 并初始化库存记录。

    create_sku service 会自动创建 inventory，这里直接写库
    模拟已创建好的状态，方便测试直接使用。
    """
    sku = SKU(
        product_id=product_id,
        sku_code=sku_code,
        name=name,
        price=Decimal(price),
        status=status,
    )
    db.add(sku)
    db.flush()  # 拿到 sku.id

    inventory = Inventory(sku_id=sku.id, stock=stock)
    db.add(inventory)

    db.commit()
    db.refresh(sku)
    return sku
