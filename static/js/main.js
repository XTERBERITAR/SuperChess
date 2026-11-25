let selectedSquare = null;
let legalDestinations = [];
let gameState = null;
let lastSnapshot = '';
let lastEventKey = null;
let lastDiceKey = null;

const gameId = typeof GAME_ID !== 'undefined' ? GAME_ID : null;

// ================= API HELPERS =================

async function fetchState() {
    if (!gameId) return null;
    const res = await fetch(`/api/state/${gameId}`);
    return res.json();
}

async function fetchLegalMoves(square) {
    if (!gameId) return [];
    const res = await fetch(`/api/legal-moves/${gameId}?square=${square}`);
    return res.json();
}

async function sendMove(from, to) {
    if (!gameId) return null;
    const res = await fetch(`/api/move/${gameId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from, to })
    });
    return res.json();
}

async function activatePowerup(powerupId) {
    const res = await fetch(`/api/powerup/activate/${gameId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ powerup_id: powerupId })
    });
    return res.json();
}

async function resolvePowerup(square) {
    const res = await fetch(`/api/powerup/resolve/${gameId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ square })
    });
    return res.json();
}

async function cancelPowerup() {
    const res = await fetch(`/api/powerup/cancel/${gameId}`, { method: 'POST' });
    return res.json();
}

// ================= HELPERS =================

function getSquareName(index) {
    const files = ['a','b','c','d','e','f','g','h'];
    const rank = 8 - Math.floor(index / 8);
    return files[index % 8] + rank;
}

function getPieceImageSrc(pieceChar) {
    const color = pieceChar === pieceChar.toUpperCase() ? 'w' : 'b';
    return `/static/pieces/${color}${pieceChar.toUpperCase()}.svg`;
}

// Can this device act right now?
function canAct(state) {
    if (!state) return false;
    if (!state.online) return true;
    return state.opponent_joined && state.my_color === state.turn;
}

// The hand to show: your own in online mode, the current player's in local mode
function handOwner(state) {
    return (state.online && state.my_color !== 'spectator') ? state.my_color : state.turn;
}

// One place that applies a new state to the screen
function applyState(state) {
    gameState = state;
    lastSnapshot = JSON.stringify(state);
    updateStatus(state);
    renderDice(state);
    renderBoard(state.board, state);
    renderPowerupPanel(state);
    handleLastEvent(state);
}

// ================= DICE =================

function renderDice(state) {
    const box = document.getElementById('dice-box');
    if (!box) return;

    if (!state.mode_info?.uses_dice || !state.dice_roll || state.status.includes('checkmate')) {
        box.style.display = 'none';
        return;
    }

    const r = state.dice_roll;
    const key = `${state.turn}-${r.value}-${r.rerolls}`;
    const isNew = key !== lastDiceKey;
    lastDiceKey = key;

    const who = (state.online && state.my_color === state.turn) ? 'You' : state.turn;
    const verb = who === 'You' ? 'must' : 'must';
    const note = r.rerolls > 0
        ? `<span class="dice-note">(re-rolled ${r.rerolls}×: no legal moves for those pieces)</span>`
        : '';

    box.innerHTML = `
        <span class="dice-face ${isNew ? 'rolling' : ''}">${r.value}</span>
        <strong>${who}</strong> ${verb} move a <strong>${r.icon} ${r.piece}</strong>${note}
    `;
    box.style.display = 'block';
}

// ================= STATUS =================

function updateStatus(state) {
    const statusEl = document.getElementById('status');
    let text = `Turn: <strong>${state.turn}</strong>`;

    if (state.online) {
        if (state.my_color === 'spectator') text += ' | 👀 Spectating';
        else text += ` | You are <strong>${state.my_color}</strong>`;

        if (!state.opponent_joined) text += ' | ⏳ Waiting for opponent…';
        else if (state.my_color === state.turn) text += ' | Your move!';
    }
    text += ` | ${state.status}`;

    if (state.status.includes('checkmate')) {
        statusEl.classList.add('status-checkmate');
        statusEl.classList.remove('status-check');
    } else if (state.status === 'check') {
        statusEl.classList.add('status-check');
        statusEl.classList.remove('status-checkmate');
    } else {
        statusEl.classList.remove('status-checkmate', 'status-check');
    }
    statusEl.innerHTML = text;

    const modeBadge = document.getElementById('mode-badge');
    if (modeBadge && state.mode_info) modeBadge.textContent = state.mode_info.name;

    // Invite link only for the host while waiting
    const joinBox = document.getElementById('join-box');
    if (joinBox) {
        joinBox.style.display =
            (state.online && !state.opponent_joined && state.my_color === 'white') ? 'block' : 'none';
    }
}

// ================= NOTIFICATION =================

function showNotification(text, color = '#f39c12', duration = 2800) {
    const existing = document.querySelector('.pu-notification');
    if (existing) existing.remove();

    const el = document.createElement('div');
    el.className = 'pu-notification';
    el.innerHTML = text;
    el.style.borderColor = color;
    document.body.appendChild(el);
    setTimeout(() => el.classList.add('show'), 10);
    setTimeout(() => {
        el.classList.remove('show');
        setTimeout(() => el.remove(), 400);
    }, duration);
}

function handleLastEvent(state) {
    const ev = state.last_event;
    if (!ev) { lastEventKey = null; return; }

    // Polling returns the same event repeatedly; only show it once
    const key = JSON.stringify(ev);
    if (key === lastEventKey) return;
    lastEventKey = key;

    const defs = state.powerup_defs || {};
    const pu = defs[ev.powerup];
    const who = ev.player ? ev.player.charAt(0).toUpperCase() + ev.player.slice(1) : '';

    if (ev.type === 'powerup_awarded') {
        showNotification(`${pu?.icon || '✨'} <strong>${who}</strong> earned <strong>${pu?.name || ev.powerup}</strong>!`, pu?.color || '#f39c12');
    }
    if (ev.type === 'powerup_used') {
        showNotification(`${pu?.icon || '✨'} <strong>${who}</strong> used <strong>${pu?.name || ev.powerup}</strong>!`, pu?.color || '#9b59b6');
    }
}

// ================= POWERUP PANEL =================

function renderPowerupPanel(state) {
    const panel = document.getElementById('powerup-panel');
    if (!panel) return;

    const uses = state.mode_info?.uses_powerups;
    panel.style.display = uses ? 'flex' : 'none';
    if (!uses) return;

    const defs = state.powerup_defs || {};
    const owner = handOwner(state);
    const hand = state.powerup_hands?.[owner] || [];
    const pending = state.pending_powerup;
    const myTurn = canAct(state);

    const label = document.getElementById('pu-turn-label');
    if (label) label.textContent = state.online ? `Your hand (${owner})` : `${owner}'s hand`;

    // Pending instruction banner (only for the player who is acting)
    const banner = document.getElementById('pu-banner');
    if (pending && myTurn) {
        let msg = '';
        if (pending.type === 'freeze') msg = `❄️ Click an <strong>enemy piece</strong> to freeze it`;
        else if (pending.type === 'shield') msg = `🛡️ Click <strong>your piece</strong> to shield it`;
        else if (pending.type === 'bomb') msg = `💣 Click an <strong>enemy piece</strong> to destroy it`;
        else if (pending.type === 'teleport' && pending.stage === 'select_target') msg = `🌀 Click <strong>your piece</strong> to teleport`;
        else if (pending.type === 'teleport' && pending.stage === 'select_destination') msg = `🌀 Click an <strong>empty square</strong> as the destination`;
        if (banner) {
            banner.innerHTML = msg + ` <button class="pu-cancel-btn" onclick="doCancelPowerup()">Cancel</button>`;
            banner.style.display = 'block';
        }
    } else if (pending) {
        if (banner) {
            banner.innerHTML = `⏳ Opponent is using a power-up…`;
            banner.style.display = 'block';
        }
    } else if (banner) {
        banner.style.display = 'none';
    }

    const handEl = document.getElementById('pu-hand');
    if (!handEl) return;
    handEl.innerHTML = '';

    if (hand.length === 0) {
        handEl.innerHTML = `<div class="pu-empty">No power-ups yet.<br>Earn one every 3 moves!</div>`;
        return;
    }

    hand.forEach(puId => {
        const pu = defs[puId] || { name: puId, icon: '?', description: '', color: '#888' };
        const card = document.createElement('div');
        card.className = 'pu-card';
        card.style.setProperty('--pu-color', pu.color);
        if (!myTurn) card.style.opacity = '0.5';
        card.innerHTML = `
            <div class="pu-icon">${pu.icon}</div>
            <div class="pu-name">${pu.name}</div>
            <div class="pu-desc">${pu.description}</div>
        `;
        card.addEventListener('click', () => doActivatePowerup(puId));
        handEl.appendChild(card);
    });
}

// ================= POWERUP ACTIONS =================

async function doActivatePowerup(puId) {
    if (!canAct(gameState)) {
        showNotification(`⏳ Wait for your turn`, '#e74c3c', 1500);
        return;
    }
    const state = await activatePowerup(puId);
    if (state.error) {
        showNotification(`❌ ${state.error}`, '#e74c3c', 2000);
        return;
    }
    applyState(state);
}

async function doCancelPowerup() {
    const state = await cancelPowerup();
    if (state.error) return;
    applyState(state);
}

// ================= FETCH + POLLING =================

async function fetchGameState() {
    const state = await fetchState();
    if (!state || state.error) return;
    applyState(state);
}

// Online mode: check for the opponent's moves every second.
// Only re-renders when something actually changed.
async function pollState() {
    try {
        const state = await fetchState();
        if (!state || state.error) return;
        if (JSON.stringify(state) === lastSnapshot) return;
        selectedSquare = null;
        legalDestinations = [];
        applyState(state);
    } catch (e) { /* ignore network hiccups */ }
}

// ================= BOARD =================

function renderBoard(fen, state) {
    const boardDiv = document.getElementById('board');
    boardDiv.innerHTML = '';

    // Build 64 squares in standard order (a8 → h1)
    const squares = [];
    fen.split(' ')[0].split('/').forEach(rank => {
        for (const ch of rank) {
            if (isNaN(ch)) squares.push(ch);
            else for (let i = 0; i < parseInt(ch); i++) squares.push(null);
        }
    });

    // Black sees the board rotated 180° (black pieces at the bottom)
    const order = squares.map((_, i) => i);
    if (state?.my_color === 'black') order.reverse();

    order.forEach(i => createSquare(boardDiv, squares[i], i, state));
}

function createSquare(container, piece, index, state) {
    const square = document.createElement('div');
    square.className = 'square';

    const squareName = getSquareName(index);
    square.dataset.square = squareName;

    // Colour depends on the actual square, so it stays correct when flipped
    const file = index % 8;
    const rank = 8 - Math.floor(index / 8);
    square.classList.add((file + rank) % 2 === 1 ? 'black-sq' : 'white-sq');

    const frozen = state?.frozen_squares?.[squareName];
    const shielded = state?.shielded_squares?.[squareName];
    if (frozen) square.classList.add('frozen-sq');
    if (shielded) square.classList.add('shielded-sq');

    if (piece) {
        const img = document.createElement('img');
        img.src = getPieceImageSrc(piece);
        img.className = 'piece';
        img.draggable = false;
        square.appendChild(img);

        if (frozen) {
            const o = document.createElement('div');
            o.className = 'frozen-overlay';
            o.textContent = '❄️';
            square.appendChild(o);
        }
        if (shielded) {
            const o = document.createElement('div');
            o.className = 'shield-overlay';
            o.textContent = '🛡️';
            square.appendChild(o);
        }
    }

    square.addEventListener('click', () => handleSquareClick(squareName));
    container.appendChild(square);
}

// ================= CLICK HANDLER =================

async function handleSquareClick(squareName) {
    const state = gameState;
    if (!state) return;

    if (state.online && state.my_color === 'spectator') return;
    if (!canAct(state)) {
        if (state.online && !state.opponent_joined) {
            showNotification('⏳ Waiting for opponent to join', '#3498db', 1500);
        } else {
            showNotification("⏳ Opponent's turn", '#3498db', 1200);
        }
        return;
    }

    // Pending power-up → resolve it
    if (state.pending_powerup) {
        const result = await resolvePowerup(squareName);
        if (result.error) {
            showNotification(`❌ ${result.error}`, '#e74c3c', 2000);
            return;
        }
        applyState(result);
        return;
    }

    if (!selectedSquare) {
        const moves = await fetchLegalMoves(squareName);
        if (moves.length > 0) {
            selectedSquare = squareName;
            legalDestinations = moves;
            highlightSquares();
        } else if (state.mode_info?.uses_dice && state.dice_roll) {
            showNotification(`🎲 You rolled a ${state.dice_roll.piece}. Move a ${state.dice_roll.piece}.`, '#e74c3c', 1500);
        }
    } else {
        if (legalDestinations.includes(squareName)) {
            const newState = await sendMove(selectedSquare, squareName);
            if (newState?.error) {
                showNotification(`❌ ${newState.error}`, '#e74c3c', 2000);
            } else if (newState) {
                applyState(newState);
            }
        }
        selectedSquare = null;
        legalDestinations = [];
        removeHighlights();
    }
}

// ================= HIGHLIGHTS =================

function highlightSquares() {
    removeHighlights();
    const selectedEl = document.querySelector(`[data-square="${selectedSquare}"]`);
    if (selectedEl) selectedEl.classList.add('selected');
    legalDestinations.forEach(sq => {
        const el = document.querySelector(`[data-square="${sq}"]`);
        if (el) el.classList.add('legal-move');
    });
}

function removeHighlights() {
    document.querySelectorAll('.square').forEach(sq => {
        sq.classList.remove('selected', 'legal-move');
    });
}

// ================= INIT =================

fetchGameState().then(() => {
    if (gameState?.online) setInterval(pollState, 1000);
});