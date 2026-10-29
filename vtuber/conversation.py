"""LLM 回覆、聊天室接入與單一對話處理流程。"""
import json
import queue
import re
import traceback
import itertools
import threading


class ConversationMixin:
    """LLM 回覆、聊天室接入與單一對話處理流程。"""

    def init_conversation_jobs(self):
        self.conversation_jobs = queue.PriorityQueue()
        self.conversation_gate = threading.Lock()
        self.model_lock = threading.RLock()
        self._jobs_lock = threading.Lock()
        self._job_sequence = itertools.count()
        self._pending_jobs = 0

    def schedule_conversation(self, kind, speaker, text, viewer_key=None, message_id=None,
                              priority=10, allow_busy=False):
        with self._jobs_lock:
            if self.stop.is_set() or (not allow_busy and self.conversation_gate.locked()):
                return False
            if self._pending_jobs == 0:
                self.conversation_gate.acquire()
            self._pending_jobs += 1
            self.conversation_jobs.put((priority, next(self._job_sequence),
                                       (kind, speaker, text, viewer_key, message_id)))
            return True

    def finish_conversation(self):
        with self._jobs_lock:
            self._pending_jobs -= 1
            idle = self._pending_jobs == 0
            if idle:
                self.conversation_gate.release()
            self.conversation_jobs.task_done()
            return idle

    def enqueue_discord_message(self, user_id, speaker, text):
        viewer_key = f'discord:id:{user_id}'
        mid = self.audience_memory.record(viewer_key, speaker, text, 'discord_voice', 'accepted')
        if not self.schedule_conversation('discord_voice', speaker, text, viewer_key, mid,
                                          priority=0, allow_busy=True):
            self.audience_memory.finish(mid, 'interrupted')
            return False
        self.post_control_event('log', f'Discord 語音已排隊：{speaker}：{text}')
        return True

    def generate_reply(self, text, speaker_name=None, viewer_key=None, message_id=None):
        """保留短期記憶，上下文不足時移除最舊對話。"""
        llm, history = self.llm, self.history
        recent = history[-self.history_turns * 2:] if self.history_turns > 0 else []
        speaker = self.speaker_name if speaker_name is None else speaker_name
        if not isinstance(speaker, str) or not speaker.strip():
            raise ValueError('發言者名稱不可為空')
        viewer_key = viewer_key or 'local:' + speaker.strip()
        if message_id is None:
            message_id = self.audience_memory.record(viewer_key, speaker.strip(), text, 'local', 'accepted')
        system_prompt = self.system_prompt + self.audience_memory.supplement(viewer_key)
        user_message = {'role': 'user', 'content': json.dumps(
            {'speaker': speaker.strip(), 'viewer_key': viewer_key, 'text': text}, ensure_ascii=False)}
        while True:
            messages = [{'role': 'system', 'content': system_prompt}, *recent, user_message]
            tokens = llm.tokenize('\n'.join((m['content'] for m in messages)).encode('utf-8'))
            estimated = len(tokens) + len(messages) * 16 + 128 + self.max_tokens
            if estimated <= self.n_ctx:
                break
            if not recent:
                if system_prompt != self.system_prompt:
                    system_prompt = self.system_prompt
                    continue
                self.audience_memory.finish(message_id, 'failed')
                raise ValueError('輸入太長，請縮短文字或增加 N_CTX')
            recent = recent[2:]
        try:
            with self.model_lock:
                result = llm.create_chat_completion(messages=messages, temperature=self.temperature, max_tokens=self.max_tokens)
        except Exception:
            self.audience_memory.finish(message_id, 'failed')
            raise
        reply = result['choices'][0]['message']['content'] or ''
        reply = re.sub('<think>.*?</think>', '', reply, flags=re.S)
        reply = reply.split('<think>', 1)[0].strip()
        if not reply:
            self.audience_memory.finish(message_id, 'failed')
            raise RuntimeError('模型沒有產生回覆，請確認聊天模板或增加 MAX_TOKENS')
        self.audience_memory.finish(message_id, 'replied', reply)
        self.save_chat_turn(speaker.strip(), text, reply)
        if self.history_turns > 0:
            history[:] = (recent + [user_message, {'role': 'assistant', 'content': reply}])[-self.history_turns * 2:]
        return reply

    def conversation_loop(self):
        if self.control_window is not None:
            self.control_conversation_loop()
            return
        self.speaker_name = self.chat_settings['speaker_name']
        print('準備完成。輸入 /clear 清除記憶，/quit 結束。')
        print(f'目前發言者：{self.speaker_name}；輸入 /name 名字 可切換發言者。')
        if self.recognizer is not None:
            print('按 Enter 開始麥克風錄音，或直接輸入文字對話。')
        try:
            while True:
                text = input(f'\n{self.speaker_name}：').strip()
                if text == '/quit':
                    break
                if text == '/clear':
                    self.clear_history()
                    print('已清除記憶')
                    continue
                if text == '/name' or text.startswith('/name '):
                    name = text[len('/name'):].strip()
                    if name:
                        self.speaker_name = name
                        print(f'發言者已切換為：{self.speaker_name}')
                    else:
                        print('用法：/name 名字')
                    continue
                try:
                    if not text and self.recognizer is not None:
                        text = self.recognize_microphone()
                        if text:
                            print(f'辨識：{text}')
                            text = self.correct_asr_names(text)
                            print(f'校正後：{text}')
                    if not text:
                        continue
                    reply = self.generate_reply(text)
                    print(f'白璃：{reply}')
                    self.synthesize(reply)
                except Exception as exc:
                    print(f'處理失敗：{exc}')
        except (KeyboardInterrupt, EOFError):
            print('\n已結束')

    def observe_twitch_message(self, viewer_key, speaker, text, external_id=None, ignored=False):
        """先永久保存所有收到的發言，包含忙碌與忽略名單中的訊息。"""
        return self.audience_memory.record(viewer_key, speaker, text, 'twitch',
                                           'ignored' if ignored else 'received', external_id)

    def enqueue_twitch_message(self, speaker, text, viewer_key=None, message_id=None):
        """只在閒置時接收一則訊息，忙碌時略過，不累積待處理訊息。"""
        self.audience_memory.finish(message_id, 'accepted')
        if not self.schedule_conversation('text', speaker, text, viewer_key, message_id):
            self.audience_memory.finish(message_id, 'skipped_busy')
            return False
        return True

    def control_conversation_loop(self):
        """所有推論與資料庫寫入依序執行，避免重複錄音或並行操作模型。"""
        while not self.stop.is_set():
            try:
                _, _, job = self.conversation_jobs.get(timeout=0.1)
                kind, speaker, text, viewer_key, message_id = job
            except queue.Empty:
                continue
            try:
                self.post_control_event('busy', None)
                self.reply_destination = 'discord' if kind == 'discord_voice' else 'local'
                self.speaker_name = speaker
                if kind == 'clear':
                    self.clear_history()
                    self.post_control_event('log', '已開始新對話，舊紀錄保留於資料庫。')
                    continue
                if kind == 'microphone':
                    text = self.recognize_microphone()
                    if self.stop.is_set() or self.recording_cancelled.is_set():
                        self.post_control_event('log', '已取消錄音。')
                        continue
                    if not text:
                        self.post_control_event('log', '未偵測到說話，請再試一次。')
                        continue
                    self.post_control_event('log', f'語音辨識：{text}')
                    self.post_control_event('status', '校正名字中……')
                    text = self.correct_asr_names(text)
                if self.stop.is_set():
                    break
                self.post_control_event('log', f'{speaker}：{text}')
                self.post_control_event('status', '白璃正在思考……')
                reply = self.generate_reply(text, speaker, viewer_key=viewer_key, message_id=message_id)
                self.post_control_event('log', f'白璃：{reply}')
                if not self.stop.is_set():
                    self.post_control_event('status', '合成與播放語音中……')
                    self.synthesize(reply)
            except Exception as exc:
                traceback.print_exc()
                self.post_control_event('error', str(exc))
            finally:
                self.reply_destination = 'local'
                self.post_control_event('done' if self.finish_conversation() else 'busy', None)
