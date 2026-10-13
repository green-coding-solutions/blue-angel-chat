# Blue Angel Pre-Check (DE-UZ 215)

A chat web app that checks whether a software product could be certified with the
**Blauer Engel für Software (DE-UZ 215)**. You enter a product URL; a Claude agent (the Claude Code harness via the
[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)) crawls the product site, privacy policy,
support pages and source repositories, searches the web, and rates every criterion in `criteria.txt` as
**Verified**, **Maybe** or **Not possible**, with comments, evidence and open questions. The agent's
work (thinking summaries, text, page fetches, searches) is streamed live into the chat.

After the report you can answer the open questions in the chat; the agent resumes the same session and re-issues
the assessment.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Authentication

- **Claude subscription (default, local use only):** leave `ANTHROPIC_API_KEY` unset. The bundled Claude Code CLI
  uses the existing `claude` login of your OS user (`claude auth status` must show `loggedIn: true`). Runs count
  against your Pro/Max limits. Anthropic does not allow offering claude.ai login to third parties, so in this mode
  the server only binds to `127.0.0.1` and runs one assessment at a time.
- **API key:** set `ANTHROPIC_API_KEY`. Required as soon as other people use the app.

## Run

```bash
.venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Configuration via environment variables, see `.env.example`
(`MODEL`, `EFFORT`, `MAX_TURNS`).

## How it works

| File | Purpose |
|---|---|
| `backend/criteria.py` | Parses `criteria.txt` (sections A-D, criteria 1-21) |
| `backend/prompts.py` | System prompt: research strategy and rating rules |
| `backend/schema.py` | Pydantic model of the report, passed as JSON schema to the SDK's `output_format` |
| `backend/tools.py` | `fetch_page` tool: browser user agent (many sites block bots, e.g. ecosia.org returns 403), HTML to text + links, SSRF protection |
| `backend/agent.py` | Runs `claude_agent_sdk.query()` and maps SDK messages to UI events |
| `backend/app.py` | FastAPI: `POST /api/assess`, `POST /api/chat` stream Server-Sent Events |
| `frontend/` | Fomantic UI + vanilla JS chat, reads the SSE stream with `fetch()` |

The agent can only use `fetch_page` and `WebSearch`: no shell, no file access, no subagents. It runs in an empty
working directory and loads no local Claude Code settings.
