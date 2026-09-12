from concurrent.futures import ThreadPoolExecutor
import requests


URL = "http://127.0.0.1:8001/users/1"


def request_user(i):
    response = requests.get(URL)
    return i, response.status_code


with ThreadPoolExecutor(max_workers=20) as executor:
    results = list(
        executor.map(
            request_user,
            range(20),
        )
    )


for result in results:
    print(result)