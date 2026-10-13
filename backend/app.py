"""FastAPI server: static frontend + streaming assessment endpoints (Server-Sent Events)."""
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl

from . import config
from .agent import run_agent
from .criteria import criteria_as_dicts
from .prompts import initial_prompt

log = logging.getLogger('blue_angel_chat')
FRONTEND_DIR = Path(__file__).resolve().parent.parent / 'frontend'

# One agent run at a time: protects subscription limits and keeps the local machine responsive.
run_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for warning in config.check_startup():
        log.warning(warning)
    log.warning('auth: %s | model: %s | effort: %s', config.AUTH_MODE, config.MODEL, config.EFFORT)
    yield


app = FastAPI(title='Blue Angel Pre-Check', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=FRONTEND_DIR), name='static')


class AssessRequest(BaseModel):
    url: HttpUrl


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=5000)


def sse(event: dict) -> str:
    return f'data: {json.dumps(event, ensure_ascii=False)}\n\n'


async def locked_stream(prompt: str, resume: str | None) -> AsyncIterator[str]:
    if run_lock.locked():
        yield sse({'type': 'status', 'message': 'Another assessment is running. Waiting for it to finish...'})
    async with run_lock:
        yield sse({'type': 'status', 'message': 'Agent started'})
        async for event in run_agent(prompt, resume=resume):
            yield sse(event)
    yield sse({'type': 'done'})


def event_stream_response(prompt: str, resume: str | None = None) -> StreamingResponse:
    return StreamingResponse(
        locked_stream(prompt, resume),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.get('/')
async def index():
    return FileResponse(FRONTEND_DIR / 'index.html')


@app.get('/api/config')
async def get_config():
    return {'auth_mode': config.AUTH_MODE, 'model': config.MODEL, 'criteria': criteria_as_dicts()}


@app.post('/api/assess')
async def assess(request: AssessRequest):
    return event_stream_response(initial_prompt(str(request.url)))


@app.post('/api/chat')
async def chat(request: ChatRequest):
    if not request.session_id.replace('-', '').isalnum():
        raise HTTPException(status_code=400, detail='Invalid session id')
    return event_stream_response(request.message, resume=request.session_id)


if __name__ == '__main__':
    import uvicorn
    config.check_startup()
    uvicorn.run(app, host=config.HOST, port=config.PORT)
