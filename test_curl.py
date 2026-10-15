import subprocess
import json

url = "https://api.tiki.vn/product-detail/api/v1/products/1391347"

result = subprocess.run(
    [
        "curl",
        "-sS",
        "--fail-with-body",
        "--max-time", "15",
        "-H", "User-Agent: Mozilla/5.0",
        "-H", "Accept: application/json",
        url
    ],
    capture_output=True,
    text=True,
    check=True
)

data = json.loads(result.stdout)

print("ID:", data["id"])
print("Name:", data["name"])