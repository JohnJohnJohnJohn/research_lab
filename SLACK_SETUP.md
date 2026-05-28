# Slack Setup — Research Lab v0.1

The Research Lab exposes a single Slack bot (`@director-bot`) via **Socket Mode**. Users send research tasks in a configured channel; the bot runs `run_pipeline()` and posts structured threaded memos.

## Prerequisites

- Slack workspace admin (or permission to create apps)
- Python env with `slack-bolt>=1.18` installed (`pip install -e .`)
- Research Lab `.env` configured for model providers (OpenRouter, etc.)

## 1. Create a Slack App

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**.
2. Name: `director-bot` (or your preference).
3. Select your workspace.

## 2. Enable Socket Mode

1. **Settings → Socket Mode** → Enable.
2. Generate an **App-Level Token** with scope `connections:write`.
3. Copy token → `SLACK_APP_TOKEN=xapp-...` in `.env`.

## 3. Bot Token Scopes

**OAuth & Permissions → Bot Token Scopes** — add:

| Scope | Purpose |
|-------|---------|
| `app_mentions:read` | @mention triggers |
| `channels:history` | Read channel messages |
| `channels:read` | Channel metadata |
| `chat:write` | Post memos and acks |
| `commands` | Slash commands |
| `reactions:read` | Reaction feedback |

Install the app to your workspace and copy **Bot User OAuth Token** → `SLACK_BOT_TOKEN=xoxb-...`.

## 4. Event Subscriptions

**Event Subscriptions** → Enable → Subscribe to bot events:

- `app_mention`
- `message.channels`
- `reaction_added`

Save changes.

## 5. Slash Commands

Register these commands (point each at your app; no Request URL needed for Socket Mode):

| Command | Description |
|---------|-------------|
| `/rerun` | Re-run full pipeline (optional section hint in text) |
| `/rerun-all` | Re-run last task |
| `/lock` | Lock a section to `coverage_state/[TICKER]/locked_sections.md` |
| `/macro` | Re-run with macro-focused feedback (v0.1: full pipeline) |

## 6. Channel Setup

1. Create `#research-lab` (or use an existing channel).
2. Copy channel ID (right-click channel → **View channel details** → bottom of About).
3. Set `SLACK_CHANNEL_ID=C0XXXXXXXXX` in `.env`.
4. **Invite the bot** to the channel: `/invite @director-bot`.

## 7. Environment Variables

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_CHANNEL_ID=C0XXXXXXXXX
```

See `.env.example` for model and data keys.

## 8. Run

```bash
python lab.py --slack
```

Expected stdout on startup:

```json
{"status": "slack_bridge_running", "channel": "C0XXXXXXXXX"}
```

## 9. Usage

| Action | Behavior |
|--------|----------|
| Message in channel | Starts full research pipeline |
| @mention bot | Same |
| Thread reply under memo/section | Re-run with feedback context |
| 👍 | Log approval (no re-run) |
| 👎 | Prompt for threaded feedback |
| 🔁 | Re-run section (full pipeline in v0.1) |
| 📌 | Lock section to `locked_sections.md` |
| ⚠️ | Compliance hold — pauses ticker until resume |

## v0.1 Limitations

- Section message timestamps tracked **in memory only** (lost on restart).
- `/rerun` and `/rerun-all` run the **full pipeline**; partial step re-runs are v0.2.
- Reactions on non-memo messages are ignored.
- 📌 lock requires `detect_ticker()` to succeed on memo text.
- Bot must be invited to `SLACK_CHANNEL_ID` before messages work.

## Verify Wiring (no tokens)

```bash
python lab.py --test-slack
```
