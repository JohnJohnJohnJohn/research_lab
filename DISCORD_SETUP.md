# Discord Setup — Research Lab v0.1

The Research Lab exposes a single Discord bot (`@ResearchDirector`) as the human front door. Users run research via `/research` or thread replies; the bot posts structured memo threads.

## 1. Create Discord Application

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications)
2. **New Application** → name it **Research Director**
3. Open the **Bot** tab → **Add Bot** → copy **Bot Token** → `DISCORD_BOT_TOKEN` in `.env`

## 2. Set Bot Permissions and Invite URL

1. Developer Portal → **OAuth2** → **URL Generator**
2. Scopes — select **both** (required for slash commands):
   - `bot`
   - `applications.commands`
3. Bot Permissions:
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Read Message History
   - Use Slash Commands
4. Copy the generated URL, open it in a browser, and add the bot to your **private server**

If you only used the `bot` scope (no `applications.commands`), slash command sync fails with **403 Missing Access (50001)**.

Manual invite URL shape:

```text
https://discord.com/api/oauth2/authorize?client_id=YOUR_APP_ID&permissions=...&scope=bot%20applications.commands
```

Replace `YOUR_APP_ID` with Application ID from **General Information**.

## 3. Get IDs (Developer Mode required)

1. Enable **Developer Mode**: User Settings → Advanced → Developer Mode
2. Right-click your **server name** (not a channel) → **Copy Server ID** → `DISCORD_GUILD_ID`
3. Right-click `#research-lab` channel → **Copy Channel ID** → `DISCORD_CHANNEL_ID`

**Common mistake:** using the channel ID for `DISCORD_GUILD_ID`. The guild ID is the server; the channel ID is different.

## 4. Configure `.env`

```env
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_GUILD_ID=your_server_id_here
DISCORD_CHANNEL_ID=your_channel_id_here
```

Also configure model and data keys per `.env.example`.

## 5. Enable Message Content Intent

1. Developer Portal → your app → **Bot**
2. **Privileged Gateway Intents** → enable **Message Content Intent**
3. Required for thread-reply feedback routing

## 6. Register Slash Commands

Slash commands sync automatically on bot startup to your guild (`DISCORD_GUILD_ID`):

| Command | Purpose |
|---------|---------|
| `/research <task>` | Run full research pipeline |
| `/rerun [section]` | Re-run last task (full pipeline in v0.1) |
| `/rerun-all` | Re-run last task |
| `/lock <section>` | Lock section in `coverage_state/` |
| `/macro <feedback>` | Re-run with macro-focused feedback |

## 7. Run the Bot

```bash
python lab.py --discord
```

Expected stdout:

```json
{"status": "discord_bridge_running", "bot": "ResearchDirector#1234", "guild_id": "...", "channel_id": "..."}
```

## Usage

- **`/research Initiate coverage on 9988 HK`** — starts pipeline; memo posted as channel message + thread
- **Reply in memo thread** — feedback re-run
- **`/rerun` / `/rerun-all`** — full pipeline re-run (v0.1)
- **`/lock thesis`** — append lock entry to `coverage_state/[TICKER]/locked_sections.md`

## v0.1 Limitations

- `_last_task` / `_last_ticker` are in-memory; lost on bot restart
- `/rerun` always runs full pipeline; partial re-runs are v0.2
- No emoji reaction handling (by design — HK accessibility)
- Bot must be in `#research-lab` with correct permissions
