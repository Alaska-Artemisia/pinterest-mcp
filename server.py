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
        "columns": "SPEND_IN_DOLLAR,IMPRESSION,TOTAL_CLICKTHROUGH,CTR,TOTAL_CONVERSIONS,CPC_IN_MICRO_DOLLAR,CPM_IN_MICRO_DOLLAR",
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
        "columns": "SPEND_IN_DOLLAR,IMPRESSION,TOTAL_CLICKTHROUGH,CTR,TOTAL_CONVERSIONS,CPC_IN_MICRO_DOLLAR",
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
        "columns": "SPEND_IN_DOLLAR,IMPRESSION,TOTAL_CLICKTHROUGH,CTR,TOTAL_CONVERSIONS,CPC_IN_MICRO_DOLLAR",
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

def format_spend(micro_dollars):
    if micro_dollars is None:
        return "N/A"
    return f"${micro_dollars / 1_000_000:.2f}"

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
                lines = []
                for p in pins:
                    lines.append(f"- {p.get('title', 'Untitled')} (ID: {p['id']})")
                return ok("\n".join(lines))
            
            elif name == "get_account_info":
                info = get_account_info()
                text = f"Username: {info.get('username')}\nAccount type: {info.get('account_type')}\nProfile: {info.get('website_url', 'N/A')}"
                return ok(text)
            
            elif name == "get_ad_accounts":
                accounts = get_ad_accounts()
                if not accounts:
                    return ok("No ad accounts found.")
                lines = [f"- {a['name']} (ID: {a['id']}, Currency: {a.get('currency', 'N/A')})" for a in accounts]
                return ok("\n".join(lines))
            
            elif name == "get_campaigns":
                campaigns = get_campaigns(args["ad_account_id"])
                if not campaigns:
                    return ok("No campaigns found.")
                lines = [f"- {c['name']} (ID: {c['id']}, Status: {c.get('status', 'N/A')}, Objective: {c.get('objective_type', 'N/A')})" for c in campaigns]
                return ok("\n".join(lines))
            
            elif name == "get_campaign_analytics":
                data = get_campaign_analytics(args["ad_account_id"], args["start_date"], args["end_date"], args.get("campaign_ids"))
                if not data:
                    return ok("No analytics data found for this period.")
                lines = ["Campaign Analytics Report", "=" * 40]
                for row in data:
                    metrics = row.get("metrics", {})
                    lines.append(f"\nCampaign ID: {row.get('campaign_id', 'N/A')}")
                    lines.append(f"  Spend: {format_spend(metrics.get('SPEND_IN_DOLLAR'))}")
                    lines.append(f"  Impressions: {metrics.get('IMPRESSION', 'N/A'):,}" if isinstance(metrics.get('IMPRESSION'), int) else f"  Impressions: {metrics.get('IMPRESSION', 'N/A')}")
                    lines.append(f"  Clicks: {metrics.get('TOTAL_CLICKTHROUGH', 'N/A')}")
                    lines.append(f"  CTR: {metrics.get('CTR', 'N/A')}%")
                    lines.append(f"  Conversions: {metrics.get('TOTAL_CONVERSIONS', 'N/A')}")
                return ok("\n".join(lines))
            
            elif name == "get_ad_group_analytics":
                data = get_ad_group_analytics(args["ad_account_id"], args["start_date"], args["end_date"], args.get("ad_group_ids"))
                return ok(json.dumps(data, indent=2))
            
            elif name == "get_ad_analytics":
                data = get_ad_analytics(args["ad_account_id"], args["start_date"], args["end_date"], args.get("ad_ids"))
                return ok(json.dumps(data, indent=2))
            
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

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
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
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "pinterest-mcp", "version": "1.0.0"}
                }
            elif method == "notifications/initialized":
                self.send_response(204)
                self.end_headers()
                return
            else:
                result = handle_mcp(method, params)
            
            response = {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}
        
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    print(f"Starting Pinterest MCP server on port {PORT}")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
