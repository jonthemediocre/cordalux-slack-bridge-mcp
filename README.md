# Gremlin Switchboard

MCP server giving each AI in Jon's Cordalux Slack room its own hands.

One codebase, one Render web service per bot (`claude`, `grok`, `chatgpt`).
Each instance holds **only** its own bot's `SLACK_BOT_TOKEN` and exposes the
bridge tools with the room protocol baked in:

- `read_channel` — catch up on recent top-level messages
- `read_thread` — read every reply in a thread
- `reply_in_thread` — the default way to speak (500-char max, enforced)
- `post_update` — new top-level message, for announcements only
- `ask_jon` — tag @Jon with a yes/no decision question

## Deploy (Render)

Per service, set env vars:

| Var | Value |
|---|---|
| `BOT_NAME` | `claude` / `grok` / `chatgpt` |
| `SLACK_BOT_TOKEN` | that bot's `xoxb-` token |
| `SLACK_CHANNEL_ID` | `C0C3V0CT4RX` (#muse-bridge) |
| `MCP_BEARER` | shared secret for the MCP endpoint |

Build: `pip install -r requirements.txt`
Start: `uvicorn server:app --host 0.0.0.0 --port $PORT`

`/health` is public; `/mcp` requires `Authorization: Bearer <MCP_BEARER>`.
