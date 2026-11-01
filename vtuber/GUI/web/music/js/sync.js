import { state } from './state.js';
import { loadSongs, control } from './api.js';
import { elements, renderSongs, renderPlayback } from './view.js';
import { playNext, finishSong, flushCompletions } from './player.js';
import { renderHistory } from './history.js';

async function refresh() {
    const version = state.controlVersion;
    try {
        const data = await loadSongs();
        if (version !== state.controlVersion || state.pendingControls) return;
        state.songs = data.songs;
        state.history = data.history || { records: [], videos: [] };
        flushCompletions();
        elements.status.textContent = data.status;
        if (data.playback && state.ready) {
            const remote = data.playback;
            state.paused = remote.paused;
            if (state.volume !== remote.volume) {
                state.player.setVolume(remote.volume);
                state.volume = remote.volume;
            }
            if (state.skip !== null && state.skip !== remote.skip) finishSong(true);
            state.skip = remote.skip;
            if (state.paused) state.player.pauseVideo();
            else if (state.current && state.player.getPlayerState() === window.YT.PlayerState.PAUSED) state.player.playVideo();
        }
        if (state.ready) {
            if (state.current) {
                const updated = state.songs.find(song => song.id === state.current.id);
                if (updated) state.current = updated;
                else finishSong();
            }
            playNext();
            control('report', state.current?.id || '').catch(() => {});
        }
        renderSongs();
        renderPlayback();
        renderHistory();
    } catch {
        if (state.ready && state.current) {
            state.player.stopVideo();
            state.current = null;
            renderSongs();
            renderPlayback();
            elements.playing.textContent = '伺服器連線中斷，已停止播放';
        }
        elements.status.textContent = '無法取得佇列，稍後自動重試';
    }
    finally { setTimeout(refresh, 500); }
}

export function startSync() { refresh(); }
