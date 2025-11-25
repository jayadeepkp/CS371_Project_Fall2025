# =================================================================================================
# Contributing Authors:	    Jayadeep Kothapalli
# Email Addresses:          jsko232@uky.edu
# Date:                     2025-11-23
# Purpose:                  Multi-threaded TCP Pong server.
#                           Accepts two clients, assigns left/right paddles, runs authoritative
#                           game loop (ball, paddles, score), and broadcasts state to both clients.
#                           Supports "reset" command so players can play multiple games.
# Misc:                     CS 371 Fall 2025 Project
# =================================================================================================

import socket
import threading
from pathlib import Path

import pygame
from assets.code.helperCode import Paddle, Ball

# Screen dimensions (must match what clients expect)
SCREEN_WIDTH = 640
SCREEN_HEIGHT = 480

# How many points to win
WIN_SCORE = 5

# Author:      Jayadeep Kothapalli
# Purpose:     Handle incoming messages from one client and update movement state.
# Pre:         conn is a connected TCP socket; move_dict is a shared dict with key "value".
# Post:        move_dict["value"] is updated based on messages from this client.
def handle_client_input(conn: socket.socket, move_dict: dict, reset_flag: dict, name: str) -> None:
    """
    Thread function to handle incoming messages from a single client.

    Messages:
      "up" / "down" / ""   -> update move_dict["value"]
      "reset"              -> set reset_flag["value"] = True
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
                    elif msg == "reset":
                        reset_flag["value"] = True
    except Exception as e:
        print(f"[SERVER] Exception in handle_client_input for {name}: {e}")

# Author:      Jayadeep Kothapalli
# Purpose:     Accept two clients, send them config, and run the main Pong game loop.
# Pre:         host/port are free; expects exactly two clients for left/right paddles.
# Post:        Broadcasts game state until a client disconnects, then shuts down server.
def run_server(host: str = "0.0.0.0", port: int = 6000) -> None:
    """
    Main server logic:
    - Creates a listening socket
    - Accepts two clients (left & right)
    - Sends initial config line: "width height side\n"
    - Runs authoritative game loop and broadcasts state
    """
    pygame.init()  # needed for pygame.Rect, etc.
    clock = pygame.time.Clock()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server.bind((host, port))
    server.listen(2)
    print(f"[SERVER] Listening on {host}:{port} ...")

    # Accept left player
    client_left, addr_left = server.accept()
    print(f"[SERVER] Left player connected from {addr_left}")
    config_left = f"{SCREEN_WIDTH} {SCREEN_HEIGHT} left\n".encode()
    client_left.sendall(config_left)

    # Accept right player
    client_right, addr_right = server.accept()
    print(f"[SERVER] Right player connected from {addr_right}")
    config_right = f"{SCREEN_WIDTH} {SCREEN_HEIGHT} right\n".encode()
    client_right.sendall(config_right)

    # Shared movement and reset flag
    left_move = {"value": ""}
    right_move = {"value": ""}
    reset_flag = {"value": False}

    # Start input threads
    t_left = threading.Thread(
        target=handle_client_input,
        args=(client_left, left_move, reset_flag, "LEFT"),
        daemon=True,
    )
    t_right = threading.Thread(
        target=handle_client_input,
        args=(client_right, right_move, reset_flag, "RIGHT"),
        daemon=True,
    )
    t_left.start()
    t_right.start()

    # Game objects (server authoritative)
    paddleHeight = 50
    paddleWidth = 10
    paddleStartPosY = (SCREEN_HEIGHT // 2) - (paddleHeight // 2)

    leftPaddle = Paddle(pygame.Rect(10, paddleStartPosY, paddleWidth, paddleHeight))
    rightPaddle = Paddle(
        pygame.Rect(SCREEN_WIDTH - 20, paddleStartPosY, paddleWidth, paddleHeight)
    )

    ball = Ball(pygame.Rect(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2, 5, 5), -5, 0)

    topWall = pygame.Rect(-10, 0, SCREEN_WIDTH + 20, 10)
    bottomWall = pygame.Rect(-10, SCREEN_HEIGHT - 10, SCREEN_WIDTH + 20, 10)

    lScore = 0
    rScore = 0

    print("[SERVER] Game loop started.")

    try:
        while True:
            # Handle reset request (from either client)
            if reset_flag["value"]:
                print("[SERVER] Reset requested, resetting game state.")
                lScore = 0
                rScore = 0
                leftPaddle.rect.y = paddleStartPosY
                rightPaddle.rect.y = paddleStartPosY
                ball.reset(nowGoing="left")
                reset_flag["value"] = False

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

            # If someone has already won, keep ball still (but allow reset)
            if lScore >= WIN_SCORE or rScore >= WIN_SCORE:
                # do nothing special here; clients will show win screen
                pass
            else:
                # Ball movement
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

            # Prepare state line for both clients
            state_line = f"{leftPaddle.rect.y} {rightPaddle.rect.y} {ball.rect.x} {ball.rect.y} {lScore} {rScore}\n"
            data = state_line.encode()

            # Send state to both clients
            try:
                client_left.sendall(data)
                client_right.sendall(data)
            except Exception as e:
                print(f"[SERVER] A client disconnected while sending. Error: {e}")
                break

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
        server.close()
        pygame.quit()
        print("[SERVER] Server shut down.")
        

if __name__ == "__main__":
    run_server(host="0.0.0.0", port=6000)