"""Analyze spoken replies with the existing LLM, without changing chat history."""
import json
import logging
import math


class EmotionAnalyzer:
    # IndexTTS order: happy, angry, sad, afraid, disgusted, melancholic, surprised, calm.
    def __init__(self, llm, settings):
        self.llm = llm
        self.settings = settings
        self.component = float(settings.get('max_component', 0.8))
        self.total = float(settings.get('max_total', 0.8))
        if not (0 < self.component <= 1 and 0 < self.total <= 0.8):
            raise ValueError('Emotion limits must satisfy component <= 1 and total <= 0.8')
        self.fallback = self.validate(settings.get('fallback_vector', [0]*7 + [0.4]))

    def validate(self, vector):
        if not isinstance(vector, list) or len(vector) != 8:
            raise ValueError('emo_vector must contain exactly eight numbers')
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector):
            raise ValueError('emo_vector must contain finite numbers')
        values = [min(self.component, max(0.0, float(x))) for x in vector]
        summed = sum(values)
        if summed > self.total:
            values = [x * self.total / summed for x in values]
        return values

    def analyze(self, text):
        self.last_error = None
        if not self.settings.get('enabled', False):
            return None
        try:
            if self.llm is None:
                raise RuntimeError('Emotion LLM is unavailable')
            response = self.llm.create_chat_completion(
                messages=[{'role': 'system', 'content': self.settings['system_prompt']},
                          {'role': 'user', 'content': json.dumps(
                              {'text': text[:int(self.settings.get('max_input_chars', 1500))]},
                              ensure_ascii=False)}],
                temperature=float(self.settings.get('temperature', 0)),
                max_tokens=int(self.settings.get('max_tokens', 160)),
                response_format={'type': 'json_object'})
            content = response['choices'][0]['message']['content']
            return self.validate(json.loads(content)['emo_vector'])
        except Exception as exc:
            self.last_error = str(exc)
            logging.getLogger(__name__).warning('Emotion analysis failed; using fallback vector', exc_info=True)
            return list(self.fallback)
