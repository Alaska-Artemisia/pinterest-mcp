# Me + Lia — Pinterest MCP Server

Connects Claude to Pinterest via the MCP protocol.

## Tools available
- `list_boards` — list all boards
- `create_board` — create a new board
- `create_pin` — post a pin to a board
- `list_pins` — see recent pins on a board
- `get_account_info` — account stats

## Deploy to Railway

1. Go to railway.app and sign up (free)
2. Click "New Project" → "Deploy from GitHub repo"
3. Upload this folder as a GitHub repo (or use Railway's CLI)
4. Add environment variable: `PINTEREST_ACCESS_TOKEN` = your token
5. Railway gives you a public URL like https://yourapp.up.railway.app
6. In Claude: Settings → Connectors → Add custom connector → paste that URL

## Environment variables required
- `PINTEREST_ACCESS_TOKEN` — generate from Pinterest Developer Platform
