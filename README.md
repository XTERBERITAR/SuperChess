# SuperChess

A chess web app built as a University project at Providence University.

## Game modes
- **Classic** — standard chess rules
- **Dice Roll** — roll a die each turn to determine which piece type you must move
- **Power-up** — use cards with special effects instead of moving a piece

## Tech stack
- Python + Flask (backend)
- python-chess (move validation and chess logic)
- Vanilla JavaScript (frontend rendering)
- Flask-SocketIO (online multiplayer)
- Stockfish (AI opponent)

## Checkpoints
- [x] Checkpoint 1 — board renders, game state API working
- [x] Checkpoint 2 — click to move, local 2-player classic chess
- [x] Checkpoint 3 — lobby with game mode selection
- [x] Checkpoint 4 — dice roll mode
- [x] Checkpoint 5 — power-up card mode
- [x] Checkpoint 6 — online multiplayer
- [x] Checkpoint 7 — AI opponent
- [x] Checkpoint 8 — online 2 player
- Deadline 25 may presentation
- June 1 final deadline
## How to run
```bash
pip install flask python-chess
python app.py
```
Then open `http://127.0.0.1:5001` in your browser.
