#!/usr/bin/env python3
"""Codex <-> Claude Code chat mirror.

  codex_mirror.py convert <thread_id> [--root DIR] [--no-thinking]  Codex thread -> Claude transcript (incremental)
  codex_mirror.py back <thread_id> <out.jsonl>                      dry run: rollout copy + pending Claude turns
  codex_mirror.py back-apply <thread_id>                            append pending Claude turns under Codex's lock
  codex_mirror.py sync [--days N] [--thread ID ...] [--json]        one tick, both directions, both origins
  codex_mirror.py status [--days N]                                 JSON snapshot for the menu bar app
  codex_mirror.py rollback                                          remove mirrored Claude transcripts

Pairs (state.json, keyed by Codex thread id) link a Codex thread with a Claude session:
- origin=codex: Claude mirror at ~/.claude/projects/<cwd>/<uuid5(thread)>.jsonl;
- origin=claude: a Codex rollout created from an existing Claude session (Codex indexes
  the new rollout on its next start).
Codex -> Claude appends native Claude records tagged with the Codex item id (codexItem).
Claude -> Codex appends finished Claude turns as native items (ids claude-*, msg_claude_*).
A side that holds the thread open (Codex writer lock / open Claude session) is skipped;
pending work is the diff between the two files and is retried on the next sync.
"""
import difflib, fcntl, glob, json, os, re, shutil, sqlite3, subprocess, sys, time, uuid
from datetime import datetime, timezone

HOME = os.path.expanduser('~')
CODEX = os.environ.get('CODEX_HOME', f'{HOME}/.codex')
CLAUDE_DIR = os.environ.get('CLAUDE_CONFIG_DIR', f'{HOME}/.claude')
CLAUDE_PROJECTS = os.path.join(CLAUDE_DIR, 'projects')
DATA = os.environ.get('MIRROR_STATE_DIR', f'{HOME}/.local/share/codex-claude-mirror')
MANIFEST = f'{DATA}/manifest.json'
NS = uuid.UUID('5f1c7f9e-6c1b-4f0e-9a52-0c0dec0dec0d')
MAX_OUT = 20000
VERSION = '2.1.283'
MODEL = '<synthetic>'  # a real model id here makes the Claude app resume with it
MIRROR_ID = ('claude-', 'msg_claude_', 'rs_claude_')  # ids of items we wrote into Codex


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _js_hash36(text):
    """Claude Code's path hash: Java-style 32-bit hash over UTF-16 code units, abs, base36."""
    h = 0
    data = text.encode('utf-16-le')
    for i in range(0, len(data), 2):
        h = ((h << 5) - h + (data[i] | data[i + 1] << 8)) & 0xFFFFFFFF
    h = abs(h - (1 << 32) if h >= 1 << 31 else h)
    digits, out = '0123456789abcdefghijklmnopqrstuvwxyz', ''
    while True:
        h, r = divmod(h, 36)
        out = digits[r] + out
        if not h:
            return out


def project_dir(root, cwd):
    """~/.claude/projects/<name> exactly as Claude Code names it (long paths: 200 chars + '-' + hash)."""
    name = re.sub(r'[^A-Za-z0-9]', '-', cwd)
    if len(name) > 200:
        name = f'{name[:200]}-{_js_hash36(cwd)}'
    return os.path.join(root, name)


def unwrap_shell(cmd):
    m = re.fullmatch(r"/bin/(?:zsh|bash) -lc '(.*)'", cmd, re.S)
    return m.group(1).replace("'\\''", "'") if m else cmd


def diff_to_edit(diff):
    """Unified diff -> (old, new) strings, hunks joined."""
    old, new = [], []
    for line in diff.splitlines():
        if line.startswith('@@'):
            if old or new:
                old.append('…'); new.append('…')
            continue
        tag, body = line[:1], line[1:]
        if tag in (' ', ''):
            old.append(body); new.append(body)
        elif tag == '-':
            old.append(body)
        elif tag == '+':
            new.append(body)
    return '\n'.join(old), '\n'.join(new)


def as_text(v):
    if v is None:
        return ''
    if isinstance(v, str):
        return v
    if isinstance(v, dict) and 'content' in v:
        return '\n'.join(c.get('text', '') for c in v['content'] if isinstance(c, dict)) or json.dumps(v, ensure_ascii=False)
    return json.dumps(v, ensure_ascii=False)


def tool_calls(it):
    """Codex item -> list of (name, input, result_text, is_error)."""
    t = it['type']
    if t == 'commandExecution':
        cmd = unwrap_shell(it.get('command', ''))
        out = it.get('aggregatedOutput') or ''
        code = it.get('exitCode')
        return [('Bash', {'command': cmd}, out if code in (0, None) else f'Exit code {code}\n{out}', code not in (0, None))]
    if t == 'fileChange':
        res = []
        for ch in it.get('changes', []):
            kind, path, diff = ch['kind']['type'], ch['path'], ch.get('diff', '')
            if kind == 'add':
                res.append(('Write', {'file_path': path, 'content': diff}, f'File created successfully at: {path}', False))
            elif kind == 'delete':
                res.append(('Bash', {'command': f'rm {path!r}'}, '', False))
            else:
                o, n = diff_to_edit(diff)
                res.append(('Edit', {'file_path': path, 'old_string': o, 'new_string': n}, f'The file {path} has been updated.', False))
        return res
    if t == 'mcpToolCall':
        err = it.get('error')
        return [(f"mcp__{it.get('server')}__{it.get('tool')}", it.get('arguments') or {}, as_text(err or it.get('result')), bool(err))]
    if t == 'dynamicToolCall':
        txt = '\n'.join(c.get('text', '') for c in it.get('contentItems') or [])
        return [(f"mcp__{it.get('namespace')}__{it.get('tool')}", it.get('arguments') or {}, txt, it.get('success') is False)]
    if t == 'webSearch':
        return [('WebSearch', {'query': it.get('query') or ''}, as_text(it.get('results')) or '(results not stored by Codex)', False)]
    if t == 'imageView':
        return [('Read', {'file_path': it.get('path')}, '(image viewed in Codex)', False)]
    return []


class Writer:
    def __init__(self, sid, cwd, branch):
        self.sid, self.cwd, self.branch = sid, cwd, branch
        self.parent, self.lines = None, []

    item = None  # Codex item id of the records being written

    def rec(self, typ, message, ts, **extra):
        u = str(uuid.uuid4())
        r = {'parentUuid': self.parent, 'isSidechain': False, 'type': typ, 'message': message, 'uuid': u,
             'timestamp': ts, 'userType': 'external', 'entrypoint': 'cli', 'cwd': self.cwd,
             'sessionId': self.sid, 'version': VERSION, 'gitBranch': self.branch or ''}
        if self.item:
            r['codexItem'] = self.item
        r.update(extra)
        self.lines.append(r)
        self.parent = u

    def user(self, content, ts, **extra):
        self.rec('user', {'role': 'user', 'content': content}, ts, **extra)

    def assistant(self, block, ts, mid):
        self.rec('assistant', {'id': mid, 'type': 'message', 'role': 'assistant', 'model': MODEL,
                               'content': [block], 'stop_reason': None, 'stop_sequence': None,
                               'usage': {'input_tokens': 0, 'output_tokens': 0}}, ts)


def normalize(it):
    """Rollout (core) item_completed item -> app-server v2 item shape used above (+ id)."""
    n = _normalize(it)
    n['id'] = it.get('id') or ''
    return n


def _normalize(it):
    t = it.get('type', '')
    if t == 'UserMessage':
        return {'type': 'userMessage', 'content': it.get('content') or []}
    if t == 'AgentMessage':
        c = it.get('content')
        text = '\n'.join(x.get('text', '') for x in c) if isinstance(c, list) else (c or it.get('text') or '')
        return {'type': 'agentMessage', 'text': text, 'phase': it.get('phase')}
    if t == 'Reasoning':
        return {'type': 'reasoning', 'summary': it.get('summary_text') or []}
    if t == 'CommandExecution':
        cmd = it.get('command')
        cmd = cmd[-1] if isinstance(cmd, list) and len(cmd) == 3 and cmd[1] == '-lc' else ' '.join(cmd or [])
        code = it.get('exit_code')
        return {'type': 'commandExecution', 'command': cmd, 'aggregatedOutput': it.get('aggregated_output') or '',
                'exitCode': int(code) if str(code).lstrip('-').isdigit() else code}
    if t == 'FileChange':
        changes = []
        for path, ch in (it.get('changes') or {}).items():
            kind = ch.get('type')
            changes.append({'path': path, 'kind': {'type': kind},
                            'diff': ch.get('content') if kind == 'add' else ch.get('unified_diff', '')})
        return {'type': 'fileChange', 'changes': changes}
    if t == 'McpToolCall':
        return {'type': 'mcpToolCall', 'server': it.get('server'), 'tool': it.get('tool'),
                'arguments': it.get('arguments'), 'result': it.get('result'), 'error': it.get('error')}
    if t == 'Extension' and it.get('kind') == 'web.search':
        return {'type': 'webSearch', 'query': it.get('query'), 'results': it.get('results')}
    if t == 'ImageView':
        return {'type': 'imageView', 'path': it.get('path')}
    return {'type': t[:1].lower() + t[1:]}


def convertible(it, thinking=True):
    """Would convert() write anything for this normalized item? (index and convert must agree)"""
    t = it['type']
    if t == 'userMessage':
        return any(c.get('type') == 'text' and c.get('text', '').strip() for c in it.get('content', []))
    if t == 'agentMessage':
        return bool(it.get('text'))
    if t == 'reasoning':
        summ = it.get('summary') or []
        return thinking and bool('\n\n'.join(summ if isinstance(summ, list) else [str(summ)]).strip())
    return bool(tool_calls(it))


def item_key(turn, it, ms):
    return it['id'] or f"{turn}:{it['type']}:{ms}"


def rollout_items(path):
    """Yield (turn_id, normalized item, created_at_ms) from item_completed events."""
    for line in open(path):
        try:
            o = json.loads(line)
        except ValueError:  # line being written by Codex
            continue
        p = o.get('payload') or {}
        if o.get('type') == 'event_msg' and p.get('type') == 'item_completed':
            ms = p.get('completed_at_ms') or p.get('started_at_ms') or \
                int(datetime.fromisoformat(o['timestamp'].replace('Z', '+00:00')).timestamp() * 1000)
            yield p.get('turn_id'), normalize(p['item']), ms


def thread_row(thread_id):
    st = sqlite3.connect(f'file:{CODEX}/state_5.sqlite?mode=ro', uri=True)
    row = st.execute('select title, cwd, git_branch, name, rollout_path from threads where id=?',
                     (thread_id,)).fetchone()
    st.close()
    if not row:
        raise LookupError(f'thread {thread_id} not found')
    return row


def thread_info(thread_id, state=None):
    """(title, cwd, branch, name, rollout_path); falls back to the pair for rollouts Codex has not indexed yet."""
    try:
        return thread_row(thread_id)
    except LookupError:
        p = (state or load_state()).get(thread_id) or {}
        if p.get('rollout'):
            return p.get('title') or '', p['cwd'], '', None, p['rollout']
        raise


def claude_path_of(thread_id, cwd, state, root=None):
    p = (state or {}).get(thread_id) or {}
    return p.get('path') or os.path.join(project_dir(root or CLAUDE_PROJECTS, cwd), sid_of(thread_id) + '.jsonl')


def sid_of(thread_id):
    return str(uuid.uuid5(NS, thread_id))


def mirrored_marks(path):
    """Codex item ids already in the Claude transcript, legacy cutoff (ms) for untagged records, leaf uuid."""
    ids, cutoff, leaf = set(), 0, None
    if not os.path.exists(path):
        return ids, cutoff, leaf
    for line in open(path):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get('codexItem'):
            ids.add(o['codexItem'])
        elif o.get('type') in ('user', 'assistant') and is_codex_origin(o):
            cutoff = max(cutoff, ms_of(o['timestamp']))
        if o.get('uuid') and not o.get('isSidechain') and o.get('type') in ('user', 'assistant', 'system', 'attachment'):
            leaf = o['uuid']
    return ids, cutoff, leaf


_PS = None


def ps_text():
    global _PS
    if _PS is None:
        _PS = subprocess.run(['ps', '-axo', 'command'], capture_output=True, text=True).stdout
    return _PS


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


_PROCS = None


def claude_procs():
    """Open Claude sessions: {sessionId: {pid, status, name}} from ~/.claude/sessions/<pid>.json (+ --resume args)."""
    global _PROCS
    if _PROCS is not None:
        return _PROCS
    _PROCS = {}
    for f in glob.glob(os.path.join(CLAUDE_DIR, 'sessions', '*.json')):
        try:
            j = json.load(open(f))
        except (ValueError, OSError):
            continue
        if j.get('sessionId') and isinstance(j.get('pid'), int) and pid_alive(j['pid']):
            _PROCS[j['sessionId']] = {'pid': j['pid'], 'status': j.get('status') or 'idle', 'name': j.get('name')}
    for sid in re.findall(r'--(?:resume|session-id)[= ]([0-9a-f-]{36})', ps_text()):
        _PROCS.setdefault(sid, {'pid': None, 'status': 'idle', 'name': None})
    return _PROCS


def claude_busy(sid):
    """True while a Claude process has this session loaded (it would not see our appends)."""
    return sid in claude_procs()


def convert(thread_id, root=None, thinking=True, quiet=False, state=None):
    own_state = state is None
    state = load_state() if own_state else state
    title, cwd, branch, name, rollout_path = thread_info(thread_id, state)
    title = (name or title or '').strip().splitlines()[0][:80] if (name or title) else 'Codex thread'
    pair = state.get(thread_id) or {}
    path = claude_path_of(thread_id, cwd, state, root)
    sid = os.path.basename(path)[:-len('.jsonl')]
    done, cutoff, leaf = mirrored_marks(path)
    cutoff = max(cutoff, pair.get('baseline_ms') or 0)
    items = []
    for turn, it, ms in rollout_items(rollout_path):
        key = item_key(turn, it, ms)
        if key in done or ms <= cutoff or it['id'].startswith(MIRROR_ID) or not convertible(it, thinking):
            continue
        items.append((turn, it, ms, key))
    if not items:
        return 'up-to-date'
    global _PROCS
    _PROCS = None  # the tick-start snapshot may be stale: the session could have been opened meanwhile
    if leaf and claude_busy(sid):
        return f'busy-claude ({len(items)} pending)'

    w = Writer(sid, cwd, branch)
    w.parent = leaf
    mid, last_turn, stats, last_prompt = None, None, {}, ''
    for turn_id, it, ms, key in items:
        t, ts = it['type'], iso(ms)
        w.item = key
        stats[t] = stats.get(t, 0) + 1
        if turn_id != last_turn:
            mid, last_turn = None, turn_id
        if t == 'userMessage':
            text = '\n'.join(c.get('text', '') for c in it.get('content', []) if c.get('type') == 'text').strip()
            if text:
                w.user(text, ts)
                mid, last_prompt = None, text
            continue
        mid = mid or f'msg_codex_{uuid.uuid4().hex[:20]}'
        if t == 'agentMessage' and it.get('text'):
            w.assistant({'type': 'text', 'text': it['text']}, ts, mid)
        elif t == 'reasoning' and thinking:
            summ = it.get('summary') or []
            txt = '\n\n'.join(summ if isinstance(summ, list) else [str(summ)]).strip()
            if txt:
                w.assistant({'type': 'thinking', 'thinking': txt, 'signature': ''}, ts, mid)
        else:
            for name_, inp, out, err in tool_calls(it):
                tid = f'toolu_codex_{uuid.uuid4().hex[:20]}'
                w.assistant({'type': 'tool_use', 'id': tid, 'name': name_, 'input': inp}, ts, mid)
                w.user([{'tool_use_id': tid, 'type': 'tool_result', 'content': out[:MAX_OUT], 'is_error': err}], ts,
                       sourceToolAssistantUUID=w.parent)
    if w.parent != leaf:
        if last_prompt:
            w.lines.append({'type': 'last-prompt', 'lastPrompt': last_prompt[:500], 'leafUuid': w.parent, 'sessionId': sid})
        if not leaf:
            w.lines.append({'type': 'custom-title', 'customTitle': title, 'sessionId': sid})

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a') as f:
        f.write(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in w.lines))
    if not leaf:
        record(path)
        state.setdefault(thread_id, {}).update({'origin': pair.get('origin', 'codex'), 'sid': sid, 'path': path,
                                                'cwd': cwd, 'title': title})
        if own_state:
            save_state(state)
    res = {'sessionId': sid, 'path': path, 'cwd': cwd, 'title': title, 'appended': len(w.lines), 'items': stats}
    if not quiet:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    return f"{'created' if not leaf else 'appended'} {len(w.lines)} records"


# ---------------- Claude -> Codex ----------------

SKIP_USER = ('<local-command-caveat>', '<command-name>', '<local-command-stdout>', '<system-reminder>')
STATE = f'{DATA}/state.json'
MAX_BACK = 20000


def uuid7(ms):
    r = uuid.uuid4().int & ((1 << 74) - 1)
    v = (ms << 80) | (0x7 << 76) | ((r >> 62) << 64) | (0b10 << 62) | (r & ((1 << 62) - 1))
    return str(uuid.UUID(int=v))


def ms_of(ts):
    return int(datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp() * 1000)


def is_codex_origin(o):
    """Records we wrote into Claude. Legacy (untagged) ones only exist in uuid5 mirror sessions."""
    m = o.get('message') or {}
    if o.get('codexItem') or o.get('codexOrigin') or m.get('model') == 'codex':
        return True
    sid = o.get('sessionId') or ''
    return (len(sid) == 36 and sid[14] == '5' and o.get('entrypoint') == 'cli' and o.get('version') == VERSION
            and 'promptId' not in o)


def user_text(o):
    if o.get('isCompactSummary') or o.get('isVisibleInTranscriptOnly'):
        return ''
    if o.get('isMeta'):
        # messages from another Claude session are meta records, but they are the prompt of this turn
        c = (o.get('message') or {}).get('content')
        c = c if isinstance(c, str) else ' '.join(b.get('text', '') for b in c or [] if isinstance(b, dict))
        m = re.search(r'<cross-session-message[^>]*>(.*?)</cross-session-message>', c or '', flags=re.S)
        return m.group(1).strip() if m else ''
    c = (o.get('message') or {}).get('content')
    blocks = [c] if isinstance(c, str) else [b.get('text', '') for b in c or [] if b.get('type') == 'text']
    return '\n'.join(b for b in map(strip_injected, blocks) if b and not b.startswith(SKIP_USER)).strip()


def strip_injected(text):
    """User text without app-injected context: Claude desktop prepends a <system-reminder> to the first prompt
    after adopting a session, and cross-session messages arrive wrapped in <cross-session-message>."""
    text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text or '', flags=re.S)
    m = re.search(r'<cross-session-message[^>]*>(.*?)</cross-session-message>', text, flags=re.S)
    return (m.group(1) if m else text).strip()


def result_text(c):
    if isinstance(c, str):
        return c
    return '\n'.join(x.get('text', '') if x.get('type') == 'text' else f"[{x.get('type')}]" for x in c or [])


CONTINUATION = '↻ продолжение ответа Claude'


def claude_turns(session_path, done, include_open=False):
    """Finished Claude-origin turns: [{'user', 'ts', 'uuids', 'parts', 'done'}] (+ the open one if asked).

    Records that arrive after an already-pushed turn without a new prompt (background notifications,
    resumed work) open a continuation turn instead of being dropped.
    """
    turns, cur = [], None

    def start(text, o):
        nonlocal cur
        if cur:
            cur['done'] = True  # a new prompt closes the previous turn
        cur = {'user': text, 'ts': o['timestamp'], 'uuids': [], 'parts': [], 'done': False}
        turns.append(cur)

    for line in open(session_path):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if cur and o.get('type') == 'system' and o.get('subtype') == 'stop_hook_summary':
            cur['done'] = True
        if o.get('isSidechain'):
            continue
        if o.get('uuid') in done:
            cur = None  # everything up to here is already in Codex
            continue
        att = o.get('attachment') or {}
        if o.get('type') == 'attachment' and att.get('type') == 'queued_command' \
                and att.get('commandMode', 'prompt') == 'prompt' and str(att.get('prompt') or '').strip():
            if cur and not cur['done']:  # typed while Claude was working: part of this turn
                cur['parts'].append(('prompt', str(att['prompt']).strip(), o['timestamp']))
                cur['uuids'].append(o['uuid'])
            continue
        if o.get('type') not in ('user', 'assistant') or is_codex_origin(o):
            continue
        if o['type'] == 'user':
            txt = user_text(o)
            if txt.startswith('[Request interrupted'):
                if cur:
                    cur['done'] = True
                continue
            if txt:
                start(txt, o)
                cur['uuids'].append(o['uuid'])
                continue
            results = [b for b in (o['message'].get('content') or []) if isinstance(b, dict) and b.get('type') == 'tool_result']
            if not results:
                continue
            if not cur or cur['done']:
                start(CONTINUATION, o)
            for b in results:
                cur['parts'].append(('result', {'id': b.get('tool_use_id'), 'text': result_text(b.get('content')),
                                                'error': bool(b.get('is_error'))}, o['timestamp']))
            cur['uuids'].append(o['uuid'])
            continue
        mid = o['message'].get('id')
        if not cur:
            start(CONTINUATION, o)
        elif cur['done'] and mid != cur.get('mid'):
            # a new assistant message after the turn ended (e.g. a background task finished);
            # blocks of one message are separate records that each carry the final stop_reason
            start(CONTINUATION, o)
        cur['mid'] = mid
        cur['uuids'].append(o['uuid'])
        if o['message'].get('stop_reason') == 'end_turn':
            cur['done'] = True
        for b in o['message'].get('content') or []:
            if b.get('type') == 'text' and b.get('text', '').strip():
                cur['parts'].append(('text', b['text'], o['timestamp']))
            elif b.get('type') == 'thinking' and b.get('thinking', '').strip():
                cur['parts'].append(('thinking', b['thinking'], o['timestamp']))
            elif b.get('type') == 'tool_use':
                cur['parts'].append(('tool', {'id': b.get('id'), 'name': b.get('name'), 'input': b.get('input') or {}},
                                     o['timestamp']))
    turns = [t for t in turns if t['parts'] or t['user'] != CONTINUATION]
    return [t for t in turns if t['done'] or include_open]


def unified(old, new):
    lines = difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm='', n=3)
    return '\n'.join(l for l in lines if not l.startswith(('---', '+++'))) + '\n'


def native_item(name, inp, res, cwd, ms):
    """Claude tool call + result -> Codex rollout item (UI-native)."""
    out, err = (res or {}).get('text', ''), (res or {}).get('error', False)
    iid = f'claude-{uuid7(ms)}'
    if name == 'Bash':
        cmd = inp.get('command', '')
        return {'type': 'CommandExecution', 'id': iid, 'command': ['/bin/zsh', '-lc', cmd], 'cwd': f'file://{cwd}',
                'parsed_cmd': [{'type': 'unknown', 'cmd': cmd}], 'source': 'unified_exec_startup',
                'status': 'failed' if err else 'completed', 'stdout': out[:MAX_BACK], 'stderr': '',
                'aggregated_output': out[:MAX_BACK], 'exit_code': 1 if err else 0,
                'duration': {'secs': 0, 'nanos': 0}, 'formatted_output': out[:MAX_BACK]}
    if name in ('Write', 'Edit', 'MultiEdit') and not err and inp.get('file_path'):
        p = inp['file_path']
        if name == 'Write':
            ch = {'type': 'add', 'content': inp.get('content', '')}
        else:
            edits = inp.get('edits') or [inp]
            ch = {'type': 'update', 'move_path': None,
                  'unified_diff': ''.join(unified(e.get('old_string', ''), e.get('new_string', '')) for e in edits)}
        return {'type': 'FileChange', 'id': iid, 'changes': {p: ch}, 'status': 'completed', 'stdout': out, 'stderr': ''}
    server, tool = (name.split('__', 2)[1:] if name.startswith('mcp__') and name.count('__') >= 2 else ['claude', name])
    return {'type': 'McpToolCall', 'id': iid, 'server': server, 'tool': tool, 'arguments': inp,
            'readOnlyHint': False, 'status': 'failed' if err else 'completed',
            'result': {'content': [{'type': 'text', 'text': out[:MAX_BACK]}], 'isError': err},
            'duration': {'secs': 0, 'nanos': 0}}


def codex_records(thread_id, cwd, turns, start_ordinal):
    out, n = [], start_ordinal

    def add(ts, typ, payload):
        nonlocal n
        n += 1
        out.append({'timestamp': ts, 'ordinal': n, 'type': typ, 'payload': payload})

    def completed(ts, turn, item):
        ms = ms_of(ts)
        add(ts, 'event_msg', {'type': 'item_completed', 'thread_id': thread_id, 'turn_id': turn, 'item': item,
                              'started_at_ms': ms, 'completed_at_ms': ms})

    def model_note(ts, meta, text, phase):
        add(ts, 'response_item', {'type': 'message', 'id': f'msg_claude_{uuid7(ms_of(ts))}', 'role': 'assistant',
                                  'content': [{'type': 'output_text', 'text': text}], 'phase': phase,
                                  'internal_chat_message_metadata_passthrough': meta})

    for t in turns:
        start = ms_of(t['ts'])
        turn = uuid7(start)
        meta = {'turn_id': turn}
        add(t['ts'], 'event_msg', {'type': 'task_started', 'turn_id': turn, 'root_turn_id': turn,
                                   'started_at': start // 1000, 'model_context_window': None,
                                   'collaboration_mode_kind': 'default'})
        add(t['ts'], 'response_item', {'type': 'message', 'id': f'msg_claude_{uuid7(start)}', 'role': 'user',
                                       'content': [{'type': 'input_text', 'text': t['user']}],
                                       'internal_chat_message_metadata_passthrough': meta})
        completed(t['ts'], turn, {'type': 'UserMessage', 'id': f'claude-{uuid7(start)}',
                                  'content': [{'type': 'text', 'text': t['user'], 'text_elements': []}]})
        results = {p[1]['id']: p[1] for p in t['parts'] if p[0] == 'result'}
        last_text_idx = max((i for i, p in enumerate(t['parts']) if p[0] == 'text'), default=-1)
        for i, (kind, body, ts) in enumerate(t['parts']):
            if kind == 'thinking':
                completed(ts, turn, {'type': 'Reasoning', 'id': f'rs_claude_{uuid7(ms_of(ts))}',
                                     'summary_text': [body], 'raw_content': []})
            elif kind == 'tool':
                res = results.get(body['id'])
                completed(ts, turn, native_item(body['name'], body['input'], res, cwd, ms_of(ts)))
                inp = json.dumps(body['input'], ensure_ascii=False)
                model_note(ts, meta, f"[Claude ran {body['name']}] {inp[:1000]}\n[result]\n{(res or {}).get('text', '')[:2000]}",
                           'commentary')
            elif kind == 'prompt':
                completed(ts, turn, {'type': 'UserMessage', 'id': f'claude-{uuid7(ms_of(ts))}',
                                     'content': [{'type': 'text', 'text': body, 'text_elements': []}]})
                add(ts, 'response_item', {'type': 'message', 'id': f'msg_claude_{uuid7(ms_of(ts))}', 'role': 'user',
                                          'content': [{'type': 'input_text', 'text': body}],
                                          'internal_chat_message_metadata_passthrough': meta})
            elif kind == 'text':
                phase = 'final_answer' if i == last_text_idx else 'commentary'
                mid = f'msg_claude_{uuid7(ms_of(ts))}'
                completed(ts, turn, {'type': 'AgentMessage', 'id': mid, 'content': [{'type': 'Text', 'text': body}],
                                     'phase': phase})
                model_note(ts, meta, body, phase)
        end_ts = t['parts'][-1][2] if t['parts'] else t['ts']
        last = t['parts'][last_text_idx][1] if last_text_idx >= 0 else None
        add(end_ts, 'event_msg', {'type': 'task_complete', 'turn_id': turn, 'last_agent_message': last,
                                  'started_at': start // 1000, 'completed_at': ms_of(end_ts) // 1000,
                                  'duration_ms': ms_of(end_ts) - start})
    return out


def last_ordinal(path):
    """Highest ordinal = ordinal of the last complete line; read from the tail, split on \\n only (JSON may hold U+2028)."""
    with open(path, 'rb') as f:
        f.seek(0, 2)
        size = f.tell()
        chunk = 1 << 18
        while True:
            f.seek(max(0, size - chunk))
            lines = f.read().split(b'\n')
            for line in reversed(lines[1:] if size > chunk else lines):
                try:
                    return int(json.loads(line).get('ordinal') or 0)
                except (ValueError, AttributeError):
                    continue
            if chunk >= size:
                return 0
            chunk *= 4


def thread_lock(thread_id):
    """Codex writer-lock protocol: coordination lock, then non-blocking thread lock. None if Codex owns it."""
    d = f'{CODEX}/thread-writer-locks'
    coord = open(f'{d}/.coordination.lock', 'a+')
    fcntl.flock(coord, fcntl.LOCK_EX)
    try:
        lock = open(f'{d}/{thread_id}.lock', 'a+')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return None
        return lock
    finally:
        fcntl.flock(coord, fcntl.LOCK_UN)
        coord.close()


def load_state():
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + '.tmp'
    json.dump(state, open(tmp, 'w'), indent=1)
    os.replace(tmp, STATE)


def back(thread_id, out_path=None, state=None, quiet=False):
    own_state = state is None
    state = load_state() if own_state else state
    _, cwd, _, _, rollout_path = thread_info(thread_id, state)
    session = claude_path_of(thread_id, cwd, state)
    if not os.path.exists(session):
        return 'no-claude-session'
    done = set(state.get(thread_id, {}).get('pushed', []))
    turns = claude_turns(session, done=done)
    if not turns:
        return 'up-to-date'
    lock = None
    if not out_path:
        lock = thread_lock(thread_id)
        if lock is None:
            return f'busy-codex ({len(turns)} turn(s) queued)'
    try:
        last_ord = last_ordinal(rollout_path)
        recs = codex_records(thread_id, cwd, turns, last_ord)
        if out_path:
            import shutil
            shutil.copyfile(rollout_path, out_path)
            with open(out_path, 'a') as f:
                f.write(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in recs))
        else:
            size = os.path.getsize(rollout_path)
            with open(rollout_path, 'a') as f:
                f.write(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in recs))
                f.flush(); os.fsync(f.fileno())
            s = state.setdefault(thread_id, {})
            s.setdefault('pushed', []).extend(u for t in turns for u in t['uuids'])
            s.setdefault('appends', []).append({'path': rollout_path, 'from_size': size, 'from_ordinal': last_ord + 1})
            s.setdefault('origin', 'codex')
            save_state(state)  # right away: a crash later in the tick must not re-push these turns
    finally:
        if lock:
            lock.close()
    if not quiet:
        for r in recs:
            p = r['payload']
            item = p.get('item') or {}
            body = item.get('content') or item.get('summary_text') or item.get('command') or item.get('changes') \
                or item.get('tool') or p.get('content') or ''
            print(r['ordinal'], r['type'], p.get('type'), item.get('type', ''), item.get('phase', p.get('role', '')),
                  json.dumps(body, ensure_ascii=False)[:110])
    return f"{len(turns)} turn(s), {len(recs)} records after ordinal {last_ord} -> {out_path or rollout_path}"


def active_threads(days):
    """Codex threads touched within `days` (excluding Codex's own imports of Claude sessions)."""
    st = sqlite3.connect(f'file:{CODEX}/state_5.sqlite?mode=ro', uri=True)
    rows = st.execute("select id from threads where archived=0 and coalesce(thread_source, '') != 'subagent' "
                      "and source not like '%subagent%' and updated_at > ? order by updated_at desc",
                      (int(time.time()) - days * 86400,)).fetchall()
    st.close()
    imported = {r['imported_thread_id'] for r in import_journal()}
    return [r[0] for r in rows if r[0] not in imported]


def import_journal():
    imp = f'{CODEX}/external_agent_session_imports.json'
    try:
        return json.load(open(imp))['records']
    except (OSError, ValueError, KeyError):
        return []


def codex_threads_meta(ids):
    """{id: (title, name, cwd, rollout_path, updated_ms, archived)} for ids known to Codex."""
    if not ids:
        return {}
    st = sqlite3.connect(f'file:{CODEX}/state_5.sqlite?mode=ro', uri=True)
    q = f"select id, title, name, cwd, rollout_path, coalesce(updated_at_ms, updated_at*1000), archived " \
        f"from threads where id in ({','.join('?' * len(ids))})"
    res = {r[0]: r[1:] for r in st.execute(q, list(ids))}
    st.close()
    return res


# ---------------- cheap indexes (status every tick must not re-read GBs) ----------------

CACHE = f'{DATA}/index.json'
CACHE_V = 7  # bump whenever turn/item parsing changes: cached summaries must be recomputed


def load_cache():
    try:
        c = json.load(open(CACHE))
        return c if c.get('v') == CACHE_V else {'v': CACHE_V}
    except (OSError, ValueError):
        return {'v': CACHE_V}


def save_cache(c):
    os.makedirs(DATA, exist_ok=True)
    tmp = CACHE + '.tmp'
    json.dump(c, open(tmp, 'w'))
    os.replace(tmp, CACHE)


def rollout_index(path, cache):
    """Incremental index of an append-only rollout: convertible items [key, ms, mirror, turn], open turn, last activity."""
    idx = cache.setdefault('rollouts', {})
    try:
        st = os.stat(path)
    except OSError:
        return None
    e = idx.get(path)
    if not e or e.get('ino') != st.st_ino or st.st_size < e['off']:
        e = {'ino': st.st_ino, 'off': 0, 'items': [], 'open': False, 'last_ms': 0, 'max_ord': 0}
    if st.st_size > e['off']:
        f = open(path, 'rb')
        f.seek(e['off'])
        for line in f:
            if not line.endswith(b'\n'):
                break  # a line being written; picked up next time
            e['off'] += len(line)
            try:
                o = json.loads(line)
            except ValueError:
                continue
            e['max_ord'] = max(e['max_ord'], o.get('ordinal') or 0)
            p = o.get('payload') or {}
            typ = p.get('type')
            if o.get('type') == 'event_msg' and typ == 'item_completed':
                ms = p.get('completed_at_ms') or p.get('started_at_ms') or ms_of(o['timestamp'])
                it = normalize(p['item'])
                if convertible(it):
                    e['items'].append([item_key(p.get('turn_id'), it, ms), ms, int(it['id'].startswith(MIRROR_ID)), p.get('turn_id')])
                e['last_ms'] = max(e['last_ms'], ms)
            elif o.get('type') == 'event_msg' and typ == 'task_started':
                e['open'] = True
            elif o.get('type') == 'event_msg' and typ in ('task_complete', 'turn_aborted', 'task_failed'):
                e['open'] = False
        f.close()
    idx[path] = e
    return e


def claude_info(path, cache):
    """Parsed summary of a Claude transcript, cached by (mtime, size)."""
    idx = cache.setdefault('claude', {})
    try:
        st = os.stat(path)
    except OSError:
        return None
    e = idx.get(path)
    if e and e['mt'] == st.st_mtime_ns and e['sz'] == st.st_size:
        return e
    ids, cutoff, leaf = mirrored_marks(path)
    cwd = entry = title = first_prompt = None
    codex_origin = None
    last_ms = 0
    for line in open(path):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get('type') == 'custom-title':
            title = o.get('customTitle') or title
        if o.get('type') not in ('user', 'assistant') or o.get('isSidechain'):
            continue
        cwd = cwd or o.get('cwd')
        if codex_origin is None:
            codex_origin = is_codex_origin(o)
            entry = o.get('entrypoint')
        if o.get('timestamp'):
            last_ms = max(last_ms, ms_of(o['timestamp']))
    turns = claude_turns(path, done=set(), include_open=True)
    for t in turns:
        first_prompt = first_prompt or t['user']
    e = {'mt': st.st_mtime_ns, 'sz': st.st_size, 'ids': sorted(ids), 'cutoff': cutoff, 'cwd': cwd,
         'entrypoint': entry, 'codex_origin': bool(codex_origin), 'last_ms': last_ms,
         'title': title or (first_prompt or '').strip().splitlines()[0][:80] if (title or first_prompt) else 'Claude session',
         # [first uuid, start ms, last uuid]: a turn counts as pushed if either end was pushed
         'turns': [[t['uuids'][0], ms_of(t['ts']), t['uuids'][-1]] for t in turns if t['done']],
         'open': bool(turns and not turns[-1]['done'])}
    idx[path] = e
    return e


def codex_locked():
    """Thread ids whose writer lock file is held open by another process (Codex app-server)."""
    d = f'{CODEX}/thread-writer-locks'
    try:
        out = subprocess.run(['lsof', '-Fpn', '+D', d], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    held, pid = set(), None
    for line in out.splitlines():
        if line.startswith('p'):
            pid = int(line[1:])
        elif line.startswith('n') and line.endswith('.lock') and pid != os.getpid():
            name = os.path.basename(line[1:])[:-5]
            if not name.startswith('.'):
                held.add(name)
    return held


def codex_running():
    return 'CodexCLI.app/Contents/MacOS/codex' in ps_text() and 'app-server' in ps_text()


def app_started_ms(marker):
    """Start time (ms) of the oldest process whose command line contains `marker`, or None."""
    out = subprocess.run(['ps', '-axo', 'lstart=,command='], capture_output=True, text=True).stdout
    starts = []
    for line in out.splitlines():
        if marker in line:
            try:
                starts.append(time.mktime(time.strptime(line[:24].strip(), '%a %b %d %H:%M:%S %Y')) * 1000)
            except ValueError:
                pass
    return min(starts) if starts else None


def mark_claude_refresh(state):
    """Remember that Claude desktop only shows our registry changes after its next start."""
    state.setdefault('_ui', {})['claude_refresh_ms'] = int(time.time() * 1000)


def claude_running():
    return '/Applications/Claude.app/' in ps_text()


# ---------------- Claude-origin sessions -> new Codex threads ----------------

def claude_candidates(days, cache, paired_paths):
    """Claude sessions (not our mirrors, not yet paired, not archived in Claude) touched within `days`."""
    since = time.time() - days * 86400
    archived = claude_archived_sids() | claude_registry()[1]  # archived or deleted in Claude: never adopt
    res = []
    for path in glob.glob(os.path.join(CLAUDE_PROJECTS, '*', '*.jsonl')):
        try:
            if os.path.getmtime(path) < since or path in paired_paths \
                    or os.path.basename(path)[:-len('.jsonl')] in archived:
                continue
        except OSError:
            continue
        try:
            info = claude_info(path, cache)
        except Exception:  # unreadable / malformed transcript: skip it, keep the rest
            continue
        if not info or info['codex_origin'] or info['entrypoint'] not in ('claude-desktop', 'cli') or not info['cwd']:
            continue
        res.append((path, info))
    return res


def rollout_template():
    """session_meta of the newest real rollout (base instructions, originator, cli version)."""
    files = sorted(glob.glob(f'{CODEX}/sessions/*/*/*/rollout-*.jsonl'), key=os.path.getmtime, reverse=True)
    for f in files[:20]:
        try:
            o = json.loads(open(f).readline())
        except (OSError, ValueError):
            continue
        p = o.get('payload') or {}
        if o.get('type') == 'session_meta' and p.get('thread_source', 'user') == 'user' \
                and not p.get('parent_thread_id') and 'subagent' not in json.dumps(p.get('source')):
            return o
    raise RuntimeError('no Codex rollout to copy session_meta from')


def adopt(path, info, state):
    """Pair a Claude-born session with a Codex thread: reuse Codex's own import if it exists, else create a rollout."""
    sid = os.path.basename(path)[:-len('.jsonl')]
    imports = sorted((r for r in import_journal() if r.get('source_path') == path),
                     key=lambda r: r.get('imported_at') or 0, reverse=True)
    metas = codex_threads_meta([r['imported_thread_id'] for r in imports])
    live = [r for r in imports if r['imported_thread_id'] in metas]
    chosen = next((r for r in live if not metas[r['imported_thread_id']][5]), live[0] if live else None)
    if chosen:
        tid = chosen['imported_thread_id']
        title, name, cwd, rollout, _, archived = metas[tid]
        e = rollout_index(rollout, {'rollouts': {}}) or {'items': []}
        imported = [i[1] for i in e['items'] if str(i[3] or '').startswith('external-import-turn-')] or \
            [i[1] for i in e['items'] if i[1] <= (chosen.get('imported_at') or 0) * 1000 + 60_000]
        cut = (chosen.get('source_modified_at') or 0) // 1_000_000
        state[tid] = {'origin': 'claude', 'sid': sid, 'path': path, 'cwd': cwd, 'rollout': rollout,
                      'title': name or title, 'archived': bool(archived),
                      # what Codex imported is already on both sides; later turns on either side still flow
                      'baseline_ms': max(imported, default=0),
                      'pushed': [u for t in claude_turns(path, done=set()) if ms_of(t['ts']) <= cut for u in t['uuids']]}
        save_state(state)
        return tid, 'paired-import' + (' (archived)' if archived else '')
    turns = claude_turns(path, done=set())
    if not turns:
        return None, 'no-finished-turns'
    first_ms = ms_of(turns[0]['ts'])
    tid = uuid7(first_ms)
    local = datetime.fromtimestamp(first_ms / 1000)
    rollout = f"{CODEX}/sessions/{local:%Y/%m/%d}/rollout-{local:%Y-%m-%dT%H-%M-%S}-{tid}.jsonl"
    meta = rollout_template()
    ts = iso(first_ms)
    meta['timestamp'] = ts
    p = meta['payload']
    for k in ('git', 'forked_from_id', 'instructions', 'parent_thread_id', 'agent_nickname', 'agent_role',
              'agent_path', 'subagent_history_start_ordinal', 'context_window', 'name', 'title', 'thread_name'):
        p.pop(k, None)
    p['thread_source'] = 'user'
    if 'runtime_workspace_roots' in p:
        p['runtime_workspace_roots'] = [info['cwd']]
    p.update(session_id=tid, id=tid, timestamp=ts, cwd=info['cwd'])
    meta['ordinal'] = 0
    recs = [meta] + codex_records(tid, info['cwd'], turns, 0)
    os.makedirs(os.path.dirname(rollout), exist_ok=True)
    with open(rollout, 'x') as f:
        f.write(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in recs))
    state[tid] = {'origin': 'claude', 'sid': sid, 'path': path, 'cwd': info['cwd'], 'rollout': rollout,
                  'title': info['title'], 'created': True,
                  'pushed': [u for t in turns for u in t['uuids']]}
    save_state(state)  # right away: a crash must not create this thread twice
    return tid, f'created codex thread ({len(turns)} turns)'


# ---------------- make new threads visible in both apps ----------------

def resolve_codex_bin():
    if os.environ.get('CODEX_BIN'):
        return os.path.expanduser(os.environ['CODEX_BIN'])
    for path in ('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',
                 '/Applications/Codex.app/Contents/Resources/codex'):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return shutil.which('codex') or 'codex'


CODEX_BIN = resolve_codex_bin()
# Claude desktop's sidebar registry. Only touched for the real Claude dir (tests override CLAUDE_CONFIG_DIR).
DESKTOP = os.environ.get('MIRROR_CLAUDE_DESKTOP') or \
    (None if 'CLAUDE_CONFIG_DIR' in os.environ else f'{HOME}/Library/Application Support/Claude')


def codex_register(named):
    """Index new rollouts in Codex's state DB the official way: thread/name/set via a short-lived app-server
    (the desktop sidebar lists threads from the DB only and never rescans the sessions folder)."""
    return codex_rpc('thread/name/set', [(tid, {'threadId': tid, 'name': name[:120]}) for tid, name in named])


def codex_rpc(method, calls):
    """Run `method` once per (key, params) on a short-lived `codex app-server`; returns keys that succeeded."""
    if not calls or not os.path.exists(CODEX_BIN):
        return set()
    import select
    p = subprocess.Popen([CODEX_BIN, 'app-server', '--listen', 'stdio://'], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=os.environ.copy())
    reqs = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
             'params': {'clientInfo': {'name': 'codex-claude-mirror', 'version': '1'}}},
            {'jsonrpc': '2.0', 'method': 'initialized'}]
    ids = {}
    for i, (key, params) in enumerate(calls, start=2):
        ids[i] = key
        reqs.append({'jsonrpc': '2.0', 'id': i, 'method': method, 'params': params})
    ok, failed = set(), set()
    try:
        p.stdin.write(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in reqs))
        p.stdin.flush()
        deadline = time.time() + 30
        while len(ok) + len(failed) < len(ids) and time.time() < deadline:
            r, _, _ = select.select([p.stdout], [], [], 1)
            if not r:
                continue
            line = p.stdout.readline()
            if not line:
                break
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get('id') in ids:
                (ok if 'result' in o else failed).add(ids[o['id']])  # failures are retried next tick
    finally:
        try:
            p.stdin.close()
            p.wait(timeout=10)
        except Exception:
            p.kill()
    return ok


def desktop_dir():
    """claude-code-sessions/<org>/<account> the desktop app is using (newest activity)."""
    if not DESKTOP:
        return None
    dirs = [d for d in glob.glob(os.path.join(DESKTOP, 'claude-code-sessions', '*', '*', ''))
            if glob.glob(os.path.join(d, 'local_*.json'))]
    return max(dirs, key=lambda d: max(os.path.getmtime(f) for f in glob.glob(os.path.join(d, 'local_*.json'))),
               default=None)


def desktop_titles():
    """cliSessionId -> sidebar title from Claude desktop's registry."""
    res = {}
    if not DESKTOP:
        return res
    for f in glob.glob(os.path.join(DESKTOP, 'claude-code-sessions', '*', '*', 'local_*.json')):
        try:
            j = json.load(open(f))
        except (OSError, ValueError):
            continue
        if j.get('cliSessionId') and j.get('title'):
            res[j['cliSessionId']] = j['title']
    return res


def claude_register(sid, cwd, title, created_ms, last_ms):
    """Show a mirrored session in Claude desktop's sidebar, the way the app registers terminal sessions."""
    d = desktop_dir()
    if not d:
        return False
    if glob.glob(os.path.join(DESKTOP, 'claude-code-sessions', '*', '*', f'local_{sid}.json')):
        return True
    now = int(time.time() * 1000)
    rec = {'sessionId': f'local_{sid}', 'cliSessionId': sid, 'cwd': cwd, 'originCwd': cwd, 'title': title[:120],
           'createdAt': created_ms or now, 'lastActivityAt': last_ms or now, 'indexedAt': now, 'isArchived': False,
           'importedFrom': 'terminal-cli', 'adoptedFromOtherSurface': True}
    path = os.path.join(d, f'local_{sid}.json')
    tmp = path + '.mirror-tmp'
    with open(tmp, 'w') as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(tmp, path)
    record(path)
    return True


def user_idle_seconds():
    try:
        out = subprocess.run(['ioreg', '-c', 'IOHIDSystem', '-d', '4'], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r'"HIDIdleTime" = (\d+)', out)
        return int(m.group(1)) / 1e9 if m else 0
    except (OSError, subprocess.TimeoutExpired):
        return 0


MANUAL = False  # set by `sync --manual`: the user pressed «Синхронизировать» and expects results in front


def claude_import(sid):
    """Make the running Claude desktop load a session now, through its official resume deep link.
    The import brings Claude to the front, so auto-sync only does it while the user is idle (or on a
    manual sync); until then the registry entry is picked up on Claude's next start."""
    if not DESKTOP or not claude_running():
        return True
    if not MANUAL and user_idle_seconds() < 60:
        return False
    subprocess.run(['open', '-g', f'claude://resume?session={sid}'], timeout=10)
    time.sleep(0.7)
    return True


def make_visible(state, cache):
    """Register every pair on the side where we created it; idempotent, retried each tick."""
    names = desktop_titles()
    todo = []
    for tid, p in state.items():
        if not isinstance(p, dict) or p.get('archived'):
            continue
        if p.get('origin') == 'claude' and p.get('created') and not p.get('codex_registered'):
            todo.append((tid, names.get(p.get('sid')) or p.get('title') or 'Claude session'))
        if p.get('origin', 'codex') == 'codex' and p.get('path') and os.path.exists(p['path']) \
                and not (p.get('claude_registered') and p.get('claude_imported')) and DESKTOP:
            ci = claude_info(p['path'], cache) or {}
            title = re.sub(r'^\[Codex\]\s*', '', p.get('title') or ci.get('title') or 'Codex thread')
            first = None
            for line in open(p['path']):
                try:
                    first = ms_of(json.loads(line)['timestamp'])
                    break
                except (ValueError, KeyError):
                    continue
            if p.get('claude_registered') or claude_register(p['sid'], p['cwd'], title, first, ci.get('last_ms')):
                p['claude_registered'] = True
                p['claude_imported'] = claude_import(p['sid'])
                if not p['claude_imported']:
                    mark_claude_refresh(state)
    names_set = dict(todo)
    for tid in codex_register(todo):
        state[tid]['codex_registered'] = True
        state[tid]['seen_codex_name'] = names_set[tid][:120]
    save_state(state)
    return len(todo)


def claude_archived_sids():
    """cliSessionIds (and registry stems) archived in Claude desktop."""
    res = set()
    if not DESKTOP:
        return res
    for f in glob.glob(os.path.join(DESKTOP, 'claude-code-sessions', '*', '*', 'local_*.json')):
        try:
            j = json.load(open(f))
        except (OSError, ValueError):
            continue
        if j.get('isArchived'):
            res.add(os.path.basename(f)[len('local_'):-len('.json')])
            if j.get('cliSessionId'):
                res.add(j['cliSessionId'])
    return res


def claude_registry_update(sid, archived=None, title=None):
    """Edit Claude desktop's registry entry (+ archive index) for a session. Only called while Claude is
    closed: the running app keeps its own copy in memory and would overwrite a concurrent edit."""
    if not DESKTOP:
        return False
    files = [f for f in glob.glob(os.path.join(DESKTOP, 'claude-code-sessions', '*', '*', 'local_*.json'))
             if os.path.basename(f) == f'local_{sid}.json' or _cli_id(f) == sid]
    for f in files:
        j = json.load(open(f))
        if archived is not None:
            j['isArchived'] = archived
        if title:
            j['title'], j['titleSource'] = title, 'user'
        tmp = f + '.mirror-tmp'
        json.dump(j, open(tmp, 'w'), ensure_ascii=False)
        os.replace(tmp, f)
        if archived is None:
            continue
        idx = os.path.join(os.path.dirname(f), 'archived-sessions.idx')
        try:
            data = json.load(open(idx))
        except (OSError, ValueError):
            data = {'v': 1, 'archived': []}
        local = os.path.basename(f)[:-len('.json')]
        ids = data.setdefault('archived', [])
        if archived and local not in ids:
            ids.append(local)
        elif not archived and local in ids:
            ids.remove(local)
        json.dump(data, open(idx + '.mirror-tmp', 'w'))
        os.replace(idx + '.mirror-tmp', idx)
    return bool(files)


def _cli_id(f):
    try:
        return json.load(open(f)).get('cliSessionId')
    except (OSError, ValueError):
        return None


def claude_registry():
    """Claude desktop's session registry, read-only: ({sid: entry}, {deleted sids}).
    Entries are keyed by both the file stem and cliSessionId (the app may remap a session to a new CLI id)."""
    entries, deleted = {}, set()
    if not DESKTOP:
        return entries, deleted
    for d in glob.glob(os.path.join(DESKTOP, 'claude-code-sessions', '*', '*', '')):
        for f in glob.glob(os.path.join(d, 'local_*.json')):
            try:
                j = json.load(open(f))
            except (OSError, ValueError):
                continue
            stem = os.path.basename(f)[len('local_'):-len('.json')]
            entries[stem] = j
            if j.get('cliSessionId'):
                entries[j['cliSessionId']] = j
        for f in glob.glob(os.path.join(d, 'deleted_*')):
            deleted.add(os.path.basename(f)[len('deleted_'):])
    return entries, deleted


def parity(state):
    """Changes made in one app follow in the other.
    Claude -> Codex (official app-server): manual rename, archive, unarchive, delete (-> archive).
    Codex -> Claude: rename, archive, unarchive, delete (-> archive) are queued and written into Claude
    desktop's registry only while Claude is closed (apply_offline), so nothing races the running app.
    Only transitions seen after pairing propagate; our own writes are recorded so they never echo back."""
    for tid, v in list(state.items()):  # pairs from the first engine version lack sid/path
        if isinstance(v, dict) and not v.get('sid') and not tid.startswith('_') and ('pushed' in v or 'origin' in v):
            try:
                cwd = thread_info(tid, state)[1]
            except (LookupError, sqlite3.Error):
                continue
            path = claude_path_of(tid, cwd, state)
            if os.path.exists(path):
                v.update(sid=os.path.basename(path)[:-len('.jsonl')], path=path, cwd=cwd, origin=v.get('origin', 'codex'))
    pairs = {k: v for k, v in state.items() if isinstance(v, dict) and v.get('sid')}
    metas = codex_threads_meta(list(pairs))
    reg, tomb = claude_registry()
    locked = codex_locked()
    archive, unarchive, rename = [], [], []
    # a thread deleted in Codex disappears from its DB; guard against an unreadable DB or a mass vanish
    vanished = {t for t, v in pairs.items() if t not in metas and v.get('seen_codex_present')}
    trust_vanish = bool(metas) and len(vanished) <= 3
    for tid, p in pairs.items():
        sid, meta, e = p['sid'], metas.get(tid), reg.get(p['sid']) or {}
        queued = p.get('pending_claude') or {}
        cx = bool(meta[5]) if meta else bool(p.get('seen_codex_archived'))
        codex_gone = trust_vanish and tid in vanished
        cname = (meta[1] or None) if meta else None
        cl = queued.get('archived', bool(e.get('isArchived')))  # what Claude will show after the queue
        gone = sid in tomb
        manual_title = e.get('title') if e.get('titleSource') in ('user', 'tool') else None
        if 'seen_codex_archived' not in p:
            # old archive states are a baseline, except for threads this engine created from a Claude
            # session that was already archived there (they should never have become visible)
            p['seen_codex_archived'], p['seen_claude_archived'] = cx, (cl and not p.get('created'))
        p.setdefault('seen_claude_deleted', gone)
        p.setdefault('seen_claude_title', manual_title)
        p.setdefault('seen_codex_name', cname)
        p.setdefault('seen_codex_present', meta is not None)
        # Codex -> Claude (queued)
        if not gone:
            if (cx and not p['seen_codex_archived'] or codex_gone) and not cl:
                queued['archived'] = cl = True
            elif p['seen_codex_archived'] and not cx and meta and cl:
                queued['archived'] = cl = False
            if cname and cname != p['seen_codex_name'] and cname != manual_title:
                queued['title'] = cname
        # changed and changed back before Claude restarted: nothing left to apply
        if 'archived' in queued and queued['archived'] == bool(e.get('isArchived')):
            queued.pop('archived')
        if queued.get('title') and e.get('title') in (queued['title'], cname):
            queued.pop('title')
        if queued:
            p['pending_claude'] = queued
        else:
            p.pop('pending_claude', None)
        # Claude -> Codex (official app-server)
        if ((cl and not p['seen_claude_archived']) or (gone and not p['seen_claude_deleted'])) and not cx and meta:
            p['pending_codex_archive'] = True
        if p['seen_claude_archived'] and not cl and not gone and cx:
            p['pending_codex_unarchive'] = True
        if manual_title and manual_title != p['seen_claude_title'] and meta and manual_title != (cname or ''):
            p['pending_codex_name'] = manual_title
        if p.get('pending_codex_archive') and not cx and tid not in locked:
            archive.append(tid)
        if p.get('pending_codex_unarchive') and cx:
            unarchive.append(tid)
        if p.get('pending_codex_name') and not cx:
            rename.append(tid)
        if not cx:
            p.pop('pending_codex_unarchive', None)
        if cx:
            p.pop('pending_codex_archive', None)
        p['seen_codex_archived'], p['seen_claude_archived'], p['seen_claude_deleted'] = cx, cl, gone
        p['seen_claude_title'], p['seen_codex_name'] = manual_title, cname
        p['seen_codex_present'] = meta is not None if (trust_vanish or meta is not None) else p['seen_codex_present']
        p['archived'] = bool(cx or cl or gone or codex_gone)
    ui = state.setdefault('_ui', {})
    for tid in codex_rpc('thread/archive', [(t, {'threadId': t}) for t in archive]):
        state[tid].pop('pending_codex_archive', None)
        state[tid]['seen_codex_archived'] = True
        ui['codex_reconcile'] = True
    for tid in codex_rpc('thread/unarchive', [(t, {'threadId': t}) for t in unarchive]):
        state[tid].pop('pending_codex_unarchive', None)
        state[tid]['seen_codex_archived'] = False
        state[tid]['archived'] = bool(state[tid].get('seen_claude_archived'))
        ui['codex_reconcile'] = True  # the sidebar's incremental refresh skips threads with an old updated_at
    for tid in codex_rpc('thread/name/set', [(t, {'threadId': t, 'name': state[t]['pending_codex_name'][:120]})
                                            for t in rename]):
        state[tid]['seen_codex_name'] = state[tid].pop('pending_codex_name')[:120]
        ui['codex_reconcile'] = True  # the sidebar keeps the old title until its next reconciliation
    save_state(state)


def apply_offline(state):
    """Writes into the apps' own stores, done only while that app is closed (e.g. during Mirror's
    quit -> sync -> relaunch), so a running app can neither race nor overwrite them."""
    ui = state.setdefault('_ui', {})
    if not claude_running():
        for tid, p in state.items():
            q = isinstance(p, dict) and p.get('pending_claude')
            if not q:
                continue
            if claude_registry_update(p['sid'], archived=q.get('archived'), title=q.get('title')):
                if q.get('title') and p.get('path') and os.path.exists(p['path']):
                    with open(p['path'], 'a') as f:  # the CLI's own rename record, for resume pickers
                        f.write(json.dumps({'type': 'custom-title', 'customTitle': q['title'],
                                            'sessionId': p['sid']}, ensure_ascii=False) + '\n')
                if 'archived' in q:
                    p['seen_claude_archived'] = q['archived']
                if q.get('title'):
                    p['seen_claude_title'] = q['title']
            p.pop('pending_claude', None)
        ui.pop('claude_refresh_ms', None)
    if ui.get('codex_reconcile') and not codex_running():
        if codex_catalog_reconcile():
            ui.pop('codex_reconcile', None)
    save_state(state)


def codex_catalog_pending():
    """True while a requested sidebar reconciliation has not run yet (it runs on the app's next start)."""
    db = f'{CODEX}/sqlite/codex-dev.db'
    if not os.path.exists(db):
        return False
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=5)
        row = con.execute("select last_full_reconciled_at from local_thread_catalog_sync_state where host_id = 'local'").fetchone()
        con.close()
        return bool(row) and row[0] is None
    except sqlite3.Error:
        return False


def codex_catalog_reconcile():
    """The Codex desktop sidebar is a separate catalog (sqlite/codex-dev.db) that only learns about archives
    through its own app-server's notifications. Clearing last_full_reconciled_at makes the app run its own
    full reconciliation on its next start, which drops threads the app-server no longer lists."""
    db = f'{CODEX}/sqlite/codex-dev.db'
    if not os.path.exists(db):
        return False
    con = sqlite3.connect(db, timeout=10)
    try:
        con.execute("update local_thread_catalog_sync_state set last_full_reconciled_at = NULL where host_id = 'local'")
        con.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        con.close()


# ---------------- status + sync ----------------

def _state_of(t):
    if t['archived']:
        return 'archived'
    if t['error']:
        return 'error'
    if t['codex_working']:
        return 'working_codex'
    if t['claude_working']:
        return 'working_claude'
    if t['to_codex'] and t['codex_locked']:
        return 'waiting_codex'
    if t['to_claude'] and t['claude_open']:
        return 'waiting_claude'
    if t['to_codex'] or t['to_claude']:
        return 'pending'
    return 'synced'


def _thread_row(tid, state, metas, cache, procs, locked, codex_on, paired_paths):
    pair = state.get(tid) or {}
    meta = metas.get(tid)
    if meta:
        title, name, cwd, rollout, updated_ms, archived = meta
    elif pair.get('rollout'):
        title, name, cwd, rollout, updated_ms, archived = pair.get('title'), None, pair['cwd'], pair['rollout'], 0, 0
    else:
        return None
    archived = bool(archived or pair.get('archived'))
    path = claude_path_of(tid, cwd, state)
    paired_paths.add(path)
    ri = rollout_index(rollout, cache)
    ci = claude_info(path, cache) if os.path.exists(path) else None
    if ri is None:
        return None
    done = set(ci['ids']) if ci else set()
    cutoff = max(ci['cutoff'] if ci else 0, pair.get('baseline_ms') or 0)
    pend = [turn for k, ms, mirror, turn in ri['items'] if not mirror and k not in done and ms > cutoff]
    to_claude = len(pend)
    pushed = set(pair.get('pushed', []))
    to_codex = sum(1 for u, _, last in (ci['turns'] if ci else []) if u not in pushed and last not in pushed)
    sid = os.path.basename(path)[:-len('.jsonl')]
    proc = procs.get(sid)
    t = {'id': tid, 'claude_session': sid if ci else None, 'origin': pair.get('origin', 'codex'),
         'title': (name or title or (ci or {}).get('title') or 'Без названия').strip().splitlines()[0][:80],
         'project': os.path.basename(cwd.rstrip('/')) or cwd, 'cwd': cwd,
         'updated_ms': max(updated_ms or 0, ri['last_ms'], (ci or {}).get('last_ms', 0)),
         'to_claude': to_claude, 'to_claude_turns': len(set(pend)), 'to_codex': to_codex,
         'codex_locked': tid in locked, 'claude_open': bool(proc and ci),
         # a turn that died without task_complete must not look like work forever
         'codex_working': bool(ri['open'] and codex_on and tid in locked
                               and time.time() - os.path.getmtime(rollout) < 20 * 60),
         'claude_working': bool(proc and proc['status'] == 'busy'),
         'codex_visible': bool(meta), 'archived': archived, 'paired': bool(ci),
         'claude_visible': bool(pair.get('claude_registered') or pair.get('origin') == 'claude'),
         'last_ok_ms': pair.get('last_ok_ms'), 'error': pair.get('last_error'), 'claude_path': path,
         'rollout': rollout}
    return t


def collect(days, state, cache):
    """Every pair/candidate with pending counts and lock/working flags (read-only)."""
    procs = claude_procs()
    locked = codex_locked()
    codex_on = codex_running()
    ids = set(active_threads(days)) | {k for k, v in state.items()
                                       if isinstance(v, dict) and ({'origin', 'pushed', 'path', 'sid'} & v.keys())}
    metas = codex_threads_meta(ids)
    threads, paired_paths = [], set()
    for tid in ids:
        try:
            row = _thread_row(tid, state, metas, cache, procs, locked, codex_on, paired_paths)
        except Exception as e:  # one unreadable file must not take the whole status down
            pair = state.get(tid) or {}
            row = {'id': tid, 'claude_session': None, 'origin': pair.get('origin', 'codex'),
                   'title': pair.get('title') or tid, 'project': '', 'cwd': '', 'updated_ms': 0,
                   'to_claude': 0, 'to_claude_turns': 0, 'to_codex': 0, 'codex_locked': False, 'claude_open': False,
                   'codex_working': False, 'claude_working': False, 'codex_visible': False, 'archived': False,
                   'paired': False, 'last_ok_ms': None, 'error': f'{type(e).__name__}: {e}'[:300],
                   'claude_path': None, 'rollout': None}
        if row:
            threads.append(row)
    for path, ci in claude_candidates(days, cache, paired_paths):
        sid = os.path.basename(path)[:-len('.jsonl')]
        proc = procs.get(sid)
        threads.append({'id': None, 'claude_session': sid, 'origin': 'claude', 'title': ci['title'],
                        'project': os.path.basename(ci['cwd'].rstrip('/')), 'cwd': ci['cwd'],
                        'updated_ms': ci['last_ms'], 'to_claude': 0, 'to_claude_turns': 0, 'to_codex': len(ci['turns']),
                        'codex_locked': False, 'claude_open': bool(proc), 'codex_working': False,
                        'claude_working': bool(proc and proc['status'] == 'busy'), 'codex_visible': False,
                        'archived': False, 'paired': False, 'last_ok_ms': None, 'error': None,
                        'claude_path': path, 'rollout': None})
    for t in threads:
        t['state'] = _state_of(t)
    threads.sort(key=lambda t: t['updated_ms'], reverse=True)
    return threads, procs, locked, codex_on


def status(days=7):
    guard = _guard()
    try:
        state, cache = load_state(), load_cache()
        threads, procs, locked, codex_on = collect(days, state, cache)
        save_cache(cache)
    finally:
        guard.close()
    by = lambda s: sum(1 for t in threads if t['state'] == s)
    live = [t for t in threads if not t['archived']]
    claude_on = claude_running()
    started = app_started_ms('/Applications/Claude.app/Contents/MacOS/Claude') if claude_on else None
    refresh = (state.get('_ui') or {}).get('claude_refresh_ms') or 0
    return {
        'generated_ms': int(time.time() * 1000), 'days': days,
        'apps': {
            'codex': {'running': codex_on, 'locked': sorted(locked),
                      # turns that only a Codex restart can deliver, and whether its sidebar awaits a restart
                      'blocked': sum(t['to_codex'] for t in live if t['codex_locked']),
                      'restart_needed': bool(codex_on and ((state.get('_ui') or {}).get('codex_reconcile')
                                                           or codex_catalog_pending())),
                      'working': [{'id': t['id'], 'title': t['title']} for t in threads if t['codex_working']]},
            'claude': {'running': claude_on,
                       'blocked': sum(t['to_claude_turns'] for t in live if t['claude_open']),
                       'restart_needed': bool(claude_on and ((started and started < refresh) or any(
                           isinstance(v, dict) and v.get('pending_claude') for v in state.values()))),
                       'open': [{'session_id': s, 'title': p.get('name'), 'status': p['status'], 'pid': p['pid']}
                                for s, p in procs.items()],
                       'working': [{'session_id': s, 'title': p.get('name') or _claude_title(s, threads)}
                                   for s, p in procs.items() if p['status'] == 'busy']},
        },
        'totals': {'threads': len(threads), 'synced': by('synced'), 'pending': by('pending'),
                   'waiting': by('waiting_codex') + by('waiting_claude'),
                   'working': by('working_codex') + by('working_claude'), 'errors': by('error'),
                   'to_claude': sum(t['to_claude'] for t in threads if not t['archived']),
                   'to_claude_turns': sum(t['to_claude_turns'] for t in threads if not t['archived']),
                   'to_codex': sum(t['to_codex'] for t in threads if not t['archived'])},
        'threads': threads,
    }


def _claude_title(sid, threads):
    return next((t['title'] for t in threads if t['claude_session'] == sid), None)


def _guard():
    os.makedirs(DATA, exist_ok=True)
    g = open(f'{DATA}/state.json.lock', 'a+')
    fcntl.flock(g, fcntl.LOCK_EX)
    return g


def sync(days=7, only=None):
    """One tick: work only where the index shows a diff and the target side is free. Returns actions."""
    guard = _guard()
    state, cache = load_state(), load_cache()
    actions = []
    try:
        threads, *_ = collect(days, state, cache)
        for t in threads:
            if t['archived'] or (only and t['id'] not in only and t['claude_session'] not in only):
                continue
            key = t['id'] or t['claude_session']
            try:
                if not t['paired'] and t['origin'] == 'claude' and t['id'] is None:
                    if t['to_codex']:
                        tid, res = adopt(t['claude_path'], claude_info(t['claude_path'], cache), state)
                        actions.append({'thread': tid, 'session': t['claude_session'], 'action': 'adopt', 'result': res})
                    continue
                if t['to_claude'] and not t['claude_open']:
                    res = convert(t['id'], quiet=True, state=state)
                    actions.append({'thread': t['id'], 'action': 'to_claude', 'result': res})
                if t['to_codex'] and not t['codex_locked']:
                    res = back(t['id'], state=state, quiet=True)
                    actions.append({'thread': t['id'], 'action': 'to_codex', 'result': res})
                s = state.setdefault(t['id'], {})
                s.pop('last_error', None)
            except Exception as e:  # one broken thread must not stop the tick
                if t['id']:
                    state.setdefault(t['id'], {})['last_error'] = f'{type(e).__name__}: {e}'[:300]
                actions.append({'thread': key, 'action': 'error', 'result': repr(e)[:300]})
        try:
            make_visible(state, cache)
        except Exception as e:
            actions.append({'thread': '-', 'action': 'error', 'result': f'make_visible: {e!r}'[:300]})
        try:
            parity(state)
            apply_offline(state)
        except Exception as e:
            actions.append({'thread': '-', 'action': 'error', 'result': f'parity: {e!r}'[:300]})
        # second pass: mark what is fully in sync now
        global _PROCS
        _PROCS = None
        threads, *_ = collect(days, state, cache)
        now = int(time.time() * 1000)
        for t in threads:
            if t['id'] and t['paired'] and not t['to_claude'] and not t['to_codex']:
                state.setdefault(t['id'], {})['last_ok_ms'] = now
    finally:
        save_state(state)
        save_cache(cache)
        guard.close()
    for a in actions:
        print(f"{datetime.now():%H:%M:%S} {a['thread']} {a['action']}: {a['result']}", file=sys.stderr, flush=True)
    return actions


def record(path):
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    m = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {'files': []}
    if path not in m['files']:
        m['files'].append(path)
    json.dump(m, open(MANIFEST, 'w'), indent=1)


def rollback():
    if not os.path.exists(MANIFEST):
        return print('nothing to roll back')
    for p in json.load(open(MANIFEST))['files']:
        if os.path.exists(p):
            os.remove(p); print('removed', p)
    os.remove(MANIFEST)


if __name__ == '__main__':
    a = sys.argv[1:]
    days = int(a[a.index('--days') + 1]) if '--days' in a else 7
    if a[:1] == ['rollback']:
        rollback()
    elif a[:1] == ['back'] and len(a) >= 3:
        print(back(a[1], a[2]))               # dry run into a copy
    elif a[:1] == ['back-apply'] and len(a) >= 2:
        g = _guard()                          # append to the real rollout under Codex's lock
        print(back(a[1]))
        g.close()
    elif a[:1] == ['convert'] and len(a) >= 2:
        root = a[a.index('--root') + 1] if '--root' in a else None
        g = _guard()
        print(convert(a[1], root, thinking='--no-thinking' not in a))
        g.close()
    elif a[:1] == ['sync']:
        ids = [a[i + 1] for i, x in enumerate(a) if x == '--thread']
        MANUAL = '--manual' in a
        res = sync(days, only=ids or None)
        if '--json' in a:
            print(json.dumps(res, ensure_ascii=False))
    elif a[:1] == ['status']:
        print(json.dumps(status(days), ensure_ascii=False))
    else:
        print(__doc__)
