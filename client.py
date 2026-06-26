# client.py — the main pygame application. draws every screen, handles all user input, and communicates with the server.
# palpal : report.pdf, chess clock display (helping kenzie)
# bisma  : all ui screens, game rooms, turn tracking, username, last-move highlight, sounds, auto promotion, disconnect detection, match status
# kenzie : leaderboard screen, chess clock display, time control selector in lobby
# alif   : move history logic, move history panel
# rama   : reconnect/rejoin flow, network message handling, basic chess logic, chat feature, spectator mode

import pygame
import chess
import threading
import time
import math
import numpy as np
import uuid
from network import Network

pygame.mixer.pre_init(44100, -16, 1, 512)
pygame.init()

CHAT_WIDTH = 250
BOARD_SIZE = 800
WIDTH = BOARD_SIZE + CHAT_WIDTH
HEIGHT = 800
SQUARE_SIZE = BOARD_SIZE // 8

WHITE = (240, 217, 181)
BROWN = (181, 136, 99)
HIGHLIGHT = (246, 246, 130)
LAST_MOVE = (205, 210, 106)
MOVE_DOT = (88, 101, 242, 160)

BG_COLOR_TOP = (18, 19, 24)
BG_COLOR_BOTTOM = (32, 34, 44)
PANEL_COLOR = (26, 28, 36)
BUTTON_COLOR = (88, 101, 242)
BUTTON_HOVER = (114, 126, 247)
BUTTON_GREEN = (46, 160, 67)
BUTTON_GREEN_HOVER = (60, 185, 82)
TEXT_COLOR = (240, 241, 245)
SUBTEXT_COLOR = (150, 154, 168)
CHAT_BG = (22, 24, 30)
CHAT_BUBBLE = (38, 41, 51)

chat_messages = []
chat_input = ""
chat_active = False
move_history = []

CHAT_INPUT_RECT = pygame.Rect(BOARD_SIZE + 10, HEIGHT - 50, CHAT_WIDTH - 20, 35)

screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("NetChess")

board = chess.Board()
network = None
player_color = None
my_turn = False
last_move = None
opponent_left = False
opponent_disconnected = False
opponent_disconnected_since = None
game_over = False
game_over_message = ""
spectator_mode = False

# STATES: "menu" | "username" | "lobby" | "waiting" | "game"
state = "menu"

username = ""
username_input = ""
username_active = True

rooms = []
room_name_input = ""
room_name_active = False
room_scroll = 0
selected_time_control = 5

leaderboard_data = []

# Chess clock state
time_control = None
clocks = {"white": 0, "black": 0}
clock_turn = "white"
clock_last_sync = time.time()

selected_square = None
legal_targets = []


# bisma: generates all sound effects (move, capture, check, end) mathematically using numpy.
# no audio files needed — pure sine or square waves at different frequencies and durations.
def make_sound(freq, duration, volume=0.3, wave="sine"):
    sample_rate = 44100
    n = int(sample_rate * duration)
    t = np.linspace(0, duration, n, False)
    if wave == "sine":
        data = np.sin(2 * np.pi * freq * t)
    else:
        data = np.sign(np.sin(2 * np.pi * freq * t))
    data = (data * volume * 32767).astype(np.int16)
    data = np.column_stack((data, data))
    return pygame.sndarray.make_sound(data)

move_sound    = make_sound(440, 0.08)
capture_sound = make_sound(280, 0.12)
check_sound   = make_sound(600, 0.18)
end_sound     = make_sound(220, 0.4, wave="square")


def get_status():
    if board.is_checkmate():
        winner = "Black" if board.turn == chess.WHITE else "White"
        return f"Checkmate! {winner} wins"
    if board.is_stalemate():
        return "Stalemate!"
    if board.is_check():
        return "Check!"
    return None


# kenzie: returns the smoothly counting-down time for a given color.
# instead of waiting for server updates, it subtracts local elapsed time from the last known server value,
# so the clock ticks smoothly on screen every frame without needing a network message every second.
def get_display_clock(color):
    """Local smooth countdown, resynced whenever the server sends fresh clock data."""
    if color == clock_turn and not game_over and not opponent_left:
        elapsed = time.time() - clock_last_sync
        return max(0, clocks.get(color, 0) - elapsed)
    return max(0, clocks.get(color, 0))


# kenzie: converts raw seconds into a mm:ss string for display on the clock panel.
def format_clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# rama: runs in a background thread and listens for every message the server sends.
# when a message arrives it updates the relevant global state (board, clocks, chat, game status, etc.)
# so the main loop always has fresh data to draw. handles: move, chat, start, clock_sync, timeout,
# opponent_disconnected, opponent_reconnected, spectator_start, resume, leaderboard, error.
def receive_messages():
    global player_color, my_turn, last_move, state, rooms
    global opponent_left, game_over, game_over_message
    global opponent_disconnected, opponent_disconnected_since
    global spectator_mode, leaderboard_data
    global time_control, clocks, clock_turn, clock_last_sync

    while True:
        try:
            data = network.receive()
            if data is None:
                break

            if data["type"] == "color":
                player_color = data["color"]

            elif data["type"] == "room_list":
                rooms = data["rooms"]

            elif data["type"] == "waiting":
                state = "waiting"

            elif data["type"] == "start":
                state = "game"
                opponent_left = False
                opponent_disconnected = False
                opponent_disconnected_since = None
                game_over = False
                game_over_message = ""
                if player_color == "white":
                    my_turn = True
                time_control = data.get("time_control")
                if data.get("clocks"):
                    clocks = data["clocks"]
                clock_turn = "white"
                clock_last_sync = time.time()

            elif data["type"] == "move":
                opponent_disconnected = False
                opponent_disconnected_since = None
                move = chess.Move.from_uci(data["move"])
                if move in board.legal_moves:
                    is_capture = board.is_capture(move)
                    move_number = board.fullmove_number
                    moving_color = "white" if board.turn == chess.WHITE else "black"

                    san = board.san(move)

                    board.push(move)

                    move_history.append({
                        "number": move_number,
                        "color": moving_color,
                        "san": san
                    })
                    last_move = move
                    if board.is_checkmate():
                        winner = "Black" if board.turn == chess.WHITE else "White"
                        game_over = True
                        game_over_message = f"Checkmate! {winner} wins"
                        end_sound.play()
                    elif board.is_stalemate():
                        game_over = True
                        game_over_message = "Stalemate! It's a draw"
                        end_sound.play()
                    elif board.is_check():
                        check_sound.play()
                    elif is_capture:
                        capture_sound.play()
                    else:
                        move_sound.play()
                my_turn = True

                if data.get("clocks"):
                    clocks = data["clocks"]
                    clock_turn = data.get("turn", clock_turn)
                    clock_last_sync = time.time()

            elif data["type"] == "clock_sync":
                clocks = data["clocks"]
                clock_turn = data.get("turn", clock_turn)
                clock_last_sync = time.time()

            elif data["type"] == "timeout":
                game_over = True
                winner_color = data.get("winner_color", "").capitalize()
                game_over_message = f"{winner_color} wins on time!" if winner_color else "Time's up!"
                my_turn = False
                end_sound.play()

            elif data["type"] == "chat":
                chat_messages.append(data["message"])
                if len(chat_messages) > 15:
                    chat_messages.pop(0)

            elif data["type"] == "opponent_left":
                opponent_left = True
                my_turn = False

            elif data["type"] == "opponent_disconnected":
                opponent_disconnected = True
                opponent_disconnected_since = time.time()

            elif data["type"] == "opponent_reconnected":
                opponent_disconnected = False
                opponent_disconnected_since = None
                chat_messages.append("Opponent reconnected.")
                if len(chat_messages) > 15:
                    chat_messages.pop(0)

            elif data["type"] == "spectator_start":

                move_history.clear()

                replay_board = chess.Board()

                for entry in data.get("history", []):

                    try:

                        uci_move = chess.Move.from_uci(entry["move"])

                        if uci_move in replay_board.legal_moves:

                            pair_number = replay_board.fullmove_number

                            moving_color = (
                                "white"
                                if replay_board.turn == chess.WHITE
                                else "black"
                            )

                            san = replay_board.san(uci_move)

                            replay_board.push(uci_move)

                            move_history.append({
                                "number": pair_number,
                                "color": moving_color,
                                "san": san
                            })

                    except (ValueError, KeyError):
                        continue

                board.set_fen(data["fen"])

                state = "game"
                spectator_mode = True
                my_turn = False
                player_color = None

            elif data["type"] == "resume":
                board.set_fen(data["fen"])

                player_color = data["color"]

                clocks = data["clocks"]
                clock_turn = data["turn"]
                clock_last_sync = time.time()

                time_control = data.get("time_control")

                state = "game"
                spectator_mode = False
                opponent_left = False
                opponent_disconnected = False
                opponent_disconnected_since = None
                game_over = False

                my_turn = (
                    player_color == clock_turn
                )
                
            elif data["type"] == "leaderboard":
                leaderboard_data = data["data"]

            elif data["type"] == "error":
                chat_messages.append(f"Error: {data['message']}")

        except Exception as e:
            print("Network error:", e)
            break


# bisma: draws the dark top-to-bottom gradient background used on all non-game screens (menu, lobby, etc.)
def draw_gradient():
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(BG_COLOR_TOP[0] + (BG_COLOR_BOTTOM[0] - BG_COLOR_TOP[0]) * t)
        g = int(BG_COLOR_TOP[1] + (BG_COLOR_BOTTOM[1] - BG_COLOR_TOP[1]) * t)
        b = int(BG_COLOR_TOP[2] + (BG_COLOR_BOTTOM[2] - BG_COLOR_TOP[2]) * t)
        pygame.draw.line(screen, (r, g, b), (0, y), (WIDTH, y))


# bisma: reusable helper that draws a rounded button with a hover color change.
# used by every screen — pass the rect, label, and current mouse position.
def draw_button(rect, label, mouse_pos, color=None, hover_color=None, font_size=36):
    c = color or BUTTON_COLOR
    hc = hover_color or BUTTON_HOVER
    col = hc if rect.collidepoint(mouse_pos) else c
    pygame.draw.rect(screen, col, rect, border_radius=12)
    font = pygame.font.SysFont(None, font_size)
    txt = font.render(label, True, TEXT_COLOR)
    screen.blit(txt, txt.get_rect(center=rect.center))


# bisma: draws the main menu screen with the NetChess title and a "Play Online" button.
# clicking the button transitions state to "username".
def draw_menu(mouse_pos):
    draw_gradient()
    panel = pygame.Rect(0, 0, 480, 340)
    panel.center = (WIDTH // 2, HEIGHT // 2)
    pygame.draw.rect(screen, PANEL_COLOR, panel, border_radius=20)

    font_title = pygame.font.SysFont(None, 90)
    title = font_title.render("NetChess", True, TEXT_COLOR)
    screen.blit(title, title.get_rect(center=(WIDTH // 2, panel.top + 90)))

    font_sub = pygame.font.SysFont(None, 26)
    sub = font_sub.render("play chess online with a friend", True, SUBTEXT_COLOR)
    screen.blit(sub, sub.get_rect(center=(WIDTH // 2, panel.top + 140)))

    btn = pygame.Rect(0, 0, 260, 65)
    btn.center = (WIDTH // 2, panel.top + 240)
    draw_button(btn, "Play Online", mouse_pos)
    return btn



# bisma: draws the username input screen. the player types their display name here before entering the lobby.
# pressing enter or clicking continue sends a reconnect message to the server with the chosen name.
def draw_username(mouse_pos):
    draw_gradient()
    panel = pygame.Rect(0, 0, 480, 300)
    panel.center = (WIDTH // 2, HEIGHT // 2)
    pygame.draw.rect(screen, PANEL_COLOR, panel, border_radius=20)

    font_title = pygame.font.SysFont(None, 52)
    title = font_title.render("Enter your username", True, TEXT_COLOR)
    screen.blit(title, title.get_rect(center=(WIDTH // 2, panel.top + 70)))

    input_rect = pygame.Rect(panel.x + 40, panel.top + 120, panel.width - 80, 48)
    pygame.draw.rect(screen, (38, 41, 51), input_rect, border_radius=10)
    pygame.draw.rect(screen, BUTTON_COLOR, input_rect, 2, border_radius=10)

    font_input = pygame.font.SysFont(None, 34)
    display = username_input if username_input else "Your name..."
    color = TEXT_COLOR if username_input else SUBTEXT_COLOR
    screen.blit(font_input.render(display, True, color), (input_rect.x + 14, input_rect.y + 13))

    confirm_btn = pygame.Rect(0, 0, 200, 50)
    confirm_btn.center = (WIDTH // 2, panel.top + 220)
    draw_button(confirm_btn, "Continue", mouse_pos)
    return input_rect, confirm_btn

# bisma: draws the game lobby screen. shows a room name input, a create button, and a list of active rooms.
# each room row shows its name, player count, and a join or watch button.
# kenzie: the time control buttons (1/5/10 min) in the create room panel are kenzie's contribution.
def draw_lobby(mouse_pos):
    draw_gradient()

    font_title = pygame.font.SysFont(None, 60)
    title = font_title.render("Game Rooms", True, TEXT_COLOR)
    screen.blit(title, title.get_rect(center=(WIDTH // 2, 60)))

    font_sub = pygame.font.SysFont(None, 24)
    sub = font_sub.render("Create a new room or join an existing one", True, SUBTEXT_COLOR)
    screen.blit(sub, sub.get_rect(center=(WIDTH // 2, 100)))

    # Create room panel
    create_panel = pygame.Rect(80, 130, WIDTH - 160, 170)
    pygame.draw.rect(screen, PANEL_COLOR, create_panel, border_radius=14)

    font_label = pygame.font.SysFont(None, 26)
    lbl = font_label.render("Room Name", True, SUBTEXT_COLOR)
    screen.blit(lbl, (create_panel.x + 20, create_panel.y + 16))

    input_rect = pygame.Rect(create_panel.x + 20, create_panel.y + 44, 380, 40)
    border = BUTTON_COLOR if room_name_active else (60, 63, 78)
    pygame.draw.rect(screen, (38, 41, 51), input_rect, border_radius=8)
    pygame.draw.rect(screen, border, input_rect, 2, border_radius=8)
    font_input = pygame.font.SysFont(None, 28)
    display_text = room_name_input if room_name_input else "Enter room name..."
    color_text = TEXT_COLOR if room_name_input else SUBTEXT_COLOR
    screen.blit(font_input.render(display_text, True, color_text), (input_rect.x + 10, input_rect.y + 10))

    create_btn = pygame.Rect(create_panel.x + 420, create_panel.y + 44, 160, 40)
    draw_button(create_btn, "Create Room", mouse_pos, BUTTON_GREEN, BUTTON_GREEN_HOVER, font_size=24)

    # Time control selector
    tc_lbl = font_label.render("Time Control", True, SUBTEXT_COLOR)
    screen.blit(tc_lbl, (create_panel.x + 20, create_panel.y + 100))

    time_buttons = []
    tx = create_panel.x + 20
    for opt in (1, 5, 10):
        tbtn = pygame.Rect(tx, create_panel.y + 124, 90, 34)
        is_selected = (selected_time_control == opt)
        col = BUTTON_COLOR if is_selected else (50, 53, 65)
        hov = BUTTON_HOVER if is_selected else (65, 68, 82)
        draw_button(tbtn, f"{opt} min", mouse_pos, col, hov, font_size=22)
        time_buttons.append((tbtn, opt))
        tx += 100

    # Room list
    list_panel = pygame.Rect(80, 320, WIDTH - 160, HEIGHT - 380)
    pygame.draw.rect(screen, PANEL_COLOR, list_panel, border_radius=14)

    font_label2 = pygame.font.SysFont(None, 26)
    lbl2 = font_label2.render("Available Rooms", True, SUBTEXT_COLOR)
    screen.blit(lbl2, (list_panel.x + 20, list_panel.y + 16))

    pygame.draw.line(screen, (45, 48, 60),
                     (list_panel.x + 16, list_panel.y + 44),
                     (list_panel.right - 16, list_panel.y + 44), 1)

    join_buttons = []
    visible_rooms = rooms[room_scroll:room_scroll + 6]
    font_room = pygame.font.SysFont(None, 26)

    if not rooms:
        no_rooms = font_room.render("No rooms available. Create one!", True, SUBTEXT_COLOR)
        screen.blit(no_rooms, no_rooms.get_rect(center=(WIDTH // 2, list_panel.y + 110)))
    else:
        for i, room in enumerate(visible_rooms):
            y = list_panel.y + 56 + i * 56
            row_rect = pygame.Rect(list_panel.x + 12, y, list_panel.width - 24, 46)
            if row_rect.collidepoint(mouse_pos):
                pygame.draw.rect(screen, (38, 41, 51), row_rect, border_radius=8)

            name_txt = font_room.render(room["name"], True, TEXT_COLOR)
            screen.blit(name_txt, (row_rect.x + 14, row_rect.y + 13))

            tc_txt = font_room.render(f"{room.get('time_control', 5)} min", True, SUBTEXT_COLOR)
            screen.blit(tc_txt, (row_rect.right - 210, row_rect.y + 13))

            players_txt = font_room.render(f"{room['players']}/2", True, SUBTEXT_COLOR)
            screen.blit(players_txt, (row_rect.right - 120, row_rect.y + 13))

            join_btn = pygame.Rect(row_rect.right - 80, row_rect.y + 8, 68, 30)
            if room["ongoing"]:
                draw_button(
                    join_btn,
                    "Watch",
                    mouse_pos
                )
            else:
                draw_button(
                    join_btn,
                    "Join",
                    mouse_pos
                )
            join_buttons.append((
                join_btn,
                room["id"],
                room["ongoing"]
            ))

    refresh_btn = pygame.Rect(list_panel.x + 20, list_panel.bottom + 10, 130, 38)
    draw_button(refresh_btn, "Refresh", mouse_pos, (50, 53, 65), (65, 68, 82), font_size=24)

    leaderboard_btn = pygame.Rect(list_panel.x + 160, list_panel.bottom + 10, 170, 38)
    draw_button(leaderboard_btn, "🏆 Leaderboard", mouse_pos, (50, 53, 65), (65, 68, 82), font_size=22)

    back_btn = pygame.Rect(list_panel.right - 150, list_panel.bottom + 10, 130, 38)
    draw_button(back_btn, "Back", mouse_pos, (50, 53, 65), (65, 68, 82), font_size=24)

    return input_rect, create_btn, join_buttons, refresh_btn, leaderboard_btn, back_btn, time_buttons


# kenzie: draws the leaderboard screen. displays up to 12 players ranked by total wins.
# gold/silver/bronze colors for top 3. data is fetched from the server (stored in leaderboard.json).
def draw_leaderboard(mouse_pos):
    draw_gradient()

    font_title = pygame.font.SysFont(None, 60)
    title = font_title.render("🏆 Leaderboard", True, TEXT_COLOR)
    screen.blit(title, title.get_rect(center=(WIDTH // 2, 60)))

    font_sub = pygame.font.SysFont(None, 24)
    sub = font_sub.render("Top players by wins", True, SUBTEXT_COLOR)
    screen.blit(sub, sub.get_rect(center=(WIDTH // 2, 100)))

    panel = pygame.Rect(80, 140, WIDTH - 160, HEIGHT - 220)
    pygame.draw.rect(screen, PANEL_COLOR, panel, border_radius=14)

    font_header = pygame.font.SysFont(None, 24)
    screen.blit(font_header.render("Rank", True, SUBTEXT_COLOR), (panel.x + 20, panel.y + 16))
    screen.blit(font_header.render("Player", True, SUBTEXT_COLOR), (panel.x + 100, panel.y + 16))
    screen.blit(font_header.render("Wins", True, SUBTEXT_COLOR), (panel.right - 100, panel.y + 16))

    pygame.draw.line(screen, (45, 48, 60),
                      (panel.x + 16, panel.y + 44),
                      (panel.right - 16, panel.y + 44), 1)

    font_row = pygame.font.SysFont(None, 28)

    if not leaderboard_data:
        empty = font_row.render("No games finished yet. Be the first to win!", True, SUBTEXT_COLOR)
        screen.blit(empty, empty.get_rect(center=(WIDTH // 2, panel.y + 110)))
    else:
        for i, entry in enumerate(leaderboard_data[:12]):
            y = panel.y + 56 + i * 42
            row_rect = pygame.Rect(panel.x + 12, y, panel.width - 24, 36)

            if i == 0:
                rank_color = (255, 215, 0)
            elif i == 1:
                rank_color = (192, 192, 192)
            elif i == 2:
                rank_color = (180, 120, 60)
            else:
                rank_color = TEXT_COLOR

            rank_txt = font_row.render(f"#{i + 1}", True, rank_color)
            screen.blit(rank_txt, (row_rect.x + 8, row_rect.y + 4))

            name_txt = font_row.render(entry["username"], True, TEXT_COLOR)
            screen.blit(name_txt, (row_rect.x + 90, row_rect.y + 4))

            wins_txt = font_row.render(str(entry["wins"]), True, TEXT_COLOR)
            screen.blit(wins_txt, (row_rect.right - 90, row_rect.y + 4))

    back_btn = pygame.Rect(0, 0, 160, 44)
    back_btn.center = (WIDTH // 2, panel.bottom + 36)
    draw_button(back_btn, "Back", mouse_pos, (50, 53, 65), (65, 68, 82))
    return back_btn


# bisma: draws the waiting screen shown to the room creator after creating a room.
# stays here until the server sends a "start" message when an opponent joins.
def draw_waiting(mouse_pos):
    draw_gradient()
    panel = pygame.Rect(0, 0, 480, 260)
    panel.center = (WIDTH // 2, HEIGHT // 2)
    pygame.draw.rect(screen, PANEL_COLOR, panel, border_radius=20)

    font_title = pygame.font.SysFont(None, 52)
    title = font_title.render("Waiting for opponent...", True, TEXT_COLOR)
    screen.blit(title, title.get_rect(center=(WIDTH // 2, panel.top + 80)))

    font_sub = pygame.font.SysFont(None, 24)
    sub = font_sub.render("Share your room name with a friend", True, SUBTEXT_COLOR)
    screen.blit(sub, sub.get_rect(center=(WIDTH // 2, panel.top + 130)))

    back_btn = pygame.Rect(0, 0, 160, 44)
    back_btn.center = (WIDTH // 2, panel.top + 200)
    draw_button(back_btn, "Cancel", mouse_pos, (50, 53, 65), (65, 68, 82))
    return back_btn

# alif: draws the move history panel in the chat sidebar.
# groups moves into white/black pairs per turn and displays them in standard algebraic notation (san).
# only shows the most recent rows that fit in the panel height.
def draw_move_history(panel_top):
    panel_rect = pygame.Rect(BOARD_SIZE + 8, panel_top, CHAT_WIDTH - 16, 130)
    pygame.draw.rect(screen, CHAT_BUBBLE, panel_rect, border_radius=10)

    font_label = pygame.font.SysFont(None, 21)
    label = font_label.render("Move history", True, SUBTEXT_COLOR)
    screen.blit(label, (panel_rect.x + 10, panel_rect.y + 6))

    pygame.draw.line(screen, PANEL_COLOR,
                     (panel_rect.x + 10, panel_rect.y + 28),
                     (panel_rect.right - 10, panel_rect.y + 28), 1)

    font_move = pygame.font.SysFont(None, 21)
    row_h = 20
    visible_rows = (panel_rect.height - 34) // row_h

    pairs = []
    for entry in move_history:
        if entry["color"] == "white":
            pairs.append([entry["number"], entry["san"], None])
        else:
            if pairs and pairs[-1][2] is None and pairs[-1][0] == entry["number"]:
                pairs[-1][2] = entry["san"]
            else:
                pairs.append([entry["number"], None, entry["san"]])

    visible_pairs = pairs[-visible_rows:] if visible_rows > 0 else []

    y = panel_rect.y + 32
    num_x = panel_rect.x + 10
    white_x = panel_rect.x + 38
    black_x = panel_rect.x + 95

    for num, white_san, black_san in visible_pairs:
        num_surf = font_move.render(f"{num}.", True, SUBTEXT_COLOR)
        screen.blit(num_surf, (num_x, y))
        if white_san:
            w_surf = font_move.render(white_san, True, TEXT_COLOR)
            screen.blit(w_surf, (white_x, y))
        if black_san:
            b_surf = font_move.render(black_san, True, TEXT_COLOR)
            screen.blit(b_surf, (black_x, y))
        y += row_h

    if not pairs:
        empty = font_move.render("No moves yet", True, SUBTEXT_COLOR)
        screen.blit(empty, (panel_rect.x + 10, panel_rect.y + 34))



# bisma: draws the entire right sidebar. includes the chat message bubbles, chat input box,
# turn indicator bar (your turn / opponent's turn / disconnected), and a menu button.
# kenzie: the chess clock widget (white/black timers) inside this sidebar is kenzie's contribution.
# rama: the spectator badge shown when watching a game is part of rama's spectator mode feature.
def draw_chat():
    pygame.draw.rect(screen, CHAT_BG, (BOARD_SIZE, 0, CHAT_WIDTH, HEIGHT))

    font_title = pygame.font.SysFont(None, 30)
    screen.blit(font_title.render("Chat", True, TEXT_COLOR), (BOARD_SIZE + 16, 16))

    mouse_pos = pygame.mouse.get_pos()
    menu_btn = pygame.Rect(BOARD_SIZE + CHAT_WIDTH - 84, 8, 72, 28)
    draw_button(menu_btn, "Menu", mouse_pos, (50, 53, 65), (65, 68, 82), font_size=22)

    # Spectator mode indicator
    if spectator_mode:
        bar_color = (50, 53, 65)
        turn_text = "👁 Spectating"
        turn_fg = TEXT_COLOR

        bar_rect = pygame.Rect(
            BOARD_SIZE + 8,
            44,
            CHAT_WIDTH - 16,
            28
        )

        pygame.draw.rect(
            screen,
            bar_color,
            bar_rect,
            border_radius=8
        )

        font_turn = pygame.font.SysFont(None, 21)

        turn_surf = font_turn.render(
            turn_text,
            True,
            turn_fg
        )

        screen.blit(
            turn_surf,
            turn_surf.get_rect(center=bar_rect.center)
        )

    # Turn indicator bar
    if player_color:
        if opponent_disconnected and not game_over and not opponent_left:
            bar_color = (74, 58, 30)
            turn_text = "🔌 Opponent disconnected"
            turn_fg = (240, 200, 130)
        elif my_turn and not game_over and not opponent_left:
            bar_color = (46, 160, 67)
            turn_text = f"⚡ Your turn  ({player_color})"
            turn_fg = (220, 255, 220)
        else:
            bar_color = (50, 53, 65)
            turn_text = f"Opponent's turn  ({player_color})"
            turn_fg = SUBTEXT_COLOR
        bar_rect = pygame.Rect(BOARD_SIZE + 8, 44, CHAT_WIDTH - 16, 28)
        pygame.draw.rect(screen, bar_color, bar_rect, border_radius=8)
        font_turn = pygame.font.SysFont(None, 21)
        turn_surf = font_turn.render(turn_text, True, turn_fg)
        screen.blit(turn_surf, turn_surf.get_rect(center=bar_rect.center))

    pygame.draw.line(screen, PANEL_COLOR,
                     (BOARD_SIZE + 16, 152), (BOARD_SIZE + CHAT_WIDTH - 16, 152), 1)

    # Chess clocks
    if time_control is not None:
        clock_rect = pygame.Rect(BOARD_SIZE + 8, 80, CHAT_WIDTH - 16, 64)
        pygame.draw.rect(screen, CHAT_BUBBLE, clock_rect, border_radius=8)

        font_clock = pygame.font.SysFont(None, 26)
        running_now = not game_over and not opponent_left

        white_active = clock_turn == "white" and running_now
        black_active = clock_turn == "black" and running_now

        white_color = (100, 220, 100) if white_active else SUBTEXT_COLOR
        black_color = (100, 220, 100) if black_active else SUBTEXT_COLOR

        w_txt = font_clock.render(f"⚪ White   {format_clock(get_display_clock('white'))}", True, white_color)
        b_txt = font_clock.render(f"⚫ Black   {format_clock(get_display_clock('black'))}", True, black_color)

        screen.blit(w_txt, (clock_rect.x + 12, clock_rect.y + 6))
        screen.blit(b_txt, (clock_rect.x + 12, clock_rect.y + 34))

    
    font = pygame.font.SysFont(None, 22)
    y = 164
    chat_zone_bottom = HEIGHT - 195
    for msg in chat_messages[-12:]:
        text = font.render(msg, True, TEXT_COLOR)
        bubble = pygame.Rect(BOARD_SIZE + 12, y - 5, text.get_width() + 16, text.get_height() + 10)
        pygame.draw.rect(screen, CHAT_BUBBLE, bubble, border_radius=10)
        screen.blit(text, (bubble.x + 8, bubble.y + 5))
        y += bubble.height + 8

    draw_move_history(chat_zone_bottom)
    border_col = BUTTON_COLOR if chat_active else PANEL_COLOR
    pygame.draw.rect(screen, (45, 48, 58) if chat_active else CHAT_BUBBLE, CHAT_INPUT_RECT, border_radius=10)
    pygame.draw.rect(screen, border_col, CHAT_INPUT_RECT, 2, border_radius=10)

    if chat_input:
        ts = font.render(chat_input, True, TEXT_COLOR)
    else:
        ts = font.render("Click here to chat...", True, SUBTEXT_COLOR)
    screen.blit(ts, (CHAT_INPUT_RECT.x + 10, CHAT_INPUT_RECT.y + 8))

    return menu_btn


# bisma: draws a semi-transparent overlay on top of the board when the game ends or the opponent leaves.
# shows the result (checkmate/stalemate/opponent left) in green (win), red (loss), or yellow (draw/other).
# has a "return to menu" button that resets all game state.
def draw_game_overlay(mouse_pos):
    overlay = pygame.Surface((BOARD_SIZE, BOARD_SIZE), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 170))
    screen.blit(overlay, (0, 0))

    panel = pygame.Rect(0, 0, 420, 220)
    panel.center = (BOARD_SIZE // 2, BOARD_SIZE // 2)
    pygame.draw.rect(screen, PANEL_COLOR, panel, border_radius=18)
    pygame.draw.rect(screen, (60, 63, 80), panel, 2, border_radius=18)

    if opponent_left:
        title_text = "Opponent Left"
        sub_text = "Your opponent disconnected."
        title_color = (220, 160, 60)
    else:
        title_text = game_over_message
        # highlight win/loss/draw
        if "wins" in game_over_message:
            my_color_name = player_color.capitalize() if player_color else ""
            if my_color_name in game_over_message:
                title_color = (100, 220, 100)   # you won
            else:
                title_color = (220, 80, 80)     # you lost
        else:
            title_color = (220, 180, 60)        # draw/stalemate
        sub_text = "The game is over."

    font_title = pygame.font.SysFont(None, 52)
    font_sub = pygame.font.SysFont(None, 26)

    t = font_title.render(title_text, True, title_color)
    screen.blit(t, t.get_rect(center=(BOARD_SIZE // 2, panel.top + 72)))

    s = font_sub.render(sub_text, True, SUBTEXT_COLOR)
    screen.blit(s, s.get_rect(center=(BOARD_SIZE // 2, panel.top + 118)))

    btn = pygame.Rect(0, 0, 200, 46)
    btn.center = (BOARD_SIZE // 2, panel.top + 170)
    draw_button(btn, "Return to Menu", mouse_pos, font_size=26)
    return btn


# bisma: draws a slim warning banner at the top of the board when the opponent disconnects mid-game.
# the game stays fully playable — clocks keep running. the banner shows a spinning arc and elapsed time
# so the player knows their opponent dropped and may reconnect.
def draw_reconnect_banner():
    """Slim, non-blocking banner shown while the opponent is disconnected.

    The game stays fully playable underneath (clocks keep running, moves
    are still allowed) -- this just lets the player know the other side
    dropped and how long they've been gone, in case they come back.
    """
    elapsed = time.time() - opponent_disconnected_since if opponent_disconnected_since else 0

    banner_w = min(460, BOARD_SIZE - 80)
    banner_h = 56
    banner = pygame.Rect(0, 0, banner_w, banner_h)
    banner.centerx = BOARD_SIZE // 2
    banner.top = 18

    surf = pygame.Surface((banner_w, banner_h), pygame.SRCALPHA)
    pygame.draw.rect(surf, (32, 27, 18, 235), surf.get_rect(), border_radius=14)
    pygame.draw.rect(surf, (220, 160, 60, 255), surf.get_rect(), 2, border_radius=14)
    screen.blit(surf, banner.topleft)

    # Rotating arc "spinner" to read as actively waiting/live, not frozen.
    spinner_center = (banner.left + 32, banner.centery)
    spinner_radius = 14
    angle = (time.time() * 220) % 360
    spinner_rect = pygame.Rect(0, 0, spinner_radius * 2, spinner_radius * 2)
    spinner_rect.center = spinner_center
    pygame.draw.circle(screen, (70, 62, 44), spinner_center, spinner_radius, 3)
    pygame.draw.arc(
        screen, (240, 180, 70), spinner_rect,
        math.radians(angle), math.radians(angle + 110), 3
    )

    font_title = pygame.font.SysFont(None, 24)
    font_sub = pygame.font.SysFont(None, 20)

    title_surf = font_title.render("Opponent disconnected", True, (240, 200, 130))
    sub_surf = font_sub.render(
        f"Waiting for them to reconnect...  {format_clock(elapsed)}",
        True, SUBTEXT_COLOR
    )

    text_x = banner.left + 58
    screen.blit(title_surf, (text_x, banner.top + 7))
    screen.blit(sub_surf, (text_x, banner.top + 29))


# rama: converts a chess square index (0–63) into a screen (row, col) position.
# flips the board horizontally when the local player is black so their pieces always appear at the bottom.
def square_to_rowcol(square):
    rank = chess.square_rank(square)
    file = chess.square_file(square)
    if player_color == "black":
        return rank, 7 - file
    return 7 - rank, file


# rama: draws the 8x8 chessboard. light/dark squares, last-move highlight (yellow tint on from/to squares),
# selected piece highlight, and legal move dots (small dot for empty squares, ring for capturable pieces).
def draw_board():
    for row in range(8):
        for col in range(8):
            color = WHITE if (row + col) % 2 == 0 else BROWN
            pygame.draw.rect(screen, color,
                             (col * SQUARE_SIZE, row * SQUARE_SIZE, SQUARE_SIZE, SQUARE_SIZE))

    if last_move is not None:
        for square in [last_move.from_square, last_move.to_square]:
            row, col = square_to_rowcol(square)
            s = pygame.Surface((SQUARE_SIZE, SQUARE_SIZE), pygame.SRCALPHA)
            s.fill((*LAST_MOVE, 140))
            screen.blit(s, (col * SQUARE_SIZE, row * SQUARE_SIZE))

    if selected_square is not None:
        row, col = square_to_rowcol(selected_square)
        hl = pygame.Surface((SQUARE_SIZE, SQUARE_SIZE), pygame.SRCALPHA)
        hl.fill((*HIGHLIGHT, 110))
        screen.blit(hl, (col * SQUARE_SIZE, row * SQUARE_SIZE))

    for target in legal_targets:
        row, col = square_to_rowcol(target)
        dot = pygame.Surface((SQUARE_SIZE, SQUARE_SIZE), pygame.SRCALPHA)
        center = (SQUARE_SIZE // 2, SQUARE_SIZE // 2)
        if board.piece_at(target):
            pygame.draw.circle(dot, MOVE_DOT, center, SQUARE_SIZE // 2 - 4, 6)
        else:
            pygame.draw.circle(dot, MOVE_DOT, center, SQUARE_SIZE // 7)
        screen.blit(dot, (col * SQUARE_SIZE, row * SQUARE_SIZE))

    pygame.draw.rect(screen, PANEL_COLOR, (0, 0, BOARD_SIZE, BOARD_SIZE), 4)


# rama: draws every piece currently on the board using the pre-loaded png images.
# uses square_to_rowcol so pieces appear in the correct position for both white and black perspectives.
def draw_pieces():
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece:
            row, col = square_to_rowcol(square)
            screen.blit(piece_images[piece.symbol()], (col * SQUARE_SIZE, row * SQUARE_SIZE))


piece_images = {}
piece_map = {
    'P': 'wp.png', 'R': 'wr.png', 'N': 'wn.png',
    'B': 'wb.png', 'Q': 'wq.png', 'K': 'wk.png',
    'p': 'bp.png', 'r': 'br.png', 'n': 'bn.png',
    'b': 'bb.png', 'q': 'bq.png', 'k': 'bk.png'
}
for piece, filename in piece_map.items():
    img = pygame.image.load(f"assets/pieces/{filename}")
    img = pygame.transform.smoothscale(img, (SQUARE_SIZE, SQUARE_SIZE))
    piece_images[piece] = img


# rama: handles a mouse click on the board. first click selects a piece and shows its legal moves.
# second click on a legal target executes the move, sends it to the server, plays a sound,
# and checks for checkmate/stalemate. automatically promotes pawns to queen on the last rank.
def handle_click(pos):
    global selected_square, my_turn, legal_targets, last_move, game_over, game_over_message
    global spectator_mode

    if spectator_mode:
        return

    if not my_turn or game_over or opponent_left:
        return

    col = pos[0] // SQUARE_SIZE
    row = pos[1] // SQUARE_SIZE

    if player_color == "black":
        file = 7 - col
        rank = row
    else:
        file = col
        rank = 7 - row

    square = chess.square(file, rank)

    if selected_square is None:
        piece = board.piece_at(square)
        if piece and piece.color == board.turn:
            selected_square = square
            legal_targets = [m.to_square for m in board.legal_moves if m.from_square == square]
    else:
        move = chess.Move(selected_square, square)
        piece = board.piece_at(selected_square)

        if piece and piece.piece_type == chess.PAWN:
            r = chess.square_rank(square)
            if r == 0 or r == 7:
                move = chess.Move(selected_square, square, promotion=chess.QUEEN)

        if move in board.legal_moves:
            is_capture = board.is_capture(move)
            board.push(move)
            last_move = move
            network.send({"type": "move", "move": move.uci(), "fen": board.fen()})

            if board.is_checkmate():
                winner = "Black" if board.turn == chess.WHITE else "White"
                game_over = True
                game_over_message = f"Checkmate! {winner} wins"
                end_sound.play()
            elif board.is_stalemate():
                game_over = True
                game_over_message = "Stalemate! It's a draw"
                end_sound.play()
            elif board.is_check():
                check_sound.play()
            elif is_capture:
                capture_sound.play()
            else:
                move_sound.play()

            my_turn = False

        selected_square = None
        legal_targets = []


# rama: creates the network connection to the server and starts receive_messages in a background thread.
# called once when the player confirms their username and enters the lobby.
def connect():
    global network
    network = Network()
    threading.Thread(target=receive_messages, daemon=True).start()


# rama: main game loop. processes all pygame events (clicks, keypresses, scroll) and calls the correct
# draw function based on the current state ("menu", "username", "lobby", "waiting", "leaderboard", "game").
running = True
play_btn = None
username_elements = None
lobby_elements = None
waiting_back_btn = None
leaderboard_back_btn = None
game_menu_btn = None
overlay_btn = None

while running:
    mouse_pos = pygame.mouse.get_pos()

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        elif event.type == pygame.MOUSEBUTTONDOWN:

            if state == "menu":
                if play_btn and play_btn.collidepoint(event.pos):
                    state = "username"

            elif state == "username":
                if username_elements:
                    u_input_rect, confirm_btn = username_elements
                    if u_input_rect.collidepoint(event.pos):
                        pass
                    if confirm_btn.collidepoint(event.pos):
                        username = username_input.strip() or "Player"
                        connect()
                        print("SENDING RECONNECT", username)
                        network.send({"type": "reconnect", "username": username})
                        state = "lobby"

            elif state == "lobby":
                if lobby_elements:
                    input_rect, create_btn, join_buttons, refresh_btn, leaderboard_btn, back_btn, time_buttons = lobby_elements

                    if input_rect.collidepoint(event.pos):
                        room_name_active = True
                    else:
                        room_name_active = False

                    for tbtn, opt in time_buttons:
                        if tbtn.collidepoint(event.pos):
                            selected_time_control = opt

                    if create_btn.collidepoint(event.pos):
                        name = room_name_input.strip() or "Room " + str(len(rooms) + 1)
                        rid = str(uuid.uuid4())[:8]
                        print("CREATE ROOM CLICKED")
                        network.send({
                            "type": "create_room",
                            "room_id": rid,
                            "name": name,
                            "username": username,
                            "time_control": selected_time_control
                        })

                    for jbtn, rid, ongoing in join_buttons:

                        if jbtn.collidepoint(event.pos):
                            if ongoing:
                                network.send({
                                    "type": "spectate",
                                    "room_id": rid
                                })
                            else:
                                network.send({
                                    "type": "join_room",
                                    "room_id": rid,
                                    "username": username
                                })

                    if refresh_btn.collidepoint(event.pos):
                        network.send({"type": "get_rooms"})

                    if leaderboard_btn.collidepoint(event.pos):
                        network.send({"type": "get_leaderboard"})
                        state = "leaderboard"

                    if back_btn.collidepoint(event.pos):
                        state = "menu"
                        network = None
                        rooms.clear()

            elif state == "leaderboard":
                if leaderboard_back_btn and leaderboard_back_btn.collidepoint(event.pos):
                    state = "lobby"
                    network.send({"type": "get_rooms"})

            elif state == "waiting":
                if waiting_back_btn and waiting_back_btn.collidepoint(event.pos):
                    state = "lobby"
                    network.send({"type": "get_rooms"})

            elif state == "game":
                # Overlay "Return to Menu" button takes priority
                if overlay_btn and (game_over or opponent_left) and overlay_btn.collidepoint(event.pos):
                    state = "menu"
                    network = None
                    board.reset()
                    player_color = None
                    spectator_mode = False
                    my_turn = False
                    last_move = None
                    selected_square = None
                    legal_targets.clear()
                    chat_messages.clear()
                    chat_input = ""
                    opponent_left = False
                    opponent_disconnected = False
                    opponent_disconnected_since = None
                    game_over = False
                    game_over_message = ""
                    time_control = None
                elif game_menu_btn and game_menu_btn.collidepoint(event.pos):
                    state = "menu"
                    network = None
                    board.reset()
                    player_color = None
                    spectator_mode = False
                    my_turn = False
                    last_move = None
                    selected_square = None
                    legal_targets.clear()
                    chat_messages.clear()
                    chat_input = ""
                    opponent_left = False
                    opponent_disconnected = False
                    opponent_disconnected_since = None
                    game_over = False
                    game_over_message = ""
                    time_control = None
                elif CHAT_INPUT_RECT.collidepoint(event.pos):
                    chat_active = True
                else:
                    chat_active = False
                    if event.pos[0] < BOARD_SIZE:
                        handle_click(event.pos)

        elif event.type == pygame.KEYDOWN:

            if state == "username":
                if event.key == pygame.K_BACKSPACE:
                    username_input = username_input[:-1]
                elif event.key == pygame.K_RETURN:
                    username = username_input.strip() or "Player"
                    connect()
                    network.send({
                        "type": "reconnect",
                        "username": username
                    })
                    state = "lobby"
                else:
                    if len(username_input) < 20:
                        username_input += event.unicode

            elif state == "lobby" and room_name_active:
                if event.key == pygame.K_BACKSPACE:
                    room_name_input = room_name_input[:-1]
                elif event.key == pygame.K_RETURN:
                    pass
                else:
                    if len(room_name_input) < 24:
                        room_name_input += event.unicode

            elif state == "game" and chat_active:
                if event.key == pygame.K_RETURN:
                    if chat_input.strip():
                        network.send({"type": "chat", "message": f"{username}: {chat_input}"})
                        chat_messages.append(f"You: {chat_input}")
                        chat_input = ""
                elif event.key == pygame.K_BACKSPACE:
                    chat_input = chat_input[:-1]
                else:
                    chat_input += event.unicode

        elif event.type == pygame.MOUSEWHEEL:
            if state == "lobby":
                room_scroll = max(0, min(room_scroll - event.y, max(0, len(rooms) - 6)))

    if state == "menu":
        play_btn = draw_menu(mouse_pos)

    elif state == "username":
        username_elements = draw_username(mouse_pos)

    elif state == "lobby":
        lobby_elements = draw_lobby(mouse_pos)

    elif state == "waiting":
        waiting_back_btn = draw_waiting(mouse_pos)

    elif state == "leaderboard":
        leaderboard_back_btn = draw_leaderboard(mouse_pos)

    elif state == "game":
        draw_board()
        draw_pieces()
        game_menu_btn = draw_chat()
        if game_over or opponent_left:
            overlay_btn = draw_game_overlay(mouse_pos)
        else:
            overlay_btn = None
            if opponent_disconnected:
                draw_reconnect_banner()

    pygame.display.flip()

pygame.quit()
