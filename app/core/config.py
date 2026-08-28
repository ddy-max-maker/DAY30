import os
from dotenv import load_dotenv

load_dotenv()

name = os.getenv("NAME")
age = int(os.getenv("AGE"))
hobby=os.getenv("hobby")
print(f"Name: {name}, Age: {age}")
print(f"{name}'s Hobby is {hobby}")
print(type(name), type(age), type(hobby))
# database_host = os.getenv("DATABASE_HOST")
# database_user = os.getenv("DATABASE_USER")
# database_password = os.getenv("DATABASE_PASSWORD")