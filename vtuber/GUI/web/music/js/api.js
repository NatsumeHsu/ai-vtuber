let token = '';

async function playbackToken() {
    const response = await fetch('/api/config', { cache: 'no-store', signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error('點歌服務尚未就緒');
    token = (await response.json()).token;
}

export async function loadSongs() {
    const response = await fetch('/api/songs', { cache: 'no-store', signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error('讀取失敗');
    return response.json();
}

export async function control(action, value = '') {
    if (!token) await playbackToken();
    const params = new URLSearchParams({ action, value: String(value) });
    const send = () => fetch(`/api/control?${params}`, { method: 'POST',
        headers: { 'X-Playback-Token': token }, signal: AbortSignal.timeout(5000) });
    let response = await send();
    if (response.status === 403) {
        await playbackToken();
        response = await send();
    }
    if (!response.ok) throw new Error('控制失敗');
    return response;
}
