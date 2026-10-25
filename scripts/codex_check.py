"""Portable, privacy-preserving Codex endpoint health checker."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import secrets
import sys
from urllib.parse import urlsplit


def read_toml(path):
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    with path.open('rb') as f:
        return tomllib.load(f)


def discover(home=None, profile=None):
    home = Path(home or os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser()
    cfg = read_toml(home / 'config.toml')
    selected = profile or cfg.get('profile')
    active = dict(cfg)
    if selected:
        if selected not in cfg.get('profiles', {}):
            raise ValueError('指定的profile不存在。')
        active.update(cfg['profiles'][selected])
    provider_id = active.get('model_provider', 'openai')
    provider = cfg.get('model_providers', {}).get(provider_id, {})
    model = active.get('model')
    base = provider.get('base_url')
    if not base and provider_id == 'openai':
        base = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1')
    if not base:
        raise ValueError('自定义provider缺少base_url，不能猜测目标地址。')
    env_name = provider.get('env_key')
    key = os.environ.get(env_name, '') if env_name else ''
    source = '环境变量 ' + env_name if key and env_name else ''
    if not key and (not env_name or provider.get('requires_openai_auth')) and os.environ.get('OPENAI_API_KEY'):
        key, source = os.environ['OPENAI_API_KEY'], '环境变量 OPENAI_API_KEY'
    if not key and (not env_name or provider.get('requires_openai_auth')):
        auth = home / 'auth.json'
        if auth.exists():
            data = json.loads(auth.read_text(encoding='utf-8'))
            key = data.get('OPENAI_API_KEY', '') or ''
            if key:
                source = 'auth.json 的 API Key'
    if not model:
        raise ValueError('Codex config.toml 未记录模型，请用 --model 指定。')
    wire = provider.get('wire_api', 'responses')
    if wire not in ('chat', 'responses'):
        raise ValueError('仅支持 chat 和 responses 接口。')
    return {'base': base.rstrip('/'), 'model': model, 'wire': wire, 'key': key,
            'key_source': source or '未找到可用 API Key', 'provider': provider_id,
            'profile': selected, 'custom_headers_present': bool(provider.get('http_headers') or provider.get('env_http_headers'))}


def safe_config(c):
    return {'model': c['model'], 'provider': c['provider'], 'profile': c['profile'],
            'api_host': urlsplit(c['base']).hostname, 'wire_api': c['wire'],
            'key_available': bool(c['key']), 'key_source': c['key_source'],
            'custom_headers_present': c['custom_headers_present'],
            'notice': '请求声明不是权重认证；动态路由可能与当前聊天实例不同。'}


def check_url(base):
    p = urlsplit(base)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError('base URL 必须是无凭证的 HTTP(S) 地址。')


def tasks(seed):
    rng = random.Random(seed)
    out = []
    def add(dim, prompt, expected, kind='exact'):
        out.append({'id': f'T{len(out)+1:02d}', 'dimension': dim, 'prompt': prompt, 'expected': expected, 'kind': kind})
    for _ in range(4):
        a, b, c = rng.randint(17, 89), rng.randint(11, 37), rng.randint(2, 13)
        add('算术与规则执行', f'Compute ({a} * {b}) + {c}. Output only the integer.', str(a*b+c))
    for _ in range(4):
        a = rng.randint(2, 20)
        if rng.random() < .5:
            add('逻辑推理', f'All dax are wug. Every wug has exactly {a} wheels. Lina is a dax. How many wheels does Lina have? Output only the integer.', str(a))
        else:
            add('逻辑推理', 'All dax are wug. Some wug are green. Does it follow that all dax are green? Output only YES or NO.', 'NO')
    for i in range(4):
        tag = ''.join(rng.choices('ABCDEFGHJKLMNPQRSTUVWXYZ', k=6))
        if i % 2:
            add('指令与结构化输出', f'Return only a valid JSON object with exactly two keys: "code" with string value "{tag}" and "count" with integer value {i+2}.', {'code': tag, 'count': i+2}, 'json')
        else:
            add('指令与结构化输出', f'Write exactly the text between angle brackets, removing the brackets: <{tag}>. No explanation.', tag)
    for _ in range(4):
        values = [rng.randint(1, 20) for _ in range(6)]
        add('代码理解', f'Output only the printed integer.\na = {values!r}\nprint(sum(x*x for x in a if x % 2 == 0))', str(sum(x*x for x in values if x % 2 == 0)))
    for _ in range(4):
        names = ['Nori', 'Kato', 'Sumi', 'Taro', 'Mina']; rng.shuffle(names)
        changes = [(rng.choice(names), rng.randint(1, 9)) for _ in range(8)]
        state = dict.fromkeys(names, 0)
        for n, v in changes: state[n] += v
        target = rng.choice(names)
        text = ' '.join(f'{n} gains {v} points.' for n, v in changes)
        add('多步状态追踪', f'Everyone starts with 0 points. {text} What is {target}\'s final score? Output only the integer.', str(state[target]))
    for _ in range(4):
        keys = ['K%03d' % n for n in rng.sample(range(100, 999), 60)]
        vals = [''.join(rng.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=8)) for _ in keys]
        ix = rng.randrange(len(keys)); records = '\n'.join(k+'='+v for k,v in zip(keys, vals))
        add('上下文检索', f'Find the value of key {keys[ix]} in the records below. Output only its exact value.\n{records}', vals[ix])
    return out


def is_correct(task, answer):
    answer = answer.strip()
    if task['kind'] == 'json':
        try:
            value = json.loads(answer)
            return value == task['expected'] and isinstance(value, dict) and type(value.get('count')) is int
        except (ValueError, TypeError):
            return False
    return answer == task['expected']


def wilson(hits, n):
    if not n: return None
    z, p = 1.95996398454, hits / n
    center = (p + z*z/(2*n))/(1+z*z/n)
    radius = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return [round(max(0, center-radius),4), round(min(1, center+radius),4)]


class API:
    def __init__(self, c):
        check_url(c['base'])
        self.c = c
    def call(self, prompt, system='Answer accurately and follow the requested output format.'):
        import requests
        c = self.c
        if c['wire'] == 'chat':
            body = {'model': c['model'], 'messages': [{'role':'system','content':system},{'role':'user','content':prompt}], 'max_tokens':1024}
            url = c['base'] + '/chat/completions'
        else:
            body = {'model': c['model'], 'instructions': system, 'input': prompt, 'max_output_tokens':1024}
            url = c['base'] + '/responses'
        try:
            r = requests.post(url, json=body, headers={'Authorization':'Bearer '+c['key']}, timeout=90)
            if r.status_code != 200: return {'error': 'HTTP '+str(r.status_code)}
            j = r.json()
            if c['wire'] == 'chat':
                ch = j['choices'][0]; return {'answer': ch['message'].get('content') or '', 'incomplete': ch.get('finish_reason')=='length'}
            if j.get('error') or j.get('status') in ('failed', 'cancelled'):
                return {'error': 'responses API failure'}
            text = j.get('output_text') or ''.join(part.get('text','') for item in j.get('output',[]) if item.get('type')=='message' for part in item.get('content',[]) if part.get('type')=='output_text')
            return {'answer': text, 'incomplete': j.get('status') == 'incomplete'}
        except requests.RequestException: return {'error':'网络连接异常或超时'}
        except (ValueError, TypeError, KeyError, IndexError): return {'error':'响应格式不兼容'}


def redact(text, config):
    for secret in (config.get('key'), config.get('base')):
        if secret:
            text = text.replace(secret, '[REDACTED]')
    return text


def run_health(api, seed, repeats, workers):
    ts = tasks(seed); jobs = [(t, i+1) for t in ts for i in range(repeats)]
    def one(x):
        t, rep = x; r = api.call(t['prompt'])
        row = {'id':t['id'],'dimension':t['dimension'],'repeat':rep,'expected':t['expected'], 'prompt_sha256':hashlib.sha256(t['prompt'].encode()).hexdigest()}
        if 'error' in r: row.update(status='api_error',error=r['error'],correct=None)
        else: row.update(status='incomplete' if r['incomplete'] else 'answered',answer=redact(r['answer'][:600],api.c),correct=not r['incomplete'] and is_correct(t,r['answer']))
        return row
    with ThreadPoolExecutor(max_workers=workers) as p: rows=list(p.map(one,jobs))
    scored=[r for r in rows if r['status']!='api_error']; dims={}
    for dim in dict.fromkeys(t['dimension'] for t in ts):
        g=[r for r in scored if r['dimension']==dim]; hit=sum(r['correct'] is True for r in g)
        dims[dim]={'correct':hit,'scored':len(g),'attempted':repeats*4,'score_percent':round(hit/len(g)*100,1) if g else None,'wilson95_descriptive':wilson(hit,len(g))}
    hit=sum(r['correct'] is True for r in scored)
    return {'seed':seed,'task_count':len(ts),'repeats':repeats,'attempted':len(rows),'scored':len(scored),'api_errors':len(rows)-len(scored),'score_percent':round(hit/len(scored)*100,1) if scored else None,'coverage_percent':round(len(scored)/len(rows)*100,1),'dimensions':dims,'records':rows,'interpretation':'无可信基线，不能估计降智百分比；这是小样本诊断。'}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--codex-home'); ap.add_argument('--profile'); ap.add_argument('--model'); ap.add_argument('--base-url'); ap.add_argument('--wire-api',choices=['chat','responses']); ap.add_argument('--inspect',action='store_true'); ap.add_argument('--run',action='store_true'); ap.add_argument('--self-test',action='store_true'); ap.add_argument('--skip-identity',action='store_true',help='公开版始终不进行身份认证（兼容参数）'); ap.add_argument('--seed',type=int); ap.add_argument('--repeats',type=int,choices=[1,2,3],default=1); ap.add_argument('--workers',type=int,choices=[1,2,3,4],default=2); ap.add_argument('--output',default='model-health-results')
    args=ap.parse_args()
    if args.self_test:
        ts=tasks(1234)
        assert len(ts)==24 and len({t['id'] for t in ts})==24
        for task in ts:
            a=json.dumps(task['expected']) if task['kind']=='json' else task['expected']
            assert is_correct(task,a) and not is_correct(task,'INVALID')
        assert wilson(0,0) is None
        print('PASS: 24个任务及评分器离线自检。未读取凭证，未调用模型。')
        return
    c=discover(args.codex_home,args.profile)
    if args.model: c['model']=args.model
    if args.base_url: c['base']=args.base_url.rstrip('/')
    if args.wire_api: c['wire']=args.wire_api
    check_url(c['base']); print(json.dumps(safe_config(c),ensure_ascii=False,indent=2))
    if args.inspect: return
    if not args.run: raise ValueError('先用 --inspect；正式请求需添加 --run。')
    if not c['key']: raise ValueError('未找到可用 API Key；请在本机设置provider对应的环境变量。')
    if c['custom_headers_present']: raise ValueError('provider使用自定义请求头，暂不支持自动复制；停止测试以免误判。')
    seed=args.seed if args.seed is not None else secrets.randbits(32); report=run_health(API(c),seed,args.repeats,args.workers)
    out=Path(args.output); out.mkdir(parents=True,exist_ok=True); (out/'体检结果.json').write_text(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),'config':safe_config(c),'health':report,'identity':{'status':'not_evaluated','authenticity_probability':None},'degradation':{'status':'not_estimable_without_baseline','percent':None}},ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'体检报告.md').write_text('# 模型能力体检\n\n- 模型声明：'+c['model']+'\n- 本次得分：'+str(report['score_percent'])+'%\n- 有效覆盖：'+str(report['coverage_percent'])+'%\n- API错误：'+str(report['api_errors'])+'\n- 随机种子：'+str(seed)+'\n\n## 各维度\n\n|维度|得分|\n|---|---|\n'+'\n'.join('|'+k+'|'+str(v['score_percent'])+'%|' for k,v in report['dimensions'].items())+'\n\n## 重要限制\n\n身份未核验。无可信基线，不判定降智百分比；请求配置不等于当前聊天路由的权重认证。24题仅为小样本诊断，不是全面能力认证。\n',encoding='utf-8')
    print('报告已生成:',out)

if __name__=='__main__':
    try: main()
    except (ValueError,FileNotFoundError) as e: print('无法执行：'+str(e),file=sys.stderr); sys.exit(2)
