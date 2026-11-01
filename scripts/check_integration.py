"""Load the actual VTuber stack without opening windows or playing audio."""
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import soundfile as sf
from vtuber import VtuberAI

import tempfile
import yaml


def main():
    original = yaml.safe_load((ROOT / 'setting.yml').read_text(encoding='utf-8'))
    # Exercise actual models against isolated data, without copying login credentials.
    settings = {key: original[key] for key in ('llm', 'chat', 'output', 'tts', 'asr', 'live2d', 'audience_memory') if key in original}
    settings['llm']['verbose'] = False
    settings['output']['path'] = 'logs/integration-test.wav'
    with tempfile.TemporaryDirectory(prefix='vtuber-model-check-') as folder:
        settings['chat']['database_path'] = str(Path(folder) / 'history.sqlite3')
        settings_path = Path(folder) / 'setting.yml'
        settings_path.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding='utf-8')
        app=VtuberAI(settings_path)
        app.auto_play=False
        started=time.monotonic()
        try:
            app.initialize_models()
            from vtuber.runtime.process_runtime import ModelProxy
            for kind in ('text', 'speech'):
                app.services[kind].start()
                app.services[kind].call('ready')
            app.llm = ModelProxy(app.services['text'])
            app.tts = ModelProxy(app.services['speech'])
            app.recognizer = app.tts
            print('All models loaded',round(time.monotonic()-started,2),flush=True)
            result=app.llm.create_chat_completion(messages=[{'role':'user','content':'請用一句繁體中文說你好。'}],max_tokens=32,temperature=0)
            print('LLM PASS:',result['choices'][0]['message']['content'],flush=True)
            if app.recognizer is not None:
                result=app.recognize_samples(np.zeros(16000,dtype=np.float32))
                assert isinstance(result,str)
                print('ASR PASS:',result,flush=True)
            app.synthesize('[shy]你好，我是白璃。新的語音系統已經準備好了。')
            assert app.last_emotion_error is None, app.last_emotion_error
            assert len(app.last_emo_vector)==8 and sum(app.last_emo_vector)<=0.800001
            print('AI emotion analysis PASS:',app.last_emo_vector,flush=True)
            audio,sr=sf.read(str(app.output))
            assert len(audio)>0 and np.isfinite(audio).all() and abs(audio).max()>0
            print('VTuber TTS PASS',sr,round(len(audio)/sr,2),'seconds',flush=True)
        finally:
            app.close()


if __name__ == '__main__':
    main()
