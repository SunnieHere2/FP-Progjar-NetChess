# server.py — the central hub that all clients connect to.
# it manages game rooms, relays moves and chat between players,
# tracks clocks, handles disconnects/reconnects, and saves the leaderboard.
# every client gets its own thread via handle_client. a separate clock_monitor thread watches for timeouts.

import socket
import threading
import json
import os
import time
import chess

HOST = "0.0.0.0"  # listen on all network interfaces so anyone on the network can connect
PORT = 5555
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"  # standard chess starting position
LEADERBOARD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leaderboard.json")

# create the tcp server socket and start listening for connections
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # allows restarting the server quickly without "address already in use" error
server.bind((HOST, PORT))
server.listen()

# rooms dict stores all active game rooms. rooms_lock prevents two threads from editing it at the same time.
rooms = {}
rooms_lock = threading.Lock()

# connected_clients tracks every connected socket. used to broadcast the leaderboard to everyone.
connected_clients = []
clients_lock = threading.Lock()

leaderboard_lock = threading.Lock()  # prevents concurrent read/write conflicts on the leaderboard dict


# reads the leaderboard from leaderboard.json on disk and returns it as a dict {username: wins}.
# if the file doesn't exist or is corrupted, returns an empty dict instead of crashing.
def load_leaderboard():
    if os.path.exists(LEADERBOARD_FILE):
        try:
            with open(LEADERBOARD_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


# writes the current leaderboard dict to leaderboard.json so wins persist across server restarts.
def save_leaderboard(data):
    try:
        with open(LEADERBOARD_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        print("Failed to save leaderboard:", e)


leaderboard = load_leaderboard()


# adds 1 win to the given player's record and immediately saves to disk.
# called whenever a game ends by checkmate or timeout.
def record_win(username):
    if not username:
        return
    with leaderboard_lock:
        leaderboard[username] = leaderboard.get(username, 0) + 1
        save_leaderboard(leaderboard)


# returns the leaderboard as a sorted list of {username, wins} dicts, highest wins first.
# capped at top 50 players. this is what gets sent to clients when they request the leaderboard.
def get_leaderboard_list():
    with leaderboard_lock:
        ranked = sorted(leaderboard.items(), key=lambda kv: kv[1], reverse=True)
        return [{"username": name, "wins": wins} for name, wins in ranked[:50]]


print(f"Server running on {HOST}:{PORT}")


# sends a python dict to a single client as a json string over tcp.
# all server-to-client communication goes through this function.
def send_json(client, data):
    client.send(json.dumps(data).encode())


# sends the current list of all game rooms to every connected client so their lobby stays up to date.
# called whenever a room is created, joined, or a player disconnects.
# the "exclude" param ensures the triggering client also gets the update (sent last separately).
def broadcast_room_list(exclude=None):
    with rooms_lock:
        room_list = [
            {
                "id": rid,
                "name": r["name"],
                "players": (
                    (1 if r["white_client"] else 0)
                    +
                    (1 if r["black_client"] else 0)
                ),
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

            if r["white_client"]:
                all_clients.append(r["white_client"])

            if r["black_client"]:
                all_clients.append(r["black_client"])

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


# sends the updated leaderboard to every currently connected client.
# called after a game ends (checkmate or timeout) so all open leaderboard screens refresh automatically.
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


# runs in its own thread for each connected client.
# continuously reads incoming data into a buffer, splits it on newlines to get complete json messages,
# then dispatches each message to the right handler block below based on its "type" field.
# when the client disconnects (recv returns empty or throws), the cleanup block at the bottom runs.
def handle_client(client):
    room_id = None

    buffer = ""  # accumulates raw bytes until a complete newline-terminated json message is ready

    while True:
        try:
            chunk = client.recv(4096).decode()

            if not chunk:
                raise ConnectionError

            buffer += chunk

            while "\n" in buffer:

                line, buffer = buffer.split("\n", 1)

                if not line.strip():
                    continue

                data = json.loads(line)

                # process packet

                # "get_rooms": client is requesting a fresh room list (e.g. on lobby load or refresh button).
                # builds the list from current rooms and sends it only to this client.
                if data["type"] == "get_rooms":
                    with rooms_lock:
                        room_list = [
                            {
                                "id": rid,
                                "name": r["name"],
                                "players": (
                                    (1 if r["white_client"] else 0)
                                    +
                                    (1 if r["black_client"] else 0)
                                ),
                                "spectators": len(r["spectators"]),
                                "ongoing": r["started"],
                                "time_control": r["time_control"]
                            }
                            for rid, r in rooms.items()
                        ]
                    send_json(client, {"type": "room_list", "rooms": room_list})

                # "get_leaderboard": client wants to see the leaderboard. sends the sorted win list back.
                elif data["type"] == "get_leaderboard":
                    send_json(client, {"type": "leaderboard", "data": get_leaderboard_list()})

                # "create_room": a player wants to create a new room.
                # stores the room in the rooms dict with the creator as white, clocks not started yet.
                # sends "color: white" and "waiting" back to the creator, then broadcasts the updated room list.
                elif data["type"] == "create_room":
                    print("CREATE ROOM RECEIVED")
                    tc = data.get("time_control", 5)
                    if tc not in (1, 5, 10):
                        tc = 5
                    with rooms_lock:
                        rid = data["room_id"]
                        rooms[rid] = {
                            "name": data["name"],
                            "white_client": client,
                            "black_client": None,
                            "white_username": data.get("username", "Player"),
                            "black_username": None,
                            "white_connected": True,
                            "black_connected": False,
                            "spectators": [],
                            "host": client,
                            "started": False,
                            "fen": None,
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

                # "join_room": a second player joins an existing room.
                # assigns them as black, initialises the clocks, and sends "start" to both players.
                # both players receive their color, the time control, and initial clock values.
                elif data["type"] == "join_room":
                    rid = data["room_id"]
                    with rooms_lock:
                        room = rooms.get(rid)
                        player_count = (
                            (1 if room["white_client"] else 0)
                            +
                            (1 if room["black_client"] else 0)
                        )

                        if room and player_count < 2:
                            room["black_client"] = client
                            room["black_username"] = data.get(
                                "username",
                                "Player"
                            )
                            room["black_connected"] = True
                            room_id = rid
                            joined = True
                        else:
                            joined = False

                    if joined:
                        rooms[rid]["started"] = True
                        rooms[rid]["fen"] = START_FEN

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

                # "spectate": a third party wants to watch an ongoing game.
                # adds them to the room's spectators list and sends them the current board position (fen)
                # plus the full move history so they can see everything that has happened so far.
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

                # "reconnect": sent by a client on startup with their username.
                # the server checks every room to see if this username belongs to a disconnected player.
                # if found, it reassigns their socket, calculates how much clock time passed while they were gone,
                # sends a "resume" message with the current board/clocks so they can pick up exactly where they left off,
                # and notifies the opponent that they came back.
                elif data["type"] == "reconnect":

                    username = data["username"]

                    with rooms_lock:
                        print("RECONNECT:", username)
                        for rid, room in rooms.items():

                            print(
                                "ROOM",
                                rid,
                                "W=", room["white_username"],
                                room["white_connected"],
                                "B=", room["black_username"],
                                room["black_connected"]
                            )

                            if (
                                room["white_username"] == username
                                and not room["white_connected"]
                            ):
                                print("REJOINED AS WHITE")
                                room["white_client"] = client
                                room["white_connected"] = True

                                room_id = rid
                                now = time.time()

                                if room["clocks"] is not None:
                                    active = room["turn"]
                                    live_clocks = dict(room["clocks"])
                                    elapsed = now - room["last_tick"]
                                    live_clocks[active] = max(
                                        0,
                                        live_clocks[active] - elapsed
                                    )

                                else:
                                    live_clocks = None
                                send_json(client, {
                                    "type": "resume",
                                    "color": "white",
                                    "fen": room["fen"],
                                    "clocks": live_clocks,
                                    "turn": room["turn"],
                                    "time_control": room["time_control"]
                                })

                                if room["black_client"]:
                                    try:
                                        send_json(room["black_client"], {
                                            "type": "opponent_reconnected"
                                        })
                                    except:
                                        pass

                                print("RECONNECT FINISHED")
                                break

                            elif (
                                room["black_username"] == username
                                and not room["black_connected"]
                            ):
                                print("REJOINED AS BLACK")
                                room["black_client"] = client
                                room["black_connected"] = True

                                room_id = rid
                                now = time.time()
                                if room["clocks"] is not None:
                                    active = room["turn"]
                                    live_clocks = dict(room["clocks"])
                                    elapsed = now - room["last_tick"]
                                    live_clocks[active] = max(
                                        0,
                                        live_clocks[active] - elapsed
                                    )
                                send_json(client, {
                                    "type": "resume",
                                    "color": "black",
                                    "fen": room["fen"],
                                    "clocks": live_clocks,
                                    "turn": room["turn"],
                                    "time_control": room["time_control"]
                                })

                                if room["white_client"]:
                                    try:
                                        send_json(room["white_client"], {
                                            "type": "opponent_reconnected"
                                        })
                                    except:
                                        pass

                                print("RECONNECT FINISHED")
                                break

                # "move": a player made a move. the server:
                # 1. saves the new board position (fen) to the room so reconnecting players and spectators get the latest state.
                # 2. deducts the elapsed time from the player who just moved and switches whose clock is running.
                # 3. forwards the move to the other player and all spectators.
                # 4. sends a clock_sync back to the player who moved so their display stays accurate.
                # 5. checks the resulting fen for checkmate — if found, records the win and broadcasts the leaderboard.
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
                                room["history"].append({
                                    "move": data.get("move"),
                                    "color": moving_color,
                                    "fen_after": data.get("fen"),
                                    "move_number": len(room["history"]) + 1
                                })
                                elapsed = now - room["last_tick"]
                                room["clocks"][moving_color] = max(0, room["clocks"][moving_color] - elapsed)
                                room["turn"] = "black" if moving_color == "white" else "white"
                                room["last_tick"] = now
                                clocks_payload = dict(room["clocks"])
                                data["clocks"] = clocks_payload
                                data["turn"] = room["turn"]

                            players = []

                            if room["white_client"]:
                                players.append(room["white_client"])

                            if room["black_client"]:
                                players.append(room["black_client"])

                            for c in players:
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
                            player_count = (
                                (1 if room["white_client"] else 0)
                                +
                                (1 if room["black_client"] else 0)
                            )

                            if fen and player_count == 2:
                                try:
                                    board_check = chess.Board(fen)
                                    if board_check.is_checkmate():
                                        # side to move (board_check.turn) is the one checkmated/loses
                                        winner_idx = 1 if board_check.turn == chess.WHITE else 0
                                        winner_client = (
                                            room["white_client"]
                                            if winner_idx == 0
                                            else room["black_client"]
                                        )
                                        winner_name = (
                                            room["white_username"]
                                            if winner_client == room["white_client"]
                                            else room["black_username"]
                                        )
                                        room["game_over"] = True
                                        record_win(winner_name)
                                        broadcast_leaderboard()
                                except ValueError:
                                    pass

                # "chat": a player sent a chat message.
                # forwards it to everyone else in the room (both players + spectators), but not back to the sender.
                elif data["type"] == "chat":

                    if room_id:

                        with rooms_lock:
                            room = rooms.get(room_id)

                        if room:

                            players = []

                            if room["white_client"]:
                                players.append(room["white_client"])

                            if room["black_client"]:
                                players.append(room["black_client"])

                            recipients = players + room["spectators"]

                            for c in recipients:
                                if c != client:
                                    send_json(c, data)

        except Exception as e:
            print("Client error:", e)
            break

    # --- disconnect cleanup ---
    # runs when the client's connection drops (recv returned empty or threw an exception).
    # removes them from their room. if both players are gone and no spectators remain, deletes the room entirely.
    # if only one player left, notifies the remaining player with "opponent_disconnected".
    # if it was a spectator, just removes them from the spectators list.
    # finally, removes the socket from connected_clients and closes it.
    if room_id:
        with rooms_lock:
            room = rooms.get(room_id)

            if room:

                was_player = (
                    client == room["white_client"]
                    or
                    client == room["black_client"]
                )

                room["spectators"] = [
                    s for s in room["spectators"]
                    if s != client
                ]

                if client == room["white_client"]:
                    room["white_connected"] = False
                    room["white_client"] = None
                    print("WHITE DISCONNECTED", room["white_username"])

                if client == room["black_client"]:
                    room["black_connected"] = False
                    room["black_client"] = None
                    print("BLACK DISCONNECTED", room["black_username"])

                no_players = (
                    room["white_client"] is None
                    and room["black_client"] is None
                )

                if no_players and not room["spectators"]:
                    del rooms[room_id]

                elif was_player:
                    players = []

                    if room["white_client"]:
                        players.append(room["white_client"])

                    if room["black_client"]:
                        players.append(room["black_client"])

                    for c in players:
                        try:
                            send_json(c, {
                                "type": "opponent_disconnected"
                            })
                        except:
                            pass

        broadcast_room_list()

    with clients_lock:
        if client in connected_clients:
            connected_clients.remove(client)

    client.close()


# background watchdog thread that checks every second if any player's clock has run out.
# this is necessary because the clock only ticks server-side when a move arrives —
# if a player just sits there and lets time run out without moving, this thread catches it.
# when a timeout is detected, it marks the game as over, records the win for the other player,
# broadcasts the updated leaderboard, and sends a "timeout" message to both players and spectators.
def clock_monitor():
    """Background watchdog: ends a match on timeout even if no move arrives."""
    while True:
        time.sleep(1)

        with rooms_lock:
            room_snapshot = list(rooms.items())

        for rid, room in room_snapshot:
            if room.get("game_over") or room.get("clocks") is None:
                continue
            player_count = (
                (1 if room["white_client"] else 0)
                +
                (1 if room["black_client"] else 0)
            )

            if player_count != 2:
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
            winner_client = (
                room["white_client"]
                if winner_idx == 0
                else room["black_client"]
            )
            winner_name = (
                room["white_username"]
                if winner_client == room["white_client"]
                else room["black_username"]
            )

            record_win(winner_name)
            broadcast_leaderboard()

            timeout_msg = {
                "type": "timeout",
                "loser_color": loser_color,
                "winner_color": winner_color,
                "winner": winner_name
            }

            players = []

            if room["white_client"]:
                players.append(room["white_client"])

            if room["black_client"]:
                players.append(room["black_client"])

            for c in players:
                try:
                    send_json(c, timeout_msg)
                except:
                    pass
            for s in room["spectators"]:
                try:
                    send_json(s, timeout_msg)
                except:
                    pass


# start the clock watchdog in the background so timeouts work even with no move activity
threading.Thread(target=clock_monitor, daemon=True).start()

# main accept loop — waits for new client connections, adds them to connected_clients,
# and spawns a dedicated handle_client thread for each one so they all run independently.
while True:
    client, addr = server.accept()
    print("Connected:", addr)
    with clients_lock:
        connected_clients.append(client)
    threading.Thread(target=handle_client, args=(client,), daemon=True).start()
