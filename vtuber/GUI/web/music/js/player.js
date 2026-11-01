import { state } from './state.js';
import { elements, renderSongs, renderPlayback } from './view.js';
import { control } from './api.js';

const completions = new Set();
const reporting = new Set();
export function flushCompletions() {
    for (const id of completions) {
        if (reporting.has(id)) continue;
        reporting.add(id);
        control('complete', id).then(() => completions.delete(id)).catch(() => {
            elements.status.textContent = '播放紀錄儲存失敗，稍後自動重試';
        }).finally(() => reporting.delete(id));
    }
}

export function playNext() {
    if (!state.ready || state.current || state.paused) return;
    const song = state.songs.find(song => !state.finished.has(song.id));
    if (song) {
        state.playerError = '';
        state.current = song;
        elements.resume.hidden = true;
        state.player.loadVideoById(song.videoId);
    }
    renderSongs();
    renderPlayback();
}

export function finishSong(completed = false) {
    if (!state.current) return;
    if (completed === true) {
        completions.add(state.current.id);
        flushCompletions();
    }
    state.finished.add(state.current.id);
    state.current = null;
    state.player.stopVideo();
    elements.resume.hidden = true;
    playNext();
    renderSongs();
    renderPlayback();
}

export function initializePlayer() {
    window.onYouTubeIframeAPIReady = () => {
        state.player = new window.YT.Player('player', {
            playerVars: { autoplay: 1, controls: 0, disablekb: 1, fs: 0, playsinline: 1, origin: location.origin },
            events: {
                onReady: () => {
                    state.ready = true;
                    state.player.getIframe().setAttribute('allow', 'autoplay; encrypted-media; picture-in-picture');
                },
                onStateChange: event => {
                    if (event.data === window.YT.PlayerState.ENDED) finishSong(true);
                    if (event.data === window.YT.PlayerState.PLAYING) {
                        elements.resume.hidden = true;
                        renderPlayback();
                    }
                },
                onAutoplayBlocked: () => {
                    elements.playing.textContent = '自動播放被阻擋，請按「開始播放」';
                    elements.resume.hidden = false;
                },
                onError: event => {
                    const reasons = {2: '影片連結無效', 5: 'YouTube HTML5 播放器錯誤',
                        100: '影片不存在或已設為私人', 101: '影片不允許嵌入播放',
                        150: '影片不允許嵌入播放', 153: 'YouTube 未收到來源識別（Referer）'};
                    const code = event?.data ?? '未知';
                    const video = state.current;
                    state.playerError = `無法播放：${reasons[code] || 'YouTube 播放器錯誤'}（${code}）`;
                    if (video) control('error', JSON.stringify({ id: video.id, videoId: video.videoId, code }))
                        .catch(() => {});
                    finishSong();
                },
            },
        });
    };
    elements.resume.addEventListener('click', () => state.ready && state.player.playVideo());
    if (window.YT?.Player) window.onYouTubeIframeAPIReady();
    else {
        const script = document.createElement('script');
        script.src = 'https://www.youtube.com/iframe_api';
        script.onerror = () => { elements.playing.textContent = 'YouTube 播放器載入失敗，請重新整理來源'; };
        document.head.append(script);
    }
}
