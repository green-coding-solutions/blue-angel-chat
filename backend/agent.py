"""Runs the Claude Agent SDK loop and translates its messages into UI events."""
import logging
from collections.abc import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)
from pydantic import ValidationError

from . import config
from .criteria import load_criteria
from .prompts import SYSTEM_PROMPT
from .schema import Assessment, assessment_json_schema
from .tools import FETCH_TOOL_NAME, web_server

log = logging.getLogger(__name__)

STRUCTURED_OUTPUT_TOOL = 'StructuredOutput'
TOOL_RESULT_PREVIEW_CHARS = 300
REPORT_PROGRESS_STEP = 1000


def build_options(resume: str | None = None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=config.MODEL,
        effort=config.EFFORT,
        thinking={'type': 'adaptive', 'display': 'summarized'},
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={'web': web_server},
        # Only web research: no shell, no file access, no subagents.
        tools=['WebSearch'],
        allowed_tools=['WebSearch', FETCH_TOOL_NAME],
        disallowed_tools=['Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep', 'WebFetch', 'Task', 'NotebookEdit'],
        permission_mode='dontAsk',
        setting_sources=[],
        include_partial_messages=True,
        output_format={'type': 'json_schema', 'schema': assessment_json_schema()},
        max_turns=config.MAX_TURNS,
        cwd=str(config.AGENT_CWD),
        resume=resume,
    )


def enrich_assessment(assessment: Assessment) -> dict:
    """Add criterion titles/sections from criteria.txt and fill in criteria the model skipped."""
    by_id = {c.id: c for c in assessment.criteria}
    criteria = []
    for criterion in load_criteria():
        item = by_id.get(criterion.id)
        entry = item.model_dump() if item else {
            'id': criterion.id, 'status': 'maybe', 'confidence': 'low',
            'comment': 'Not assessed by the agent.', 'evidence': [], 'open_questions': [],
        }
        entry.update(section=criterion.section, section_title=criterion.section_title,
                     title=criterion.title, question=criterion.question)
        criteria.append(entry)
    data = assessment.model_dump()
    data['criteria'] = criteria
    return data


def tool_result_text(block: ToolResultBlock) -> str:
    if isinstance(block.content, str):
        return block.content
    if isinstance(block.content, list):
        return '\n'.join(part.get('text', '') for part in block.content if isinstance(part, dict))
    return ''


class StreamTranslator:
    """Translates raw API stream events (partial messages) into UI events."""

    def __init__(self):
        self.report_block_index = None
        self.report_chars = 0
        self.report_tool_id = None

    def translate(self, event: dict) -> dict | None:
        event_type = event.get('type')
        if event_type == 'content_block_start':
            block = event.get('content_block', {})
            if block.get('type') in ('text', 'thinking'):
                return {'type': f'{block["type"]}_start'}
            # Announce the report as soon as it starts; the complete tool call only arrives at the end.
            if block.get('type') == 'tool_use' and block.get('name') == STRUCTURED_OUTPUT_TOOL:
                self.report_block_index, self.report_chars = event.get('index'), 0
                self.report_tool_id = block.get('id')
                return {'type': 'tool_start', 'id': block.get('id'), 'name': STRUCTURED_OUTPUT_TOOL, 'input': {}}
        elif event_type == 'content_block_delta':
            delta = event.get('delta', {})
            if delta.get('type') == 'text_delta':
                return {'type': 'text_delta', 'text': delta.get('text', '')}
            if delta.get('type') == 'thinking_delta':
                return {'type': 'thinking_delta', 'text': delta.get('thinking', '')}
            if delta.get('type') == 'input_json_delta' and event.get('index') == self.report_block_index:
                before = self.report_chars // REPORT_PROGRESS_STEP
                self.report_chars += len(delta.get('partial_json', ''))
                if self.report_chars // REPORT_PROGRESS_STEP > before:
                    return {'type': 'tool_progress', 'id': self.report_tool_id, 'chars': self.report_chars}
        elif event_type == 'content_block_stop' and event.get('index') == self.report_block_index:
            self.report_block_index = None
        return None


async def run_agent(prompt: str, resume: str | None = None) -> AsyncIterator[dict]:
    """Yield UI events: session, text_start/_delta, thinking_start/_delta, tool_start, tool_result,
    rate_limit, result, error."""
    tool_names: dict[str, str] = {}
    translator = StreamTranslator()
    try:
        async for message in query(prompt=prompt, options=build_options(resume)):
            if isinstance(message, StreamEvent):
                if message.parent_tool_use_id is None and (ui_event := translator.translate(message.event)):
                    yield ui_event

            elif isinstance(message, SystemMessage):
                if message.subtype == 'init':
                    yield {'type': 'session', 'session_id': message.data.get('session_id'),
                           'model': message.data.get('model')}

            elif isinstance(message, AssistantMessage):
                if message.error:
                    yield {'type': 'error', 'kind': message.error,
                           'message': f'Claude returned an error: {message.error}'}
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        tool_names[block.id] = block.name
                        if block.name != STRUCTURED_OUTPUT_TOOL:  # already announced from the stream
                            yield {'type': 'tool_start', 'id': block.id, 'name': block.name, 'input': block.input}

            elif isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        text = tool_result_text(block)
                        yield {'type': 'tool_result', 'id': block.tool_use_id,
                               'name': tool_names.get(block.tool_use_id, ''),
                               'is_error': bool(block.is_error), 'chars': len(text),
                               'preview': text[:TOOL_RESULT_PREVIEW_CHARS]}

            elif isinstance(message, RateLimitEvent):
                info = message.rate_limit_info
                if info.status != 'allowed':
                    yield {'type': 'rate_limit', 'status': info.status, 'resets_at': info.resets_at,
                           'limit_type': info.rate_limit_type, 'utilization': info.utilization}

            elif isinstance(message, ResultMessage):
                stats = {'session_id': message.session_id, 'cost_usd': message.total_cost_usd,
                         'duration_ms': message.duration_ms, 'turns': message.num_turns,
                         'auth_mode': config.AUTH_MODE}
                if message.subtype == 'success' and message.structured_output:
                    try:
                        assessment = Assessment.model_validate(message.structured_output)
                    except ValidationError as exc:
                        yield {'type': 'error', 'kind': 'invalid_output',
                               'message': f'The agent returned an invalid assessment: {exc}', **stats}
                    else:
                        yield {'type': 'result', 'assessment': enrich_assessment(assessment), **stats}
                else:
                    errors = '; '.join(message.errors or []) or message.result or message.subtype
                    yield {'type': 'error', 'kind': message.subtype, 'message': f'Run ended without a report: {errors}',
                           **stats}
    except ClaudeSDKError as exc:
        log.exception('Agent run failed')
        yield {'type': 'error', 'kind': type(exc).__name__, 'message': str(exc)}
