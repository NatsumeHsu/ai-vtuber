import json, math, sys, unittest
from pathlib import Path
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import yaml
from vtuber.emotion_middleware import EmotionAnalyzer

class EmotionTests(unittest.TestCase):
    def setUp(self):
        self.settings=yaml.safe_load((ROOT/'setting.yml').read_text(encoding='utf-8'))
        self.options=self.settings['tts']['emotion_analysis']
    def analyzer(self, value):
        llm=Mock()
        llm.create_chat_completion.return_value={'choices':[{'message':{'content':value}}]}
        return EmotionAnalyzer(llm,self.options)
    def test_valid_and_scaled(self):
        a=self.analyzer('{"emo_vector":[1,1,0,0,0,0,0,0]}')
        self.assertTrue(all(abs(x-y)<1e-9 for x,y in zip(a.analyze('好開心'),[.4,.4,0,0,0,0,0,0])))
        request=a.llm.create_chat_completion.call_args.kwargs
        self.assertEqual(len(request['messages']),2)
        self.assertEqual(json.loads(request['messages'][1]['content'])['text'],'好開心')
    def test_bad_results_fallback(self):
        for payload in ['oops','{}','{"emo_vector":[1]}','{"emo_vector":[true,0,0,0,0,0,0,0]}','{"emo_vector":[NaN,0,0,0,0,0,0,0]}']:
            with self.subTest(payload=payload):
                self.assertEqual(self.analyzer(payload).analyze('你好'), self.options['fallback_vector'])
    def test_failure_and_disabled(self):
        a=self.analyzer('{}');a.llm.create_chat_completion.side_effect=RuntimeError('failure')
        self.assertEqual(a.analyze('你好'),self.options['fallback_vector'])
        a.settings=dict(a.settings,enabled=False)
        self.assertIsNone(a.analyze('你好'))
    def test_tts_boundary_converts_only_spoken_text(self):
        import tempfile
        import threading
        from opencc import OpenCC
        from vtuber.index_tts_client import IndexTTSClient
        client=IndexTTSClient.__new__(IndexTTSClient)
        client.lock=threading.Lock()
        client.model=Mock()
        client.reference=Path('reference.wav')
        client.settings={'generation': {}}
        client.text_converter=OpenCC('t2s')
        original='歡迎回來'
        with tempfile.TemporaryDirectory() as folder:
            client.synthesize(original,Path(folder)/'test.wav')
        self.assertEqual(client.model.infer.call_args.kwargs['text'],'欢迎回来')
        self.assertEqual(original,'歡迎回來')

if __name__=='__main__': unittest.main()
