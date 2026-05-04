# app/integrations/zernio/oauth.py

from __future__ import annotations

import time
from typing import Any

from zerino.publishing.zernio.client import get_zernio_client


def _as_dict(obj: Any) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return dict(obj)


def _get_profile_id(p: Any) -> str | None:
    # Your SDK Profile uses field_id
    if isinstance(p, dict):
        return p.get("field_id") or p.get("_id") or p.get("id")
    return getattr(p, "field_id", None) or getattr(p, "_id", None) or getattr(p, "id", None)


def _get_account_id(a: Any) -> str | None:
    if isinstance(a, dict):
        return a.get("_id") or a.get("id") or a.get("field_id")
    return getattr(a, "_id", None) or getattr(a, "id", None) or getattr(a, "field_id", None)


def get_connect_url(platform: str, profile_id: str, redirect_url: str | None = None) -> str:
    client = get_zernio_client()
    res = client.connect.get_connect_url(
        platform=platform,
        profile_id=profile_id,
        redirect_url=redirect_url,
    )
    auth_url = res.get("authUrl") or res.get("auth_url")
    if not auth_url:
        raise RuntimeError(f"Unexpected get_connect_url response: {res}")
    return auth_url


def connect_social_account(profile_id: str, platform: str, redirect_url: str | None = None) -> str:
    """
    Returns the OAuth URL. User must open it and complete OAuth.
    """
    return get_connect_url(platform=platform, profile_id=profile_id, redirect_url=redirect_url)


def connect_social_account_interactive(
    profile_id: str,
    platform: str,
    *,
    redirect_url: str | None = None,
    poll_profile_id: str | None = None,
    max_wait_seconds: int = 120,
    poll_seconds: int = 2,
) -> str:
    """
    Dev helper:
    - prints the OAuth URL
    - waits for you to complete OAuth in browser
    - polls accounts until the connected account appears
    - returns connected account id
    """
    client = get_zernio_client()

    url = connect_social_account(profile_id, platform, redirect_url=redirect_url)
    print(f"Open this URL and complete OAuth:\n{url}\n")
    input("Press Enter AFTER you finished connecting in the browser...")

    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        res = client.accounts.list(profile_id=poll_profile_id or profile_id)
        accounts = res["accounts"] if isinstance(res, dict) else res.accounts

        for a in accounts:
            raw_platform = a.get("platform") if isinstance(a, dict) else getattr(a, "platform", None)            
            a_platform = _platform_to_service(raw_platform)

            if a_platform and a_platform.lower() == platform.lower():
                account_id = _get_account_id(a)
                if account_id:
                    return account_id

        time.sleep(poll_seconds)

    raise TimeoutError(f"Connected account for platform='{platform}' did not appear after {max_wait_seconds}s.")


def _platform_to_service(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if hasattr(value, "value"):
        return str(value.value)
    if hasattr(value, "name"):
        return str(value.name)
    return str(value)

def pick_existing_profile_id() -> str:
    client = get_zernio_client()
    res = client.profiles.list()
    profiles = res["profiles"] if isinstance(res, dict) else res.profiles

    if not profiles:
        raise RuntimeError("No profiles found.")

    print("Profiles:")
    for p in profiles:
        pd = _as_dict(p)
        print(f"- {pd.get('name')}  id={pd.get('field_id') or pd.get('_id') or pd.get('id')}")

    pid = _get_profile_id(profiles[0])
    if not pid:
        raise RuntimeError(f"Could not determine profile id from: {_as_dict(profiles[0])}")
    return pid


def create_profile(name: str = "My First Profile", description: str = "Testing the Zernio API") -> str:
    client = get_zernio_client()
    res = client.profiles.create(name=name, description=description)

    # could be dict or pydantic; normalize
    rd = _as_dict(res)
    # some SDKs return {"profile": {...}}
    profile = rd.get("profile") or rd
    pid = profile.get("field_id") or profile.get("_id") or profile.get("id")

    if not pid:
        raise RuntimeError(f"Unexpected create_profile response: {rd}")

    print(f"Profile created: {pid}")
    return pid

if __name__ == '__main__':
    from zerino.publishing.zernio.client import get_zernio_client

    client = get_zernio_client()

    res = client.accounts.list()              # or: client.accounts.list(profile_id="<profile_id>")
    accounts = res.accounts

    for a in accounts:
        # a is likely a Pydantic model; model_dump() shows all fields
        print(a.model_dump())
        # common quick view:
        print("platform:", a.platform, "account_id:", getattr(a, "_id", None))


'''
(venv) MacBookAir:posting_system donaldk$ python3 -m app.integrations.zernio.oauth
{'field_id': '69eeb6fa985e734bf3bcbb9a', 'platform': <Platform5.TWITTER: 'twitter'>, 'profileId': {'field_id': '69e8e66be05b1e465a0fa3a0', 'userId': None, 'name': 'My First Profile', 'description': None, 'color': None, 'isDefault': None, 'isOverLimit': None, 'createdAt': None}, 'username': 'Sp00n_718', 'displayName': 'K', 'profilePicture': 'https://pbs.twimg.com/profile_images/1615888592420601860/Gp3PQikf_normal.jpg', 'profileUrl': 'https://x.com/Sp00n_718', 'isActive': True, 'followersCount': None, 'followersLastUpdated': None, 'parentAccountId': None, 'enabled': True, 'metadata': {'scope': 'tweet.moderate.write offline.access dm.read tweet.write media.write like.write users.read dm.write tweet.read bookmark.write follows.write', 'expires_in': 7200, 'profileData': {'id': '415912796', 'username': 'Sp00n_718', 'displayName': 'K', 'profilePicture': 'https://pbs.twimg.com/profile_images/1615888592420601860/Gp3PQikf_normal.jpg', 'profileUrl': 'https://x.com/Sp00n_718', 'followersCount': 170, 'bio': '', 'extraData': {'isPremium': False, 'verifiedType': 'none', 'subscriptionType': 'None', 'followingCount': 265, 'tweetCount': 2474, 'listedCount': 1}}, 'connectedAt': '2026-04-27T01:08:10.303Z'}}
platform: Platform5.TWITTER account_id: None
'''