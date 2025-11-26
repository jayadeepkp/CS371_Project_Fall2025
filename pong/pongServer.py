# =================================================================================================
# Contributing Authors:     Jayadeep Kothapalli, Harshini Ponnam, Rudwika Manne
# Email Addresses:          jsko232@uky.edu, hpo245@uky.edu, rma425@uky.edu
# Date:                     2025-11-26
# Purpose:                  Multi-threaded TCP Pong server.
#                           Accepts two clients as players (left/right), plus any number of
#                           additional spectator clients. Runs authoritative game loop (ball,
#                           paddles, score) and broadcasts state to all connected clients.
#                           Supports coordinated "Play Again" rematch: both players must press R
#                           (client sends "ready") before a new game starts.
#                           Also serves a persistent leaderboard on HTTP port 80.
# Misc:                     CS 371 Fall 2025 Project
# =================================================================================================

from __future__ import annotations

import json
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List

import pygame
from assets.code.helperCode import Paddle, Ball
from security import encrypt_data, decrypt_data, register_user, authenticate

# Screen dimensions (must match what clients expect and what client uses)
SCREEN_WIDTH: int = 640
SCREEN_HEIGHT: int = 480

# How many points to win a game
WIN_SCORE: int = 5

# File to persist leaderboard between server restarts
LEADERBOARD_FILE: str = "leaderboard.json"

# Protects concurrent access to leaderboard
leaderboard_lock: threading.Lock = threading.Lock()


# ---------------------------------------------------------------------------------------------
# Leaderboard helpers
# ---------------------------------------------------------------------------------------------

# Author:      Harshini Ponnam
# Purpose:     Load the persistent leaderboard from disk if present.
# Pre:         LEADERBOARD_FILE may or may not exist; process has read permission.
# Post:        Returns a dict mapping player initials (str) to win counts (int).
def load_leaderboard() -> Dict[str, int]:
    """Load leaderboard from disk or return empty dict if not present."""
    try:
        with open(LEADERBOARD_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# Author:      Harshini Ponnam
# Purpose:     Save the current leaderboard mapping to disk as JSON.
# Pre:         board is a dictionary mapping player initials (str) to win counts (int);
#              process has write permission in the current directory.
# Post:        LEADERBOARD_FILE is overwritten with the serialized leaderboard.
def save_leaderboard(board: Dict[str, int]) -> None:
    """Save leaderboard to disk."""
    with open(LEADERBOARD_FILE, "w") as f:
        json.dump(board, f)


# In-memory leaderboard: { "HP": 3, "RM": 1, ... }
leaderboard: Dict[str, int] = load_leaderboard()


# Author:      Harshini Ponnam
# Purpose:     Increment win count for given player initials and persist update to disk.
# Pre:         initials is a non-empty string identifying the player. leaderboard_lock
#              is available to synchronize concurrent writes.
# Post:        Global leaderboard[initials] is incremented by 1 and saved to LEADERBOARD_FILE.
def record_win(initials: str) -> None:
    """
    Increment win count for the given player's initials and persist to disk.
    """
    if not initials:
        return
    initials = initials.strip().upper()
    with leaderboard_lock:
        leaderboard[initials] = leaderboard.get(initials, 0) + 1
        save_leaderboard(leaderboard)


# ---------------------------------------------------------------------------------------------
# HTTP leaderboard handler
# ---------------------------------------------------------------------------------------------

# Author:      Harshini Ponnam
# Purpose:     Serve a simple HTML leaderboard page on HTTP GET.
# Pre:         Global leaderboard dict is initialized; leaderboard_lock protects access.
# Post:        For paths "/" or "/leaderboard", sends a 200 response with an HTML table
#              of player initials and win counts. For other paths, sends 404.
class LeaderboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in ("/", "/leaderboard"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        with leaderboard_lock:
            items = sorted(leaderboard.items(), key=lambda kv: kv[1], reverse=True)

        rows = ""
        for initials, wins in items:
            rows += f"<tr><td>{initials}</td><td>{wins}</td></tr>"

        html = f"""
        <html>
        <head>
            <title>CS371 Pong Leaderboard</title>
            <style>
                body {{ font-family: Arial, sans-serif; background: #111; color: #eee; }}
                table {{ border-collapse: collapse; margin: 40px auto; }}
                th, td {{ border: 1px solid #555; padding: 8px 16px; }}
                th {{ background: #333; }}
                h1 {{ text-align: center; }}
            </style>
        </head>
        <body>
            <h1>Pong Leaderboard</h1>
            <table>
                <tr><th>Player Initials</th><th>Wins</th></tr>
                {rows or "<tr><td colspan='2'>No games recorded yet.</td></tr>"}
            </table>
        </body>
        </html>
        """

        html_bytes = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.end_headers()
        self.wfile.write(html_bytes)


# Author:      Harshini Ponnam
# Purpose:     Start a blocking HTTP server on port 80 in a background thread.
# Pre:         Port 80 is available on the host (may require administrator privileges).
# Post:        LeaderboardHandler is used to serve HTTP requests until the process exits
#              or an exception occurs. This function is intended to be run in a daemon thread.
def start_leaderboard_server() -> None:
    """Start HTTP leaderboard server on port 80."""
    try:
        httpd = HTTPServer(("0.0.0.0", 80), LeaderboardHandler)
        print("[SERVER] Leaderboard HTTP server running on port 80...")
        httpd.serve_forever()
    except Exception as e:
        print(f"[SERVER] Could not start leaderboard HTTP server on port 80: {e}")


# ---------------------------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------------------------

# ---------------------------------------------------------------------------------------------
# Player registration + authentication (plaintext commands, encrypted gameplay)
# ---------------------------------------------------------------------------------------------

# Author:   Rudwika Manne
# Purpose:     Simple username/password registration + login before the game starts.
#              Protocol (per client, PLAINTEXT lines):
#                  register <username> <password>
#                  login <username> <password>
#              Server stores a salted+hashed password in users.json using security.py helpers.
# Pre:         conn is a connected socket. Called right after the TCP connection is accepted.
# Post:        Returns an authenticated username string, which we later shorten to initials for
#              the leaderboard. Retries until success or disconnect.
def auth_player(conn: socket.socket, role: str) -> str:
    """
    Handle registration / login for a single player.

    Returns:
        Authenticated username (str). If the client disconnects, raises ConnectionError.
    """
    intro = (
        f"AUTH {role}: type 'register <username> <password>' "
        f"or 'login <username> <password>'\n"
    )
    conn.sendall(intro.encode("utf-8"))

    buffer = ""
    while True:
        data = conn.recv(1024)
        if not data:
            raise ConnectionError(f"Client {role} disconnected during auth.")
        buffer += data.decode("utf-8")

        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            parts = line.strip().split()
            if len(parts) != 3:
                conn.sendall(b"ERR invalid format; use register/login <user> <pass>\n")
                continue

            cmd, username, password = parts
            cmd = cmd.lower()

            if cmd == "register":
                if register_user(username, password):
                    conn.sendall(b"OK registered\n")
                    return username
                else:
                    conn.sendall(b"ERR username already exists\n")
            elif cmd == "login":
                if authenticate(username, password):
                    conn.sendall(b"OK logged-in\n")
                    return username
                else:
                    conn.sendall(b"ERR bad username or password\n")
            else:
                conn.sendall(b"ERR unknown command (use register/login)\n")


# Author:      Jayadeep Kothapalli, Rudwika Manne
# Purpose:     Receive encrypted movement and rematch messages from a single client.
# Pre:         conn is a connected TCP socket. move_dict and ready_flag are dictionaries
#              with key "value" shared with the main thread. After authentication, the
#              client sends encrypted Fernet tokens (one per line) for:
#                  "up", "down", "" (no movement), or "ready".
# Post:        While the connection is open, move_dict["value"] is updated whenever an
#              "up"/"down"/"" message is received. When a "ready" message is received,
#              ready_flag["value"] is set to True. When the client disconnects, the
#              function returns and the thread exits.
def handle_client_input(
    conn: socket.socket,
    move_dict: Dict[str, str],
    ready_flag: Dict[str, bool],
    name: str
) -> None:
    """
    Thread function to handle incoming *encrypted* messages from a single client.

    Each line from the client is:
        encrypt_data("up" | "down" | "" | "ready") + b"\\n"
    """
    try:
        with conn:
            buffer = b""
            while True:
                data = conn.recv(1024)
                if not data:
                    print(f"[SERVER] {name} disconnected.")
                    break
                buffer += data

                # Process complete encrypted lines
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        msg = decrypt_data(line)
                    except Exception as e:
                        print(f"[SERVER] Failed to decrypt message from {name}: {e}")
                        continue

                    msg = msg.strip()
                    if msg in ("up", "down", ""):
                        move_dict["value"] = msg
                    elif msg == "ready":
                        ready_flag["value"] = True
    except Exception as e:
        print(f"[SERVER] Exception in handle_client_input for {name}: {e}")


# Author:      Jayadeep Kothapalli
# Purpose:     Accept additional spectator clients and add them to a shared list.
# Pre:         server is a bound and listening TCP socket. spectators is a list of
#              socket objects. spectators_lock is a threading.Lock protecting the list.
# Post:        For each new connection, sends a "spec" config line and appends the
#              connection to spectators. If the server socket is closed (accept raises
#              OSError), the loop breaks and the thread exits.
def accept_spectators(
    server: socket.socket,
    spectators: List[socket.socket],
    spectators_lock: threading.Lock
) -> None:
    """Accept spectator clients and register them for state updates."""
    while True:
        try:
            conn, addr = server.accept()
        except OSError:
            # Server socket was closed; exit thread
            print("[SERVER] accept_spectators: server socket closed, stopping.")
            break

        print(f"[SERVER] Spectator connected from {addr}")
        try:
            config_spec = f"{SCREEN_WIDTH} {SCREEN_HEIGHT} spec\n".encode()
            conn.sendall(config_spec)
        except Exception as e:
            print(f"[SERVER] Failed to send config to spectator {addr}: {e}")
            conn.close()
            continue

        with spectators_lock:
            spectators.append(conn)
        print(f"[SERVER] Total spectators: {len(spectators)}")


# ---------------------------------------------------------------------------------------------
# Main server
# ---------------------------------------------------------------------------------------------

# Author:      Jayadeep Kothapalli
# Purpose:     Run the main Pong server: accept two player clients, accept any number of
#              spectator clients, run the authoritative game loop, handle scoring, wins,
#              rematches (both players must press R), and broadcast state to all clients.
# Pre:         host and port are free to bind. helperCode (Paddle, Ball) and pygame are
#              installed and importable. Called as the main entry point for the server.
# Post:        Continues running until a player disconnects or a send fails. On exit,
#              all client sockets, the listening socket, and pygame are cleanly closed.
def run_server(host: str = "0.0.0.0", port: int = 6000) -> None:
    """
    Main server logic:
    - Creates a listening socket
    - Accepts two clients (left & right) as players
    - Starts a thread to accept any number of additional spectator clients
    - Sends initial config line: "width height side\n" (side = left/right/spec)
    - Runs authoritative game loop and broadcasts state to all clients
    """
    pygame.init()  # needed for pygame.Rect, etc.
    clock: pygame.time.Clock = pygame.time.Clock()

    server: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server.bind((host, port))
    server.listen(10)  # allow more than 2 pending connections
    print(f"[SERVER] Listening on {host}:{port} ...")

    # Start HTTP leaderboard server in the background
    http_thread: threading.Thread = threading.Thread(
        target=start_leaderboard_server,
        daemon=True,
    )
    http_thread.start()

    # -------------------------------------------------------------------------
    # Accept left and right players (required for basic two-player game)
    # -------------------------------------------------------------------------
    client_left, addr_left = server.accept()
    print(f"[SERVER] Left player connected from {addr_left}")
    client_left.sendall(f"{SCREEN_WIDTH} {SCREEN_HEIGHT} left\n".encode("utf-8"))

    client_right, addr_right = server.accept()
    print(f"[SERVER] Right player connected from {addr_right}")
    client_right.sendall(f"{SCREEN_WIDTH} {SCREEN_HEIGHT} right\n".encode("utf-8"))

    # -------------------------------------------------------------------------
    # Player registration + authentication (uses security.py)
    # -------------------------------------------------------------------------
    left_username = auth_player(client_left, "LEFT")
    right_username = auth_player(client_right, "RIGHT")

    # Use first 3 characters of username as leaderboard initials
    left_initials: str = (left_username[:3] or "LEFT").upper()
    right_initials: str = (right_username[:3] or "RIGHT").upper()


    # -------------------------------------------------------------------------
    # Shared movement and rematch state
    # -------------------------------------------------------------------------
    left_move: Dict[str, str] = {"value": ""}
    right_move: Dict[str, str] = {"value": ""}
    left_ready: Dict[str, bool] = {"value": False}
    right_ready: Dict[str, bool] = {"value": False}

    # Spectators: additional clients who can watch the game
    spectators: List[socket.socket] = []
    spectators_lock: threading.Lock = threading.Lock()

    # Start input threads for the two players
    t_left: threading.Thread = threading.Thread(
        target=handle_client_input,
        args=(client_left, left_move, left_ready, "LEFT"),
        daemon=True,
    )
    t_right: threading.Thread = threading.Thread(
        target=handle_client_input,
        args=(client_right, right_move, right_ready, "RIGHT"),
        daemon=True,
    )
    t_left.start()
    t_right.start()

    # Start an acceptor thread to allow any number of spectator connections
    t_specs: threading.Thread = threading.Thread(
        target=accept_spectators,
        args=(server, spectators, spectators_lock),
        daemon=True,
    )
    t_specs.start()

    # -------------------------------------------------------------------------
    # Game objects (server authoritative)
    # -------------------------------------------------------------------------
    paddleHeight: int = 50
    paddleWidth: int = 10
    paddleStartPosY: int = (SCREEN_HEIGHT // 2) - (paddleHeight // 2)

    leftPaddle: Paddle = Paddle(
        pygame.Rect(10, paddleStartPosY, paddleWidth, paddleHeight)
    )
    rightPaddle: Paddle = Paddle(
        pygame.Rect(SCREEN_WIDTH - 20, paddleStartPosY, paddleWidth, paddleHeight)
    )

    ball: Ball = Ball(
        pygame.Rect(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2, 5, 5),
        -5,
        0,
    )

    topWall: pygame.Rect = pygame.Rect(-10, 0, SCREEN_WIDTH + 20, 10)
    bottomWall: pygame.Rect = pygame.Rect(-10, SCREEN_HEIGHT - 10, SCREEN_WIDTH + 20, 10)

    lScore: int = 0
    rScore: int = 0
    winner_recorded: bool = False

    print("[SERVER] Game loop started.")

    try:
        while True:
            # Update paddles based on last movement commands

            # Left paddle
            if left_move["value"] == "down":
                if leftPaddle.rect.bottom < SCREEN_HEIGHT - 10:
                    leftPaddle.rect.y += leftPaddle.speed
            elif left_move["value"] == "up":
                if leftPaddle.rect.top > 10:
                    leftPaddle.rect.y -= leftPaddle.speed

            # Right paddle
            if right_move["value"] == "down":
                if rightPaddle.rect.bottom < SCREEN_HEIGHT - 10:
                    rightPaddle.rect.y += rightPaddle.speed
            elif right_move["value"] == "up":
                if rightPaddle.rect.top > 10:
                    rightPaddle.rect.y -= rightPaddle.speed

            # ---------------------------------------------------------------------------------
            # Win + coordinated rematch logic
            # ---------------------------------------------------------------------------------
            if lScore >= WIN_SCORE or rScore >= WIN_SCORE:
                # Record winner once
                if not winner_recorded:
                    if lScore >= WIN_SCORE:
                        record_win(left_initials)
                        print(f"[SERVER] Game over. Winner: {left_initials}")
                    elif rScore >= WIN_SCORE:
                        record_win(right_initials)
                        print(f"[SERVER] Game over. Winner: {right_initials}")
                    winner_recorded = True

                # Wait for both players to press R (send "ready")
                if left_ready["value"] and right_ready["value"]:
                    print("[SERVER] Both players ready. Starting rematch.")
                    lScore = 0
                    rScore = 0
                    leftPaddle.rect.y = paddleStartPosY
                    rightPaddle.rect.y = paddleStartPosY
                    ball.reset(nowGoing="left")
                    winner_recorded = False
                    left_ready["value"] = False
                    right_ready["value"] = False

                # While game is in "win" state, do not move the ball
            else:
                # Normal ball movement
                ball.updatePos()

                # Ball out of bounds -> score
                if ball.rect.x > SCREEN_WIDTH:
                    lScore += 1
                    ball.reset(nowGoing="left")
                elif ball.rect.x < 0:
                    rScore += 1
                    ball.reset(nowGoing="right")

                # Ball & paddle collisions
                if ball.rect.colliderect(leftPaddle.rect):
                    ball.hitPaddle(leftPaddle.rect.center[1])
                elif ball.rect.colliderect(rightPaddle.rect):
                    ball.hitPaddle(rightPaddle.rect.center[1])

                # Ball & wall collisions
                if ball.rect.colliderect(topWall) or ball.rect.colliderect(bottomWall):
                    ball.hitWall()

            # Prepare state line for all clients
            state_line: str = (
                f"{leftPaddle.rect.y} {rightPaddle.rect.y} "
                f"{ball.rect.x} {ball.rect.y} {lScore} {rScore}"
            )
            # Plaintext version (for spectators)
            plain_state: bytes = (state_line + "\n").encode("utf-8")
            # Encrypted version (for LEFT/RIGHT players)
            enc_state: bytes = encrypt_data(state_line) + b"\n"

            # Send encrypted state to both players
            try:
                client_left.sendall(enc_state)
                client_right.sendall(enc_state)
            except Exception as e:
                print(f"[SERVER] A player disconnected while sending. Error: {e}")
                break

            # Send plaintext state to all spectators (no change needed on spectator clients)
            with spectators_lock:
                dead_specs: List[socket.socket] = []
                for spec in spectators:
                    try:
                        spec.sendall(plain_state)
                    except Exception as e:
                        print(f"[SERVER] Spectator send failed, removing: {e}")
                        dead_specs.append(spec)
                for d in dead_specs:
                    try:
                        d.close()
                    except Exception:
                        pass
                    spectators.remove(d)

            clock.tick(60)  # 60 updates per second

    finally:
        print("[SERVER] Shutting down.")
        try:
            client_left.close()
        except Exception:
            pass
        try:
            client_right.close()
        except Exception:
            pass

        # Close all spectators
        try:
            with spectators_lock:
                for spec in spectators:
                    try:
                        spec.close()
                    except Exception:
                        pass
                spectators.clear()
        except Exception:
            pass

        try:
            server.close()
        except Exception:
            pass

        pygame.quit()
        print("[SERVER] Server shut down.")


if __name__ == "__main__":
    run_server(host="0.0.0.0", port=6000)