import { state } from './state.js';
import { control, loadSongs } from './api.js';

const search = document.getElementById('history-search');
const results = document.getElementById('history-results');
const status = document.getElementById('history-status');
const recordsList = document.getElementById('history-records');

export function renderHistory() {
    const query = (search.value || '').trim().toLocaleLowerCase();
    const videos = (state.history?.videos || []).filter(video =>
        `${video.title || ''} ${video.videoId}`.toLocaleLowerCase().includes(query)
    ).sort((a, b) => Date.parse(b.lastPlayedAt || '1970-01-01T00:00:00Z') - Date.parse(a.lastPlayedAt || '1970-01-01T00:00:00Z'));
    results.replaceChildren(...videos.map(video => {
        const item = document.createElement('li');
        const title = document.createElement('a');
        title.href = video.url;
        title.target = '_blank';
        title.rel = 'noopener noreferrer';
        title.textContent = video.title || video.videoId;
        const details = document.createElement('p');
        details.textContent = video.lastPlayedAt
            ? `已播放 ${video.playCount} 次 · 最近播放 ${new Date(video.lastPlayedAt).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei' })}`
            : '已播放 0 次 · 尚未播放';
        const button = document.createElement('button');
        const queued = state.songs.some(song => song.videoId === video.videoId);
        button.type = 'button';
        button.textContent = queued ? '已在待播清單' : '加入待播清單';
        button.disabled = queued;
        button.addEventListener('click', async () => {
            button.disabled = true;
            try {
                await control('enqueue', video.videoId);
                status.textContent = `已加入：${video.title || video.videoId}`;
                const data = await loadSongs();
                state.songs = data.songs;
                renderHistory();
            } catch {
                status.textContent = '加入失敗，可能已在清單中或清單已滿，請稍後再試';
                button.disabled = false;
            }
        });
        item.append(title, details, button);
        return item;
    }));
    if (!videos.length) {
        const empty = document.createElement('li');
        empty.textContent = query ? '沒有符合的播放紀錄' : '目前沒有播放紀錄';
        results.append(empty);
    }
    const latest = new Map();
    for (const record of state.history?.records || []) {
        const normalized = {
            ...record,
            requestedBy: record.lastrequestedBy ?? record.requestedBy,
            completedAt: record.lastcompletedAt ?? record.completedAt,
        };
        const previous = latest.get(record.videoId);
        if (!previous || Date.parse(normalized.completedAt || 0) > Date.parse(previous.completedAt || 0)) {
            latest.set(record.videoId, normalized);
        }
    }
    const records = [...latest.values()].filter(record =>
        `${record.title || ''} ${record.videoId}`.toLocaleLowerCase().includes(query)
    ).sort((a, b) => Date.parse(b.completedAt || '1970-01-01T00:00:00Z') - Date.parse(a.completedAt || '1970-01-01T00:00:00Z'));
    recordsList.replaceChildren(...records.map(record => {
        const item = document.createElement('li');
        const link = document.createElement('a');
        link.href = record.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = record.title || record.videoId;
        const details = document.createElement('p');
        details.textContent = `點歌者：${record.requestedBy} · 播放完成 ${new Date(record.completedAt).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei' })}`;
        item.append(link, details);
        return item;
    }));
    if (!records.length) {
        const empty = document.createElement('li');
        empty.textContent = query ? '沒有符合的播放紀錄' : '目前沒有播放紀錄';
        recordsList.append(empty);
    }
}

export function initializeHistory() {
    search.value = '';
    search.addEventListener('input', renderHistory);
    renderHistory();
}
