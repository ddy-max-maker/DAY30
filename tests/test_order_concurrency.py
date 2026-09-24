"""并发测试：防超卖 / 并发幂等 / 支付与取消竞态。

本阶段最重要的测试：多个线程 + 独立数据库 Session 模拟并发请求
（FastAPI TestClient 并发不稳定，按需求采用 Service 层并发测试）。

覆盖：
1. 防超卖：库存 5 + 20 并发各买 1 → 成功 5、库存不足 15、最终库存 0
2. 并发幂等：同用户同 key 同内容 10 并发 → 只创建 1 个订单、库存只扣一次
3. 支付与取消竞态：两请求同时到达 → 只产生一种合法结果，无双重副作用
4. 多 SKU 事务回滚（并发前提下的原子性确认）
"""

import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from app.core.config import DB_HOST, DB_PASSWORD, DB_PORT, DB_USER
from app.exceptions.errors import BusinessError
from app.models.inventory import Inventory
from app.models.order import Order
from app.schemas.order import OrderCreate, OrderItemRequest
from app.services import order_service, payment_service
from tests.conftest import TestingSessionLocal
from tests.factories.product_factory import create_test_product
from tests.factories.sku_factory import create_test_sku
from tests.factories.user_factory import create_test_merchant, create_test_user


def _make_session_factory() -> sessionmaker:
    """并发专用 session 工厂：独立 engine（连接池 ≥ 并发线程数）。

    每个线程必须用独立 Session，禁止多线程共享同一个 SQLAlchemy Session。
    """
    url = URL.create(
        drivername="mysql+pymysql",
        username=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        database="learner_lab_test",
    )
    engine = create_engine(url, pool_size=30, max_overflow=0, pool_pre_ping=True)
    return sessionmaker(bind=engine)


def _setup_sku(stock: int):
    """直接写库准备商家/商品/SKU/消费者，返回 (user_id, sku_id)。

    返回普通 ID 而非 ORM 对象，避免 session 关闭后对象脱管。
    """
    db = TestingSessionLocal()
    merchant = create_test_merchant(db, email="conc-merchant@example.com")
    product = create_test_product(db, merchant_id=merchant.id, name="ConcProduct")
    sku = create_test_sku(
        db, product_id=product.id, sku_code="CONC-SKU", price="10.00", stock=stock
    )
    user = create_test_user(db, email="conc-user@example.com")
    user_id, sku_id = user.id, sku.id
    db.close()
    return user_id, sku_id


def _get_stock(sku_id: int) -> int:
    db = TestingSessionLocal()
    inv = db.scalar(select(Inventory).where(Inventory.sku_id == sku_id))
    stock = inv.stock
    db.close()
    return stock


def _count_orders() -> int:
    db = TestingSessionLocal()
    count = len(db.scalars(select(Order)).all())
    db.close()
    return count


# ================ 1. 并发防超卖 ================
def test_concurrent_orders_do_not_oversell():
    """库存 5 + 20 并发各买 1 → 成功 5、库存不足 15、库存恰为 0、永不 < 0。"""
    user_id, sku_id = _setup_sku(stock=5)
    SessionFactory = _make_session_factory()
    workers = 20
    barrier = threading.Barrier(workers)

    def buy_one(_: int) -> str:
        db = SessionFactory()
        try:
            barrier.wait(timeout=10)
            order_service.create_order(
                db,
                user_id,
                OrderCreate(items=[OrderItemRequest(sku_id=sku_id, quantity=1)]),
            )
            return "ok"
        except BusinessError as exc:
            return f"err:{exc.code}"
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(buy_one, range(workers)))

    assert results.count("ok") == 5
    assert results.count("err:10103") == 15  # InsufficientStockError
    assert _get_stock(sku_id) == 0
    assert _count_orders() == 5


# ================ 2. 并发幂等：同 key 只创建一个订单 ================
def test_concurrent_same_idempotency_key_creates_single_order():
    """同用户同 key 同内容 10 并发 → 只创建 1 个订单，库存只扣一次。

    唯一约束 UNIQUE(user_id, idempotency_key) 兜底：
    并发通过预检的请求只有一个 INSERT 成功，
    其余在 IntegrityError 后回查返回同一笔订单。
    """
    user_id, sku_id = _setup_sku(stock=10)
    SessionFactory = _make_session_factory()
    workers = 10
    barrier = threading.Barrier(workers)

    def buy_with_key(_: int) -> int:
        db = SessionFactory()
        try:
            barrier.wait(timeout=10)
            order = order_service.create_order(
                db,
                user_id,
                OrderCreate(items=[OrderItemRequest(sku_id=sku_id, quantity=2)]),
                idempotency_key="race-key",
            )
            return order.id
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        order_ids = list(pool.map(buy_with_key, range(workers)))

    assert len(set(order_ids)) == 1  # 只有一个订单
    assert _get_stock(sku_id) == 8  # 只扣一次
    assert _count_orders() == 1


# ================ 3. 支付与取消竞态 ================
def test_payment_and_cancel_race_produces_consistent_result():
    """支付回调与取消同时到达：串行化后只产生一种合法结果。

    情况A：支付先拿锁 → PAID，取消请求看到 PAID → 409，库存不恢复
    情况B：取消先拿锁 → CANCELLED，支付请求看到 CANCELLED → 409，库存恢复
    绝不能出现：CANCELLED 但库存没恢复 / PAID 但库存被恢复。
    """
    user_id, sku_id = _setup_sku(stock=10)
    SessionFactory = _make_session_factory()

    # 准备一笔待支付订单（买 2 件，库存 10 → 8）
    db = SessionFactory()
    order = order_service.create_order(
        db, user_id, OrderCreate(items=[OrderItemRequest(sku_id=sku_id, quantity=2)])
    )
    order_id = order.id
    db.close()

    barrier = threading.Barrier(2)
    outcome: dict[str, str] = {}

    def pay_worker():
        db = SessionFactory()
        try:
            barrier.wait(timeout=10)
            payment_service.payment_callback(db, order_id, "PAY-RACE-0001")
            outcome["pay"] = "ok"
        except BusinessError as exc:
            outcome["pay"] = f"err:{exc.code}"
        finally:
            db.close()

    def cancel_worker():
        db = SessionFactory()
        try:
            barrier.wait(timeout=10)
            order_service.cancel_order(db, order_id, user_id)
            outcome["cancel"] = "ok"
        except BusinessError as exc:
            outcome["cancel"] = f"err:{exc.code}"
        finally:
            db.close()

    t1 = threading.Thread(target=pay_worker)
    t2 = threading.Thread(target=cancel_worker)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    # 查询最终状态
    db = SessionFactory()
    final_order = db.scalar(select(Order).where(Order.id == order_id))
    final_status = final_order.status.value
    db.close()
    stock = _get_stock(sku_id)

    if final_status == "paid":
        # 情况A：支付成功，取消被状态机拒绝，库存不恢复
        assert outcome["pay"] == "ok"
        assert outcome["cancel"] == "err:10105"
        assert stock == 8
    elif final_status == "cancelled":
        # 情况B：取消成功，支付被状态机拒绝，库存恢复
        assert outcome["cancel"] == "ok"
        assert outcome["pay"] == "err:10105"
        assert stock == 10
    else:
        raise AssertionError(f"非法终态: {final_status}")


# ================ 4. 多 SKU 事务回滚（并发安全前提）================
def test_multi_sku_partial_failure_rolls_back_everything():
    """SKU A 库存 10、SKU B 库存 0，下单 A×1 + B×1 → 整单失败。

    确认：A 仍为 10（没有被扣成 9）、B 仍为 0、订单不存在。
    """
    db = TestingSessionLocal()
    merchant = create_test_merchant(db, email="rollback-m@example.com")
    product = create_test_product(db, merchant_id=merchant.id, name="RollbackP")
    sku_a = create_test_sku(
        db, product_id=product.id, sku_code="RB-A", price="1.00", stock=10
    )
    sku_b = create_test_sku(
        db, product_id=product.id, sku_code="RB-B", price="2.00", stock=0
    )
    user = create_test_user(db, email="rollback-u@example.com")
    user_id, sku_a_id, sku_b_id = user.id, sku_a.id, sku_b.id
    db.close()
    SessionFactory = _make_session_factory()

    db = SessionFactory()
    try:
        order_service.create_order(
            db,
            user_id,
            OrderCreate(
                items=[
                    OrderItemRequest(sku_id=sku_a_id, quantity=1),
                    OrderItemRequest(sku_id=sku_b_id, quantity=1),
                ]
            ),
        )
        raised = False
    except BusinessError as exc:
        raised = True
        assert exc.code == 10103  # InsufficientStockError
    finally:
        db.close()

    assert raised
    assert _get_stock(sku_a_id) == 10  # A 未被扣
    assert _get_stock(sku_b_id) == 0
    assert _count_orders() == 0  # 没有留下残缺订单
