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
    r = httpx.get(f"{PINTEREST_API}/boards/{board_id}/pins", headers=auth_headers(),
                  params={"page_size": 25})
    r.raise_for_status()
    return r.json().get("items", [])


def get_account_info():
    r = httpx.get(f"{PINTEREST_API}/user_account", headers=auth_headers())
    r.raise_for_status()
    return r.json()


TOOLS = {
    "list_boards": {"description": "List all Pinterest boards", "inputSchema": {"type": "object", "properties": {}}},
    "create_board": {"description": "Create a Pinterest board", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"}, "privacy": {"type": "string"}}, "required": ["name"]}},
    "create_pin": {"description": "Create a Pinterest pin", "inputSchema": {"type": "object", "properties": {"board_id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "image_url": {"type": "string"}, "link": {"type": "string"}, "alt_text": {"type": "string"}}, "required": ["board_id", "title", "description", "image_url"]}},
    "list_pins": {"description": "List pins on a board", "inputSchema": {"type": "object", "properties": {"board_id": {"type": "string"}}, "required": ["board_id"]}},
    "get_account_info": {"description": "Get Pinterest account info", "inputSchema": {"type": "object", "properties": {}}}
}


def handle_mcp(body):
    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def err(msg):
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": msg}}

    if method == "initialize":
        return ok({"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "pinterest", "version": "1.0.0"}})

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
        self.wfile.write(json.dumps({"status": "ok"}).encode())

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
