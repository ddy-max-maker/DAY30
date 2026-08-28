from app.utils.logger import logger
from pathlib import Path
import json


file_path = Path("data/users.json")

with file_path.open("r", encoding="utf-8") as f:
    users = json.load(f)
input_name = input("Enter your name: ")
for user in users:
    if user['name'] == input_name:
        print(f"Name: {user['name']}, Age: {user['age']}, Email: {user['email']}")
        logger.info(
            f"查询用户成功: {input_name}"
        )
        break
else:
        print(f"No user found with the name: {input_name}")    
        logger.warning(
        f"用户不存在: {input_name}"
    )