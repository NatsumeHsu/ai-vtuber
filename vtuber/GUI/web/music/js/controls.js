import { state } from './state.js';
import { control } from './api.js';
import { elements, renderSongs, renderPlayback } from './view.js';
import { playNext, finishSong } from './player.js';

const actions = new Map([
    ['play', () => {
        state.paused = false;
        if (state.current) state.player.playVideo();
        else playNext();
    }],
    ['pause', () => { state.paused = true; state.player.pauseVideo(); }],
    ['next', () => finishSong(true)],
    ['volume', value => {
        state.volume = Number(value);
        state.player.setVolume(state.volume);
        if (state.volume > 0) state.player.unMute();
    }],
]);

async function sendControl(action, value) {
    if (!state.ready) {
        elements['control-status'].textContent = '播放器尚未就緒，請稍候';
        return;
    }
    state.controlVersion++;
    state.pendingControls++;
    try {
        actions.get(action)?.(value);
        renderSongs();
        renderPlayback();
        elements['control-status'].textContent = '已操作播放器，正在同步…';
        const response = await control(action, value);
        const skip = response.headers.get('X-Playback-Skip');
        if (skip !== null) state.skip = Number(skip);
        elements['control-status'].textContent = '操作完成';
    } catch {
        elements['control-status'].textContent = '操作或同步失敗，請確認伺服器與播放器連線';
    } finally { state.pendingControls--; }
}

export function initializeControls() {
    elements.controls.querySelectorAll('[data-action]').forEach(button => {
        button.addEventListener('click', () => sendControl(button.dataset.action));
    });
    elements.volume.addEventListener('input', event => {
        elements['volume-value'].textContent = `${event.target.value}%`;
    });
    elements.volume.addEventListener('change', event => sendControl('volume', event.target.value));
}
