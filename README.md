# ♟️ NetChess

A Python-based multiplayer chess application built with Pygame. Two players connect over a network, create or join game rooms, and play chess in real-time with live chat.

---

## Features

- 🏠 Game room lobby — create and join named rooms
- ♟️ Real-time move synchronization over TCP
- 💬 In-game chat panel
- 🔊 Sound effects for moves, captures, check, and checkmate
- 🟡 Last-move highlight and legal move indicators
- ⚠️ Check / Checkmate / Stalemate detection
- 👤 Custom username before entering lobby

---

## Requirements

- Python 3.10+
- [pygame](https://pypi.org/project/pygame/) — `pip install pygame`
- [python-chess](https://pypi.org/project/chess/) — `pip install python-chess`
- [numpy](https://pypi.org/project/numpy/) — `pip install numpy`

Or install all at once:
```
pip install pygame python-chess numpy
```

---

## How to Run

### Same Machine (2 players, 1 computer)

**Terminal 1 — start the server:**
```
python server.py
```

**Terminal 2 — Player 1:**
```
python client.py
```

**Terminal 3 — Player 2:**
```
python client.py
```

Both clients connect to `127.0.0.1` (localhost) by default.

---

### Different Machines (2 players, 2 computers)

**On the host machine:**

1. Run the server:
   ```
   python server.py
   ```
2. Find your local IP address:
   - Windows: open CMD and run `ipconfig` → look for **IPv4 Address**
   - Example: `192.168.1.5`

**On both machines:**

Open `network.py` and change the host to the server's IP:
```python
def __init__(self, host="192.168.1.5", port=5555):
```

Then run on each machine:
```
python client.py
```

> Both machines must be on the **same Wi-Fi or local network**. If connecting over the internet, the host must forward port `5555` in their router settings.

---

## How to Play

1. Launch `client.py` on both machines
2. Enter your username
3. One player clicks **Create Room**, the other clicks **Join**
4. White moves first — click a piece, then click a destination
5. Use the chat panel on the right to talk during the match

---

## Project Structure

```
FP-Progjar-NetChess/
├── server.py        # TCP server — manages rooms and relays messages
├── client.py        # Pygame GUI client — game board, lobby, chat
├── network.py       # Socket wrapper — send/receive JSON messages
├── assets/
│   └── pieces/      # Chess piece images (.png)
└── README.md
```

---

## Team

| Name | NRP | Role |
|------|-----|------|
| Palpal Yalmialam | 5025241002 | 
| Kenzie Maheswara | 5025241001 | 
| Bismantaka Revano Dirgantara | 5025241075 | 
| Ramasyamsi Ahmad Shabri | 5025241008 | 
| Alif Muflih Jauhary | 5025241003 | 
---

*Final Project — Pemrograman Jaringan (Progjar) — Institut Teknologi Sepuluh Nopember*