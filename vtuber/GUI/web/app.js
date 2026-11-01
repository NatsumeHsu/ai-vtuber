/* Local rendering and generated speech. OBS allows autoplay; browsers can unlock it by clicking. */
(() => {
  const obs = new URLSearchParams(location.search).get('obs') === '1';
  if (obs) document.documentElement.classList.add('obs');
  const status = document.getElementById('status');
  const error = document.getElementById('error');
  const sound = document.getElementById('sound');
  const audio = document.getElementById('speech');
  const client = crypto.randomUUID();
  let config, model, pixi, context, analyser, samples;
  let serverMouth = 0, lastSequence = 0, playing = false, queue = [], activeClip;
  let currentExpression = '', stateExpressions = [], smoothMouth = 0;
  let soundEnabled = obs;
  let zoom = 1;
  let offsetX = 0, offsetY = 0, drag = null;
  audio.volume = Number(localStorage.getItem('hakuri-volume') ?? 0.8);
  document.getElementById('volume').value = audio.volume;
  document.getElementById('volume').addEventListener('input', event => {
    audio.volume = Number(event.target.value);
    localStorage.setItem('hakuri-volume', audio.volume);
  });
  document.getElementById('background').addEventListener('click', () => {
    document.documentElement.classList.toggle('transparent');
    document.body.classList.toggle('transparent');
  });
  const fail = message => { error.hidden = false; error.textContent = message; };
  const clearError = () => { error.hidden = true; };

  function prepareAudio() {
    if (!context) {
      context = new (window.AudioContext || window.webkitAudioContext)();
      analyser = context.createAnalyser();
      analyser.fftSize = 1024;
      samples = new Float32Array(analyser.fftSize);
      context.createMediaElementSource(audio).connect(analyser);
      analyser.connect(context.destination);
    }
    // Do not await a suspended browser context before a user has interacted.
    context.resume().catch(() => {});
  }
  sound.addEventListener('click', async () => {
    prepareAudio();
    await context.resume();
    soundEnabled = true;
    sound.textContent = '聲音已啟用';
    clearError();
    if (activeClip && audio.paused && playing) {
      try { await audio.play(); } catch (_) { fail('聲音尚未啟用，請再按一次「啟用聲音」。'); }
    } else playNext();
  });

  async function acknowledge(clip, state) {
    try {
      await fetch('/api/playback', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({id:clip.id, state, token:config.token})});
    } catch (_) { /* Reconnection is handled by the state poll. */ }
  }

  async function applyExpression() {
    if (!model) return;
    const names = playing && activeClip ? activeClip.emotions : stateExpressions;
    const selected = names[0] || '';
    if (selected === currentExpression) return;
    currentExpression = selected;
    try {
      if (selected) await model.expression(selected);
      else model.internalModel.motionManager.expressionManager?.resetExpression();
    } catch (_) { /* Optional expressions must not interrupt speech. */ }
  }

  async function playNext() {
    if (playing || !queue.length || !soundEnabled) return;
    prepareAudio();
    activeClip = queue.shift();
    playing = true;
    audio.src = activeClip.url;
    status.textContent = '白璃正在說話…';
    applyExpression();
    try {
      await audio.play();
      clearError();
    } catch (failure) {
      await acknowledge(activeClip, failure.name === 'NotAllowedError' ? 'blocked' : 'error');
      if (failure.name === 'NotAllowedError') {
        soundEnabled = false;
        status.textContent = '點擊「啟用聲音」後播放';
        fail('瀏覽器暫停了自動播放，請點擊「啟用聲音」。');
      } else {
        playing = false;
        fail('這段語音無法播放，將繼續接收新的語音。');
        playNext();
      }
    }
  }
  audio.addEventListener('ended', () => {
    if (activeClip) acknowledge(activeClip, 'ended');
    playing = false;
    activeClip = null;
    status.textContent = '角色已就緒，等待語音';
    applyExpression();
    playNext();
  });
  audio.addEventListener('error', () => {
    if (activeClip) acknowledge(activeClip, 'error');
    playing = false;
    activeClip = null;
    playNext();
  });

  function fit() {
    if (!model) return;
    const inset = !obs && innerWidth <= 600 ? 155 : 0;
    const scale = Math.min(pixi.screen.width / model.internalModel.width,
                           (pixi.screen.height - inset) / model.internalModel.height) * 0.94 * zoom;
    model.scale.set(scale);
    model.position.set(pixi.screen.width / 2 + offsetX, (pixi.screen.height + inset) / 2 + offsetY);
  }

  const canvas = document.getElementById('avatar');
  canvas.style.touchAction = 'none';
  canvas.style.cursor = 'grab';
  canvas.addEventListener('pointerdown', event => {
    if (!model || drag || event.button !== 0) return;
    drag = {id:event.pointerId, x:event.clientX, y:event.clientY};
    canvas.setPointerCapture(event.pointerId);
    canvas.style.cursor = 'grabbing';
    event.preventDefault();
  });
  canvas.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    const bounds = canvas.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    offsetX += (event.clientX - drag.x) * pixi.screen.width / bounds.width;
    offsetY += (event.clientY - drag.y) * pixi.screen.height / bounds.height;
    drag.x = event.clientX;
    drag.y = event.clientY;
    fit();
  });
  function endDrag(event) {
    if (!drag || drag.id !== event.pointerId) return;
    drag = null;
    canvas.style.cursor = 'grab';
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  }
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('lostpointercapture', endDrag);

  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    if (!model) return;
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? innerHeight : 1;
    zoom = Math.min(4, Math.max(0.3, zoom * Math.exp(-event.deltaY * unit * 0.0015)));
    fit();
  }, {passive:false});

  async function poll() {
    try {
      const ready = soundEnabled && (!context || context.state === 'running');
      const response = await fetch(`/api/state?client=${client}&ready=${ready ? 1 : 0}`);
      if (!response.ok) throw Error('connection');
      const state = await response.json();
      if (state.instance !== config.instance) {
        location.reload();
        return;
      }
      serverMouth = Math.min(1, Math.max(0, state.mouth));
      stateExpressions = state.expressions;
      if (!playing && status.textContent === '等待本機服務重新連線…') status.textContent = '角色已就緒，等待語音';
      const available = new Set(state.audio.map(clip => clip.id));
      queue = queue.filter(clip => available.has(clip.id));
      for (const clip of state.audio) {
        if (clip.id > lastSequence) {
          queue.push(clip);
          lastSequence = clip.id;
        }
      }
      applyExpression();
      playNext();
    } catch (_) {
      status.textContent = '等待本機服務重新連線…';
      serverMouth = 0;
    } finally { setTimeout(poll, 100); }
  }

  async function start() {
    try {
      const response = await fetch('/api/config');
      if (!response.ok) throw Error('本機服務尚未就緒');
      config = await response.json();
      lastSequence = config.audio_sequence;
      pixi = new PIXI.Application({view:document.getElementById('avatar'), resizeTo:window,
        backgroundAlpha:0, antialias:true, autoDensity:true, resolution:Math.min(devicePixelRatio, 2)});
      model = await PIXI.live2d.Live2DModel.from(config.model, {autoInteract:false});
      pixi.stage.addChild(model);
      model.anchor.set(0.5, 0.5);
      fit();
      window.addEventListener('resize', () => requestAnimationFrame(fit));
      const settings = model.internalModel.settings;
      const groups = settings.groups || settings.json?.Groups || [];
      const lips = groups.find(group => group.Name === 'LipSync')?.Ids || [];
      const parameters = lips.length ? lips : ['ParamMouthOpenY'];
      model.internalModel.on('beforeModelUpdate', () => {
        let mouth = serverMouth;
        if (analyser && !audio.paused) {
          analyser.getFloatTimeDomainData(samples);
          const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
          mouth = Math.min(1, rms * 6);
        }
        smoothMouth += (mouth - smoothMouth) * 0.5;
        for (const parameter of parameters) model.internalModel.coreModel.setParameterValueById(parameter, smoothMouth);
      });
      status.textContent = '角色已就緒，等待語音';
      if (obs) prepareAudio();
      // Diagnostics expose only presentation state, never credentials or model settings.
      window.hakuriView = {get model(){return model;}, get playing(){return playing;},
        get mouth(){return smoothMouth;}, get sequence(){return lastSequence;}};
      poll();
    } catch (failure) {
      status.textContent = '角色載入失敗';
      fail(`無法顯示角色：${failure.message}。請確認本機模型及 WebGL 可用，重新整理後再試。`);
    }
  }
  start();
})();
