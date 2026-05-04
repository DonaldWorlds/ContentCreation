# app/integrations/zernio/accounts.py

from __future__ import annotations
from typing import Any
from zerino.publishing.zernio.client import get_zernio_client

'''
Yes. Each connected social account in Zernio has its own account id (a string).

You get it from client.accounts.list(...).
It’s usually in the _id field (24‑char ObjectId string), depending on your SDK model.
When you create/schedule a post, you must include it as accountId inside the platforms list:
'''


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    # last resort
    return dict(obj)


def _get_account_platform(a: Any) -> str | None:
    if isinstance(a, dict):
        return a.get("platform")
    return getattr(a, "platform", None)


def _get_account_id(a: Any) -> str | None:
    # Accounts usually have _id; keep fallbacks
    if isinstance(a, dict):
        return a.get("_id") or a.get("id") or a.get("field_id")
    return getattr(a, "_id", None) or getattr(a, "id", None) or getattr(a, "field_id", None)


def list_accounts(profile_id: str | None = None) -> list[Any]:
    client = get_zernio_client()
    res = client.accounts.list(profile_id=profile_id)
    return res["accounts"] if isinstance(res, dict) else res.accounts


#Account id for twitter 
def resolve_account_id(platform: str, profile_id: str | None = None) -> str:
    accounts = list_accounts(profile_id=profile_id)

    want = platform.lower()
    aliases = {"twitter": {"twitter", "x"}, "x": {"twitter", "x"}}
    acceptable = aliases.get(want, {want})

    for a in accounts:
        a_platform = _platform_to_str(_get_account_platform(a)).lower()
        if a_platform in acceptable:
            account_id = _get_account_id(a)
            if not account_id:
                raise RuntimeError(f"Matched platform={platform} but could not read account id from: {_as_dict(a)}")
            return account_id

    available = [(_get_account_platform(a) or "") for a in accounts]
    raise RuntimeError(f"No connected account for platform={platform}. Available platforms: {available}")

# get all connected accounts
def get_connected_accounts(profile_id: str | None = None) -> list[dict[str, Any]]:
    """
    Returns accounts as dicts (easy to print/log/store).
    """
    accounts = list_accounts(profile_id=profile_id)
    return [_as_dict(a) for a in accounts]

# print connected accounts
def print_connected_accounts(profile_id: str | None = None) -> None:
    for a in get_connected_accounts(profile_id=profile_id):
        print(f"{a.get('platform')}: {a.get('_id') or a.get('id') or a.get('field_id')}")


def _platform_to_str(p) -> str:
    if p is None:
        return ""
    if isinstance(p, str):
        return p
    # Enum from SDK (like Platform5.TWITTER)
    if hasattr(p, "value"):
        return str(p.value)
    if hasattr(p, "name"):
        return str(p.name)
    return str(p)