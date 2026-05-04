def pinterest_payload(
    *,
    account_id: str,
    title: str,
    description: str,
    media_items: list[dict[str, Any]],  # typically image (or video if supported)
    scheduled_for: datetime | str | None = None,
    timezone: str = "UTC",
    link: str | None = None,
    board_id: str | None = None,
) -> dict[str, Any]:
    # Pinterest often needs extra fields (board, destination link).
    # Put them in metadata unless your Zernio docs specify dedicated fields.
    md: dict[str, Any] = {"pinterest": {}}
    if link:
        md["pinterest"]["link"] = link
    if board_id:
        md["pinterest"]["boardId"] = board_id
    if not md["pinterest"]:
        md = {}
    return {
        "title": title,
        "content": description,
        "platforms": [_platform_entry("pinterest", account_id)],
        "media_items": media_items,
        "scheduled_for": scheduled_for,
        "timezone": timezone,
        "metadata": md or None,
    }