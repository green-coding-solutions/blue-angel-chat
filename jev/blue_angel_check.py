#!/usr/bin/env python3
"""Blue Angel for Software (DE-UZ 215) pre-check.

Fetches a product webpage, sends it together with the certification criteria to
the Jev decision model (OpenAI-compatible chat completions API) and streams the
per-criterion verdicts live into the terminal.

Configuration via env vars: JEV_BASE_URL, JEV_API_KEY, JEV_MODEL
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

DEFAULT_CRITERIA = Path(__file__).resolve().parent.parent / 'criteria.txt'
USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/128.0 Safari/537.36')

STATUS_STYLE = {
    'pending': ('… pending', 'dim'),
    'verified': ('✅ Verified', 'bold green'),
    'not_possible': ('❌ Not possible', 'bold red'),
    'maybe': ('❓ Maybe', 'bold yellow'),
}

console = Console()


# --------------------------------------------------------------------------- criteria

def parse_criteria(path):
    criteria = []
    section = None
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if m := re.match(r'^([A-Z])\.\s+(.+)$', line):
            section = f'{m.group(1)}. {m.group(2)}'
        elif m := re.match(r'^(\d+)\.\s+([^:]+):\s*(.+)$', line):
            criteria.append({
                'id': int(m.group(1)),
                'section': section,
                'title': m.group(2).strip(),
                'text': m.group(3).strip(),
            })
    return criteria


# --------------------------------------------------------------------------- fetching

def fetch_page(url, max_chars):
    resp = httpx.get(url, follow_redirects=True, timeout=30, headers={'User-Agent': USER_AGENT})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')

    title = soup.title.get_text(strip=True) if soup.title else ''
    meta = soup.find('meta', attrs={'name': 'description'})
    description = meta.get('content', '').strip() if meta else ''

    host = urlparse(str(resp.url)).netloc
    links = {}
    for a in soup.find_all('a', href=True):
        href = urljoin(str(resp.url), a['href']).split('#')[0]
        label = a.get_text(' ', strip=True)[:80]
        if href.startswith('http') and href not in links:
            links[href] = label
    # same-site links first, they usually contain privacy/download/docs hints
    ordered = sorted(links.items(), key=lambda kv: urlparse(kv[0]).netloc != host)[:150]

    for tag in soup(['script', 'style', 'noscript', 'svg', 'template']):
        tag.decompose()
    text = re.sub(r'\n\s*\n+', '\n\n', soup.get_text('\n', strip=True))

    return {
        'url': str(resp.url),
        'status': resp.status_code,
        'bytes': len(resp.content),
        'title': title,
        'description': description,
        'text': text[:max_chars],
        'text_truncated': len(text) > max_chars,
        'links': ordered,
    }


# --------------------------------------------------------------------------- prompt

SYSTEM_PROMPT = """You are an expert auditor for the German eco-label "Blauer Engel für Software" (DE-UZ 215, resource and energy-efficient software products).
You perform a pre-check: based ONLY on the provided webpage content of a software product, you assess each certification criterion.

For every criterion decide one status:
- "verified": the page contains clear evidence that the criterion is fulfilled (or the fallback condition is fulfilled).
- "not_possible": the page contains clear evidence of a disqualifier that cannot reasonably be fixed (e.g. product is an operating system, contains third-party advertising).
- "maybe": not enough information on the page, or it depends on the applicant's willingness/measurements. Say exactly what the vendor must clarify or provide.

Rules:
- Write all comments in English, even though the criteria are in German.
- Never invent evidence. Evidence must be a short quote from the page text or a URL from the link list.
- Criteria 19-21 are about the applicant's willingness; they are almost always "maybe" unless the page explicitly commits.
- Be concise: 1-3 comments per criterion.

OUTPUT FORMAT (strict): NDJSON. Output exactly one JSON object per line, no markdown, no code fences, no other text.
First one line per criterion, in ascending id order:
{"id": <int>, "status": "verified"|"not_possible"|"maybe", "confidence": <float 0..1>, "comments": ["..."], "evidence": ["..."]}
Then one final line:
{"summary": "<2-4 sentence overall assessment>", "overall": "likely"|"unclear"|"unlikely"}
"""


def build_user_prompt(criteria, page):
    crit = '\n'.join(f'{c["id"]}. [{c["section"]}] {c["title"]}: {c["text"]}' for c in criteria)
    links = '\n'.join(f'- {u} ({label})' if label else f'- {u}' for u, label in page['links'])
    return f"""CRITERIA (DE-UZ 215):
{crit}

WEBPAGE: {page['url']}
Title: {page['title']}
Meta description: {page['description']}

Visible text{' (truncated)' if page['text_truncated'] else ''}:
\"\"\"
{page['text']}
\"\"\"

Links on the page:
{links}
"""


# --------------------------------------------------------------------------- model

def stream_model(messages):
    """Yields text deltas from the Jev model (OpenAI-compatible streaming chat completions)."""
    base_url = os.environ['JEV_BASE_URL'].rstrip('/')
    headers = {'Authorization': f'Bearer {os.environ["JEV_API_KEY"]}'}
    body = {'model': os.environ['JEV_MODEL'], 'messages': messages, 'stream': True, 'temperature': 0}

    with httpx.stream('POST', f'{base_url}/chat/completions', json=body, headers=headers,
                      timeout=httpx.Timeout(30, read=300)) as resp:
        if resp.status_code >= 400:
            resp.read()
            raise RuntimeError(f'Jev API error {resp.status_code}: {resp.text[:500]}')
        for line in resp.iter_lines():
            if not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                break
            chunk = json.loads(data)
            for choice in chunk.get('choices', []):
                if delta := (choice.get('delta') or {}).get('content'):
                    yield delta


# --------------------------------------------------------------------------- display

class Dashboard:
    def __init__(self, criteria):
        self.criteria = criteria
        self.results = {c['id']: {'status': 'pending', 'comments': [], 'evidence': []} for c in criteria}
        self.phase = 'starting'
        self.chars = 0
        self.warnings = []

    def __rich__(self):
        table = Table(expand=True, show_lines=False)
        table.add_column('#', justify='right', width=3)
        table.add_column('Criterion', ratio=2)
        table.add_column('Status', width=16)
        table.add_column('Comments', ratio=5)
        section = None
        for c in self.criteria:
            if c['section'] != section:
                section = c['section']
                table.add_row('', Text(section, style='bold cyan'), '', '')
            r = self.results[c['id']]
            label, style = STATUS_STYLE.get(r['status'], STATUS_STYLE['maybe'])
            table.add_row(str(c['id']), c['title'], Text(label, style=style), ' '.join(r['comments']))
        footer = Spinner('dots', text=Text(f'{self.phase}  ({self.chars} chars received)', style='magenta'))
        parts = [table, footer]
        parts += [Text(f'⚠ {w}', style='yellow') for w in self.warnings[-3:]]
        return Group(*parts)


def print_details(criteria, results, summary):
    for c in criteria:
        r = results[c['id']]
        label, style = STATUS_STYLE.get(r['status'], STATUS_STYLE['maybe'])
        body = Text()
        body.append(c['text'] + '\n\n', style='dim')
        for comment in r['comments']:
            body.append(f'• {comment}\n')
        for ev in r['evidence']:
            body.append(f'  ↳ {ev}\n', style='italic blue')
        conf = r.get('confidence')
        subtitle = f'confidence {conf:.2f}' if isinstance(conf, (int, float)) else None
        console.print(Panel(body, title=Text(f'{c["id"]}. {c["title"]} — {label}', style=style),
                            subtitle=subtitle, title_align='left'))
    if summary:
        style = {'likely': 'green', 'unlikely': 'red'}.get(summary.get('overall'), 'yellow')
        console.print(Panel(summary.get('summary', ''), title=f'Overall: {summary.get("overall", "?")}',
                            border_style=style, title_align='left'))


# --------------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description='Blue Angel for Software (DE-UZ 215) pre-check of a product webpage')
    parser.add_argument('url', help='Product webpage to assess')
    parser.add_argument('--criteria', default=DEFAULT_CRITERIA, help='Path to criteria.txt')
    parser.add_argument('--json', dest='json_out', help='Write structured result to this file')
    parser.add_argument('--max-chars', type=int, default=30000, help='Max chars of page text sent to the model')
    args = parser.parse_args()

    missing = [v for v in ('JEV_BASE_URL', 'JEV_API_KEY', 'JEV_MODEL') if not os.environ.get(v)]
    if missing:
        console.print(f'[red]Please set {", ".join(missing)}[/red]')
        sys.exit(2)

    criteria = parse_criteria(args.criteria)
    console.print(f'[green]✓[/green] Loaded {len(criteria)} criteria from {args.criteria}')

    with console.status(f'Fetching {args.url} …'):
        try:
            page = fetch_page(args.url, args.max_chars)
        except httpx.HTTPError as e:
            console.print(f'[red]✗ Could not fetch page: {e}[/red]')
            sys.exit(1)
    console.print(f'[green]✓[/green] {page["status"]} {page["url"]} — {page["bytes"] // 1024} KB, '
                  f'{len(page["text"])} chars text{" (truncated)" if page["text_truncated"] else ""}, '
                  f'{len(page["links"])} links')
    console.print(f'  [bold]{page["title"]}[/bold]')

    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': build_user_prompt(criteria, page)},
    ]

    dash = Dashboard(criteria)
    summary = None
    buffer = ''

    def handle_line(line):
        nonlocal summary
        line = line.strip().strip('`')
        if not line or line == 'json':
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            dash.warnings.append(f'Unparseable model line: {line[:100]}')
            return
        if 'summary' in obj:
            summary = obj
        elif obj.get('id') in dash.results:
            obj['status'] = obj.get('status') if obj.get('status') in STATUS_STYLE else 'maybe'
            obj.setdefault('comments', [])
            obj.setdefault('evidence', [])
            dash.results[obj['id']] = obj
            dash.phase = f'assessed criterion {obj["id"]}/{len(criteria)}'
        else:
            dash.warnings.append(f'Unknown object from model: {line[:100]}')

    dash.phase = f'asking Jev ({os.environ["JEV_MODEL"]}) …'
    try:
        with Live(dash, console=console, refresh_per_second=8, vertical_overflow='visible'):
            for delta in stream_model(messages):
                dash.chars += len(delta)
                if dash.phase.startswith('asking'):
                    dash.phase = 'Jev is answering …'
                buffer += delta
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    handle_line(line)
            handle_line(buffer)
            dash.phase = 'done'
    except (httpx.HTTPError, RuntimeError) as e:
        console.print(f'[red]✗ Model request failed: {e}[/red]')
        sys.exit(1)

    for r in dash.results.values():
        if r['status'] == 'pending':
            r.update(status='maybe', comments=['The model gave no answer for this criterion.'])

    console.rule('Details')
    print_details(criteria, dash.results, summary)

    if args.json_out:
        out = {
            'url': page['url'],
            'title': page['title'],
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'model': os.environ['JEV_MODEL'],
            'criteria': [{**c, **{k: v for k, v in dash.results[c['id']].items() if k != 'id'}} for c in criteria],
            'summary': (summary or {}).get('summary'),
            'overall': (summary or {}).get('overall'),
        }
        Path(args.json_out).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')
        console.print(f'[green]✓[/green] Wrote {args.json_out}')


if __name__ == '__main__':
    main()
