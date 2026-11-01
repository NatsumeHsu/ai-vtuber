export const state = {
    songs: [], player: null, ready: false, current: null,
    finished: new Set(), paused: false, skip: null, volume: null,
    controlVersion: 0, pendingControls: 0,
    history: { records: [], videos: [] },
    playerError: '',
};
