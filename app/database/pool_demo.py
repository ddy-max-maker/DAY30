from app.database.database import engine


pool = engine.pool

print(pool.status())