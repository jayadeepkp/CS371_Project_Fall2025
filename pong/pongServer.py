# =================================================================================================
# Contributing Authors:     Jayadeep Kothapalli, Harshini Ponnam
# Email Addresses:          jsko232@uky.edu, hpo245@uky.edu
# Date:                     2025-11-23
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

# Author:      Jayadeep Kothapalli
# Purpose:     Receive movement and rematch messages from a single client.
# Pre:         conn is a connected TCP socket. move_dict and ready_flag are dictionaries
#              with key "value" shared with the main thread. move_dict["value"] holds the
#              last valid movement command ("up", "down", or ""), and ready_flag["value"]
#              is a boolean indicating if this player has requested a rematch.
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
    Thread function to handle incoming messages from a single client.

    Messages:
      "up" / "down" / ""   -> update move_dict["value"]
      "ready"              -> set ready_flag["value"] = True (player pressed R to rematch)
    """
    try:
        with conn:
            buffer = ""
            while True:
                data = conn.recv(1024)
                if not data:
                    print(f"[SERVER] {name} disconnected.")
                    break
                buffer += data.decode()
                # Process complete lines
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    msg = line.strip()
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
    config_left = f"{SCREEN_WIDTH} {SCREEN_HEIGHT} left\n".encode()
    client_left.sendall(config_left)

    client_right, addr_right = server.accept()
    print(f"[SERVER] Right player connected from {addr_right}")
    config_right = f"{SCREEN_WIDTH} {SCREEN_HEIGHT} right\n".encode()
    client_right.sendall(config_right)

    # Ask for player initials for leaderboard recording
    try:
        left_initials: str = input("Enter initials for LEFT player (e.g., HP): ").strip().upper() or "LEFT"
        right_initials: str = input("Enter initials for RIGHT player (e.g., RM): ").strip().upper() or "RIGHT"
    except EOFError:
        left_initials = "LEFT"
        right_initials = "RIGHT"

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
                f"{ball.rect.x} {ball.rect.y} {lScore} {rScore}\n"
            )
            data: bytes = state_line.encode()

            # Send state to both players
            try:
                client_left.sendall(data)
                client_right.sendall(data)
            except Exception as e:
                print(f"[SERVER] A player disconnected while sending. Error: {e}")
                break

            # Send state to all spectators
            with spectators_lock:
                dead_specs: List[socket.socket] = []
                for spec in spectators:
                    try:
                        spec.sendall(data)
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