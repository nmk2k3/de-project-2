"""Transform the API product response into the required output fields."""
import re

from bs4 import BeautifulSoup


def clean_description(html):
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))


def transform_product(data):
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "url_key": data.get("url_key"),
        "price": data.get("price"),
        "description": clean_description(data.get("description")),
        "images_url": [
            image["base_url"]
            for image in (data.get("images") or [])
            if image.get("base_url")
        ],
    }
