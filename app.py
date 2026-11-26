from flask import Flask, jsonify, render_template, request, redirect, url_for, session, Response, g
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import time
import threading
import chess
import uuid
import random
import socket
import io
import qrcode
import qrcode.image.svg

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
# Correct scheme/host when running behind a proxy or tunnel (Render, Cloudflare, etc.)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

games = {}

def get_lan_ip():
    """Best-guess LAN IP so the other device can connect."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def get_player_id():
    if "pid" not in session:
        session["pid"] = uuid.uuid4().hex
    return session["pid"]


def claim_seat(game):
    """Assign this browser a seat (called when the game page loads)."""
    if not game["online"]:
        return "both"
    pid = get_player_id()
    p = game["players"]
    if p["white"] == pid:
        return "white"
    if p["black"] == pid:
        return "black"
    if p["white"] is None:
        p["white"] = pid
        return "white"
    if p["black"] is None:
        p["black"] = pid
        return "black"
    return "spectator"


def my_color(game):
    """Look up this browser's seat without claiming one."""
    if not game["online"]:
        return "both"
    pid = session.get("pid")
    if pid and game["players"]["white"] == pid:
        return "white"
    if pid and game["players"]["black"] == pid:
        return "black"
    return "spectator"


def check_turn(game):
    """Returns an error response if this player may not act right now."""
    if not game["online"]:
        return None
    if game["players"]["black"] is None:
        return jsonify({"error": "Waiting for opponent to join"}), 403
    if my_color(game) != game["turn"]:
        return jsonify({"error": "It's not your turn"}), 403
    return None

# ─── Power-up definitions ───────────────────────────────────────────────────
POWERUPS = {
    "freeze": {
        "id": "freeze",
        "name": "Freeze",
        "icon": "❄️",
        "description": "Freeze one enemy piece for 2 turns (it can't move)",
        "color": "#3498db"
    },
    "shield": {
        "id": "shield",
        "name": "Shield",
        "icon": "🛡️",
        "description": "Protect one of your pieces from capture for 2 turns",
        "color": "#27ae60"
    },
    "teleport": {
        "id": "teleport",
        "name": "Teleport",
        "icon": "🌀",
        "description": "Move any of your pieces to any empty square",
        "color": "#9b59b6"
    },
    "bomb": {
        "id": "bomb",
        "name": "Bomb",
        "icon": "💣",
        "description": "Destroy one enemy piece (not the King)",
        "color": "#e74c3c"
    },
    "resurrect": {
        "id": "resurrect",
        "name": "Resurrect",
        "icon": "✨",
        "description": "Bring back your last captured piece as a Pawn",
        "color": "#e91e63"
    }
}

POWERUP_KEYS = list(POWERUPS.keys())

DICE_FACES = {
    1: {"piece_type": chess.PAWN,   "name": "Pawn",   "icon": "♟"},
    2: {"piece_type": chess.KNIGHT, "name": "Knight", "icon": "♞"},
    3: {"piece_type": chess.BISHOP, "name": "Bishop", "icon": "♝"},
    4: {"piece_type": chess.ROOK,   "name": "Rook",   "icon": "♜"},
    5: {"piece_type": chess.QUEEN,  "name": "Queen",  "icon": "♛"},
    6: {"piece_type": chess.KING,   "name": "King",   "icon": "♚"},
}

GAME_MODES = {
    "classic": {
        "name": "Classic Chess",
        "description": "Standard chess rules",
        "uses_dice": False,
        "uses_powerups": False
    },
    "dice": {
        "name": "Dice Chess",
        "description": "Roll dice to determine which pieces can move",
        "uses_dice": True,
        "uses_powerups": False
    },
    "powerup": {
        "name": "Power-Up Chess",
        "description": "Earn and unleash game-changing power-ups every few moves",
        "uses_dice": False,
        "uses_powerups": True
    },
    "super": {
        "name": "SuperChess",
        "description": "Dice + Cards + Power-Ups for maximum chaos",
        "uses_dice": True,
        "uses_powerups": True
    }
}


def create_game(mode="classic", online=False, public=False):
    game_id = str(uuid.uuid4())[:8]
    board = chess.Board()
    games[game_id] = {
        "online": online,
        "public": public,
        "created": time.time(),
        "last_active": time.time(),
        "board_obj": board,
        "board": board.fen(),
        "turn": "white",
        "mode": mode,
        "status": "active",
        "dice_roll": None,
        "dice_used": False,
        "hands": {"white": [], "black": []},
        "deck": [],
        "effects": {"white": {}, "black": {}},
        "players": {"white": None, "black": None},
        # Power-up state
        "powerup_hands": {"white": [], "black": []},
        "move_count": {"white": 0, "black": 0},
        "frozen_squares": {},      # square -> turns_remaining
        "shielded_squares": {},    # square -> turns_remaining
        "captured_pieces": {"white": [], "black": []},  # pieces captured FROM each side
        "pending_powerup": None,   # {"type": ..., "player": ..., "stage": ...}
        "last_event": None,        # for frontend notifications
    }
    roll_dice(games[game_id])   # <-- new: white's first roll
    return game_id


def award_powerup(game, player):
    """Give a random power-up to the player (max 3 in hand)."""
    hand = game["powerup_hands"][player]
    if len(hand) < 3:
        pu = random.choice(POWERUP_KEYS)
        hand.append(pu)
        return pu
    return None


def tick_effects(game):
    """Decrement freeze/shield counters after each full round."""
    to_remove_frozen = [sq for sq, turns in game["frozen_squares"].items() if turns <= 1]
    for sq in to_remove_frozen:
        del game["frozen_squares"][sq]
    for sq in game["frozen_squares"]:
        game["frozen_squares"][sq] -= 1

    to_remove_shielded = [sq for sq, turns in game["shielded_squares"].items() if turns <= 1]
    for sq in to_remove_shielded:
        del game["shielded_squares"][sq]
    for sq in game["shielded_squares"]:
        game["shielded_squares"][sq] -= 1

def uses_dice(game):
    return GAME_MODES[game["mode"]].get("uses_dice", False)


def playable_moves(game):
    """Legal moves, minus frozen pieces and captures of shielded pieces."""
    board = game["board_obj"]
    moves = []
    for m in board.legal_moves:
        if chess.SQUARE_NAMES[m.from_square] in game["frozen_squares"]:
            continue
        if chess.SQUARE_NAMES[m.to_square] in game["shielded_squares"]:
            continue
        moves.append(m)
    return moves


def roll_dice(game):
    """Roll for the current player. Re-rolls until the piece type can actually move."""
    if not uses_dice(game):
        return
    board = game["board_obj"]
    available = {board.piece_type_at(m.from_square) for m in playable_moves(game)}
    if not available:
        game["dice_roll"] = None
        return

    rerolls = 0
    while True:
        value = random.randint(1, 6)
        if DICE_FACES[value]["piece_type"] in available:
            break
        rerolls += 1

    face = DICE_FACES[value]
    game["dice_roll"] = {
        "value": value,
        "piece": face["name"],
        "icon": face["icon"],
        "rerolls": rerolls,
    }


def ensure_dice_valid(game):
    """After a power-up changes the board, re-roll if the current roll became unplayable."""
    if not uses_dice(game):
        return
    roll = game["dice_roll"]
    if roll is None:
        roll_dice(game)
        return
    pt = DICE_FACES[roll["value"]]["piece_type"]
    board = game["board_obj"]
    if not any(board.piece_type_at(m.from_square) == pt for m in playable_moves(game)):
        roll_dice(game)

# ─── Routes ─────────────────────────────────────────────────────────────────

games_lock = threading.RLock()

@app.before_request
def _before():
    if request.path.startswith('/api/'):
        games_lock.acquire()
        g.locked = True
    gid = (request.view_args or {}).get('game_id')
    if gid in games:
        games[gid]["last_active"] = time.time()

@app.teardown_request
def _after(exc):
    if g.pop('locked', False):
        games_lock.release()
def cleanup_loop():
    while True:
        time.sleep(300)
        cutoff = time.time() - 2 * 3600
        for gid, gm in list(games.items()):
            if gm["last_active"] < cutoff:
                games.pop(gid, None)

threading.Thread(target=cleanup_loop, daemon=True).start()

@app.route('/')
def lobby():
    return render_template('lobby.html', game_modes=GAME_MODES)


@app.route('/create-game', methods=['POST'])
def create_game_route():
    mode = request.form.get('mode', 'classic')
    if mode not in GAME_MODES:
        mode = 'classic'
    online = request.form.get('online') == '1'
    public = online and request.form.get('public') == '1'
    game_id = create_game(mode, online, public)
    return redirect(url_for('game', game_id=game_id))

def build_join_url(game_id):
    public = os.environ.get("PUBLIC_URL")        # optional manual override
    if public:
        return f"{public.rstrip('/')}/game/{game_id}"
    host = request.host
    name, _, port = host.partition(':')
    if name in ("localhost", "127.0.0.1"):       # other devices can't use these
        host = get_lan_ip() + (f":{port}" if port else "")
    return f"{request.scheme}://{host}/game/{game_id}"

@app.route('/game/<game_id>')
def game(game_id):
    if game_id not in games:
        return redirect(url_for('lobby'))
    claim_seat(games[game_id])
    return render_template('index.html', game_id=game_id, join_url=build_join_url(game_id))


@app.route('/qr/<game_id>.svg')
def qr_code(game_id):
    if game_id not in games:
        return "Game not found", 404
    img = qrcode.make(
        build_join_url(game_id),
        image_factory=qrcode.image.svg.SvgPathImage,
        box_size=10,
        border=2,
    )
    buf = io.BytesIO()
    img.save(buf)
    return Response(buf.getvalue(), mimetype="image/svg+xml")


@app.route('/api/modes')
def get_modes():
    return jsonify(GAME_MODES)


@app.route('/api/state/<game_id>')
def get_state(game_id):
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404
    return jsonify(_full_state(games[game_id]))
    
    game = games[game_id]
    # make_move, activate_powerup, resolve_powerup, cancel_powerup:
    err = check_turn(game)
    if err:
        return err
    return jsonify({
        "board": game["board"],
        "turn": game["turn"],
        "mode": game["mode"],
        "status": game["status"],
        "dice_roll": game["dice_roll"],
        "dice_used": game["dice_used"],
        "hands": game["hands"],
        "effects": game["effects"],
        "mode_info": GAME_MODES[game["mode"]],
        "powerup_hands": game["powerup_hands"],
        "frozen_squares": game["frozen_squares"],
        "shielded_squares": game["shielded_squares"],
        "pending_powerup": game["pending_powerup"],
        "last_event": game["last_event"],
        "powerup_defs": POWERUPS,
    })


@app.route('/api/legal-moves/<game_id>')
def get_legal_moves(game_id):
    if game_id not in games:
        return jsonify([])
    game = games[game_id]

    if game.get("online") and (game["players"]["black"] is None or my_color(game) != game["turn"]):
        return jsonify([])

    board = game["board_obj"]
    square_name = request.args.get('square')
    if not square_name:
        return jsonify([])

    try:
        from_square = chess.SQUARE_NAMES.index(square_name)
    except ValueError:
        return jsonify([])

    piece = board.piece_at(from_square)
    current_turn_color = chess.WHITE if game["turn"] == "white" else chess.BLACK
    if not piece or piece.color != current_turn_color:
        return jsonify([])

    # Dice: only the rolled piece type may move
    if uses_dice(game):
        roll = game["dice_roll"]
        if roll is None or piece.piece_type != DICE_FACES[roll["value"]]["piece_type"]:
            return jsonify([])

    moves = [chess.SQUARE_NAMES[m.to_square]
             for m in playable_moves(game)
             if m.from_square == from_square]
    return jsonify(moves)


@app.route('/api/move/<game_id>', methods=['POST'])
def make_move(game_id):
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404
    game = games[game_id]           # 1. define game first
    err = check_turn(game)          # 2. then check the turn
    if err:
        return err
    if game_over(game):
        return jsonify({"error": "The game is over"}), 400
    board = game["board_obj"]
    data = request.json
    move_uci = f"{data.get('from')}{data.get('to')}"
    game["last_event"] = None

    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        return jsonify({"error": "Invalid move format"}), 400

    piece = board.piece_at(move.from_square)
    # Auto-promote
    if piece and piece.piece_type == chess.PAWN:
        if (board.turn == chess.WHITE and chess.square_rank(move.to_square) == 7) or \
                (board.turn == chess.BLACK and chess.square_rank(move.to_square) == 0):
            move = chess.Move(move.from_square, move.to_square, promotion=chess.QUEEN)

    if move not in board.legal_moves:
        return jsonify({"error": "Illegal move"}), 400
    if uses_dice(game):
        roll = game["dice_roll"]
        if roll is None or piece.piece_type != DICE_FACES[roll["value"]]["piece_type"]:
            need = roll["piece"] if roll else "?"
            return jsonify({"error": f"You rolled a {need}. Move a {need}."}), 400
    
    if chess.SQUARE_NAMES[move.from_square] in game["frozen_squares"]:
        return jsonify({"error": "This piece is frozen and cannot move"}), 400
    # Track captured piece
    captured = board.piece_at(move.to_square)
    if captured:
        cap_owner = "white" if captured.color == chess.WHITE else "black"
        game["captured_pieces"][cap_owner].append(captured.symbol())

    # Remove freeze/shield from moved piece's new square
    dest_name = chess.SQUARE_NAMES[move.to_square]
    if dest_name in game["shielded_squares"]:
        del game["shielded_squares"][dest_name]
    # Also move shield/freeze if piece moved away
    src_name = chess.SQUARE_NAMES[move.from_square]
    if src_name in game["shielded_squares"]:
        game["shielded_squares"][dest_name] = game["shielded_squares"].pop(src_name)
    if src_name in game["frozen_squares"]:
        del game["frozen_squares"][src_name]

    board.push(move)

    current_player = game["turn"]
    game["move_count"][current_player] = game["move_count"].get(current_player, 0) + 1
    game["board"] = board.fen()

    uses_powerups = GAME_MODES[game["mode"]].get("uses_powerups", False)

    # Switch turn
    game["turn"] = "black" if current_player == "white" else "white"
    tick_effects(game)

    # Award power-up every 3 moves per player
    if uses_powerups and game["move_count"][current_player] % 3 == 0:
        awarded = award_powerup(game, current_player)
        if awarded:
            game["last_event"] = {"type": "powerup_awarded", "player": current_player, "powerup": awarded}

    _update_status(game, board)
    roll_dice(game)                      # <-- new: next player's roll
    return jsonify(_full_state(game))



# ─── Power-up activation ────────────────────────────────────────────────────

@app.route('/api/powerup/activate/<game_id>', methods=['POST'])
def activate_powerup(game_id):
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404
    game = games[game_id]
    err = check_turn(game)
    if err:
        return err
    data = request.json
    player = game["turn"]
    pu_id = data.get("powerup_id")
    game["last_event"] = None

    hand = game["powerup_hands"][player]
    if pu_id not in hand:
        return jsonify({"error": "Power-up not in hand"}), 400

    uses_powerups = GAME_MODES[game["mode"]].get("uses_powerups", False)
    if not uses_powerups:
        return jsonify({"error": "Power-ups not enabled in this mode"}), 400

    # Instant power-ups

    if pu_id == "resurrect":
        caps = game["captured_pieces"][player]
        if not caps:
            return jsonify({"error": "No captured pieces to resurrect"}), 400
        hand.remove(pu_id)
        # Find an empty back-rank square to place a pawn
        board = game["board_obj"]
        back_rank = 0 if player == "white" else 7
        placed = False
        for file in range(8):
            sq = chess.square(file, back_rank)
            if board.piece_at(sq) is None:
                color = chess.WHITE if player == "white" else chess.BLACK
                board.set_piece_at(sq, chess.Piece(chess.PAWN, color))
                game["board"] = board.fen()
                placed = True
                game["last_event"] = {"type": "powerup_used", "powerup": pu_id, "player": player,
                                       "square": chess.SQUARE_NAMES[sq]}
                break
        if not placed:
            return jsonify({"error": "No empty square on back rank"}), 400
        return jsonify(_full_state(game))

    # Target-requiring power-ups → set pending state, frontend will pick target
    if pu_id in ("freeze", "shield", "teleport", "bomb"):
        game["pending_powerup"] = {"type": pu_id, "player": player, "stage": "select_target"}
        game["last_event"] = {"type": "powerup_pending", "powerup": pu_id}
        return jsonify(_full_state(game))

    return jsonify({"error": "Unknown power-up"}), 400


@app.route('/api/powerup/resolve/<game_id>', methods=['POST'])
def resolve_powerup(game_id):
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404
    game = games[game_id]
    err = check_turn(game)
    if err:
        return err
    data = request.json
    pending = game["pending_powerup"]
    if not pending:
        return jsonify({"error": "No pending power-up"}), 400

    player = pending["player"]
    pu_id = pending["type"]
    target_square = data.get("square")
    board = game["board_obj"]
    game["last_event"] = None

    if pu_id == "freeze":
        # Freeze enemy piece on target_square
        piece = board.piece_at(chess.SQUARE_NAMES.index(target_square))
        enemy_color = chess.BLACK if player == "white" else chess.WHITE
        if not piece or piece.color != enemy_color:
            return jsonify({"error": "Must target an enemy piece"}), 400
        game["frozen_squares"][target_square] = 2
        game["powerup_hands"][player].remove(pu_id)
        game["pending_powerup"] = None
        game["last_event"] = {"type": "powerup_used", "powerup": pu_id, "player": player, "square": target_square}

    elif pu_id == "shield":
        # Shield own piece
        piece = board.piece_at(chess.SQUARE_NAMES.index(target_square))
        own_color = chess.WHITE if player == "white" else chess.BLACK
        if not piece or piece.color != own_color:
            return jsonify({"error": "Must target your own piece"}), 400
        game["shielded_squares"][target_square] = 2
        game["powerup_hands"][player].remove(pu_id)
        game["pending_powerup"] = None
        game["last_event"] = {"type": "powerup_used", "powerup": pu_id, "player": player, "square": target_square}

    elif pu_id == "bomb":
        # Destroy enemy piece (not King)
        sq_idx = chess.SQUARE_NAMES.index(target_square)
        piece = board.piece_at(sq_idx)
        enemy_color = chess.BLACK if player == "white" else chess.WHITE
        if not piece or piece.color != enemy_color or piece.piece_type == chess.KING:
            return jsonify({"error": "Must target an enemy non-King piece"}), 400
        board.remove_piece_at(sq_idx)
        game["board"] = board.fen()
        # Remove any shields/freeze on that square
        game["shielded_squares"].pop(target_square, None)
        game["frozen_squares"].pop(target_square, None)
        game["powerup_hands"][player].remove(pu_id)
        game["pending_powerup"] = None
        game["last_event"] = {"type": "powerup_used", "powerup": pu_id, "player": player, "square": target_square}

    elif pu_id == "teleport":
        if pending["stage"] == "select_target":
            # First click: select which of your pieces to teleport
            sq_idx = chess.SQUARE_NAMES.index(target_square)
            piece = board.piece_at(sq_idx)
            own_color = chess.WHITE if player == "white" else chess.BLACK
            if not piece or piece.color != own_color or piece.piece_type == chess.KING:
                return jsonify({"error": "Must target your own non-King piece"}), 400
            pending["stage"] = "select_destination"
            pending["piece_square"] = target_square
            pending["piece_type"] = piece.piece_type
            return jsonify(_full_state(game))
        elif pending["stage"] == "select_destination":
            # Second click: pick empty destination
            sq_idx = chess.SQUARE_NAMES.index(target_square)
            if board.piece_at(sq_idx) is not None:
                return jsonify({"error": "Destination must be empty"}), 400
            src_idx = chess.SQUARE_NAMES.index(pending["piece_square"])
            piece = board.piece_at(src_idx)
            board.remove_piece_at(src_idx)
            board.set_piece_at(sq_idx, piece)
            game["board"] = board.fen()
            # Move shield if applicable
            if pending["piece_square"] in game["shielded_squares"]:
                game["shielded_squares"][target_square] = game["shielded_squares"].pop(pending["piece_square"])
            game["powerup_hands"][player].remove(pu_id)
            game["pending_powerup"] = None
            game["last_event"] = {"type": "powerup_used", "powerup": pu_id, "player": player,
                                   "from": pending["piece_square"], "square": target_square}

    _update_status(game, board)
    return jsonify(_full_state(game))


@app.route('/api/powerup/cancel/<game_id>', methods=['POST'])
def cancel_powerup(game_id):
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404
    game = games[game_id]
    err = check_turn(game)
    if err:
        return err
    game["pending_powerup"] = None
    game["last_event"] = None
    return jsonify(_full_state(game))


# ─── Helpers ────────────────────────────────────────────────────────────────

def _update_status(game, board):
    if board.is_checkmate():
        winner = "black" if board.turn == chess.WHITE else "white"
        game["status"] = f"checkmate - {winner} wins"
    elif board.is_stalemate() or board.is_insufficient_material() or board.can_claim_draw():
        game["status"] = "draw"
    elif board.is_check():
        game["status"] = "check"
    else:
        game["status"] = "active"

def game_over(game):
    return game["status"].startswith("checkmate") or game["status"] == "draw"


def _full_state(game):
    return {
        "board": game["board"],
        "turn": game["turn"],
        "mode": game["mode"],
        "status": game["status"],
        "dice_roll": game["dice_roll"],
        "dice_used": game["dice_used"],
        "hands": game["hands"],
        "effects": game["effects"],
        "mode_info": GAME_MODES[game["mode"]],
        "powerup_hands": game["powerup_hands"],
        "frozen_squares": game["frozen_squares"],
        "shielded_squares": game["shielded_squares"],
        "pending_powerup": game["pending_powerup"],
        "last_event": game["last_event"],
        "powerup_defs": POWERUPS,
        "captured_pieces": game["captured_pieces"],
        "online": game["online"],
        "my_color": my_color(game),
        "opponent_joined": (not game["online"]) or game["players"]["black"] is not None,
    }

@app.route('/join')
def join_by_code():
    code = request.args.get('code', '').strip().lower()
    if code in games:
        return redirect(url_for('game', game_id=code))
    return redirect(url_for('lobby'))


@app.route('/api/open-games')
def open_games():
    out = []
    for gid, gm in list(games.items()):
        if (gm["online"] and gm.get("public")
                and gm["players"]["white"] is not None
                and gm["players"]["black"] is None):
            out.append({
                "id": gid,
                "mode": GAME_MODES[gm["mode"]]["name"],
                "age": int(time.time() - gm["created"]),
            })
    return jsonify(out)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    cert, key = "192.168.1.23+2.pem", "192.168.1.23+2-key.pem"
    ssl_ctx = (cert, key) if os.path.exists(cert) and os.path.exists(key) else None
    scheme = "https" if ssl_ctx else "http"
    print(f"\n  Other devices on your network: {scheme}://{get_lan_ip()}:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, use_debugger=False, ssl_context=ssl_ctx)