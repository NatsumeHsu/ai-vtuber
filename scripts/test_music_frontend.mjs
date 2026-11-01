import { test } from 'node:test';
import assert from 'node:assert/strict';

test('前端 ES 模組共用狀態，更新歌單與直接操作播放器', async t => {
    const nodes = new Map();
    const buttons = ['play', 'pause', 'next'].map(action => ({
        dataset: { action }, listeners: {}, addEventListener(type, callback) { this.listeners[type] = callback; },
    }));
    const element = id => {
        if (!nodes.has(id)) nodes.set(id, {
            hidden: true, textContent: '', listeners: {}, children: [],
            addEventListener(type, callback) { this.listeners[type] = callback; },
            querySelectorAll: () => buttons,
            replaceChildren(...children) { this.children = children; },
            append(...children) { this.children.push(...children); }, setAttribute() {},
        });
        return nodes.get(id);
    };
    const calls = [];
    const completed = [];
    let playerEvents;
    const remote = { paused: false, volume: 100, skip: 0 };
    let songs = [{ id: 'a', videoId: 'aaaaaaaaaaa', title: '第一首' }, { id: 'b', videoId: 'bbbbbbbbbbb' }];
    const timers = [];
    t.mock.method(globalThis, 'setTimeout', callback => { timers.push(callback); });
    t.mock.method(globalThis, 'fetch', async url => {
        if (url === '/api/config') return { ok: true, json: async () => ({token:'test-token'}) };
        if (url === '/api/songs') return { ok: true, json: async () => ({ songs, status: '測試', playback: { ...remote } }) };
        const params = new URL(url, 'http://localhost').searchParams;
        const action = params.get('action');
        if (action === 'complete') completed.push(params.get('value'));
        if (action === 'pause') remote.paused = true;
        if (action === 'play') remote.paused = false;
        if (action === 'next') remote.skip++;
        if (action === 'volume') remote.volume = Number(params.get('value'));
        if (action === 'enqueue') songs = [...songs, { id: 'from-history', videoId: params.get('value') }];
        return { ok: true, headers: new Headers({ 'X-Playback-Skip': String(remote.skip) }) };
    });
    const saved = Object.fromEntries(['document', 'window', 'location'].map(key => [key, globalThis[key]]));
    globalThis.document = { getElementById: element, createElement: () => element(Symbol()), activeElement: null };
    globalThis.location = { origin: 'http://localhost' };
    globalThis.window = { YT: {
        PlayerState: { ENDED: 0, PLAYING: 1, PAUSED: 2 },
        Player: function (id, options) {
            playerEvents = options.events;
            return {
                getIframe: () => element('iframe'), getPlayerState: () => 1,
                loadVideoById: id => calls.push(['load', id]),
                stopVideo() {}, playVideo: () => calls.push(['play']), pauseVideo: () => calls.push(['pause']),
                setVolume: value => calls.push(['volume', value]), unMute() {},
            };
        },
    } };
    t.after(() => { for (const [key, value] of Object.entries(saved)) {
        if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
    } });
    const { state } = await import('../vtuber/GUI/web/music/js/state.js');
    const { initializePlayer } = await import('../vtuber/GUI/web/music/js/player.js');
    const { initializeControls } = await import('../vtuber/GUI/web/music/js/controls.js');
    const { startSync } = await import('../vtuber/GUI/web/music/js/sync.js');
    initializePlayer();
    playerEvents.onReady();
    initializeControls();
    startSync();
    await new Promise(setImmediate);
    assert.equal(state.current.id, 'a');
    assert.equal(element('songs').children.length, 1);
    await buttons[1].listeners.click();
    assert.equal(state.paused, true);
    assert.deepEqual(calls.at(-1), ['pause']);
    await buttons[0].listeners.click();
    assert.deepEqual(calls.at(-1), ['play']);
    await buttons[2].listeners.click();
    assert.equal(state.current.id, 'b');
    assert.deepEqual(completed, ['a']);
    await timers.shift()();
    assert.equal(state.current.id, 'b'); // A local skip must not be applied again by polling.
    assert.deepEqual(completed, ['a']);
    songs = [...songs, { id: 'c', videoId: 'ccccccccccc' }];
    await timers.shift()();
    assert.equal(element('songs').children.length, 1);
    playerEvents.onStateChange({ data: 0 });
    assert.equal(state.current.id, 'c');
    assert.deepEqual(completed, ['a', 'b']);
    const { initializeHistory, renderHistory } = await import('../vtuber/GUI/web/music/js/history.js');
    state.history = { videos: [{ videoId: 'ddddddddddd', title: '歷史測試歌曲', playCount: 2, lastPlayedAt: '2026-10-25T10:00:00Z', url: 'https://youtu.be/ddddddddddd' }] };
    initializeHistory();
    element('history-search').value = '找不到';
    element('history-search').listeners.input();
    assert.equal(element('history-results').children[0].textContent, '沒有符合的播放紀錄');
    element('history-search').value = '測試歌曲';
    renderHistory();
    const result = element('history-results').children[0];
    assert.match(result.children[1].textContent, /2 次/);
    await result.children[2].listeners.click();
    assert.ok(state.songs.some(song => song.videoId === 'ddddddddddd'));
    assert.equal(element('history-results').children[0].children[2].disabled, true);
    element('history-search').value = '';
    state.history.records = [
        { videoId: 'a', title: 'A 舊紀錄', requestedBy: 'old', completedAt: '2026-10-25T09:00:00Z' },
        { videoId: 'b', title: 'B', requestedBy: 'viewer', completedAt: '2026-10-25T11:00:00Z' },
        { videoId: 'a', title: 'A 最新紀錄', lastrequestedBy: 'new', lastcompletedAt: '2026-10-25T12:00:00Z', count: 2 },
    ];
    renderHistory();
    const records = element('history-records').children;
    assert.equal(records.length, 2);
    assert.equal(records[0].children[0].textContent, 'A 最新紀錄');
    assert.match(records[0].children[1].textContent, /new/);
    assert.equal(records[1].children[0].textContent, 'B');
    playerEvents.onError({ data: 150 });
    assert.deepEqual(completed, ['a', 'b']);
    assert.equal(state.current.videoId, 'ddddddddddd');
    playerEvents.onError({ data: 150 });
    assert.equal(state.current, null);
    assert.deepEqual(completed, ['a', 'b']);
    assert.match(element('playing').textContent, /不允許嵌入播放.*150/);
});
