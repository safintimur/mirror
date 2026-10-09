#!/usr/bin/env python3
"""End-to-end parity test for codex_mirror.py in a throwaway sandbox.

    MIRROR_E2E_ROOT=<dir> python3 tests/test_parity.py

Creates <root>/e2e-<n> with its own CODEX_HOME, CLAUDE_CONFIG_DIR and MIRROR_STATE_DIR, then drives
the real engine (subprocess, env overrides) and a sandbox `codex app-server` through Codex-born,
Claude-born, import-paired and busy-side scenarios. Prints one PASS/FAIL line per scenario and
writes <sandbox>/results.json. Uses synthetic session metadata by default; an explicit
MIRROR_E2E_TEMPLATE glob can supply a compatible rollout's first line for local diagnostics.
Thread cwds are short fake paths (/nonexistent-mirror-e2e/<n>/...): never created, they only name
Claude project dirs (the sandbox path itself would exceed the 255-byte file-name limit).
"""
import difflib, glob, hashlib, json, os, queue, re, shutil, sqlite3, subprocess, sys, tempfile, threading, time, traceback, uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / 'codex_mirror.py'
sys.path.insert(0, str(REPO))
from codex_mirror import CODEX_BIN
HOME = Path.home()
REAL_DIRS = [HOME / '.codex', HOME / '.claude', HOME / '.local/share/codex-claude-mirror']
NS = uuid.UUID('5f1c7f9e-6c1b-4f0e-9a52-0c0dec0dec0d')  # engine namespace: mirror sid = uuid5(NS, thread)
MIRROR_IDS = ('claude-', 'msg_claude_', 'rs_claude_')
SKIP_USER = ('<local-command-caveat>', '<command-name>', '<local-command-stdout>', '<system-reminder>')
ENGINE_VERSION = re.search(r"^VERSION = '([^']+)'", ENGINE.read_text(), re.M).group(1)
DAYS = '7'


class Fail(AssertionError):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def uuid7(ms):
    r = uuid.uuid4().int & ((1 << 74) - 1)
    return str(uuid.UUID(int=(ms << 80) | (0x7 << 76) | ((r >> 62) << 64) | (0b10 << 62) | (r & ((1 << 62) - 1))))


def jl(path):
    out = []
    for line in open(path):
        if line.strip():
            out.append(json.loads(line))
    return out


def udiff(old, new):
    lines = difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm='', n=3)
    return '\n'.join(l for l in lines if not l.startswith(('---', '+++'))) + '\n'


class Clock:
    """Monotonic fake time shared by both sides (starts 6 h ago so everything is 'recent')."""

    def __init__(self):
        self.ms = int(time.time() * 1000) - 6 * 3600 * 1000

    def tick(self, sec=1.0):
        self.ms += int(sec * 1000)
        return self.ms


# ---------------------------------------------------------------- sandbox + engine

class Sandbox:
    def __init__(self, root):
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        nums = [int(m.group(1)) for p in root.iterdir() if (m := re.fullmatch(r'e2e-(\d+)', p.name))]
        self.n = max(nums, default=0) + 1
        self.dir = root / f'e2e-{self.n}'
        for real in REAL_DIRS:
            r = real.resolve()
            if self.dir == r or r in self.dir.parents or self.dir in r.parents:
                sys.exit(f'refusing to run: sandbox {self.dir} overlaps real data {r}')
        self.codex, self.claude, self.state, self.logs = (self.dir / x for x in ('codex', 'claude', 'state', 'logs'))
        for d in (self.codex, self.claude / 'projects', self.claude / 'sessions', self.state, self.logs):
            d.mkdir(parents=True)
        # the user's documented setting: Codex's own Claude import must not run concurrently
        (self.codex / 'config.toml').write_text('[desktop]\nexternal-agent-import-sync-enabled = false\n')
        self.fake_root = f'/nonexistent-mirror-e2e/{self.n}'
        self.meta = meta_template()

    def env(self):
        e = dict(os.environ)
        e.update(CODEX_HOME=str(self.codex), CLAUDE_CONFIG_DIR=str(self.claude), MIRROR_STATE_DIR=str(self.state))
        e.pop('MIRROR_CLAUDE_DESKTOP', None)
        for k in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'MIRROR_STATE_DIR'):
            assert e[k].startswith(str(self.dir) + '/'), k
        return e

    def engine(self, *args):
        p = subprocess.run([sys.executable, str(ENGINE), *args], env=self.env(), capture_output=True, text=True,
                           timeout=180)
        with open(self.logs / 'engine.log', 'a') as f:
            f.write(f'$ codex_mirror.py {" ".join(args)}  [rc={p.returncode}]\n{p.stdout}{p.stderr}\n')
        if p.returncode:
            raise Fail(f'engine {args} rc={p.returncode}: {p.stderr.strip()[-600:]}')
        return p

    def sync(self):
        out = self.engine('sync', '--json', '--days', DAYS).stdout.strip()
        return json.loads(out.splitlines()[-1]) if out else []

    def status(self):
        return json.loads(self.engine('status', '--json', '--days', DAYS).stdout)

    def pairs(self):
        f = self.state / 'state.json'
        return json.loads(f.read_text()) if f.exists() else {}

    def project(self, cwd):
        return self.claude / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', cwd)

    def cwd(self, name):
        return f'{self.fake_root}/{name}'

    def insert_thread(self, tid, rollout, cwd, title, name=None, archived=False, created_ms=None):
        now = int(time.time() * 1000)
        created_ms = created_ms or now
        row = dict(id=tid, rollout_path=str(rollout), created_at=created_ms // 1000, updated_at=now // 1000,
                   source='vscode', model_provider='openai', cwd=cwd, title=title, sandbox_policy='{"type":"disabled"}',
                   approval_mode='never', archived=int(archived), archived_at=now // 1000 if archived else None,
                   cli_version='0.158.0-alpha.2.1', first_user_message=title, created_at_ms=created_ms,
                   updated_at_ms=now, thread_source='user', preview=title, history_mode='paginated', name=name,
                   originator='Codex Desktop', model='gpt-6-astra')
        db = sqlite3.connect(self.codex / 'state_5.sqlite')
        db.execute(f"insert into threads ({','.join(row)}) values ({','.join('?' * len(row))})", list(row.values()))
        db.commit()
        db.close()

    def add_import_record(self, rec):
        f = self.codex / 'external_agent_session_imports.json'
        j = json.loads(f.read_text()) if f.exists() else {'records': [], 'detected_connector_records': []}
        j['records'].append(rec)
        f.write_text(json.dumps(j, indent=1))


def meta_template():
    """Synthetic metadata; real transcripts are read only when explicitly requested."""
    override = os.environ.get('MIRROR_E2E_TEMPLATE')
    if not override:
        return json.loads((REPO / 'tests/fixtures/session_meta.json').read_text())
    for f in sorted(glob.glob(override)):
        try:
            o = json.loads(open(f).readline())
            if o.get('type') == 'session_meta':
                return o
        except (OSError, ValueError):
            pass
    raise Fail('MIRROR_E2E_TEMPLATE did not match a valid session_meta record')


class AppServer:
    """Sandbox `codex app-server --listen stdio://` JSON-RPC client."""

    def __init__(self, sb):
        self.sb = sb

    def __enter__(self):
        self.err = open(self.sb.logs / 'app-server.stderr', 'a')
        self.p = subprocess.Popen([CODEX_BIN, 'app-server', '--listen', 'stdio://'], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=self.err, text=True, bufsize=1,
                                  env=self.sb.env())
        self.q, self.n = queue.Queue(), 0
        threading.Thread(target=lambda: [self.q.put(l) for l in self.p.stdout], daemon=True).start()
        self.call('initialize', {'clientInfo': {'name': 'mirror-parity-test', 'version': '0'}})
        self._send({'jsonrpc': '2.0', 'method': 'initialized'})
        return self

    def _send(self, msg):
        self.p.stdin.write(json.dumps(msg) + '\n')
        self.p.stdin.flush()

    def call(self, method, params=None, timeout=60):
        self.n += 1
        msg = {'jsonrpc': '2.0', 'id': self.n, 'method': method}
        if params is not None:
            msg['params'] = params
        self._send(msg)
        end = time.time() + timeout
        while time.time() < end:
            try:
                o = json.loads(self.q.get(timeout=max(0.01, end - time.time())))
            except queue.Empty:
                break
            if o.get('id') == self.n and ('result' in o or 'error' in o):
                if 'error' in o:
                    raise Fail(f'{method}: {o["error"]}')
                return o['result']
        raise Fail(f'{method}: no response in {timeout}s')

    def list_threads(self):
        res, cursor = {}, None
        for _ in range(20):
            r = self.call('thread/list', {'limit': 100, **({'cursor': cursor} if cursor else {})})
            res.update({t['id']: t for t in r['data']})
            cursor = r.get('nextCursor')
            if not cursor:
                break
        return res

    def turns(self, tid):
        self.thread = self.call('thread/resume', {'threadId': tid})['thread']
        out, cursor = [], None
        for _ in range(20):
            r = self.call('thread/turns/list', {'threadId': tid, 'itemsView': 'full', **({'cursor': cursor} if cursor else {})})
            out += r['data']
            cursor = r.get('nextCursor')
            if not cursor:
                break
        return out

    def __exit__(self, *a):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        self.p.terminate()
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(5)
        self.err.close()


def turn_prompt(turn):
    return next(('\n'.join(c.get('text', '') for c in it['content']).strip()
                 for it in turn['items'] if it['type'] == 'userMessage'), None)


# ---------------------------------------------------------------- Codex side

class Rollout:
    def __init__(self, sb, path, tid, cwd):
        self.sb, self.path, self.tid, self.cwd = sb, Path(path), tid, cwd

    @classmethod
    def new(cls, sb, clock, cwd, where=None, ms=None):
        ms = ms or clock.tick()
        tid = uuid7(ms)
        local = datetime.fromtimestamp(ms / 1000)
        d = where or sb.codex / f'sessions/{local:%Y/%m/%d}'
        path = Path(d) / f'rollout-{local:%Y-%m-%dT%H-%M-%S}-{tid}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = json.loads(json.dumps(sb.meta))
        meta.update(timestamp=iso(ms), ordinal=0)
        p = meta['payload']
        p.pop('git', None)
        p.update(session_id=tid, id=tid, timestamp=iso(ms), cwd=cwd, runtime_workspace_roots=[cwd],
                 context_window={'window_id': uuid7(ms)})
        path.write_text(json.dumps(meta, ensure_ascii=False) + '\n')
        return cls(sb, path, tid, cwd)

    def records(self):
        return jl(self.path)

    def size(self):
        return self.path.stat().st_size

    def append(self, recs):
        n = max(o.get('ordinal', 0) for o in self.records())
        with open(self.path, 'a') as f:
            for ms, typ, payload in recs:
                n += 1
                f.write(json.dumps({'timestamp': iso(ms), 'ordinal': n, 'type': typ, 'payload': payload},
                                   ensure_ascii=False) + '\n')

    def codex_turn(self, clock, prompt, steps=(), final=None):
        """Append one native Codex turn (shapes as in real rollouts)."""
        recs, t0 = [], clock.tick()
        turn = uuid7(t0)

        def done(ms, item):
            recs.append((ms, 'event_msg', {'type': 'item_completed', 'thread_id': self.tid, 'turn_id': turn,
                                           'item': item, 'started_at_ms': ms, 'completed_at_ms': ms}))

        def say(ms, text, phase):
            mid = 'msg_' + uuid.uuid4().hex + uuid.uuid4().hex[:18]
            done(ms, {'type': 'AgentMessage', 'id': mid, 'content': [{'type': 'Text', 'text': text}], 'phase': phase})
            recs.append((ms, 'response_item', {'type': 'message', 'id': mid, 'role': 'assistant',
                                               'content': [{'type': 'output_text', 'text': text}], 'phase': phase,
                                               'internal_chat_message_metadata_passthrough': {'turn_id': turn}}))

        recs.append((t0, 'event_msg', {'type': 'task_started', 'turn_id': turn, 'root_turn_id': turn,
                                       'started_at': t0 // 1000, 'model_context_window': 258400,
                                       'collaboration_mode_kind': 'default'}))
        t = clock.tick(0.2)
        recs.append((t, 'response_item', {'type': 'message', 'id': f'msg_{uuid7(t)}', 'role': 'user',
                                          'content': [{'type': 'input_text', 'text': prompt}],
                                          'internal_chat_message_metadata_passthrough': {
                                              'turn_id': turn, 'create_time': t / 1000, 'content_item_kinds': ['user.text']}}))
        done(t, {'type': 'UserMessage', 'id': uuid7(t), 'client_id': str(uuid.uuid4()),
                 'content': [{'type': 'text', 'text': prompt + '\n', 'text_elements': []}]})
        for s in steps:
            t = clock.tick()
            iid = f'exec-{uuid.uuid4()}'
            if s[0] == 'reason':
                done(t, {'type': 'Reasoning', 'id': 'rs_' + uuid.uuid4().hex, 'summary_text': [s[1]], 'raw_content': []})
            elif s[0] == 'say':
                say(t, s[1], 'commentary')
            elif s[0] == 'cmd':
                _, cmd, out, code = s
                done(t, {'type': 'CommandExecution', 'id': iid, 'process_id': '4242', 'command': ['/bin/zsh', '-lc', cmd],
                         'cwd': f'file://{self.cwd}', 'parsed_cmd': [{'type': 'unknown', 'cmd': cmd}],
                         'source': 'unified_exec_startup', 'status': 'completed' if code == 0 else 'failed',
                         'stdout': out, 'stderr': '', 'aggregated_output': out, 'exit_code': code,
                         'duration': {'secs': 0, 'nanos': 1500000}, 'formatted_output': out})
            elif s[0] == 'edit':
                _, path, old, new = s
                done(t, {'type': 'FileChange', 'id': iid, 'status': 'completed', 'stdout': '', 'stderr': '',
                         'changes': {path: {'type': 'update', 'move_path': None, 'unified_diff': udiff(old, new)}}})
            elif s[0] == 'add':
                _, path, content = s
                done(t, {'type': 'FileChange', 'id': iid, 'status': 'completed', 'stdout': '', 'stderr': '',
                         'changes': {path: {'type': 'add', 'content': content}}})
            elif s[0] == 'mcp':
                _, server, tool, args, text = s
                done(t, {'type': 'McpToolCall', 'id': iid, 'server': server, 'tool': tool, 'arguments': args,
                         'readOnlyHint': True, 'status': 'completed',
                         'result': {'content': [{'type': 'text', 'text': text}], 'isError': False},
                         'duration': {'secs': 0, 'nanos': 300000000}})
        if final:
            say(clock.tick(), final, 'final_answer')
        t = clock.tick(0.2)
        recs.append((t, 'event_msg', {'type': 'task_complete', 'turn_id': turn, 'last_agent_message': final,
                                      'started_at': t0 // 1000, 'completed_at': t // 1000, 'duration_ms': t - t0}))
        self.append(recs)
        return turn

    def import_turns(self, ms, turns):
        """Codex's own import of a Claude session (external-import-turn-N / item-K, all stamped at import time)."""
        recs, k = [], 0
        for i, (prompt, final) in enumerate(turns, 1):
            turn = f'external-import-turn-{i}'

            def item(it):
                nonlocal k
                k += 1
                it['id'] = f'item-{k}'
                recs.append((ms, 'event_msg', {'type': 'item_completed', 'thread_id': self.tid, 'turn_id': turn,
                                               'item': it, 'completed_at_ms': ms}))
            recs.append((ms, 'event_msg', {'type': 'task_started', 'turn_id': turn, 'started_at': ms // 1000,
                                           'model_context_window': None, 'collaboration_mode_kind': 'default'}))
            item({'type': 'UserMessage', 'content': [{'type': 'text', 'text': prompt, 'text_elements': []}]})
            recs.append((ms, 'response_item', {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': prompt}]}))
            note = '[external_agent_tool_call: Bash]\ndescription: look around\ncommand: ls'
            for text in (note, final):
                item({'type': 'AgentMessage', 'content': [{'type': 'Text', 'text': text}], 'phase': None})
                recs.append((ms, 'response_item', {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}))
            recs.append((ms, 'event_msg', {'type': 'task_complete', 'turn_id': turn, 'last_agent_message': None,
                                           'started_at': ms // 1000}))
        self.append(recs)


def codex_items(path):
    return [o['payload']['item'] for o in jl(path)
            if o.get('type') == 'event_msg' and o['payload'].get('type') == 'item_completed']


def codex_prompts(path):
    return ['\n'.join(c.get('text', '') for c in it.get('content') or []).strip()
            for it in codex_items(path) if it['type'] == 'UserMessage']


def codex_texts(path):
    return [('\n'.join(c.get('text', '') for c in it['content']) if isinstance(it.get('content'), list) else it.get('text', '')).strip()
            for it in codex_items(path) if it['type'] == 'AgentMessage']


def codex_tools(path):
    names = []
    for it in codex_items(path):
        if it['type'] == 'CommandExecution':
            names.append('Bash')
        elif it['type'] == 'FileChange':
            names += [{'add': 'Write', 'delete': 'Bash'}.get(ch['type'], 'Edit') for ch in it['changes'].values()]
        elif it['type'] == 'McpToolCall':
            names.append(it['tool'] if it['server'] == 'claude' else f"mcp__{it['server']}__{it['tool']}")
    return names


def codex_thinking(path):
    return ['\n\n'.join(it.get('summary_text') or []).strip() for it in codex_items(path)
            if it['type'] == 'Reasoning' and ''.join(it.get('summary_text') or []).strip()]


def turn_count(path):
    return sum(1 for o in jl(path) if o.get('type') == 'event_msg' and o['payload'].get('type') == 'task_started')


# ---------------------------------------------------------------- Claude side

class ClaudeSession:
    def __init__(self, sb, cwd, sid=None, path=None, entrypoint='claude-desktop', version='2.1.281'):
        self.sb, self.cwd, self.sid = sb, cwd, sid or str(uuid.uuid4())
        self.path = Path(path) if path else sb.project(cwd) / f'{self.sid}.jsonl'
        self.entrypoint, self.version = entrypoint, version

    def size(self):
        return self.path.stat().st_size if self.path.exists() else 0

    def records(self):
        return jl(self.path)

    def leaf(self):
        leaf = None
        if self.path.exists():
            for o in self.records():
                if o.get('uuid') and not o.get('isSidechain') and o.get('type') in ('user', 'assistant', 'system', 'attachment'):
                    leaf = o['uuid']
        return leaf

    def _write(self, recs):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'a') as f:
            f.write(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in recs))

    def _rec(self, ms, parent, typ, **kw):
        r = {'parentUuid': parent, 'isSidechain': False, 'type': typ}
        r.update(kw)
        r.update(uuid=str(uuid.uuid4()), timestamp=iso(ms), userType='external', entrypoint=self.entrypoint,
                 cwd=self.cwd, sessionId=self.sid, version=self.version, gitBranch='HEAD')
        return r

    def _assistant(self, ms, parent, mid, block, stop=None):
        return self._rec(ms, parent, 'assistant', requestId='req_' + uuid.uuid4().hex[:24], message={
            'model': 'claude-opus-5-5', 'id': mid, 'type': 'message', 'role': 'assistant', 'content': [block],
            'container': None, 'stop_reason': stop, 'stop_sequence': None,
            'usage': {'input_tokens': 2, 'cache_read_input_tokens': 1000, 'output_tokens': 40}})

    def start(self, clock):
        t = clock.tick()
        self._write([{'type': 'queue-operation', 'operation': 'enqueue', 'timestamp': iso(t), 'sessionId': self.sid},
                     self._rec(t, None, 'attachment', attachment={
                         'type': 'hook_success', 'hookName': 'SessionStart:startup', 'toolUseID': str(uuid.uuid4()),
                         'hookEvent': 'SessionStart', 'content': '', 'stdout': '{}', 'stderr': '', 'exitCode': 0})])

    def turn(self, clock, prompt, steps=(), final=None, finish=True, resumed=False, title=None):
        """Append one Claude turn. steps: ('think', t) ('text', t) ('tool', name, input, result, is_error)
        ('open_tool', name, input) — the latter leaves the turn unfinished (tool still running)."""
        recs, parent, pid = [], self.leaf(), str(uuid.uuid4())

        def add(r):
            nonlocal parent
            recs.append(r)
            if r.get('uuid'):
                parent = r['uuid']
            return r

        t = clock.tick()
        recs += [{'type': 'queue-operation', 'operation': op, 'timestamp': iso(t), 'sessionId': self.sid}
                 for op in ('enqueue', 'dequeue')]
        if resumed:  # what Claude desktop writes when it opens an existing transcript (as in real mirrors)
            add(self._rec(t, parent, 'attachment', attachment={
                'type': 'hook_success', 'hookName': 'SessionStart:resume', 'toolUseID': str(uuid.uuid4()),
                'hookEvent': 'SessionStart', 'content': '', 'stdout': '{}', 'stderr': '', 'exitCode': 0}))
            add(self._rec(t, parent, 'user', promptId=pid, isMeta=True, message={'role': 'user', 'content':
                '<local-command-caveat>Caveat: The messages below were generated by the user while running local commands.</local-command-caveat>'}))
            add(self._rec(t, parent, 'user', promptId=pid, message={'role': 'user', 'content':
                '<command-name>/model</command-name>\n<command-message>model</command-message>\n<command-args>claude-opus-5-5</command-args>'}))
            add(self._rec(t, parent, 'user', promptId=pid, message={'role': 'user', 'content':
                '<local-command-stdout>Set model to `claude-opus-5-5`</local-command-stdout>'}))
            content = [{'type': 'text', 'text': '<system-reminder>\nThis conversation is now continuing in the Claude desktop app (Code tab).\n</system-reminder>'},
                       {'type': 'text', 'text': prompt}]
        else:
            content = prompt
        t = clock.tick(0.1)
        add(self._rec(t, parent, 'user', promptId=pid, message={'role': 'user', 'content': content},
                      permissionMode='auto', origin={'kind': 'human'}, promptSource='sdk', turnOrigin='human'))
        add(self._rec(t, parent, 'attachment', attachment={'type': 'date', 'date': datetime.now().strftime('%Y-%m-%d')}))
        mid = 'msg_01' + uuid.uuid4().hex[:22]
        add(self._assistant(clock.tick(), parent, mid, {'type': 'thinking', 'thinking': '', 'signature': 'CAQS' + uuid.uuid4().hex}))
        open_ctx = None
        for s in steps:
            t = clock.tick()
            if s[0] == 'think':
                add(self._assistant(t, parent, mid, {'type': 'thinking', 'thinking': s[1], 'signature': 'CAQS' + uuid.uuid4().hex}))
            elif s[0] == 'text':
                add(self._assistant(t, parent, mid, {'type': 'text', 'text': s[1]}))
            elif s[0] in ('tool', 'open_tool'):
                tu = 'toolu_01' + uuid.uuid4().hex[:22]
                a = add(self._assistant(t, parent, mid, {'type': 'tool_use', 'id': tu, 'name': s[1], 'input': s[2],
                                                         'caller': {'type': 'direct'}}, stop='tool_use'))
                if s[0] == 'open_tool':
                    open_ctx = {'tool': tu, 'asst': a['uuid'], 'pid': pid, 'name': s[1]}
                    break
                add(self._tool_result(clock.tick(0.5), parent, pid, tu, a['uuid'], s[1], s[3], s[4]))
                mid = 'msg_01' + uuid.uuid4().hex[:22]
        if finish and not open_ctx:
            recs += self._finish(clock, parent, mid, final, prompt, title)
        self._write(recs)
        return {'prompt_uuid': next(r['uuid'] for r in recs if r.get('promptId') == pid and r['type'] == 'user'
                                    and not r.get('isMeta') and r['message']['content'] in (prompt, content)),
                'open': open_ctx, 'prompt': prompt}

    def _tool_result(self, ms, parent, pid, tu, asst, name, out, err):
        tur = {'stdout': out, 'stderr': '', 'interrupted': False, 'isImage': False} if name == 'Bash' else out
        return self._rec(ms, parent, 'user', promptId=pid, message={'role': 'user', 'content': [
            {'tool_use_id': tu, 'type': 'tool_result', 'content': out, 'is_error': err}]},
            toolUseResult=tur, sourceToolAssistantUUID=asst)

    def _finish(self, clock, parent, mid, final, prompt, title):
        recs = []
        a = self._assistant(clock.tick(), parent, mid, {'type': 'text', 'text': final}, stop='end_turn')
        t = clock.tick(0.1)
        h = self._rec(t, a['uuid'], 'attachment', attachment={
            'type': 'hook_success', 'hookName': 'Stop', 'toolUseID': str(uuid.uuid4()), 'hookEvent': 'Stop',
            'content': '', 'stdout': '{}\n', 'stderr': '', 'exitCode': 0})
        s = self._rec(t, h['uuid'], 'system', subtype='stop_hook_summary', hookCount=1, hookInfos=[{'command': 'callback'}],
                      hookErrors=[], preventedContinuation=False, stopReason='', hasOutput=False, level='suggestion')
        recs += [a, h, s, {'type': 'last-prompt', 'lastPrompt': prompt[:200], 'leafUuid': s['uuid'], 'sessionId': self.sid}]
        if title:
            recs += [{'type': 'custom-title', 'customTitle': title, 'sessionId': self.sid},
                     {'type': 'agent-name', 'agentName': title, 'sessionId': self.sid}]
        return recs

    def compact(self, clock):
        """Auto-compaction as Claude Code records it: compact_boundary + a compact-summary user record."""
        t = clock.tick()
        b = self._rec(t, None, 'system', subtype='compact_boundary', content='Conversation compacted', isMeta=False,
                      level='info', logicalParentUuid=self.leaf(), compactMetadata={'trigger': 'auto', 'preTokens': 160000})
        u = self._rec(t, b['uuid'], 'user', isCompactSummary=True, isVisibleInTranscriptOnly=True, message={
            'role': 'user', 'content': 'This session is being continued from a previous conversation that ran out of '
                                       'context. The conversation is summarized below:\nSummary: the user asked X.'})
        self._write([b, u])

    def finish_open(self, clock, ctx, out, final):
        parent = self.leaf()
        r = self._tool_result(clock.tick(), parent, ctx['pid'], ctx['tool'], ctx['asst'], ctx['name'], out, False)
        self._write([r] + self._finish(clock, r['uuid'], 'msg_01' + uuid.uuid4().hex[:22], final, 'finish', None))


def claude_msgs(path):
    return [o for o in jl(path) if o.get('type') in ('user', 'assistant') and not o.get('isSidechain')]


def claude_prompts(path):
    res = []
    for o in claude_msgs(path):
        if o['type'] != 'user' or o.get('isMeta'):
            continue
        c = o['message'].get('content')
        blocks = [c] if isinstance(c, str) else [b.get('text', '') for b in c or [] if b.get('type') == 'text']
        txt = '\n'.join(b for b in blocks if b and not b.lstrip().startswith(SKIP_USER)).strip()
        if txt and not txt.startswith('[Request interrupted'):
            res.append(txt)
    return res


def claude_blocks(path, kind):
    return [b for o in claude_msgs(path) if o['type'] == 'assistant' for b in o['message'].get('content') or []
            if isinstance(b, dict) and b.get('type') == kind]


def claude_texts(path):
    return [b['text'].strip() for b in claude_blocks(path, 'text') if b.get('text', '').strip()]


def claude_tools(path):
    return [b['name'] for b in claude_blocks(path, 'tool_use')]


def claude_thinking(path):
    return [b['thinking'].strip() for b in claude_blocks(path, 'thinking') if b.get('thinking', '').strip()]


def check_chain(path):
    """Single linear parentUuid chain over every record that has a uuid; unique uuids."""
    recs = [o for o in jl(path) if o.get('uuid') and not o.get('isSidechain')]
    check(recs and recs[0].get('parentUuid') is None, 'first record must have parentUuid null')
    for a, b in zip(recs, recs[1:]):
        check(b.get('parentUuid') == a['uuid'], f"chain broken at {b['uuid']} ({b['type']}): parent {b.get('parentUuid')} != {a['uuid']}")
    check(len({o['uuid'] for o in recs}) == len(recs), 'duplicate uuids')
    return len(recs)


def check_tool_pairs(path):
    """Every tool_use is answered by the next message with a matching tool_result (and vice versa)."""
    msgs = claude_msgs(path)
    uses = 0
    for i, o in enumerate(msgs):
        for b in o['message'].get('content') or []:
            if not isinstance(b, dict):
                continue
            if o['type'] == 'assistant' and b.get('type') == 'tool_use':
                uses += 1
                nxt = msgs[i + 1] if i + 1 < len(msgs) else None
                check(nxt and any(isinstance(x, dict) and x.get('type') == 'tool_result' and x.get('tool_use_id') == b['id']
                                  for x in nxt['message'].get('content') or []), f"tool_use {b['id']} ({b['name']}) has no result")
                check(nxt.get('sourceToolAssistantUUID') == o['uuid'], f"tool_result for {b['id']} lacks sourceToolAssistantUUID")
            if o['type'] == 'user' and b.get('type') == 'tool_result':
                prev = msgs[i - 1]
                check(any(isinstance(x, dict) and x.get('id') == b['tool_use_id'] for x in prev['message'].get('content') or []),
                      f"orphan tool_result {b['tool_use_id']}")
    return uses


def new_lines(path, from_size):
    with open(path) as f:
        f.seek(from_size)
        return [json.loads(l) for l in f.read().splitlines() if l.strip()]


def acts_for(acts, key, action=None):
    return [a for a in acts if (a.get('thread') == key or a.get('session') == key) and (action is None or a['action'] == action)]


def thread_status(st, tid):
    return next((t for t in st['threads'] if t['id'] == tid), None)


LOCK_HOLDER = ('import fcntl, sys\nf = open(sys.argv[1], "a+")\nfcntl.flock(f, fcntl.LOCK_EX)\n'
               'print("locked", flush=True)\nsys.stdin.read()\n')


# ---------------------------------------------------------------- scenarios

class Ctx:
    pass


def s1_codex_born(c, ev):
    sb, clock = c.sb, c.clock
    c.cwd1 = sb.cwd('proj-codex')
    c.r1 = Rollout.new(sb, clock, c.cwd1)
    c.P = {'s1': 'S1 fix the README typo and add notes'}
    c.r1.codex_turn(clock, c.P['s1'], steps=[
        ('reason', 'Plan: inspect repo, fix typo, add notes.'),
        ('say', 'Looking at the project first.'),
        ('cmd', 'ls -la', 'README.md\nsrc\n', 0),
        ('cmd', 'cat missing.txt', 'cat: missing.txt: No such file or directory\n', 1),
        ('edit', f'{c.cwd1}/README.md', 'Helo world\nline2\n', 'Hello world\nline2\n'),
        ('add', f'{c.cwd1}/NOTES.md', '# Notes\n- parity\n'),
        ('mcp', 'linear', 'get_issue', {'id': 'PAR-1'}, '{"title":"Parity"}')], final='S1 done: typo fixed, notes added.')
    sb.insert_thread(c.r1.tid, c.r1.path, c.cwd1, c.P['s1'], name='Parity S1 thread')
    c.tid1, c.sid1 = c.r1.tid, str(uuid.uuid5(NS, c.r1.tid))
    acts = sb.sync()
    a = acts_for(acts, c.tid1, 'to_claude')
    check(len(a) == 1 and a[0]['result'].startswith('created'), f'expected to_claude created, got {acts}')
    mirror = sb.project(c.cwd1) / f'{c.sid1}.jsonl'
    check(mirror.exists(), f'mirror missing: {mirror}')
    c.mirror = ClaudeSession(sb, c.cwd1, sid=c.sid1, path=mirror)
    n = check_chain(mirror)
    uses = check_tool_pairs(mirror)
    check(uses == 5, f'expected 5 tool_use/tool_result pairs, got {uses}')
    msgs = claude_msgs(mirror)
    check(all(o.get('codexItem') and o['sessionId'] == c.sid1 for o in msgs), 'records without codexItem/sessionId')
    check(claude_prompts(mirror) == [c.P['s1']], f'prompts {claude_prompts(mirror)}')
    check(claude_texts(mirror) == ['Looking at the project first.', 'S1 done: typo fixed, notes added.'], f'texts {claude_texts(mirror)}')
    check(claude_thinking(mirror) == ['Plan: inspect repo, fix typo, add notes.'], 'reasoning not mirrored as thinking')
    tools = {b['name']: b['input'] for b in claude_blocks(mirror, 'tool_use')}
    check(sorted(claude_tools(mirror)) == sorted(['Bash', 'Bash', 'Edit', 'Write', 'mcp__linear__get_issue']), f'tools {claude_tools(mirror)}')
    check(tools['Edit'] == {'file_path': f'{c.cwd1}/README.md', 'old_string': 'Helo world\nline2', 'new_string': 'Hello world\nline2'}, f"Edit {tools['Edit']}")
    check(tools['Write'] == {'file_path': f'{c.cwd1}/NOTES.md', 'content': '# Notes\n- parity\n'}, f"Write {tools['Write']}")
    check(tools['mcp__linear__get_issue'] == {'id': 'PAR-1'}, 'mcp args')
    results = [b for o in msgs if o['type'] == 'user' for b in o['message']['content'] if isinstance(o['message']['content'], list)]
    failed = [r for r in results if r.get('is_error')]
    check(len(failed) == 1 and failed[0]['content'].startswith('Exit code 1'), f'failed command result {failed}')
    titles = [o['customTitle'] for o in jl(mirror) if o.get('type') == 'custom-title']
    check(titles == ['Parity S1 thread'], f'custom-title {titles}')
    check(sb.pairs()[c.tid1]['origin'] == 'codex', 'pair origin')
    size = mirror.stat().st_size
    again = sb.sync()
    check(again == [] and mirror.stat().st_size == size, f'second sync not a no-op: {again}')
    ev.append(f'mirror {mirror.name}: {n} chained records, 5 tool pairs, title {titles[0]!r}, 1 is_error result; 2nd sync []')


def s2_codex_continues(c, ev):
    sb, clock = c.sb, c.clock
    leaf, size = c.mirror.leaf(), c.mirror.size()
    old_items = Counter(o.get('codexItem') for o in claude_msgs(c.mirror.path))
    c.P['s2'] = 'S2 now run the unit tests'
    c.r1.codex_turn(clock, c.P['s2'], steps=[('cmd', 'pytest -q', '2 passed\n', 0)], final='S2 tests pass.')
    acts = sb.sync()
    a = acts_for(acts, c.tid1, 'to_claude')
    check(len(a) == 1 and a[0]['result'].startswith('appended'), f'expected append, got {acts}')
    new = [o for o in new_lines(c.mirror.path, size) if o.get('uuid')]
    check(new[0]['parentUuid'] == leaf, f'first new record parent {new[0]["parentUuid"]} != previous leaf {leaf}')
    check(not (set(o['codexItem'] for o in new) & set(old_items)), 'already mirrored items written again')
    check_chain(c.mirror.path)
    check_tool_pairs(c.mirror.path)
    per_item = Counter((o.get('codexItem'), o['type']) for o in claude_msgs(c.mirror.path))
    check(max(per_item.values()) == 1, f'duplicate records per item: {[k for k, v in per_item.items() if v > 1]}')
    check(claude_prompts(c.mirror.path) == [c.P['s1'], c.P['s2']], 'prompts after S2')
    check(sb.sync() == [], 'second sync not a no-op')
    ev.append(f'{len(new)} records appended, parent = previous leaf {leaf[:8]}, no duplicate codexItem; 2nd sync []')


def s3_claude_continues_mirror(c, ev):
    sb, clock, cwd = c.sb, c.clock, c.cwd1
    before = c.r1.size()
    c.P['s3'] = 'S3 run the tests, fix the greeting, log it and check PAR-2'
    c.P['s3_open'] = 'S3b one more check (still running)'
    c.mirror.turn(clock, c.P['s3'], resumed=True, steps=[
        ('think', 'Tests first, then the fix.'),
        ('text', 'Running the tests first.'),
        ('tool', 'Bash', {'command': 'pytest -q', 'description': 'Run tests'}, '3 passed in 0.12s', False),
        ('tool', 'Read', {'file_path': f'{cwd}/app.py'}, '1\tprint("hi")\n', False),
        ('tool', 'Edit', {'file_path': f'{cwd}/app.py', 'old_string': 'print("hi")', 'new_string': 'print("hello")',
                          'replace_all': False}, f'The file {cwd}/app.py has been updated successfully.', False),
        ('tool', 'Write', {'file_path': f'{cwd}/CHANGELOG.md', 'content': '- greet properly\n'},
         f'File created successfully at: {cwd}/CHANGELOG.md', False),
        ('tool', 'mcp__linear__get_issue', {'id': 'PAR-2'}, '{"title":"Greeting"}', False)],
        final='S3 done: greeting fixed and logged.')
    c.open3 = c.mirror.turn(clock, c.P['s3_open'], steps=[('text', 'Checking one more thing.'),
                                                           ('open_tool', 'Bash', {'command': 'sleep 100'})], finish=False)
    acts = sb.sync()
    a = acts_for(acts, c.tid1, 'to_codex')
    check(len(a) == 1 and a[0]['result'].startswith('1 turn(s)'), f'expected 1 turn to Codex, got {acts}')
    check(not acts_for(acts, c.tid1, 'to_claude'), 'something bounced back to Claude')
    new = new_lines(c.r1.path, before)
    ev_types = Counter(o['payload']['type'] for o in new if o['type'] == 'event_msg')
    check(ev_types['task_started'] == 1 and ev_types['task_complete'] == 1, f'turn events {ev_types}')
    items = [o['payload']['item'] for o in new if o['type'] == 'event_msg' and o['payload']['type'] == 'item_completed']
    check(all(it['id'].startswith(MIRROR_IDS) for it in items), 'item ids without claude marker')
    types = Counter(it['type'] for it in items)
    want = Counter({'UserMessage': 1, 'Reasoning': 1, 'AgentMessage': 2, 'CommandExecution': 1, 'FileChange': 2, 'McpToolCall': 2})
    check(types == want, f'items {dict(types)} != {dict(want)}')
    check(json.dumps(new, ensure_ascii=False).count(c.P['s3_open']) == 0, 'unfinished turn was pushed')
    um = next(it for it in items if it['type'] == 'UserMessage')
    check(um['content'][0]['text'] == c.P['s3'], f"prompt {um['content'][0]['text']!r} (system-reminder not stripped?)")
    fc = {list(it['changes'])[0]: list(it['changes'].values())[0] for it in items if it['type'] == 'FileChange'}
    check(fc[f'{cwd}/app.py']['type'] == 'update' and '-print("hi")' in fc[f'{cwd}/app.py']['unified_diff']
          and '+print("hello")' in fc[f'{cwd}/app.py']['unified_diff'], f'Edit diff {fc}')
    check(fc[f'{cwd}/CHANGELOG.md'] == {'type': 'add', 'content': '- greet properly\n'}, 'Write change')
    ce = next(it for it in items if it['type'] == 'CommandExecution')
    check(ce['command'] == ['/bin/zsh', '-lc', 'pytest -q'] and ce['exit_code'] == 0 and ce['aggregated_output'] == '3 passed in 0.12s', f'cmd {ce}')
    mcp = sorted((it['server'], it['tool']) for it in items if it['type'] == 'McpToolCall')
    check(mcp == [('claude', 'Read'), ('linear', 'get_issue')], f'mcp {mcp}')
    check(sb.sync() == [], 'second sync not a no-op (unfinished turn must wait)')
    ev.append(f'1 finished turn -> {len(new)} rollout records {dict(types)}; unfinished turn held back')
    # app-server loads the thread with the Claude turn as native items
    with AppServer(sb) as app:
        turns = app.turns(c.tid1)
    check(len(turns) == 3, f'app-server shows {len(turns)} turns, expected 3')
    t3 = next((t for t in turns if turn_prompt(t) == c.P['s3']), None)
    check(t3 and t3['status'] == 'completed', f"S3 turn status {t3 and t3['status']}")
    kinds = Counter(i['type'] for i in t3['items'])
    for k, n in (('commandExecution', 1), ('fileChange', 2), ('mcpToolCall', 2)):
        check(kinds[k] == n, f'app-server items {dict(kinds)}')
    bad = [i for i in t3['items'] if i['type'] in ('commandExecution', 'fileChange', 'mcpToolCall') and i.get('status') != 'completed']
    check(not bad, f'items not completed: {bad}')
    final = [i for i in t3['items'] if i['type'] == 'agentMessage' and i.get('phase') == 'final_answer']
    check(final and final[-1]['text'] == 'S3 done: greeting fixed and logged.', 'final answer missing in app-server')
    check(all(turn_prompt(t) != c.P['s3_open'] for t in turns), 'unfinished turn visible in Codex')
    ev.append(f"app-server: 3 turns, S3 turn completed with {dict(kinds)}, all tool items completed")
    # the held-back turn flows once it finishes
    before = c.r1.size()
    c.mirror.finish_open(clock, c.open3['open'], 'slept', 'S3b finished too.')
    acts = sb.sync()
    check(acts_for(acts, c.tid1, 'to_codex') and acts_for(acts, c.tid1, 'to_codex')[0]['result'].startswith('1 turn(s)'), f'trailing turn not pushed: {acts}')
    check(codex_prompts(c.r1.path).count(c.P['s3_open']) == 1, 'trailing turn prompt count')
    check(sb.sync() == [], 'sync after trailing turn not a no-op')
    ev.append('trailing turn pushed once after it finished')


def s4_claude_born(c, ev):
    sb, clock = c.sb, c.clock
    c.cwd4 = sb.cwd('proj-claude')
    c.c4 = ClaudeSession(sb, c.cwd4)
    c.c4.start(clock)
    c.P.update(s4a='S4a explain the layout', s4b='S4b check git and fix the typo', s4c='S4c search notion and write a summary')
    c.c4.turn(clock, c.P['s4a'], steps=[('text', 'Let me describe it.')], final='S4a: src/ holds the app.', title='Parity Claude-born')
    c.c4.turn(clock, c.P['s4b'], steps=[
        ('tool', 'Bash', {'command': 'git status --short', 'description': 'status'}, ' M app.py\n', False),
        ('tool', 'Edit', {'file_path': f'{c.cwd4}/app.py', 'old_string': 'teh', 'new_string': 'the', 'replace_all': False},
         'updated', False)], final='S4b: typo fixed.')
    c.c4.turn(clock, c.P['s4c'], steps=[
        ('tool', 'mcp__notion__search', {'query': 'parity'}, '[]', False),
        ('tool', 'Write', {'file_path': f'{c.cwd4}/SUMMARY.md', 'content': '# Summary\n'}, 'created', False),
        ('tool', 'Grep', {'pattern': 'TODO'}, 'No matches found', False)], final='S4c: summary written.')
    acts = sb.sync()
    a = acts_for(acts, c.c4.sid, 'adopt')
    check(len(a) == 1 and a[0]['result'] == 'created codex thread (3 turns)', f'adopt: {acts}')
    c.tid4 = a[0]['thread']
    files = glob.glob(str(sb.codex / f'sessions/*/*/*/rollout-*-{c.tid4}.jsonl'))
    check(len(files) == 1, f'rollout files {files}')
    first = next(o for o in c.c4.records() if o.get('promptId') and o['type'] == 'user')
    day = datetime.fromtimestamp(datetime.fromisoformat(first['timestamp'].replace('Z', '+00:00')).timestamp())
    check(Path(files[0]).parent == sb.codex / f'sessions/{day:%Y/%m/%d}', f'rollout not under sessions/{day:%Y/%m/%d}')
    c.r4 = Rollout(sb, files[0], c.tid4, c.cwd4)
    meta = c.r4.records()[0]
    check(meta['type'] == 'session_meta' and meta['payload']['id'] == c.tid4 and meta['payload']['cwd'] == c.cwd4, 'session_meta')
    check(codex_prompts(c.r4.path) == [c.P['s4a'], c.P['s4b'], c.P['s4c']], f'prompts {codex_prompts(c.r4.path)}')
    check(sorted(codex_tools(c.r4.path)) == sorted(claude_tools(c.c4.path)), 'tool parity')
    pair = sb.pairs()[c.tid4]
    check(pair['origin'] == 'claude' and pair['path'] == str(c.c4.path) and pair.get('created'), f'pair {pair}')
    check(not (sb.project(c.cwd4) / f'{uuid.uuid5(NS, c.tid4)}.jsonl').exists(), 'uuid5 mirror created for a Claude-born thread')
    check(sb.sync() == [], 'second sync not a no-op')
    ev.append(f'adopted -> {Path(files[0]).relative_to(sb.codex)} (3 turns, tools {sorted(codex_tools(c.r4.path))}); 2nd sync []')
    with AppServer(sb) as app:
        listed = app.list_threads()
        check(c.tid4 in listed, f'app-server started after adopt does not list {c.tid4}')
        turns = app.turns(c.tid4)
        c.env4 = app.thread.get('environments')
    check(len(turns) == 3 and all(t['status'] == 'completed' for t in turns), f'turns {[(turn_prompt(t), t["status"]) for t in turns]}')
    check(sorted(turn_prompt(t) for t in turns) == sorted([c.P['s4a'], c.P['s4b'], c.P['s4c']]), 'turn prompts')
    kinds = Counter(i['type'] for t in turns for i in t['items'])
    check(kinds['commandExecution'] == 1 and kinds['fileChange'] == 2 and kinds['mcpToolCall'] == 2, f'items {dict(kinds)}')
    ev.append(f"app-server lists it (preview {listed[c.tid4].get('preview')!r}); turns/list: 3 completed turns, items {dict(kinds)}")


def s5_codex_continues_claude_born(c, ev):
    sb, clock = c.sb, c.clock
    leaf, size = c.c4.leaf(), c.c4.size()
    c.P['s5'] = 'S5 codex: run make test'
    c.r4.codex_turn(clock, c.P['s5'], steps=[('cmd', 'make test', 'ok\n', 0)], final='S5 codex: all green.')
    rsize = c.r4.size()
    acts = sb.sync()
    a = acts_for(acts, c.tid4, 'to_claude')
    check(len(a) == 1 and a[0]['result'].startswith('appended'), f'to_claude: {acts}')
    check(not acts_for(acts, c.tid4, 'to_codex') and c.r4.size() == rsize, 'bounced back into the rollout')
    new = [o for o in new_lines(c.c4.path, size) if o.get('uuid')]
    check(new and new[0]['parentUuid'] == leaf, 'not parented to the previous leaf')
    check(all(o['sessionId'] == c.c4.sid and o.get('codexItem') for o in new), 'records not tagged / wrong sessionId')
    files = sorted(p.name for p in sb.project(c.cwd4).glob('*.jsonl'))
    check(files == [c.c4.path.name], f'project dir files {files} (expected only the original session)')
    check(claude_prompts(c.c4.path).count(c.P['s5']) == 1, 'S5 prompt count in Claude')
    check_chain(c.c4.path)
    check_tool_pairs(c.c4.path)
    again = sb.sync()
    check(again == [] and c.r4.size() == rsize, f'second sync: {again}')
    ev.append(f'{len(new)} records appended to original session {c.c4.sid[:8]}; no uuid5 mirror; rollout untouched; 2nd sync []')


def s6_claude_continues_claude_born(c, ev):
    sb, clock = c.sb, c.clock
    before, csize = c.r4.size(), None
    c.P['s6'] = 'S6 claude: lint it'
    c.c4.turn(clock, c.P['s6'], steps=[('tool', 'Bash', {'command': 'make lint'}, 'clean\n', False)], final='S6 claude: lint clean.')
    csize = c.c4.size()
    acts = sb.sync()
    a = acts_for(acts, c.tid4, 'to_codex')
    check(len(a) == 1 and a[0]['result'].startswith('1 turn(s)'), f'to_codex: {acts}')
    check(c.c4.size() == csize, 'Claude session modified (bounce)')
    want = [c.P['s4a'], c.P['s4b'], c.P['s4c'], c.P['s5'], c.P['s6']]
    check(codex_prompts(c.r4.path) == want, f'rollout prompts {codex_prompts(c.r4.path)}')
    check(claude_prompts(c.c4.path) == want, f'claude prompts {claude_prompts(c.c4.path)}')
    check(sb.sync() == [], 'second sync not a no-op')
    ev.append(f'{len(new_lines(c.r4.path, before))} records appended to rollout; both sides now {len(want)} prompts in the same order')


def import_session(c, cl, turns, archived=False, ms=None):
    """Simulate Codex's own import of Claude session `cl`: rollout + threads row + journal record."""
    sb = c.sb
    ms = ms or c.clock.tick()
    r = Rollout.new(sb, c.clock, cl.cwd, where=sb.codex / 'archived_sessions' if archived else None, ms=ms)
    r.import_turns(ms, turns)
    sb.insert_thread(r.tid, r.path, cl.cwd, turns[0][0], archived=archived, created_ms=ms)
    sb.add_import_record({'source_path': str(cl.path), 'content_sha256': hashlib.sha256(cl.path.read_bytes()).hexdigest(),
                          'imported_thread_id': r.tid, 'imported_at': ms // 1000, 'source_modified_at': ms * 1_000_000,
                          'connector_names': [], 'title': None})
    return r


def claude_with_turns(c, name, prompts):
    cl = ClaudeSession(c.sb, c.sb.cwd(name))
    cl.start(c.clock)
    turns = []
    for p in prompts:
        final = f'{p} -> answered'
        cl.turn(c.clock, p, steps=[('tool', 'Bash', {'command': 'ls'}, 'a\n', False)], final=final)
        turns.append((p, final))
    return cl, turns


def s7_import_pairing(c, ev):
    sb, clock = c.sb, c.clock
    cl, turns = claude_with_turns(c, 'proj-imported', ['S7 hist one', 'S7 hist two'])
    r = import_session(c, cl, turns)
    csize, rsize = cl.size(), r.size()
    acts = sb.sync()
    a = acts_for(acts, cl.sid, 'adopt')
    check(len(a) == 1 and a[0]['result'] == 'paired-import' and a[0]['thread'] == r.tid, f'adopt: {acts}')
    check(cl.size() == csize and r.size() == rsize, 'pairing wrote history (duplication)')
    check(sb.sync() == [] and cl.size() == csize and r.size() == rsize, 'post-pairing sync not a no-op')
    ev.append(f'paired-import {r.tid[:8]}, history untouched on both sides')
    # new turns after pairing flow both ways, exactly once
    r.codex_turn(clock, 'S7 codex new', steps=[('cmd', 'git log -1', 'abc\n', 0)], final='S7 codex new done')
    cl.turn(clock, 'S7 claude new', steps=[('tool', 'Bash', {'command': 'git diff'}, '', False)], final='S7 claude new done')
    acts = sb.sync()
    check(acts_for(acts, r.tid, 'to_claude') and acts_for(acts, r.tid, 'to_codex'), f'both directions expected: {acts}')
    cp, rp = claude_prompts(cl.path), codex_prompts(r.path)
    check(cp == ['S7 hist one', 'S7 hist two', 'S7 claude new', 'S7 codex new'], f'claude prompts {cp}')
    check(rp == ['S7 hist one', 'S7 hist two', 'S7 codex new', 'S7 claude new'], f'codex prompts {rp}')
    check(sb.sync() == [], 'final sync not a no-op')
    ev.append(f'new turns flowed both ways once: claude={cp[2:]}, codex={rp[2:]}')
    # archived import: skipped entirely
    cl2, turns2 = claude_with_turns(c, 'proj-archived', ['S7 archived hist'])
    r2 = import_session(c, cl2, turns2, archived=True)
    acts = sb.sync()
    r2.codex_turn(clock, 'S7 archived codex new', final='x')
    cl2.turn(clock, 'S7 archived claude new', final='y')
    csize, rsize = cl2.size(), r2.size()
    acts += sb.sync()
    check(cl2.size() == csize and r2.size() == rsize, 'archived thread was written to')
    check(not [x for x in acts if x['action'] in ('to_claude', 'to_codex') and x['thread'] == r2.tid], f'actions on archived: {acts}')
    t = thread_status(sb.status(), r2.tid)
    check(t and t['state'] == 'archived', f'status of archived thread: {t and t["state"]}')
    ev.append(f"archived import: {[x['action'] + ':' + x['result'] for x in acts_for(acts, cl2.sid)]}, no writes, status 'archived'")


def s8_busy(c, ev):
    sb, clock = c.sb, c.clock
    # (a) Claude session open -> Codex->Claude waits
    pidfile = sb.claude / 'sessions' / f'{os.getpid()}.json'
    now = int(time.time() * 1000)
    pidfile.write_text(json.dumps({'pid': os.getpid(), 'sessionId': c.sid1, 'cwd': c.cwd1, 'startedAt': now,
                                   'version': '2.1.281', 'kind': 'interactive', 'entrypoint': 'claude-desktop',
                                   'name': 'parity S8a', 'status': 'idle', 'updatedAt': now}))
    try:
        c.P['s8a'] = 'S8a codex while claude is open'
        c.r1.codex_turn(clock, c.P['s8a'], steps=[('cmd', 'date', 'today\n', 0)], final='S8a done')
        msize = c.mirror.size()
        acts = sb.sync()
        check(not acts_for(acts, c.tid1, 'to_claude') and c.mirror.size() == msize, f'wrote into an open Claude session: {acts}')
        st = sb.status()
        t = thread_status(st, c.tid1)
        check(t['claude_open'] and t['state'] == 'waiting_claude' and t['to_claude'] > 0, f"status {t['state']} open={t['claude_open']}")
        check(any(o['session_id'] == c.sid1 and o['pid'] == os.getpid() for o in st['apps']['claude']['open']), 'apps.claude.open')
        ev.append(f"(a) open: skipped, status {t['state']} to_claude={t['to_claude']}")
    finally:
        pidfile.unlink()
    acts = sb.sync()
    check(acts_for(acts, c.tid1, 'to_claude'), f'not flushed after close: {acts}')
    check(claude_prompts(c.mirror.path).count(c.P['s8a']) == 1, 'S8a prompt count')
    ev.append('closed -> appended')
    # (b) Codex holds the thread writer lock -> Claude->Codex waits
    lock = sb.codex / 'thread-writer-locks' / f'{c.tid1}.lock'
    holder = subprocess.Popen([sys.executable, '-c', LOCK_HOLDER, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        check(holder.stdout.readline().strip() == 'locked', 'lock holder did not start')
        c.P['s8b'] = 'S8b claude while codex holds the lock'
        c.mirror.turn(clock, c.P['s8b'], steps=[('tool', 'Bash', {'command': 'uptime'}, 'up\n', False)], final='S8b done')
        rsize = c.r1.size()
        acts = sb.sync()
        check(not [a for a in acts_for(acts, c.tid1, 'to_codex') if not a['result'].startswith('busy')] and c.r1.size() == rsize,
              f'wrote into a locked rollout: {acts}')
        st = sb.status()
        t = thread_status(st, c.tid1)
        check(t['codex_locked'] and t['state'] == 'waiting_codex' and t['to_codex'] > 0, f"status {t['state']} locked={t['codex_locked']}")
        check(c.tid1 in st['apps']['codex']['locked'], 'apps.codex.locked')
        ev.append(f"(b) locked: skipped, status {t['state']} to_codex={t['to_codex']}")
    finally:
        holder.stdin.close()
        holder.wait(10)
    acts = sb.sync()
    check(acts_for(acts, c.tid1, 'to_codex'), f'not flushed after unlock: {acts}')
    check(codex_prompts(c.r1.path).count(c.P['s8b']) == 1, 'S8b prompt count')
    ev.append('released -> appended')
    # (c) a process started with `--resume <sid>` (the ps-based detection path) -> Codex->Claude waits
    proc = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()', '--resume', c.sid1], stdin=subprocess.PIPE)
    try:
        c.P['s8c'] = 'S8c codex while a --resume process runs'
        c.r1.codex_turn(clock, c.P['s8c'], final='S8c done')
        msize = c.mirror.size()
        acts = sb.sync()
        check(not acts_for(acts, c.tid1, 'to_claude') and c.mirror.size() == msize, f'wrote into a resumed session: {acts}')
        t = thread_status(sb.status(), c.tid1)
        check(t['state'] == 'waiting_claude', f"status {t['state']}")
    finally:
        proc.stdin.close()
        proc.wait(10)
    acts = sb.sync()
    check(acts_for(acts, c.tid1, 'to_claude') and claude_prompts(c.mirror.path).count(c.P['s8c']) == 1, f'not flushed: {acts}')
    ev.append('(c) --resume process: skipped (waiting_claude), exit -> appended')


def parity(ev, label, rollout, claude):
    cp, rp = claude_prompts(claude), codex_prompts(rollout)
    check(cp == rp, f'{label}: prompts differ\n claude={cp}\n codex ={rp}')
    check(Counter(claude_texts(claude)) == Counter(codex_texts(rollout)),
          f'{label}: agent texts differ {Counter(claude_texts(claude)) - Counter(codex_texts(rollout))} / {Counter(codex_texts(rollout)) - Counter(claude_texts(claude))}')
    check(Counter(claude_tools(claude)) == Counter(codex_tools(rollout)), f'{label}: tools differ {Counter(claude_tools(claude))} vs {Counter(codex_tools(rollout))}')
    check(Counter(claude_thinking(claude)) == Counter(codex_thinking(rollout)), f'{label}: thinking differs')
    check_chain(claude)
    check_tool_pairs(claude)
    ev.append(f'{label}: {len(cp)} prompts / {len(claude_texts(claude))} texts / {len(claude_tools(claude))} tools equal on both sides')


def s9_idempotent(c, ev):
    sb = c.sb
    a1, a2 = sb.sync(), sb.sync()
    check(a1 == [] and a2 == [], f'repeated sync not empty: {a1} {a2}')
    st = sb.status()
    tot = st['totals']
    check(tot['pending'] == 0 and tot['waiting'] == 0 and tot['errors'] == 0 and tot['to_claude'] == 0 and tot['to_codex'] == 0,
          f'totals {tot}')
    live = [t for t in st['threads'] if not t['archived']]
    check(all(t['state'] == 'synced' and t['paired'] for t in live), f"not synced: {[(t['id'], t['state']) for t in live if t['state'] != 'synced']}")
    ev.append(f"totals {json.dumps(tot)}; sync x2 -> []")
    parity(ev, 'codex-born', c.r1.path, c.mirror.path)
    parity(ev, 'claude-born', c.r4.path, c.c4.path)
    # Codex view after all the external appends (projection cache from S3/S4 kept on purpose)
    stale = []
    with AppServer(sb) as app:
        for label, tid, r in (('codex-born', c.tid1, c.r1), ('claude-born', c.tid4, c.r4)):
            turns = app.turns(tid)
            if sorted(turn_prompt(t) or '' for t in turns) != sorted(codex_prompts(r.path)):
                stale.append(f'{label}: app-server {len(turns)} turns vs rollout {turn_count(r.path)}')
            elif any(t['status'] != 'completed' for t in turns):
                stale.append(f"{label}: statuses {[t['status'] for t in turns]}")
    check(not stale, f'app-server view differs from rollout: {stale}')
    ev.append(f'app-server (cache kept) shows every turn: {turn_count(c.r1.path)} + {turn_count(c.r4.path)} completed')


def live_discovery(c, ev):
    sb, clock = c.sb, c.clock
    res = {}
    with AppServer(sb) as app:
        before = app.list_threads()
        cl, _ = claude_with_turns(c, 'proj-live', ['LIVE adopt me'])
        acts = sb.sync()
        a = acts_for(acts, cl.sid, 'adopt')
        check(a and a[0]['result'].startswith('created'), f'adopt: {acts}')
        tid = a[0]['thread']
        res['listed_immediately'] = tid in app.list_threads()
        db = sqlite3.connect(f"file:{sb.codex / 'state_5.sqlite'}?mode=ro", uri=True)
        res['in_state_db_after_list'] = bool(db.execute('select 1 from threads where id=?', (tid,)).fetchone())
        db.close()
        time.sleep(3)
        res['listed_after_3s'] = tid in app.list_threads()
        try:
            turns = app.turns(tid)
            res['resume_same_process'] = f'ok, {len(turns)} turn(s): {[turn_prompt(t) for t in turns]}'
        except Fail as e:
            res['resume_same_process'] = f'error: {e}'
        res['listed_after_resume'] = tid in app.list_threads()
    db = sqlite3.connect(f"file:{sb.codex / 'state_5.sqlite'}?mode=ro", uri=True)
    res['in_state_db_after_resume'] = bool(db.execute('select 1 from threads where id=?', (tid,)).fetchone())
    db.close()
    with AppServer(sb) as app:
        res['listed_after_restart'] = tid in app.list_threads()
    res['threads_before'] = len(before)
    c.live = res
    ev.append(json.dumps(res, ensure_ascii=False))


# ---- extra probes for suspected engine gaps (reported separately)

def x_adopted_meta(c, ev):
    """session_meta of an adopted rollout must describe its own project, not the template thread's."""
    m4, m1 = c.r4.records()[0]['payload'], c.r1.records()[0]['payload']
    roots = m4.get('runtime_workspace_roots')
    env = ((getattr(c, 'env4', None) or [{}])[0]).get('runtimeWorkspaceRoots')
    ev.append(f"cwd={m4['cwd']} runtime_workspace_roots={roots} app-server env roots={env} "
              f"window_id shared with template thread: {m4.get('context_window') == m1.get('context_window')}")
    check(roots in (None, [c.cwd4]), 'adopted thread inherits runtime_workspace_roots of the newest (unrelated) rollout')
    check(m4.get('context_window') != m1.get('context_window'), 'adopted thread reuses context_window.window_id of another thread')


def x_import_continued_before_pairing(c, ev):
    """Imported thread continued in Codex (and Claude) after the import but before the engine paired it."""
    sb, clock = c.sb, c.clock
    cl, turns = claude_with_turns(c, 'proj-imp-cont', ['X7c hist'])
    r = import_session(c, cl, turns)
    r.codex_turn(clock, 'X7c codex after import', steps=[('cmd', 'pwd', '/x\n', 0)], final='X7c codex reply')
    cl.turn(clock, 'X7c claude after import', final='X7c claude reply')
    sb.sync()
    sb.sync()
    cp, rp = claude_prompts(cl.path), codex_prompts(r.path)
    ev.append(f'claude={cp} codex={rp} pair.baseline_ms={sb.pairs()[r.tid].get("baseline_ms")}')
    check('X7c claude after import' in rp, 'Claude turn made after import did not reach Codex')
    check('X7c codex after import' in cp, 'Codex turn made after import (before pairing) never reaches Claude: baseline_ms covers it')


def x_reimported_session(c, ev):
    """Session imported twice: first import archived, the re-import active."""
    sb, clock = c.sb, c.clock
    cl, turns = claude_with_turns(c, 'proj-reimport', ['X7d hist'])
    old = import_session(c, cl, turns, archived=True)
    new = import_session(c, cl, turns)
    acts = sb.sync()
    a = acts_for(acts, cl.sid, 'adopt')
    ev.append(f"adopt -> {a and a[0]['thread']} (archived import {old.tid}, active re-import {new.tid})")
    check(a and a[0]['thread'] == new.tid, 'paired with the archived import; the active re-import is never synced')


def x_compaction(c, ev):
    """Claude auto-compaction inside a Claude-born session (record shape per Claude Code; none in local data)."""
    sb, clock = c.sb, c.clock
    cl = ClaudeSession(sb, sb.cwd('proj-compact'))
    cl.start(clock)
    cl.turn(clock, 'XC before compaction', final='XC first answer')
    cl.compact(clock)
    cl.turn(clock, 'XC after compaction', final='XC second answer')
    acts = sb.sync()
    a = acts_for(acts, cl.sid, 'adopt')
    check(a, f'not adopted: {acts}')
    r = glob.glob(str(sb.codex / f"sessions/*/*/*/rollout-*-{a[0]['thread']}.jsonl"))[0]
    ev.append(f"adopt: {a[0]['result']}; codex prompts={[p[:40] for p in codex_prompts(r)]}")
    check(codex_prompts(r) == ['XC before compaction', 'XC after compaction'], 'compact summary pushed to Codex as a user turn')


def x_cli_version_collision(c, ev):
    """Native Claude Code CLI session whose version equals the engine's synthetic VERSION."""
    sb, clock = c.sb, c.clock
    cl = ClaudeSession(sb, sb.cwd('proj-cli'), entrypoint='cli', version=ENGINE_VERSION)
    cl.start(clock)
    cl.turn(clock, 'X1 cli prompt', steps=[('tool', 'Bash', {'command': 'ls'}, 'a\n', False)], final='X1 cli answer')
    acts = sb.sync()
    a = acts_for(acts, cl.sid, 'adopt')
    check(a, f'not adopted: {acts}')
    r = glob.glob(str(sb.codex / f"sessions/*/*/*/rollout-*-{a[0]['thread']}.jsonl"))[0]
    ev.append(f'version={ENGINE_VERSION}: rollout texts={codex_texts(r)} tools={codex_tools(r)}')
    check(codex_texts(r) == ['X1 cli answer'] and codex_tools(r) == ['Bash'],
          'assistant records of a real CLI session are taken for Codex-origin (is_codex_origin VERSION heuristic) and dropped')


# ---------------------------------------------------------------- runner

def main():
    root = os.environ.get('MIRROR_E2E_ROOT') or os.path.join(tempfile.gettempdir(), 'codex-mirror-e2e')
    check(shutil.which(CODEX_BIN), 'Codex executable not found; install Codex or set CODEX_BIN')
    c = Ctx()
    c.sb, c.clock = Sandbox(root), Clock()
    print(f'sandbox {c.sb.dir}\nengine {ENGINE} (VERSION {ENGINE_VERSION})', flush=True)
    with AppServer(c.sb) as app:  # let Codex create its sqlite schema in the sandbox
        app.list_threads()
    for d in ('sessions', 'thread-writer-locks'):  # present in any real CODEX_HOME that has opened a thread
        (c.sb.codex / d).mkdir(exist_ok=True)
    plan = [('S1 codex-born thread -> Claude mirror', s1_codex_born, True),
            ('S2 Codex continues -> appended to mirror', s2_codex_continues, True),
            ('S3 Claude continues mirror -> finished turn to rollout, app-server view', s3_claude_continues_mirror, True),
            ('S4 Claude-born session -> new rollout, app-server lists it', s4_claude_born, True),
            ('S5 Codex continues Claude-born -> original session', s5_codex_continues_claude_born, True),
            ('S6 Claude continues Claude-born -> rollout', s6_claude_continues_claude_born, True),
            ('S7 import-journal pairing (+archived)', s7_import_pairing, True),
            ('S8 busy sides (open Claude / Codex lock)', s8_busy, True),
            ('S9 idempotency, status totals, parity', s9_idempotent, True),
            ('LIVE running app-server discovery', live_discovery, True),
            ('X4 adopted rollout session_meta belongs to its own project', x_adopted_meta, False),
            ('X7c imported thread continued before pairing', x_import_continued_before_pairing, False),
            ('X7d re-imported session (archived first import)', x_reimported_session, False),
            ('XC Claude auto-compaction in a Claude-born session', x_compaction, False),
            ('X1 Claude CLI session with version == engine VERSION', x_cli_version_collision, False)]
    results = []
    for name, fn, core in plan:
        ev = []
        try:
            fn(c, ev)
            ok = True
        except Exception as e:
            ok = False
            ev.append(f'{type(e).__name__}: {e}')
            with open(c.sb.logs / 'failures.log', 'a') as f:
                f.write(f'== {name}\n{traceback.format_exc()}\n')
        results.append({'name': name, 'passed': ok, 'core': core, 'evidence': ' | '.join(ev)})
        print(f"{'PASS' if ok else 'FAIL'} {name}\n     {' | '.join(ev)}", flush=True)
    out = {'sandbox': str(c.sb.dir), 'results': results, 'live_discovery': getattr(c, 'live', None)}
    (c.sb.dir / 'results.json').write_text(json.dumps(out, indent=1, ensure_ascii=False))
    core_fail = [r['name'] for r in results if r['core'] and not r['passed']]
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} passed; core failures: {core_fail or 'none'}; "
          f"results {c.sb.dir / 'results.json'}")
    return 1 if core_fail else 0


if __name__ == '__main__':
    sys.exit(main())
