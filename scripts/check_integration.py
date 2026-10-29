"""Load the actual VTuber stack without opening windows or playing audio."""
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import soundfile as sf
from vtuber import VtuberAI

app=VtuberAI()
app.auto_play=False
app.output_path='logs/integration-test.wav'
started=time.monotonic()
try:
    from vtuber.hand_gestures import HandGestures
    gesture=app.live2d_settings.get('camera_tracking',{}).get('gestures',{})
    if gesture.get('enabled'):
        hands=HandGestures(app.project_path(gesture['model_path']),gesture)
        try:
            assert not hands.process(np.zeros((480,640,3),dtype=np.uint8))
            print('MediaPipe model/inference PASS',flush=True)
        finally:
            hands.close()
    app.initialize_models()
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
    if app.tts is not None:
        app.tts.close()
