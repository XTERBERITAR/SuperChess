from flask import Flask, jsonify, render_template, request, redirect, url_for, session
import chess
import uuid
import random

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'

games = {}

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


def create_game(mode="classic"):
    game_id = str(uuid.uuid4())[:8]
    board = chess.Board()
    games[game_id] = {
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


# ─── Routes ─────────────────────────────────────────────────────────────────

@app.route('/')
def lobby():
    return render_template('lobby.html', game_modes=GAME_MODES)


@app.route('/create-game', methods=['POST'])
def create_game_route():
    mode = request.form.get('mode', 'classic')
    if mode not in GAME_MODES:
        mode = 'classic'
    game_id = create_game(mode)
    return redirect(url_for('game', game_id=game_id))


@app.route('/game/<game_id>')
def game(game_id):
    if game_id not in games:
        return redirect(url_for('lobby'))
    return render_template('index.html', game_id=game_id)


@app.route('/api/modes')
def get_modes():
    return jsonify(GAME_MODES)


@app.route('/api/state/<game_id>')
def get_state(game_id):
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404
    game = games[game_id]
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

    # Frozen pieces can't move
    if square_name in game["frozen_squares"]:
        return jsonify([])

    moves = []
    for m in board.legal_moves:
        if m.from_square == from_square:
            dest = chess.SQUARE_NAMES[m.to_square]
            # Can't capture shielded pieces (unless it's the pending_powerup bomb action)
            if dest in game["shielded_squares"]:
                continue
            moves.append(dest)

    return jsonify(moves)


@app.route('/api/move/<game_id>', methods=['POST'])
def make_move(game_id):
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404
    game = games[game_id]
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
    return jsonify(_full_state(game))


# ─── Power-up activation ────────────────────────────────────────────────────

@app.route('/api/powerup/activate/<game_id>', methods=['POST'])
def activate_powerup(game_id):
    if game_id not in games:
        return jsonify({"error": "Game not found"}), 404
    game = games[game_id]
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
    }


if __name__ == '__main__':
    app.run(debug=True, port=5001)
