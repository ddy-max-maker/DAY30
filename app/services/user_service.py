import logging
from redis.exceptions import LockNotOwnedError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database.redis import redis_client
from app.core.security import hash_password
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate, UserResponse
from app.services.cache_service import (
    get_cache,
    set_cache,
    delete_cache,
    NULL_CACHE,
)
from app.exceptions import BusinessError, VersionConflictError
from app.exceptions.errors import UserNotFoundError,EmailAlreadyExistsError

logger = logging.getLogger(__name__)


def get_all_users(db: Session) -> list[User]:
    return list(db.scalars(select(User)).all())


def get_user_by_id(
    db: Session,
    user_id: int
) -> UserResponse | None:

    """
    Cache Aside
    + 空值缓存防穿透
    + Redis Lock 防击穿
    + Double Check
    + TTL jitter 防雪崩
    + blocking_timeout 超时降级
    """

    key = f"user:{user_id}"
    lock_key = f"lock:user:{user_id}"

    # =========================
    # 1. 第一次查 Redis
    # =========================

    cache_user = get_cache(key)

    # 命中空值缓存
    if cache_user == NULL_CACHE:
        logger.info("[Cache NULL HIT] key=%s", key)
        return None

    # 命中正常缓存
    if cache_user is not None:
        logger.info("[Cache HIT] key=%s", key)

        return UserResponse.model_validate(cache_user)

    logger.info("[Cache MISS] key=%s", key)

    # =========================
    # 2. 创建 Redis Lock
    # =========================

    lock = redis_client.lock(
        lock_key,

        # 锁本身最多存在 10 秒
        timeout=10,

        # 最多等 2 秒抢锁
        blocking_timeout=2,
    )

    # =========================
    # 3. 尝试获取锁
    # =========================

    acquired = lock.acquire()

    # =========================
    # 4. 抢锁失败
    # =========================

    if not acquired:

        logger.warning(
            "[LOCK TIMEOUT] key=%s",
            lock_key
        )

        # 很重要：
        # 等了2秒后，别人可能已经把缓存建好了
        # 所以再查一次 Redis

        cache_user = get_cache(key)

        if cache_user == NULL_CACHE:
            return None

        if cache_user is not None:
            logger.info(
                "[Cache HIT AFTER LOCK TIMEOUT] key=%s",
                key
            )

            return UserResponse.model_validate(
                cache_user
            )

        # 等了2秒缓存仍然没有
        # 不继续攻击数据库
        # 直接降级

        raise BusinessError(
            code=503,
            message="系统繁忙，请稍后重试"
        )

    # =========================
    # 5. 成功获得锁
    # =========================

    try:

        logger.info(
            "[LOCK ACQUIRED] key=%s",
            lock_key
        )

        # =========================
        # 6. Double Check
        # =========================

        cache_user = get_cache(key)

        if cache_user == NULL_CACHE:
            logger.info(
                "[Cache NULL HIT AFTER LOCK] key=%s",
                key
            )

            return None

        if cache_user is not None:
            logger.info(
                "[Cache HIT AFTER LOCK] key=%s",
                key
            )

            return UserResponse.model_validate(
                cache_user
            )

        # =========================
        # 7. 真正查询 MySQL
        # =========================

        logger.info(
            "[DB QUERY] user_id=%s",
            user_id
        )

        user = db.get(User, user_id)

        # =========================
        # 8. 数据库不存在
        # =========================

        if user is None:

            # 空值缓存：
            # 防止不存在的数据反复穿透数据库
            set_cache(
                key,
                NULL_CACHE,

                # 基础60秒
                expire=60,

                # 随机增加0~30秒
                jitter=30,
            )

            logger.info(
                "[Cache NULL SET] key=%s",
                key
            )

            return None

        # =========================
        # 9. ORM → UserResponse
        # =========================

        user_response = UserResponse.model_validate(
            user
        )

        # =========================
        # 10. 回填正常缓存
        # =========================

        set_cache(
            key,
            user_response.model_dump(),

            # 基础5分钟
            expire=300,

            # 随机增加0~60秒
            jitter=60,
        )

        logger.info(
            "[Cache SET] key=%s",
            key
        )

        return user_response

    # =========================
    # 11. 无论成功失败都释放锁
    # =========================

    finally:
        try:
            lock.release()

            logger.info(
                "[LOCK RELEASED] key=%s",
                lock_key
            )

        except LockNotOwnedError:

            logger.warning(
                "[LOCK EXPIRED OR LOST] key=%s",
                lock_key
            )




def create_user(#创建用户
    db: Session,
    user_data: UserCreate
) -> User:

    new_user = User(
        name=user_data.name,
        email=user_data.email,
        password_hash=hash_password(user_data.password),
    )

    try:
        db.add(new_user)

        db.commit()

        db.refresh(new_user)

        return new_user

    except IntegrityError:
        db.rollback()

        raise EmailAlreadyExistsError()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def update_user(db: Session, user_id: int, update_data: UserUpdate) -> UserResponse:
    """乐观锁更新 + 缓存失效。

    核心 SQL：
        UPDATE users SET ... , version = version + 1
        WHERE id = ? AND version = ?   -- 客户端持有的版本

    rowcount == 1 → 没人抢先修改，更新成功
    rowcount == 0 → 记录不存在或 version 已变化（冲突），拒绝覆盖

    顺序：DB 条件更新 → COMMIT → 删 Redis 缓存（Cache Aside：先库后缓存）
    """
    # 先确认用户存在：把 404（记录不存在）和 409（版本冲突）区分开
    user = db.get(User, user_id)
    if user is None:
        raise UserNotFoundError()

    update_dict = update_data.model_dump(exclude_unset=True, exclude={"version"})
    if "password" in update_dict:
        update_dict["password_hash"] = hash_password(update_dict.pop("password"))

    # 只传了 version 没有其他字段：无事可做，直接返回当前数据
    if not update_dict:
        return UserResponse.model_validate(user)

    # =========================
    # 1. 乐观锁条件更新
    # =========================
    stmt = (
        update(User)
        .where(
            User.id == user_id,
            User.version == update_data.version,
        )
        .values(
            **update_dict,
            version=User.version + 1,
        )
        # 不做 session 内对象同步，后面统一 refresh 拿最新值
        .execution_options(synchronize_session=False)
    )

    result = db.execute(stmt)

    # =========================
    # 2. rowcount 判断冲突
    # =========================
    if result.rowcount == 0:
        # version 不匹配：别人比你先更新了，绝不覆盖
        db.rollback()
        logger.warning(
            "[Update CONFLICT] user_id=%s, client version=%s",
            user_id,
            update_data.version,
        )
        raise VersionConflictError()

    # =========================
    # 3. 提交数据库
    # =========================
    db.commit()

    logger.info(
        "[Update OK] user_id=%s, version %s -> %s",
        user_id,
        update_data.version,
        update_data.version + 1,
    )

    # =========================
    # 4. 缓存失效：先 DB 成功，再删 Redis
    #    删缓存失败只记日志，不影响主流程（DB 已 commit）
    # =========================
    cache_key = f"user:{user_id}"
    try:
        delete_cache(cache_key)
        logger.info("[Cache DEL] key=%s (after update)", cache_key)
    except Exception:
        logger.exception("[Cache DEL FAILED] key=%s, DB 已更新但缓存未能删除，依赖 TTL 最终一致", cache_key)

    # =========================
    # 5. 重新查询最新数据（version 已 +1）
    # =========================
    db.refresh(user)

    return UserResponse.model_validate(user)

