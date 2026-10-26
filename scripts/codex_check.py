"""Portable, privacy-preserving Codex endpoint health checker.

Two-step flow: (1) fingerprint identity check against a bundled 32-model
reference library, (2) capability health check with auto-scored tasks."""
import argparse
from collections import Counter, defaultdict
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

SKILL_DIR = Path(__file__).resolve().parents[1]


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
    def call(self, prompt, system='Answer accurately and follow the requested output format.', fingerprint=False):
        import requests
        c = self.c
        if c['wire'] == 'chat':
            body = {'model': c['model'], 'messages': [{'role':'system','content':system},{'role':'user','content':prompt}], 'max_tokens': 16 if fingerprint else 1024}
            if fingerprint:
                body['temperature'] = 1.0
            url = c['base'] + '/chat/completions'
        else:
            body = {'model': c['model'], 'instructions': system, 'input': prompt, 'max_output_tokens': 16 if fingerprint else 1024}
            if c.get('reasoning_effort'):
                body['reasoning'] = {'effort': c['reasoning_effort']}
            if fingerprint:
                body['temperature'] = 1.0
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


def identity_check(api, reps, workers):
    """第一步: 指纹身份认证。本机采集单 token 分布, 与内置 32 模型参考库比对。

    凭证不出本机; 参考库来自中转站标签, 匹配不是官方权重认证。
    """
    fw = SKILL_DIR / 'fingerprint_web'
    if str(fw) not in sys.path:
        sys.path.insert(0, str(fw))
    import server
    from agent_client import _normalize, REFUSAL_PAT
    client = server.app.test_client()
    session = client.post('/api/privacy/session/start', json={
        'declared_model': api.c['model'], 'battery': 'orig', 'reps': reps}).get_json()
    buckets = defaultdict(Counter)
    errors = Counter()
    jobs = [cell for cell in session['cells'] for _ in range(reps)]

    def one(cell):
        return cell, api.call(cell['prompt'], cell['system'], fingerprint=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for cell, r in pool.map(one, jobs):
            if 'error' in r:
                errors[r['error']] += 1
                continue
            raw = r['answer']
            norm = _normalize(raw)
            if not norm or r.get('incomplete') or REFUSAL_PAT.search(raw):
                errors['拒答、空回答或截断'] += 1
                continue
            buckets[cell['cell_key']][norm] += 1
    total_valid = sum(sum(c.values()) for c in buckets.values())
    if len(buckets) < 10 or total_valid < len(jobs) * 0.8:
        return {'status': 'insufficient_data', 'errors': dict(errors),
                'note': '有效样本不足或端点不支持温度1.0短输出, 不能作身份判断。'}
    resp = client.post('/api/privacy/session/' + session['session_id'] + '/analyze',
                       json={'distributions': {k: dict(v) for k, v in buckets.items()}})
    if resp.status_code != 200:
        return {'status': 'analysis_failed', 'errors': dict(errors)}
    data = resp.get_json()
    quality = data.get('quality')
    return {'status': 'completed',
            'verdict': data['verdict'], 'verdict_zh': data['verdict_zh'],
            'declared_in_db': data['declared_in_db'],
            'nearest': data['nearest'], 'ranking': data['ranking'],
            'score_declared': data['score_declared'],
            'thresholds': data['thresholds'],
            'quality': quality,
            'n_cells': len(buckets), 'n_valid': total_valid,
            'errors': dict(errors),
            'note': '参考库为项目中转站标签采集, 未逐一官方认证; 库外≠假模型。',
            'protocol_note': ('Responses接口与参考chat采样协议不完全一致, 匹配为探索性。'
                              if api.c['wire'] == 'responses' else
                              '温度1.0单token探针, 与参考库同协议。')}


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
    ap=argparse.ArgumentParser(); ap.add_argument('--codex-home'); ap.add_argument('--profile'); ap.add_argument('--model'); ap.add_argument('--base-url'); ap.add_argument('--wire-api',choices=['chat','responses']); ap.add_argument('--inspect',action='store_true'); ap.add_argument('--run',action='store_true'); ap.add_argument('--self-test',action='store_true'); ap.add_argument('--skip-identity',action='store_true',help='跳过第一步指纹认证, 只做能力体检'); ap.add_argument('--reps',type=int,choices=[3,4,6,8,12],default=6,help='指纹认证每单元采样次数(20单元)'); ap.add_argument('--seed',type=int); ap.add_argument('--repeats',type=int,choices=[1,2,3],default=1); ap.add_argument('--workers',type=int,choices=[1,2,3,4],default=2); ap.add_argument('--output',default='model-health-results')
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
    api=API(c); seed=args.seed if args.seed is not None else secrets.randbits(32)
    # 第一步: 指纹身份认证 (20 单元 x reps 次单 token 采样)
    if args.skip_identity:
        identity={'status':'skipped','note':'用户指定跳过指纹认证。'}
        print('跳过指纹认证。')
    else:
        print(f'第一步: 指纹身份认证 (约 {20*args.reps} 个请求) ...', flush=True)
        identity=identity_check(api,args.reps,args.workers)
        if identity['status']=='completed':
            print('身份判定:', identity['verdict_zh'], end='')
            if identity.get('nearest'): print(' | 最近:', identity['nearest']['model'], identity['nearest']['dist'], end='')
            if identity.get('quality'): print(' | 质量:', identity['quality']['verdict_zh'], end='')
            print()
    # 第二步: 能力体检 (24 个请求)
    print('第二步: 能力体检 (24 个请求) ...', flush=True)
    report=run_health(api,seed,args.repeats,args.workers)
    out=Path(args.output); out.mkdir(parents=True,exist_ok=True)
    (out/'体检结果.json').write_text(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),'config':safe_config(c),'identity':identity,'health':report,'degradation':{'behavior_shift':(identity.get('quality') or {}).get('verdict'),'capability_percent':report['score_percent'],'percent':None,'note':'无可信基线,不折算降智百分比;行为偏离与能力得分分开报告。'}},ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 模型端点体检: 身份认证 + 能力体检','','- 模型声明：'+c['model'],'- 时间：'+datetime.now(timezone.utc).isoformat()[:19],'','## 第一步: 指纹身份认证','']
    if identity.get('status')=='completed':
        lines += ['- **判定：'+identity['verdict_zh']+'**']
        if identity.get('nearest'): lines.append('- 最近参考模型：'+identity['nearest']['model']+'（'+identity['nearest']['family']+' 家族, 距离 '+str(identity['nearest']['dist'])+'）')
        if identity.get('score_declared') is not None and identity.get('thresholds'): lines.append('- 与声明模型距离：'+str(identity['score_declared'])+'（阈值 ≤'+str(identity['thresholds']['tau_gen'])+' 一致 / ≥'+str(identity['thresholds']['tau_imp'])+' 不一致）')
        if not identity.get('declared_in_db'): lines.append('- 声明模型不在 32 模型参考库中, 仅提供家族归因; 库外≠假模型。')
        lines.append('- 协议：'+identity.get('protocol_note',''))
        q=identity.get('quality')
        lines += ['','### 行为偏离检验（探索性）','']
        if q:
            lines += ['- '+('✅ '+q['verdict_zh'] if q['verdict']=='nominal' else '⚠️ '+q['verdict_zh'])+'（p='+str(q['p_value'])+', 效应量 '+str(q['effect'])+'×）']
            if q.get('deviating_cells'): lines.append('- 偏离最大单元：'+', '.join(c['cell']+' '+str(c['jsd_obs']) for c in q['deviating_cells'][:3]))
            lines.append('- 偏离≠能力下降证明; 是与该版本参考行为的差异信号。')
        else:
            lines.append('- 未执行（身份不一致/灰区或数据不足时无意义）。')
    else:
        lines += ['- 状态：'+identity.get('status','未执行')+' — '+identity.get('note','')]
    lines += ['','## 第二步: 能力体检','','- 本次得分：'+str(report['score_percent'])+'%','- 有效覆盖：'+str(report['coverage_percent'])+'%','- API错误：'+str(report['api_errors'])+'','- 随机种子：'+str(seed),'','|维度|得分|','|---|---|']
    lines += ['|'+k+'|'+str(v['score_percent'])+'%|' for k,v in report['dimensions'].items()]
    lines += ['','## 重要限制','','- 参考库来自中转站标签, 匹配不是官方权重认证。','- 行为偏离与能力得分分开报告; 无可信基线, 不折算降智百分比。','- 配置端点不等于当前聊天路由的权重实例。','- 24题+20单元指纹为小样本诊断, 不是全面认证。']
    (out/'体检报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('报告已生成:',out)
    print('身份:',identity.get('verdict_zh',identity.get('status')),'| 能力得分:',report['score_percent'],'| 降智百分比: 无基线不可估计')

if __name__=='__main__':
    try: main()
    except (ValueError,FileNotFoundError) as e: print('无法执行：'+str(e),file=sys.stderr); sys.exit(2)
