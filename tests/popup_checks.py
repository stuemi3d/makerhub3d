"""Shared checks for the pop-ups (issue #76 and later): what a pop-up answer looks like."""

import re

HX = {"HX-Request": "true"}


def is_popup(html: str) -> bool:
    """A pop-up answer is only the content: no page around it, a title, and its own form."""
    return (
        "<html" not in html
        and "<header" not in html
        and 'id="popup-title"' in html
        and "hx-post=" in html
    )


def opens_in_popup(html: str, href: str) -> bool:
    """The page has a link to `href` that opens it in the pop-up and still works as a link."""
    for match in re.finditer(r"<a [^>]*>", html):
        tag = match.group(0)
        if f'href="{href}"' in tag and f'hx-get="{href}"' in tag and "data-popup" in tag:
            return True
    return False


def saved_in_popup(response) -> bool:
    """The answer to a confirmed pop-up: no content, the page closes it and refreshes."""
    return response.status_code == 204 and "popup-saved" in response.headers.get("HX-Trigger", "")


def leaves_to(response, address: str) -> bool:
    """The answer to a confirmed pop-up whose page is gone: the browser goes to `address`."""
    return response.status_code == 204 and response.headers.get("HX-Redirect") == address
