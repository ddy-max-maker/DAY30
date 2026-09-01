from sqlalchemy import select

from app.database.database import SessionLocal
from app.models.todo import Todo


# with SessionLocal() as session:

#     statement = select(Todo)

#     todos = session.scalars(statement).all()

#     for todo in todos:

#         print(
#             todo.id,
#             todo.title,
#             todo.completed,
#             todo.user_id
#         )

# with SessionLocal() as session:

#     new_todo = Todo(
#         title="学习 SQLAlchemy ORM",
#         completed=False,
#         user_id=1
#     )

#     session.add(new_todo)

#     session.commit()

#     print("新增成功")

# with SessionLocal() as session:

#     todo = session.get(Todo, 1)

#     if todo:
#         print(todo.id, todo.title, todo.completed)
#     else:
#         print("Todo不存在")

from app.database.database import SessionLocal
from app.models import Todo, User


with SessionLocal() as session:

    todo = session.get(Todo, 1)

    if todo:
        print("Todo：", todo.title)
        print("user_id：", todo.user_id)

        if todo.user:
            print("所属用户：", todo.user.name)