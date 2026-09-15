"""商品测试工厂。

只负责创建 Product 测试对象，不包含业务断言。
"""

from app.models.product import Product, ProductStatus


def create_test_product(
    db,
    name: str = "TestProduct",
    description: str = "Test product description",
    status: ProductStatus = ProductStatus.ON_SALE,
) -> Product:
    """创建一个商品。"""
    product = Product(name=name, description=description, status=status)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product
