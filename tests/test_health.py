import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('check',Path(__file__).resolve().parents[1]/'scripts/codex_check.py')
check=importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class Tests(unittest.TestCase):
    def test_all_ground_truth(self):
        for seed in range(50):
            ts=check.tasks(seed)
            self.assertEqual(len(ts),24)
            for t in ts:
                answer=json.dumps(t['expected']) if t['kind']=='json' else t['expected']
                self.assertTrue(check.is_correct(t,answer))
                self.assertFalse(check.is_correct(t,'WRONG'))

    def test_config_does_not_cross_provider_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'config.toml').write_text('model="demo"\nmodel_provider="custom"\n[model_providers.custom]\nbase_url="https://example.com/v1"\nenv_key="PRIVATE_TEST_KEY"\nwire_api="chat"\n',encoding='utf-8')
            with patch.dict(os.environ,{'OPENAI_API_KEY':'must-not-leak'},clear=True):
                cfg=check.discover(p)
                self.assertFalse(cfg['key'])
                self.assertNotIn('must-not-leak',json.dumps(check.safe_config(cfg)))
                with self.assertRaises(ValueError):check.discover(p,'missing')

    def test_errors_not_in_scores(self):
        class Fake:
            c={'key':'secret-test','base':'https://example.com/v1'}
            def call(self,prompt):return {'error':'HTTP 401'}
        r=check.run_health(Fake(),123,1,2)
        self.assertIsNone(r['score_percent'])
        self.assertEqual(r['api_errors'],24)

    def test_local_http_chat_and_responses(self):
        import threading
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        lookup={t['prompt']:json.dumps(t['expected']) if t['kind']=='json' else t['expected'] for t in check.tasks(123)}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                prompt=body.get('input') or body['messages'][-1]['content']
                answer=lookup[prompt]
                result={'choices':[{'message':{'content':answer},'finish_reason':'stop'}]} if self.path.endswith('completions') else {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':answer}]}]}
                raw=json.dumps(result).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for wire in ('chat','responses'):
                cfg={'model':'demo','wire':wire,'key':'local-test','base':f'http://127.0.0.1:{server.server_port}/v1'}
                r=check.run_health(check.API(cfg),123,1,2)
                self.assertEqual(r['score_percent'],100)
                self.assertEqual(r['api_errors'],0)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_redaction(self):
        self.assertNotIn('secret',check.redact('secret',{'key':'secret'}))
        with self.assertRaises(ValueError):check.check_url('https://user:pass@example.com')


if __name__=='__main__':unittest.main()
