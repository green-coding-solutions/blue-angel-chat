"""Runtime configuration from environment variables."""
import os
import shutil
from pathlib import Path

MODEL = os.environ.get('MODEL', 'claude-opus-5-5')
EFFORT = os.environ.get('EFFORT', 'medium')
MAX_TURNS = int(os.environ.get('MAX_TURNS', '40'))
HOST = os.environ.get('HOST', '127.0.0.1')
PORT = int(os.environ.get('PORT', '8000'))
ALLOW_REMOTE = os.environ.get('ALLOW_REMOTE') == '1'

# With an API key the SDK bills the API account. Without one, the bundled Claude Code CLI
# uses the existing `claude` login of this OS user (Pro/Max subscription).
# Anthropic does not allow offering subscription login to third parties, so subscription
# mode is meant for local, personal / internal use only.
AUTH_MODE = 'api_key' if os.environ.get('ANTHROPIC_API_KEY') else 'subscription'

# Empty working directory for the agent, so it never sees this project's files.
AGENT_CWD = Path(os.environ.get('AGENT_CWD', Path.home() / '.cache' / 'blue-angel-chat' / 'agent-cwd'))


def check_startup() -> list[str]:
    """Return warnings; raise on configurations that must not run."""
    warnings = []
    if AUTH_MODE == 'subscription':
        if HOST not in ('127.0.0.1', 'localhost', '::1') and not ALLOW_REMOTE:
            raise RuntimeError(
                f'Refusing to bind to {HOST} in subscription mode. Subscription login may only be used '
                'locally for yourself. Set ANTHROPIC_API_KEY for a shared deployment, or ALLOW_REMOTE=1 '
                'if you know what you are doing.')
        if not (Path.home() / '.claude' / '.credentials.json').exists() and not shutil.which('claude'):
            warnings.append('No Claude Code login found. Run `claude` and log in with your subscription.')
    AGENT_CWD.mkdir(parents=True, exist_ok=True)
    return warnings
