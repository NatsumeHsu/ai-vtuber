import { state } from './state.js';

export const elements = Object.fromEntries([
    'songs', 'playing', 'resume', 'count', 'empty', 'status',
    'controls', 'remote-playing', 'volume', 'volume-value', 'control-status',
].map(id => [id, document.getElementById(id)]));

export function renderSongs() {
    const waiting = state.songs.filter(song => !state.finished.has(song.id) && song.id !== state.current?.id);
    elements.count.textContent = waiting.length;
    elements.empty.hidden = waiting.length > 0;
    elements.songs.replaceChildren(...waiting.map(song => {
        const item = document.createElement('li');
        const link = document.createElement('a');
        link.href = song.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = song.title
            ? (song.title.length > 20 ? `${song.title.slice(0, 20)}...` : song.title)
            : `YouTube · ${song.videoId}（標題讀取中）`;
        const by = document.createElement('p');
        by.textContent = `點歌者：${song.requestedBy}`;
        item.append(link, by);
        return item;
    }));
}

export function renderPlayback() {
    const title = state.current?.title || state.current?.videoId || '等待點歌';
    const label = `${state.paused ? '已暫停' : '正在播放'}：${title}`;
    if (elements.resume.hidden) elements.playing.textContent = state.current ? label : (state.playerError || '等待新的點歌');
    elements['remote-playing'].textContent = state.ready ? label : '播放器載入中…';
    if (state.volume !== null && document.activeElement !== elements.volume) {
        elements.volume.value = state.volume;
        elements['volume-value'].textContent = `${state.volume}%`;
    }
}
