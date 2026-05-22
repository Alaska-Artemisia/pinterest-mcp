"""
Me + Lia — Pinterest MCP Server
Exposes Pinterest v5 API actions to Claude via MCP protocol.
"""

import os
import json
import httpx
from typing import Any
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pinterest")

PINTEREST_API = "https://api.pinterest.com/v5"
ACCESS_TOKEN = os.environ.get("PINTEREST_ACCESS_TOKEN", "")


def headers():
    return {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


# ── BOARDS ──────────────────────────────────────────────────

@mcp.tool()
def list_boards() -> str:
    """List all Pinterest boards for the Me + Lia account."""
    r = httpx.get(f"{PINTEREST_API}/boards", headers=headers(), params={"page_size": 100})
    r.raise_for_status()
    boards = r.json().get("items", [])
    result = []
    for b in boards:
        result.append(f"• {b['name']} (id: {b['id']}) — {b.get('description', 'no description')}")
    return "\n".join(result) if result else "No boards found."


@mcp.tool()
def create_board(name: str, description: str = "", privacy: str = "PUBLIC") -> str:
    """
    Create a new Pinterest board.
    privacy: PUBLIC or SECRET
    """
    payload = {"name": name, "description": description, "privacy": privacy}
    r = httpx.post(f"{PINTEREST_API}/boards", headers=headers(), json=payload)
    r.raise_for_status()
    data = r.json()
    return f"Board created: '{data['name']}' (id: {data['id']})"


# ── PINS ─────────────────────────────────────────────────────

@mcp.tool()
def create_pin(
    board_id: str,
    title: str,
    description: str,
    image_url: str,
    link: str = "https://meandlia.com",
    alt_text: str = "",
) -> str:
    """
    Create a new Pinterest pin on a board.
    board_id: the board id (get from list_boards)
    image_url: publicly accessible image URL
    link: destination URL when pin is clicked
    """
    payload = {
        "board_id": board_id,
        "title": title,
        "description": description,
        "link": link,
        "alt_text": alt_text or title,
        "media_source": {
            "source_type": "image_url",
            "url": image_url,
        },
    }
    r = httpx.post(f"{PINTEREST_API}/pins", headers=headers(), json=payload)
    r.raise_for_status()
    data = r.json()
    return f"Pin created: '{data['title']}' (id: {data['id']}) on board {board_id}"


@mcp.tool()
def list_pins(board_id: str) -> str:
    """List recent pins on a specific board."""
    r = httpx.get(
        f"{PINTEREST_API}/boards/{board_id}/pins",
        headers=headers(),
        params={"page_size": 25},
    )
    r.raise_for_status()
    pins = r.json().get("items", [])
    result = []
    for p in pins:
        result.append(f"• {p.get('title', 'Untitled')} (id: {p['id']})")
    return "\n".join(result) if result else "No pins found."


@mcp.tool()
def get_account_info() -> str:
    """Get Me + Lia Pinterest account details."""
    r = httpx.get(f"{PINTEREST_API}/user_account", headers=headers())
    r.raise_for_status()
    data = r.json()
    return (
        f"Account: {data.get('username')}\n"
        f"Name: {data.get('business_name') or data.get('username')}\n"
        f"Followers: {data.get('follower_count', 0)}\n"
        f"Following: {data.get('following_count', 0)}\n"
        f"Pin count: {data.get('pin_count', 0)}"
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
