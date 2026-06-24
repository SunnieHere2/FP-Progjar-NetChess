import socket
import threading
import json
import os
import time
import chess

HOST = "0.0.0.0"
PORT = 5555
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
LEADERBOARD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leaderboard.json")

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((HOST, PORT))
server.listen()

rooms = {}
rooms_lock = threading.Lock()

connected_clients = []
clients_lock = threading.Lock()

leaderboard_lock = threading.Lock()


def load_leaderboard():
    if os.path.exists(LEADERBOARD_FILE):
        try:
            with open(LEADERBOARD_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_leaderboard(data):
    try:
        with open(LEADERBOARD_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        print("Failed to save leaderboard:", e)


leaderboard = load_leaderboard()


def record_win(username):
    if not username:
        return
    with leaderboard_lock:
        leaderboard[username] = leaderboard.get(username, 0) + 1
        save_leaderboard(leaderboard)


def get_leaderboard_list():
    with leaderboard_lock:
        ranked = sorted(leaderboard.items(), key=lambda kv: kv[1], reverse=True)
        return [{"username": name, "wins": wins} for name, wins in ranked[:50]]


print(f"Server running on {HOST}:{PORT}")


def send_json(client, data):
    client.send(json.dumps(data).encode())


def broadcast_room_list(exclude=None):
    with rooms_lock:
        room_list = [
            {
                "id": rid,
                "name": r["name"],
                "players": len(r["clients"]),
                "spectators": len(r["spectators"]),
                "ongoing": r["started"],
                "time_control": r["time_control"]
            }
            for rid, r in rooms.items()
        ]

    msg = json.dumps({
        "type": "room_list",
        "rooms": room_list
    }).encode()

    with rooms_lock:
        all_clients = []

        for r in rooms.values():
            all_clients.extend(r["clients"])
            all_clients.extend(r["spectators"])

    for c in all_clients:
        if c != exclude:
            try:
                c.send(msg)
            except:
                pass

    if exclude:
        try:
            exclude.send(msg)
        except:
            pass


def broadcast_leaderboard():
    msg = json.dumps({
        "type": "leaderboard",
        "data": get_leaderboard_list()
    }).encode()

    with clients_lock:
        targets = list(connected_clients)

    for c in targets:
        try:
            c.send(msg)
        except:
            pass


def handle_client(client):
    room_id = None

    while True:
        try:
            raw = b""
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    raise ConnectionError
                raw += chunk
                try:
                    data = json.loads(raw.decode())
                    break
                except json.JSONDecodeError:
                    continue

            if data["type"] == "get_rooms":
                with rooms_lock:
                    room_list = [
                        {
                            "id": rid,
                            "name": r["name"],
                            "players": len(r["clients"]),
                            "spectators": len(r["spectators"]),
                            "ongoing": r["started"],
                            "time_control": r["time_control"]
                        }
                        for rid, r in rooms.items()
                    ]
                send_json(client, {"type": "room_list", "rooms": room_list})

            elif data["type"] == "get_leaderboard":
                send_json(client, {"type": "leaderboard", "data": get_leaderboard_list()})

            elif data["type"] == "create_room":
                tc = data.get("time_control", 5)
                if tc not in (1, 5, 10):
                    tc = 5
                with rooms_lock:
                    rid = data["room_id"]
                    rooms[rid] = {
                        "name": data["name"],
                        "clients": [client],
                        "spectators": [],
                        "host": client,
                        "started": False,
                        "fen": None,
                        "usernames": {client: data.get("username", "Player")},
                        "time_control": tc,
                        "clocks": None,
                        "turn": "white",
                        "last_tick": None,
                        "game_over": False,
                        "history": []
                    }
                room_id = rid
                send_json(client, {"type": "color", "color": "white"})
                send_json(client, {"type": "waiting"})
                broadcast_room_list()

            elif data["type"] == "join_room":
                rid = data["room_id"]
                with rooms_lock:
                    room = rooms.get(rid)
                    if room and len(room["clients"]) < 2:
                        room["clients"].append(client)
                        room_id = rid
                        joined = True
                    else:
                        joined = False

                if joined:
                    rooms[rid]["started"] = True
                    rooms[rid]["fen"] = START_FEN
                    rooms[rid]["usernames"][client] = data.get("username", "Player")

                    minutes = rooms[rid]["time_control"]
                    total_seconds = minutes * 60
                    rooms[rid]["clocks"] = {"white": total_seconds, "black": total_seconds}
                    rooms[rid]["turn"] = "white"
                    rooms[rid]["last_tick"] = time.time()

                    send_json(client, {"type": "color", "color": "black"})
                    host = rooms[rid]["host"]
                    start_payload = {
                        "type": "start",
                        "time_control": minutes,
                        "clocks": rooms[rid]["clocks"]
                    }
                    send_json(host, start_payload)
                    send_json(client, start_payload)
                    broadcast_room_list()

            elif data["type"] == "spectate":
                    print("SPECTATE REQUEST", data["room_id"])
                    rid = data["room_id"]

                    with rooms_lock:
                        room = rooms.get(rid)
                        print("ROOM =", room)
                        if room:
                            print("STARTED =", room["started"])

                        if room and room["started"]:
                            room["spectators"].append(client)

                            room_id = rid

                            send_json(client, {
                                "type": "spectator_start",
                                "fen": room["fen"] or START_FEN,
                                "history": room["history"]
                            })

                            print("SENT spectator_start")

                        else:

                            send_json(client, {
                                "type": "error",
                                "message": "Match not available"
                            })

            elif data["type"] == "move":
                if room_id:

                    with rooms_lock:
                        room = rooms.get(room_id)

                    if room and not room.get("game_over"):

                        room["fen"] = data.get("fen")

                        # Deduct elapsed time from the side that just moved, switch the clock
                        clocks_payload = None
                        if room.get("clocks") is not None:
                            now = time.time()
                            moving_color = room["turn"]
                            elapsed = now - room["last_tick"]
                            room["clocks"][moving_color] = max(0, room["clocks"][moving_color] - elapsed)

                            room["history"].append({
                                "move": data.get("move"),
                                "color": moving_color,
                                "fen_after": data.get("fen"),
                                "move_number": len(room["history"]) + 1
                            })

                            room["turn"] = "black" if moving_color == "white" else "white"
                            room["last_tick"] = now
                            clocks_payload = dict(room["clocks"])
                            data["clocks"] = clocks_payload
                            data["turn"] = room["turn"]

                        for c in room["clients"]:
                            if c != client:
                                send_json(c, data)

                        for s in room["spectators"]:
                            send_json(s, data)

                        # Sync the clock back to the player who just moved too
                        if clocks_payload is not None:
                            send_json(client, {
                                "type": "clock_sync",
                                "clocks": clocks_payload,
                                "turn": room["turn"]
                            })

                        # Server-side checkmate check (authoritative) for the leaderboard
                        fen = data.get("fen")
                        if fen and len(room["clients"]) == 2:
                            try:
                                board_check = chess.Board(fen)
                                if board_check.is_checkmate():
                                    # side to move (board_check.turn) is the one checkmated/loses
                                    winner_idx = 1 if board_check.turn == chess.WHITE else 0
                                    winner_client = room["clients"][winner_idx]
                                    winner_name = room["usernames"].get(winner_client, "Player")
                                    room["game_over"] = True
                                    record_win(winner_name)
                                    broadcast_leaderboard()
                            except ValueError:
                                pass

            elif data["type"] == "chat":

                if room_id:

                    with rooms_lock:
                        room = rooms.get(room_id)

                    if room:

                        recipients = (
                            room["clients"]
                            + room["spectators"]
                        )

                        for c in recipients:
                            if c != client:
                                send_json(c, data)

        except Exception as e:
            print("Client error:", e)
            break

    if room_id:
        with rooms_lock:
            room = rooms.get(room_id)

            if room:

                was_player = client in room["clients"]

                room["clients"] = [
                    c for c in room["clients"]
                    if c != client
                ]

                room["spectators"] = [
                    s for s in room["spectators"]
                    if s != client
                ]

                if not room["clients"] and not room["spectators"]:
                    del rooms[room_id]

                elif was_player:
                    room["game_over"] = True
                    for c in room["clients"]:
                        try:
                            send_json(c, {
                                "type": "opponent_left"
                            })
                        except:
                            pass

        broadcast_room_list()

    with clients_lock:
        if client in connected_clients:
            connected_clients.remove(client)

    client.close()


def clock_monitor():
    """Background watchdog: ends a match on timeout even if no move arrives."""
    while True:
        time.sleep(1)

        with rooms_lock:
            room_snapshot = list(rooms.items())

        for rid, room in room_snapshot:
            if room.get("game_over") or room.get("clocks") is None:
                continue
            if len(room["clients"]) != 2:
                continue

            turn_color = room["turn"]
            now = time.time()
            remaining = room["clocks"][turn_color] - (now - room["last_tick"])

            if remaining > 0:
                continue

            with rooms_lock:
                room = rooms.get(rid)
                if not room or room.get("game_over") or room.get("clocks") is None:
                    continue
                room["game_over"] = True
                room["clocks"][turn_color] = 0

            loser_color = turn_color
            winner_color = "black" if loser_color == "white" else "white"
            winner_idx = 0 if winner_color == "white" else 1
            winner_client = room["clients"][winner_idx]
            winner_name = room["usernames"].get(winner_client, "Player")

            record_win(winner_name)
            broadcast_leaderboard()

            timeout_msg = {
                "type": "timeout",
                "loser_color": loser_color,
                "winner_color": winner_color,
                "winner": winner_name
            }

            for c in room["clients"]:
                try:
                    send_json(c, timeout_msg)
                except:
                    pass
            for s in room["spectators"]:
                try:
                    send_json(s, timeout_msg)
                except:
                    pass


threading.Thread(target=clock_monitor, daemon=True).start()

while True:
    client, addr = server.accept()
    print("Connected:", addr)
    with clients_lock:
        connected_clients.append(client)
    threading.Thread(target=handle_client, args=(client,), daemon=True).start()