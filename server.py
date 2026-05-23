import os
import json
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler

PINTEREST_API = "https://api.pinterest.com/v5"
ACCESS_TOKEN = os.environ.get("PINTEREST_ACCESS_TOKEN", "")
PORT = int(os.environ.get("PORT", 8000))


def auth_headers():
    return {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}


def list_boards():
    r = httpx.get(f"{PINTEREST_API}/boards", headers=auth_headers(), params={"page_size": 100})
    r.raise_for_status()
    return r.json().get("items", [])


def create_board(name, description="", privacy="PUBLIC"):
    r = httpx.post(f"{PINTEREST_API}/boards", headers=auth_headers(),
                   json={"name": name, "description": description, "privacy": privacy})
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
    r = httpx.post(f"{PINTEREST_API}/pins", headers=auth_headers(), json=payload)
    r.raise_for_status()
    return r.json()


def list_pins(board_id):
    r = httpx.get(f"{PINTEREST_API}/boards/{board_id}/pins", headers=auth_headers(), params={"page_size": 25})
    r.raise_for_status()
    return r.json().get("items", [])


def get_account_info():
    r = httpx.get(f"{PINTEREST_API}/user_account", headers=auth_headers())
    r.raise_for_status()
    return r.json()


def get_ad_accounts():
    r = httpx.get(f"{PINTEREST_API}/ad_accounts", headers=auth_headers(), params={"page_size": 25})
    r.raise_for_status()
    return r.json().get("items", [])


def get_campaigns(ad_account_id):
    r = httpx.get(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/campaigns",
                  headers=auth_headers(), params={"page_size": 25})
    r.raise_for_status()
    return r.json().get("items", [])


def get_campaign_analytics(ad_account_id, start_date, end_date, campaign_ids=None):
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "columns": "SPEND_IN_DOLLAR,IMPRESSION_1,CLICK_1,CTR,CHECKOUT,CPC_IN_MICRO_DOLLAR,CPM_IN_MICRO_DOLLAR",
        "granularity": "TOTAL",
    }
    if campaign_ids:
        params["campaign_ids"] = ",".join(campaign_ids)
    r = httpx.get(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/campaigns/analytics",
                  headers=auth_headers(), params=params)
    r.raise_for_status()
    return r.json()


def get_ad_group_analytics(ad_account_id, start_date, end_date, ad_group_ids=None):
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "columns": "SPEND_IN_DOLLAR,IMPRESSION_1,CLICK_1,CTR,CHECKOUT,CPC_IN_MICRO_DOLLAR",
        "granularity": "TOTAL",
    }
    if ad_group_ids:
        params["ad_group_ids"] = ",".join(ad_group_ids)
    r = httpx.get(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/ad_groups/analytics",
                  headers=auth_headers(), params=params)
    r.raise_for_status()
    return r.json()


def get_ad_analytics(ad_account_id, start_date, end_date, ad_ids=None):
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "columns": "SPEND_IN_DOLLAR,IMPRESSION_1,CLICK_1,CTR,CHECKOUT,CPC_IN_MICRO_DOLLAR",
        "granularity": "TOTAL",
    }
    if ad_ids:
        params["ad_ids"] = ",".join(ad_ids)
    r = httpx.get(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/ads/analytics",
                  headers=auth_headers(), params=params)
    r.raise_for_status()
    return r.json()


TOOLS = {
    "list_boards": {"description": "List all Pinterest boards", "inputSchema": {"type": "object", "properties": {}}},
    "create_board": {"description": "Create a Pinterest board", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"}, "privacy": {"type": "string"}}, "required": ["name"]}},
    "create_pin": {"description": "Create a Pinterest pin on a board", "inputSchema": {"type": "object", "properties": {"board_id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "image_url": {"type": "string"}, "link": {"type": "string"}, "alt_text": {"type": "string"}}, "required": ["board_id", "title", "description", "image_url"]}},
    "list_pins": {"description": "List pins on a board", "inputSchema": {"type": "object", "properties": {"board_id": {"type": "string"}}, "required": ["board_id"]}},
    "get_account_info": {"description": "Get Pinterest account info and stats", "inputSchema": {"type": "object", "properties": {}}},
    "get_ad_accounts": {"description": "List all Pinterest ad accounts", "inputSchema": {"type": "object", "properties": {}}},
    "get_campaigns": {"description": "List all campaigns for an ad account", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}}, "required": ["ad_account_id"]}},
    "get_campaign_analytics": {"description": "Get campaign analytics: spend, impressions, clicks, CTR, conversions for a date range", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"}, "campaign_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ad_account_id", "start_date", "end_date"]}},
    "get_ad_group_analytics": {"description": "Get ad group level analytics for a date range", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}, "ad_group_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ad_account_id", "start_date", "end_date"]}},
    "get_ad_analytics": {"description": "Get individual ad performance analytics for a date range", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}, "ad_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ad_account_id", "start_date", "end_date"]}},
}


def format_spend(micro):
    try:
        return f"${float(micro)/1000000:.2f}"
    except Exception:
        return str(micro)


def handle_mcp(body):
    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def err(msg):
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": msg}}

    if method == "initialize":
        return ok({"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "pinterest", "version": "2.0.0"}})

    if method == "tools/list":
        tools = [{"name": k, "description": v["description"], "inputSchema": v["inputSchema"]} for k, v in TOOLS.items()]
        return ok({"tools": tools})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        try:
            if name == "list_boards":
                boards = list_boards()
                text = "\n".join(f"- {b['name']} (id: {b['id']})" for b in boards) or "No boards found."
            elif name == "create_board":
                b = create_board(args["name"], args.get("description", ""), args.get("privacy", "PUBLIC"))
                text = f"Board created: {b['name']} (id: {b['id']})"
            elif name == "create_pin":
                p = create_pin(args["board_id"], args["title"], args["description"], args["image_url"], args.get("link", "https://meandlia.com"), args.get("alt_text", ""))
                text = f"Pin created: {p['title']} (id: {p['id']})"
            elif name == "list_pins":
                pins = list_pins(args["board_id"])
                text = "\n".join(f"- {p.get('title', 'Untitled')} (id: {p['id']})" for p in pins) or "No pins found."
            elif name == "get_account_info":
                a = get_account_info()
                text = f"Account: {a.get('username')}\nFollowers: {a.get('follower_count', 0)}\nPins: {a.get('pin_count', 0)}"
            elif name == "get_ad_accounts":
                accounts = get_ad_accounts()
                text = "\n".join(f"- {a.get('name', 'Unnamed')} (id: {a['id']}, currency: {a.get('currency', 'N/A')})" for a in accounts) or "No ad accounts found."
            elif name == "get_campaigns":
                campaigns = get_campaigns(args["ad_account_id"])
                text = "\n".join(f"- {c.get('name')} (id: {c['id']}, status: {c.get('status')}, objective: {c.get('objective_type')})" for c in campaigns) or "No campaigns found."
            elif name == "get_campaign_analytics":
                data = get_campaign_analytics(args["ad_account_id"], args["start_date"], args["end_date"], args.get("campaign_ids"))
                lines = []
                for item in data:
                    m = item.get("metrics", {})
                    spend = m.get("SPEND_IN_DOLLAR", 0)
                    impressions = m.get("TOTAL_IMPRESSIONS", 0)
                    clicks = m.get("TOTAL_CLICKS", 0)
                    ctr = float(m.get("CTR", 0)) * 100
                    conversions = m.get("TOTAL_CONVERSIONS", 0)
                    checkouts = m.get("TOTAL_CLICK_CHECKOUT", 0)
                    cpc = format_spend(m.get("CPC_IN_MICRO_DOLLAR", 0))
                    cpm = format_spend(m.get("CPM_IN_MICRO_DOLLAR", 0))
                    lines.append(f"Campaign {item.get('CAMPAIGN_ID', 'N/A')}:\n  Spend: ${float(spend):.2f}\n  Impressions: {int(impressions):,}\n  Clicks: {int(clicks):,}\n  CTR: {ctr:.2f}%\n  Conversions: {conversions}\n  Checkouts (click): {checkouts}\n  CPC: {cpc}\n  CPM: {cpm}")
                text = "\n\n".join(lines) or "No campaign analytics data found."
            elif name == "get_ad_group_analytics":
                data = get_ad_group_analytics(args["ad_account_id"], args["start_date"], args["end_date"], args.get("ad_group_ids"))
                lines = []
                for item in data:
                    m = item.get("metrics", {})
                    lines.append(f"Ad Group {item.get('AD_GROUP_ID', 'N/A')}: Spend ${float(m.get('SPEND_IN_DOLLAR', 0)):.2f}, Impressions {int(m.get('TOTAL_IMPRESSIONS', 0)):,}, Clicks {int(m.get('TOTAL_CLICKS', 0)):,}, CTR {float(m.get('CTR', 0))*100:.2f}%, Conversions {m.get('TOTAL_CONVERSIONS', 0)}")
                text = "\n".join(lines) or "No ad group analytics found."
            elif name == "get_ad_analytics":
                data = get_ad_analytics(args["ad_account_id"], args["start_date"], args["end_date"], args.get("ad_ids"))
                lines = []
                for item in data:
                    m = item.get("metrics", {})
                    lines.append(f"Ad {item.get('AD_ID', 'N/A')}: Spend ${float(m.get('SPEND_IN_DOLLAR', 0)):.2f}, Impressions {int(m.get('TOTAL_IMPRESSIONS', 0)):,}, Clicks {int(m.get('TOTAL_CLICKS', 0)):,}, CTR {float(m.get('CTR', 0))*100:.2f}%, Conversions {m.get('TOTAL_CONVERSIONS', 0)}")
                text = "\n".join(lines) or "No ad analytics found."
            else:
                return err(f"Unknown tool: {name}")
            return ok({"content": [{"type": "text", "text": text}]})
        except Exception as e:
            return err(str(e))

    return err(f"Unknown method: {method}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "version": "2.0.0"}).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        response = handle_mcp(body)
        data = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    print(f"Pinterest MCP server running on port {PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
