"""Sorting of a list from the address (?sort=...&dir=...), shared by the stock lists.

The address is never trusted: an unknown column or direction means "default order".
"""

from urllib.parse import urlencode


def chosen_sort(keys: tuple[str, ...], sort: str | None, direction: str | None):
    """(key, direction) from the address, or None (default order) if missing or invalid."""
    if sort not in keys or direction not in (None, "asc", "desc"):
        return None
    return sort, direction or "asc"


def sort_links(
    path: str,
    keys: tuple[str, ...],
    chosen: tuple[str, str] | None,
    default: tuple[str, str],
    keep: dict[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Per column: where a click leads and the aria-sort state. A click on the sorted column
    reverses it. `keep` holds address parts that stay (a filter); the default order counts as
    the chosen one when nothing is chosen."""
    current = chosen or default
    links = {}
    for key in keys:
        active = key == current[0]
        next_dir = "desc" if active and current[1] == "asc" else "asc"
        params = {**(keep or {}), "sort": key, "dir": next_dir}
        links[key] = {
            "href": f"{path}?{urlencode(params)}",
            "aria": ("ascending" if current[1] == "asc" else "descending") if active else "none",
        }
    return links
