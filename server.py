"""
Me + Lia - Pinterest MCP Server (v2)
Exposes Pinterest v5 API actions to Claude via MCP protocol.

Auth: uses a long-lived REFRESH TOKEN to mint short-lived access tokens
automatically, so tokens never go stale. Capture the refresh token once by
visiting /login in a browser (see README / setup notes).

Env vars:
  PINTEREST_CLIENT_ID       - app id (e.g. 1571876)
  PINTEREST_CLIENT_SECRET   - app secret
  PINTEREST_REFRESH_TOKEN   - long-lived refresh token (from /login)
  PINTEREST_ACCESS_TOKEN    - optional static token override (legacy/testing)
  PORT                      - set by host (Railway/Render)
"""

import os
import json
import time
import base64
from urllib.parse import urlparse, parse_qs

import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler

PINTEREST_API = "https://api.pinterest.com/v5"
AUTHORIZE_URL = "https://www.pinterest.com/oauth/"
SCOPES = "ads:read,ads:write,boards:read,boards:write,pins:read,pins:write,user_accounts:read"

CLIENT_ID = os.environ.get("PINTEREST_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("PINTEREST_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("PINTEREST_REFRESH_TOKEN", "")
STATIC_TOKEN = os.environ.get("PINTEREST_ACCESS_TOKEN", "")  # optional override
PORT = int(os.environ.get("PORT", 8080))

# In-memory access-token cache: {"access_token": str, "expires_at": epoch}
_token_cache = {"access_token": "", "expires_at": 0.0}


# ----------------------------------------------------------------------------
# Auth: refresh-token flow
# ----------------------------------------------------------------------------
def _basic_auth_header():
    raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def refresh_access_token():
    """Exchange the refresh token for a fresh access token; cache it."""
    if not REFRESH_TOKEN:
        raise RuntimeError(
            "No PINTEREST_REFRESH_TOKEN set. Visit /login once to authorize, "
            "then paste the refresh token into the host's env vars."
        )
    r = httpx.post(
        f"{PINTEREST_API}/oauth/token",
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": REFRESH_TOKEN},
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    _token_cache["access_token"] = j["access_token"]
    # refresh slightly early; default 30d if not provided
    _token_cache["expires_at"] = time.time() + int(j.get("expires_in", 2592000))
    return _token_cache["access_token"]


def get_access_token():
    if STATIC_TOKEN:
        return STATIC_TOKEN
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]
    return refresh_access_token()


def auth_headers():
    return {"Authorization": f"Bearer {get_access_token()}", "Content-Type": "application/json"}


# ----------------------------------------------------------------------------
# Boards & Pins
# ----------------------------------------------------------------------------
def list_boards():
    r = httpx.get(f"{PINTEREST_API}/boards", headers=auth_headers(), params={"page_size": 100}, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


def create_board(name, description="", privacy="PUBLIC"):
    r = httpx.post(f"{PINTEREST_API}/boards", headers=auth_headers(),
                   json={"name": name, "description": description, "privacy": privacy}, timeout=30)
    r.raise_for_status()
    return r.json()


def create_pin(board_id, title, description, image_url, link="https://meandlia.com", alt_text=""):
    payload = {
        "board_id": board_id,
        "title": title,
        "description": description,
        "link": link,
        "alt_text": alt_text or title,
        "media_source": {"source_type": "image_url", "url": image_url},
    }
    r = httpx.post(f"{PINTEREST_API}/pins", headers=auth_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def list_pins(board_id):
    r = httpx.get(f"{PINTEREST_API}/boards/{board_id}/pins", headers=auth_headers(),
                  params={"page_size": 25}, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


def get_account_info():
    r = httpx.get(f"{PINTEREST_API}/user_account", headers=auth_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------------------------
# Ads: read
# ----------------------------------------------------------------------------
def get_ad_accounts():
    r = httpx.get(f"{PINTEREST_API}/ad_accounts", headers=auth_headers(), params={"page_size": 25}, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


def get_campaigns(ad_account_id):
    r = httpx.get(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/campaigns",
                  headers=auth_headers(), params={"page_size": 25}, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


def get_ad_groups(ad_account_id, campaign_id=None):
    params = {"page_size": 25}
    if campaign_id:
        params["campaign_ids"] = campaign_id
    r = httpx.get(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/ad_groups",
                  headers=auth_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


# ----------------------------------------------------------------------------
# Ads: write
# ----------------------------------------------------------------------------
def update_campaign(ad_account_id, campaign_id, status=None, name=None, daily_budget_micro=None):
    """Update a campaign: pause/resume (status), rename (name), or change daily
    budget (daily_budget_micro). Only provided fields are changed."""
    obj = {"id": str(campaign_id)}
    if status is not None:
        obj["status"] = status
    if name is not None:
        obj["name"] = name
    if daily_budget_micro is not None:
        obj["daily_spend_cap"] = int(daily_budget_micro)
    r = httpx.patch(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/campaigns",
                    headers=auth_headers(), json=[obj], timeout=30)
    r.raise_for_status()
    return r.json()


def create_ad_group(ad_account_id, campaign_id, name, country="US",
                    daily_budget_micro=None, bid_micro=None, billing_event="IMPRESSION",
                    status="PAUSED"):
    # Created PAUSED by default so nothing spends until reviewed in Ads Manager.
    ad_group = {
        "campaign_id": campaign_id,
        "name": name,
        "status": status,
        "billing_event": billing_event,
        "auto_targeting_enabled": True,
        "targeting_spec": {"GEO": [country]},
    }
    if daily_budget_micro is not None:
        ad_group["budget_in_micro_currency"] = int(daily_budget_micro)
        ad_group["budget_type"] = "DAILY"
    if bid_micro is not None:
        ad_group["bid_in_micro_currency"] = int(bid_micro)
    r = httpx.post(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/ad_groups",
                   headers=auth_headers(), json=[ad_group], timeout=30)
    r.raise_for_status()
    return r.json()


def create_ad(ad_account_id, ad_group_id, pin_id, name, status="PAUSED", creative_type="REGULAR"):
    # Created PAUSED by default. Body is an ARRAY of ad objects (v5 bulk create).
    payload = [{
        "ad_group_id": ad_group_id,
        "creative_type": creative_type,
        "pin_id": pin_id,
        "name": name,
        "status": status,
    }]
    r = httpx.post(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/ads",
                   headers=auth_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------------------------
# Ads: analytics  (validated v5 column names)
# ----------------------------------------------------------------------------
_ANALYTICS_COLS = "SPEND_IN_DOLLAR,IMPRESSION_1,CLICKTHROUGH_1,OUTBOUND_CLICK_1,CTR,TOTAL_CONVERSIONS"


def _analytics(path, ad_account_id, start_date, end_date, id_param=None, ids=None):
    params = {"start_date": start_date, "end_date": end_date,
              "columns": _ANALYTICS_COLS, "granularity": "TOTAL"}
    if id_param and ids:
        params[id_param] = ",".join(ids)
    r = httpx.get(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/{path}/analytics",
                  headers=auth_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_campaign_analytics(ad_account_id, start_date, end_date, campaign_ids=None):
    return _analytics("campaigns", ad_account_id, start_date, end_date, "campaign_ids", campaign_ids)


def get_ad_group_analytics(ad_account_id, start_date, end_date, ad_group_ids=None):
    return _analytics("ad_groups", ad_account_id, start_date, end_date, "ad_group_ids", ad_group_ids)


def get_ad_analytics(ad_account_id, start_date, end_date, ad_ids=None):
    return _analytics("ads", ad_account_id, start_date, end_date, "ad_ids", ad_ids)


# ----------------------------------------------------------------------------
# MCP tool registry
# ----------------------------------------------------------------------------
TOOLS = {
    "list_boards": {"description": "List all Pinterest boards", "inputSchema": {"type": "object", "properties": {}}},
    "create_board": {"description": "Create a Pinterest board", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"}, "privacy": {"type": "string"}}, "required": ["name"]}},
    "create_pin": {"description": "Create a Pinterest pin on a board. image_url must be a public HTTPS URL.", "inputSchema": {"type": "object", "properties": {"board_id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "image_url": {"type": "string"}, "link": {"type": "string"}, "alt_text": {"type": "string"}}, "required": ["board_id", "title", "description", "image_url"]}},
    "list_pins": {"description": "List pins on a board", "inputSchema": {"type": "object", "properties": {"board_id": {"type": "string"}}, "required": ["board_id"]}},
    "get_account_info": {"description": "Get Pinterest account info and stats", "inputSchema": {"type": "object", "properties": {}}},
    "get_ad_accounts": {"description": "List all Pinterest ad accounts", "inputSchema": {"type": "object", "properties": {}}},
    "get_campaigns": {"description": "List all campaigns for an ad account", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}}, "required": ["ad_account_id"]}},
    "update_campaign": {"description": "Update a campaign: pause/resume (status=PAUSED|ACTIVE), rename (name), or change daily budget (daily_budget_micro, in micro-currency e.g. 20 AUD = 20000000). Only provided fields change.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "campaign_id": {"type": "string"}, "status": {"type": "string", "description": "PAUSED or ACTIVE"}, "name": {"type": "string"}, "daily_budget_micro": {"type": "integer"}}, "required": ["ad_account_id", "campaign_id"]}},
    "get_ad_groups": {"description": "List ad groups for an ad account, optionally filtered to one campaign", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "campaign_id": {"type": "string"}}, "required": ["ad_account_id"]}},
    "create_ad_group": {"description": "Create an ad group under a campaign. Created PAUSED by default. For CBO campaigns (Consideration/Web Conversion) budget is inherited from the campaign and can be omitted.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "campaign_id": {"type": "string"}, "name": {"type": "string"}, "country": {"type": "string", "description": "Two-letter geo code, default US"}, "daily_budget_micro": {"type": "integer", "description": "ABO only: daily budget in micro-currency (e.g. 5 USD = 5000000)"}, "bid_micro": {"type": "integer"}, "billing_event": {"type": "string", "description": "IMPRESSION or CLICKTHROUGH"}, "status": {"type": "string", "description": "PAUSED (default) or ACTIVE"}}, "required": ["ad_account_id", "campaign_id", "name"]}},
    "create_ad": {"description": "Create an ad by promoting an existing pin into an ad group. Created PAUSED by default so it does not spend until you set it live in Ads Manager.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "ad_group_id": {"type": "string"}, "pin_id": {"type": "string"}, "name": {"type": "string"}, "status": {"type": "string", "description": "PAUSED (default) or ACTIVE"}, "creative_type": {"type": "string", "description": "Default REGULAR (standard image pin)"}}, "required": ["ad_account_id", "ad_group_id", "pin_id", "name"]}},
    "get_campaign_analytics": {"description": "Get campaign analytics: spend, impressions, clicks, CTR, conversions for a date range", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"}, "campaign_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ad_account_id", "start_date", "end_date"]}},
    "get_ad_group_analytics": {"description": "Get ad group level analytics for a date range", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}, "ad_group_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ad_account_id", "start_date", "end_date"]}},
    "get_ad_analytics": {"description": "Get individual ad performance analytics for a date range", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}, "ad_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ad_account_id", "start_date", "end_date"]}},
}


def format_spend(micro_dollars):
    if micro_dollars is None:
        return "N/A"
    return f"${micro_dollars / 1_000_000:.2f}"


def extract_created(resp):
    items = resp.get("items") if isinstance(resp, dict) else resp
    if not items:
        return None, None
    first = items[0]
    if isinstance(first, dict):
        return first.get("data", first), first.get("exceptions")
    return first, None


def _fmt_analytics(title, data):
    if not data:
        return ok(f"No analytics data found for this period.")
    lines = [title, "=" * 40]
    rows = data if isinstance(data, list) else [data]
    for row in rows:
        metrics = row.get("metrics", row) if isinstance(row, dict) else {}
        ident = row.get("campaign_id") or row.get("ad_group_id") or row.get("ad_id") or ""
        if ident:
            lines.append(f"\nID: {ident}")
        for k, v in metrics.items():
            if k.endswith("_ID"):
                continue
            if k == "SPEND_IN_DOLLAR":
                lines.append(f"  Spend: {format_spend(v * 1_000_000) if isinstance(v, (int, float)) else v}")
            else:
                lines.append(f"  {k}: {v}")
    return ok("\n".join(lines))


def handle_mcp(method, params):
    if method == "tools/list":
        return {"tools": [{"name": k, "description": v["description"], "inputSchema": v["inputSchema"]} for k, v in TOOLS.items()]}

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        try:
            if name == "list_boards":
                boards = list_boards()
                text = "\n".join([f"- {b['name']} (ID: {b['id']})" for b in boards]) or "No boards found."
                return ok(text)

            elif name == "create_board":
                board = create_board(args["name"], args.get("description", ""), args.get("privacy", "PUBLIC"))
                return ok(f"Board created: {board['name']} (ID: {board['id']})")

            elif name == "create_pin":
                pin = create_pin(args["board_id"], args["title"], args["description"], args["image_url"], args.get("link", "https://meandlia.com"), args.get("alt_text", ""))
                return ok(f"Pin created! ID: {pin['id']}\nURL: https://pinterest.com/pin/{pin['id']}/")

            elif name == "list_pins":
                pins = list_pins(args["board_id"])
                if not pins:
                    return ok("No pins found on this board.")
                return ok("\n".join([f"- {p.get('title', 'Untitled')} (ID: {p['id']})" for p in pins]))

            elif name == "get_account_info":
                info = get_account_info()
                return ok(f"Username: {info.get('username')}\nAccount type: {info.get('account_type')}\nProfile: {info.get('website_url', 'N/A')}")

            elif name == "get_ad_accounts":
                accounts = get_ad_accounts()
                if not accounts:
                    return ok("No ad accounts found.")
                return ok("\n".join([f"- {a['name']} (ID: {a['id']}, Currency: {a.get('currency', 'N/A')})" for a in accounts]))

            elif name == "get_campaigns":
                campaigns = get_campaigns(args["ad_account_id"])
                if not campaigns:
                    return ok("No campaigns found.")
                lines = []
                for c in campaigns:
                    cap = c.get("daily_spend_cap")
                    cap_s = format_spend(cap) if isinstance(cap, int) else "N/A"
                    lines.append(f"- {c['name']} (ID: {c['id']}, Status: {c.get('status', 'N/A')}, Objective: {c.get('objective_type', 'N/A')}, Daily cap: {cap_s})")
                return ok("\n".join(lines))

            elif name == "update_campaign":
                resp = update_campaign(args["ad_account_id"], args["campaign_id"],
                                       args.get("status"), args.get("name"), args.get("daily_budget_micro"))
                data, exc = extract_created(resp)
                if exc:
                    return err(f"Update returned exceptions: {json.dumps(exc)}")
                d = data or {}
                return ok(f"Campaign updated. ID: {d.get('id', args['campaign_id'])}, Status: {d.get('status', 'N/A')}, Name: {d.get('name', 'N/A')}")

            elif name == "get_ad_groups":
                ad_groups = get_ad_groups(args["ad_account_id"], args.get("campaign_id"))
                if not ad_groups:
                    return ok("No ad groups found.")
                return ok("\n".join([f"- {a.get('name', 'Unnamed')} (ID: {a['id']}, Status: {a.get('status', 'N/A')}, Campaign: {a.get('campaign_id', 'N/A')})" for a in ad_groups]))

            elif name == "create_ad_group":
                resp = create_ad_group(args["ad_account_id"], args["campaign_id"], args["name"],
                                       args.get("country", "US"), args.get("daily_budget_micro"),
                                       args.get("bid_micro"), args.get("billing_event", "IMPRESSION"),
                                       args.get("status", "PAUSED"))
                data, exc = extract_created(resp)
                if exc:
                    return err(f"Ad group creation returned exceptions: {json.dumps(exc)}")
                return ok(f"Ad group created (PAUSED unless set otherwise)! ID: {data.get('id')} (status: {data.get('status', 'N/A')})")

            elif name == "create_ad":
                resp = create_ad(args["ad_account_id"], args["ad_group_id"], args["pin_id"],
                                 args["name"], args.get("status", "PAUSED"), args.get("creative_type", "REGULAR"))
                data, exc = extract_created(resp)
                if exc:
                    return err(f"Ad creation returned exceptions: {json.dumps(exc)}")
                return ok(f"Ad created (PAUSED unless set otherwise)! ID: {data.get('id')} (status: {data.get('status', 'N/A')})")

            elif name == "get_campaign_analytics":
                data = get_campaign_analytics(args["ad_account_id"], args["start_date"], args["end_date"], args.get("campaign_ids"))
                return _fmt_analytics("Campaign Analytics Report", data)

            elif name == "get_ad_group_analytics":
                data = get_ad_group_analytics(args["ad_account_id"], args["start_date"], args["end_date"], args.get("ad_group_ids"))
                return _fmt_analytics("Ad Group Analytics Report", data)

            elif name == "get_ad_analytics":
                data = get_ad_analytics(args["ad_account_id"], args["start_date"], args["end_date"], args.get("ad_ids"))
                return _fmt_analytics("Ad Analytics Report", data)

            else:
                return err(f"Unknown tool: {name}")

        except httpx.HTTPStatusError as e:
            return err(f"Pinterest API error: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            return err(f"Error: {str(e)}")

    return err(f"Unknown method: {method}")


def ok(text):
    return {"content": [{"type": "text", "text": text}]}


def err(msg):
    return {"content": [{"type": "text", "text": msg}], "isError": True}


# ----------------------------------------------------------------------------
# HTTP handler: /mcp (POST), /health, /login, /callback, / (GET)
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _html(self, code, body):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            return

        if path == "/":
            self._html(200, "<h2>Pinterest MCP</h2><p>Status: ok. POST /mcp for tools. Visit <a href='/login'>/login</a> to (re)authorize.</p>")
            return

        if path == "/login":
            host = self.headers.get("Host", "")
            redirect_uri = f"https://{host}/callback"
            url = (f"{AUTHORIZE_URL}?client_id={CLIENT_ID}&redirect_uri={redirect_uri}"
                   f"&response_type=code&scope={SCOPES}&state=melia")
            self.send_response(302)
            self.send_header("Location", url)
            self.end_headers()
            return

        if path == "/callback":
            global REFRESH_TOKEN
            qs = parse_qs(parsed.query)
            code = (qs.get("code") or [""])[0]
            if not code:
                self._html(400, "<h3>No code in callback.</h3>")
                return
            host = self.headers.get("Host", "")
            redirect_uri = f"https://{host}/callback"
            try:
                r = httpx.post(
                    f"{PINTEREST_API}/oauth/token",
                    headers={"Authorization": _basic_auth_header(),
                             "Content-Type": "application/x-www-form-urlencoded"},
                    data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
                    timeout=30,
                )
                r.raise_for_status()
                j = r.json()
                rt = j.get("refresh_token", "")
                at = j.get("access_token", "")
                # Make the server usable immediately (until restart).
                if rt:
                    REFRESH_TOKEN = rt
                if at:
                    _token_cache["access_token"] = at
                    _token_cache["expires_at"] = time.time() + int(j.get("expires_in", 2592000))
                self._html(200,
                    "<h2>Authorized.</h2>"
                    "<p>Copy this refresh token and paste it into the host's env var "
                    "<b>PINTEREST_REFRESH_TOKEN</b>, then redeploy so it survives restarts:</p>"
                    f"<textarea style='width:100%;height:120px'>{rt}</textarea>"
                    "<p>Scopes granted: " + j.get("scope", "") + "</p>")
            except httpx.HTTPStatusError as e:
                self._html(502, f"<h3>Token exchange failed: {e.response.status_code}</h3><pre>{e.response.text}</pre>")
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/mcp":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
            method = req.get("method", "")
            params = req.get("params", {})
            req_id = req.get("id")

            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "pinterest-mcp", "version": "2.0.0"}}
            elif method == "notifications/initialized":
                self.send_response(204)
                self.end_headers()
                return
            else:
                result = handle_mcp(method, params)

            response = {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}

        out = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    print(f"Starting Pinterest MCP server (v2) on port {PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
