"""Provider adapters for public model APIs and an installed Codex CLI."""
from __future__ import annotations
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import httpx


class AgentCancelled(RuntimeError):
    pass


def read_settings(path):
    values = {}
    if path and Path(path).is_file():
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                name, value = line.split('=', 1)
                values[name.strip()] = value.strip().strip('"\'')
    # Agent settings are re-read for each chat, without mutating the process env.
    # Explicit .env values win for this optional feature only.
    return {**os.environ, **values}


@dataclass
class Settings:
    provider: str = 'openai'
    url: str = 'https://api.openai.com/v1'
    key: str = field(default='', repr=False)
    model: str = ''
    protocol: str = 'chat'
    tool_mode: str = 'tools'
    max_turns: int | None = None
    timeout: float = 120.0
    output_tokens: int = 16384
    execution: str = 'off'
    check_timeout: float = 120.0
    codex: str = 'codex'
    secrets: tuple = field(default=(), repr=False)

    @classmethod
    def load(cls, env_path):
        v = read_settings(env_path)
        provider = v.get('AB_AGENT_PROVIDER', 'openai').strip().lower()
        default_url = 'https://api.anthropic.com/v1' if provider == 'anthropic' else 'https://api.openai.com/v1'
        turns = v.get('AB_AGENT_MAX_TURNS', '').strip()
        cfg = cls(provider=provider, url=v.get('AB_AGENT_API_URL', default_url).rstrip('/'),
            key=v.get('AB_AGENT_API_KEY', ''), model=v.get('AB_AGENT_MODEL', '').strip(),
            protocol=v.get('AB_AGENT_PROTOCOL', 'chat').strip(),
            tool_mode=v.get('AB_AGENT_TOOL_MODE', 'tools').strip(),
            max_turns=int(turns) if turns and turns.lower() != 'none' else None,
            timeout=float(v.get('AB_AGENT_TIMEOUT', '120')),
            output_tokens=int(v.get('AB_AGENT_MAX_OUTPUT_TOKENS', '16384')),
            execution=v.get('AB_AGENT_EXECUTION', 'off').strip(),
            check_timeout=float(v.get('AB_AGENT_CHECK_TIMEOUT', '120')),
            codex=v.get('AB_AGENT_CODEX_PATH', 'codex'),
            secrets=tuple(value for name, value in v.items() if value and
                re.search(r'(?:KEY|TOKEN|PASSWORD|SECRET)(?:$|_)', name, re.I)))
        if cfg.provider not in ('openai', 'anthropic', 'codex'):
            raise ValueError('AB_AGENT_PROVIDER must be openai, anthropic, or codex.')
        if cfg.tool_mode not in ('tools', 'json') or cfg.protocol not in ('chat', 'responses'):
            raise ValueError('Use AB_AGENT_TOOL_MODE=tools/json and AB_AGENT_PROTOCOL=chat/responses.')
        if cfg.execution not in ('off', 'local', 'sandbox'):
            raise ValueError('AB_AGENT_EXECUTION must be off, local, or sandbox.')
        if not all(math.isfinite(x) and x > 0 for x in (cfg.timeout, cfg.check_timeout)) or cfg.output_tokens < 1 or (cfg.max_turns is not None and cfg.max_turns < 1):
            raise ValueError('Agent timeouts and explicit limits must be positive.')
        if cfg.provider != 'codex':
            url = urlsplit(cfg.url)
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError('AB_AGENT_API_URL must be an HTTP(S) API base URL without credentials or query.')
        return cfg

    def redact(self, value):
        if isinstance(value, dict):
            return {k: '[REDACTED]' if re.search(r'(api.?key|password|access.?token|secret)', str(k), re.I)
                    else self.redact(v) for k, v in value.items()}
        if isinstance(value, list): return [self.redact(x) for x in value]
        if isinstance(value, str):
            for secret in sorted(set((*self.secrets, self.key)) - {''}, key=len, reverse=True):
                value = value.replace(secret, '[REDACTED]')
            value = re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9_.-]+', r'\1[REDACTED]', value)
        return value

    def public(self):
        return {'provider':self.provider, 'model':self.model,
                'api_url':self.url if self.provider != 'codex' else '',
                'configured':bool(self.model) if self.provider != 'codex' else bool(shutil.which(self.codex)),
                'key_present':bool(self.key), 'execution':self.execution, 'tool_mode':self.tool_mode,
                'protocol':self.protocol, 'max_turns':self.max_turns}


def _json_answer(text):
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    data = json.loads(text)
    if not isinstance(data, dict): raise ValueError('Expected an agent JSON object.')
    actions = data.get('actions', [])
    if not isinstance(actions, list): raise ValueError('Agent actions must be a list.')
    return str(data.get('message', '')), actions


class APIProvider:
    def __init__(self, settings, system, tools, transcript, *, transport=None):
        self.cfg, self.tools = settings, tools
        self.client = httpx.Client(timeout=settings.timeout, follow_redirects=False, trust_env=False, transport=transport)
        self.messages = [{'role':'system','content':system}, {'role':'user','content':transcript}]
        self.anthropic = [{'role':'user','content':transcript}]
        self.responses = [{'role':'user','content':transcript}]
        self.system = system

    def close(self): self.client.close()

    def _post(self, path, payload):
        headers = {'Content-Type':'application/json'}
        if self.cfg.provider == 'anthropic':
            headers.update({'x-api-key':self.cfg.key, 'anthropic-version':'2023-06-01'})
        elif self.cfg.key: headers['Authorization'] = 'Bearer ' + self.cfg.key
        try:
            response = self.client.post(self.cfg.url + path, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise RuntimeError('Model connection failed: ' + type(exc).__name__ + '. Check the API URL and connection.') from exc
        if response.status_code >= 300:
            detail = ''
            try:
                error = response.json().get('error', {})
                detail = str(error.get('message', '')) if isinstance(error, dict) else str(error)
            except (ValueError, AttributeError): pass
            raise RuntimeError(f'Model API HTTP {response.status_code}: ' + self.cfg.redact(detail)[:700])
        return response.json()

    def next(self):
        cfg = self.cfg
        if not cfg.model: raise ValueError('Set AB_AGENT_MODEL in the project .env.')
        if cfg.provider == 'anthropic':
            payload = {'model':cfg.model, 'system':self.system, 'messages':self.anthropic,
                       'max_tokens':cfg.output_tokens}
            if cfg.tool_mode == 'tools': payload['tools'] = [
                {'name':t['name'],'description':t['description'],'input_schema':t['parameters']} for t in self.tools]
            data = self._post('/messages', payload)
            content = data.get('content', [])
            if data.get('stop_reason') == 'max_tokens':
                raise RuntimeError('Model response was truncated. Increase AB_AGENT_MAX_OUTPUT_TOKENS or request smaller file edits.')
            self.anthropic.append({'role':'assistant','content':content})
            text = '\n'.join(x.get('text','') for x in content if x.get('type') == 'text')
            calls = [{'id':x['id'],'name':x['name'],'arguments':x['input']}
                     for x in content if x.get('type') == 'tool_use']
        elif cfg.protocol == 'responses':
            payload = {'model':cfg.model, 'instructions':self.system, 'input':self.responses,
                       'store':False, 'max_output_tokens':cfg.output_tokens,
                       'include':['reasoning.encrypted_content']}
            if cfg.tool_mode == 'tools': payload['tools'] = [dict(type='function', strict=False, **t) for t in self.tools]
            data = self._post('/responses', payload)
            if data.get('status') in ('incomplete', 'failed'):
                raise RuntimeError('Model response did not complete. Check the model token limit and provider status.')
            output = data.get('output', [])
            self.responses.extend(output)  # Retain reasoning and call IDs across tool turns.
            text = '\n'.join(c.get('text','') for x in output if x.get('type') == 'message'
                             for c in x.get('content',[]) if c.get('type') == 'output_text')
            calls = [{'id':x['call_id'],'name':x['name'],'arguments':json.loads(x['arguments'])}
                     for x in output if x.get('type') == 'function_call']
        else:
            payload = {'model':cfg.model, 'messages':self.messages}
            token_field = 'max_completion_tokens' if urlsplit(cfg.url).hostname == 'api.openai.com' else 'max_tokens'
            payload[token_field] = cfg.output_tokens
            if cfg.tool_mode == 'tools': payload['tools'] = [{'type':'function','function':t} for t in self.tools]
            data = self._post('/chat/completions', payload)
            choice = data['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise RuntimeError('Model response was truncated. Request smaller file edits or change the provider token limit.')
            message = choice['message']
            # Keep provider reasoning fields, which some compatible APIs require on later tool turns.
            self.messages.append(message)
            text = message.get('content') or ''
            calls = [{'id':x['id'],'name':x['function']['name'],
                      'arguments':json.loads(x['function']['arguments'])} for x in message.get('tool_calls',[])]
        if cfg.tool_mode == 'json':
            text, actions = _json_answer(text)
            calls = [{'id':f'action-{i}', **action} for i, action in enumerate(actions)]
        return text, calls

    def results(self, results):
        if self.cfg.tool_mode == 'json':
            message = 'Tool results:\n' + json.dumps(results, ensure_ascii=False)
            self.messages.append({'role':'user','content':message})
            self.anthropic.append({'role':'user','content':message})
            self.responses.append({'role':'user','content':message})
        elif self.cfg.provider == 'anthropic':
            self.anthropic.append({'role':'user','content':[{'type':'tool_result','tool_use_id':r['id'],
                'content':json.dumps(r['result'],ensure_ascii=False)} for r in results]})
        elif self.cfg.protocol == 'responses':
            self.responses.extend({'type':'function_call_output','call_id':r['id'],
                'output':json.dumps(r['result'],ensure_ascii=False)} for r in results)
        else:
            self.messages.extend({'role':'tool','tool_call_id':r['id'],
                'content':json.dumps(r['result'],ensure_ascii=False)} for r in results)


class CodexProvider:
    """Codex plans actions; the same SDK tool dispatcher executes them.

    Read-only CLI sandbox is preserved. No bypass or automatic project trust
    flag is used. Generated code checks use the explicit SDK execution setting.
    """
    def __init__(self, settings, system, tools, transcript, cancel, notify):
        self.cfg, self.system, self.transcript = settings, system, transcript
        self.cancel, self.notify, self.turn = cancel, notify, 0
        self.events = []
        self.scratch = tempfile.TemporaryDirectory(prefix='ab-agent-codex-')
        self.root = Path(self.scratch.name)
        self.schema = self.root/'response.schema.json'
        self.schema.write_text(json.dumps({'type':'object','additionalProperties':False,
            'properties':{'message':{'type':'string'}, 'actions':{'type':'array','items':{
                'type':'object','additionalProperties':False,'properties':{
                    'name':{'type':'string'},'arguments_json':{'type':'string'}},
                'required':['name','arguments_json']}}},'required':['message','actions']}))
        self.tools = tools

    def close(self): self.scratch.cleanup()

    def next(self):
        self.turn += 1
        output = self.root/f'answer-{self.turn}.json'
        command = [self.cfg.codex, 'exec', '--skip-git-repo-check', '--sandbox', 'read-only',
                   '-c', 'approval_policy="never"', '-c', 'shell_environment_policy.inherit="none"',
                   '--cd', str(self.root), '--json', '--output-schema', str(self.schema),
                   '--output-last-message', str(output)]
        if self.cfg.model: command += ['--model', self.cfg.model]
        command += ['-']
        prompt = self.system + '\nUse only the SDK actions below. Return arguments_json as a JSON-encoded object.\n' + json.dumps(self.tools)
        prompt += '\nConversation and tool results:\n' + self.transcript + '\n' + json.dumps(self.events)
        env = {k:v for k,v in os.environ.items() if k in ('PATH','HOME','USERPROFILE','SYSTEMROOT','WINDIR','TMP','TEMP','TMPDIR','LANG','LC_ALL','CODEX_HOME')}
        if self.cfg.key: env['CODEX_API_KEY'] = self.cfg.key
        with (self.root/'events.jsonl').open('w') as log:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log,
                text=True, env=env, cwd=self.root)
            try:
                process.stdin.write(prompt); process.stdin.close()
                while process.poll() is None:
                    if self.cancel.wait(.2):
                        process.terminate()
                        try: process.wait(timeout=3)
                        except subprocess.TimeoutExpired: process.kill(); process.wait()
                        raise AgentCancelled('Agent stopped.')
                if process.returncode or not output.is_file():
                    detail = (self.root/'events.jsonl').read_text(errors='replace')[-3000:]
                    raise RuntimeError('Codex did not finish: ' + self.cfg.redact(detail))
                data = json.loads(output.read_text())
            finally:
                if process.poll() is None: process.kill(); process.wait()
        calls = [{'id':f'codex-{self.turn}-{i}', 'name':a['name'],
                  'arguments':json.loads(a['arguments_json'])} for i,a in enumerate(data.get('actions',[]))]
        self.events.append(data)
        return data.get('message',''), calls

    def results(self, results): self.events.append({'tool_results':results})
