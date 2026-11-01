if (new URLSearchParams(location.search).get('obs') === '1') document.body.classList.add('obs');
import { initializePlayer } from './js/player.js';
import { initializeControls } from './js/controls.js';
import { startSync } from './js/sync.js';
import { initializeHistory } from './js/history.js';

initializeControls();
initializeHistory();
initializePlayer();
startSync();
