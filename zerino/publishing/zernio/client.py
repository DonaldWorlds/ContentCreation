# app/zernio_client.py
import os
from dotenv import load_dotenv
from functools import lru_cache
from zernio import Zernio

load_dotenv()

@lru_cache(maxsize=1)
def get_zernio_client() -> Zernio:
    api_key = os.getenv("ZERNIO_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ZERNIO_API_KEY (set it in env or .env)")
    return Zernio(api_key=api_key)