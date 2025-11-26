# =================================================================================================
# Contributing Authors:      Harshini Ponnam, Rudwika Manne, Jayadeep Kothapalli
# Email Addresses:           hpo245@uky.edu, rma425@uky.edu, jsko232@uky.edu
# Date:                      2025-11-23
# Purpose:                   Pong game client - Tkinter start screen (if available) or CLI fallback.
#                            Connects to TCP server using TCP sockets, sends paddle movement, and
#                            renders authoritative game state from the server using Pygame.
#                            Uses a background thread to receive state to reduce input lag.
#                            Supports spectator mode and coordinated "Play Again" rematch:
#                            both players must press R (send "ready") before a new game starts.
# Misc:                      CS 371 Fall 2025 Project
# =================================================================================================

import socket
from typing import Optional, Tuple, TextIO, Dict
from pathlib import Path
from threading import Thread, Lock

import pygame
from assets.code.helperCode import *  # Paddle, Ball, updateScore, etc.

# Try to import tkinter; if not available (e.g., some macOS Python builds), fall back to CLI.
try:
    import tkinter as tk
    HAS_TK: bool = True
except Exception:
    HAS_TK = False
    tk = None

# ---------------------------------------------------------------------------------------------
# Constants / asset paths
# ---------------------------------------------------------------------------------------------
BASE_DIR: Path = Path(__file__).resolve().parent          # .../pong
ASSETS_DIR: Path = BASE_DIR / "assets"
FONTS_DIR: Path = ASSETS_DIR / "fonts"
IMAGES_DIR: Path = ASSETS_DIR / "images"
SOUNDS_DIR: Path = ASSETS_DIR / "sounds"

WIN_SCORE: int = 5  # must match server


# ---------------------------------------------------------------------------------------------
# recv_state function
# ---------------------------------------------------------------------------------------------
# Author:      Harshini Ponnam
# Purpose:     Read one line of game state from the server and parse it.
# Pre:         sock_file is a text-mode file-like object wrapping the TCP socket with
#              one state update per line in the format:
#                  <leftPaddleY> <rightPaddleY> <ballX> <ballY> <leftScore> <rightScore>
# Post:        Returns a 6-tuple of integers (l_y, r_y, b_x, b_y, l_score, r_score)
#              on success, or None if the connection is closed or the data is invalid.
def recv_state(sock_file: TextIO) -> Optional[Tuple[int, int, int, int, int, int]]:
    """
    Read a single state update line from the server.

    Expected format (one line, space-separated):
        <leftPaddleY> <rightPaddleY> <ballX> <ballY> <leftScore> <rightScore>

    Returns:
        (l_y, r_y, b_x, b_y, l_score, r_score) as ints,
        or None if server closed or sent bad data.
    """
    try:
        line: str = sock_file.readline()
        if not line:
            print("recv_state: empty line (server closed connection).")
            return None
        parts = line.strip().split()
        if len(parts) != 6:
            print("recv_state: bad state line from server:", repr(line))
            return None
        l_y, r_y, b_x, b_y, l_score, r_score = map(int, parts)
        return l_y, r_y, b_x, b_y, l_score, r_score
    except Exception as e:
        print("recv_state: exception while reading:", e)
        return None


# ---------------------------------------------------------------------------------------------
# receive_loop function
# ---------------------------------------------------------------------------------------------
# Author:      Harshini Ponnam
# Purpose:     Continuously read state updates from the server on a background thread and
#              store the newest values into a shared dictionary.
# Pre:         sock_file wraps the connected TCP socket; shared_state is a dict with keys
#              "l_y", "r_y", "b_x", "b_y", "lScore", "rScore", and "connected"; state_lock
#              is a Lock protecting access to shared_state.
# Post:        While valid data is received, shared_state is updated. If data is invalid
#              or the server closes the connection, shared_state["connected"] is set to
#              False and the thread exits.
def receive_loop(sock_file: TextIO, shared_state: Dict[str, int], state_lock: Lock) -> None:
    """
    Background thread function that continuously receives state updates from the server
    and writes them into shared_state.
    """
    while True:
        state = recv_state(sock_file)
        if state is None:
            with state_lock:
                # Use 0/1 for int compatibility; bool works as an int subclass.
                shared_state["connected"] = 0
            print("receive_loop: server closed connection or bad data, stopping receiver.")
            break

        l_y, r_y, b_x, b_y, l_score, r_score = state

        with state_lock:
            shared_state["l_y"] = l_y
            shared_state["r_y"] = r_y
            shared_state["b_x"] = b_x
            shared_state["b_y"] = b_y
            shared_state["lScore"] = l_score
            shared_state["rScore"] = r_score


# ---------------------------------------------------------------------------------------------
# Main game loop - uses shared_state updated by background thread
# ---------------------------------------------------------------------------------------------
# Author:      Jayadeep Kothapalli
# Purpose:     Render the Pong game state from the server and send local paddle movement.
#              Handles keyboard input, draws paddles/ball/score, and shows a win message
#              when someone reaches WIN_SCORE. When the player presses R after game over,
#              the client sends "ready" to the server. A new game starts when the server
#              resets the scores (after both players are ready).
# Pre:         screenWidth, screenHeight, and playerPaddle ("left", "right", or "spec")
#              are provided by the server's config line. client is a connected TCP socket.
#              Asset files (fonts, sounds, logo) exist in the assets directory.
# Post:        When the user closes the window or the connection drops, the loop ends,
#              the socket and pygame are cleaned up, and the function returns.
def playGame(screenWidth: int, screenHeight: int, playerPaddle: str, client: socket.socket) -> None:
    print("Starting playGame with:", screenWidth, screenHeight, playerPaddle)

    # Treat "spec" as spectator mode
    is_spectator: bool = (playerPaddle == "spec")

    # Wrap the socket in a file-like object for line-based reading
    sock_file: TextIO = client.makefile("r")

    # Shared state between network thread and game loop
    # Using int for everything; "connected" uses 1/0 as a simple flag.
    state_lock: Lock = Lock()
    shared_state: Dict[str, int] = {
        "l_y": screenHeight // 2,
        "r_y": screenHeight // 2,
        "b_x": screenWidth // 2,
        "b_y": screenHeight // 2,
        "lScore": 0,
        "rScore": 0,
        "connected": 1,
    }

    # Start background receiver thread
    recv_thread: Thread = Thread(
        target=receive_loop,
        args=(sock_file, shared_state, state_lock),
        daemon=True,
    )
    recv_thread.start()

    # Pygame inits
    pygame.mixer.pre_init(44100, -16, 2, 2048)
    pygame.init()

    # Constants
    WHITE = (255, 255, 255)
    clock: pygame.time.Clock = pygame.time.Clock()

    # Load fonts
    scoreFont = pygame.font.Font(str(FONTS_DIR / "pong-score.ttf"), 32)
    winFont = pygame.font.Font(str(FONTS_DIR / "visitor.ttf"), 48)

    # Load sounds
    pointSound = pygame.mixer.Sound(str(SOUNDS_DIR / "point.wav"))
    bounceSound = pygame.mixer.Sound(str(SOUNDS_DIR / "bounce.wav"))

    # Display objects
    screen = pygame.display.set_mode((screenWidth, screenHeight))
    pygame.display.set_caption("CS371 Pong Client")

    # Load logo and position it at the top center
    logo = pygame.image.load(str(IMAGES_DIR / "logo.png")).convert_alpha()
    logoRect = logo.get_rect()
    logoRect.center = (screenWidth // 2, 20)

    winMessage = pygame.Rect(0, 0, 0, 0)
    topWall = pygame.Rect(-10, 0, screenWidth + 20, 10)
    bottomWall = pygame.Rect(-10, screenHeight - 10, screenWidth + 20, 10)

    centerLine = []
    for i in range(0, screenHeight, 10):
        centerLine.append(pygame.Rect((screenWidth / 2) - 5, i, 5, 5))

    # Paddle properties and init
    paddleHeight: int = 50
    paddleWidth: int = 10
    paddleStartPosY: float = (screenHeight / 2) - (paddleHeight / 2)
    leftPaddle = Paddle(pygame.Rect(10, paddleStartPosY, paddleWidth, paddleHeight))
    rightPaddle = Paddle(
        pygame.Rect(screenWidth - 20, paddleStartPosY, paddleWidth, paddleHeight)
    )

    # Ball (position is driven by server; we just store and draw it)
    ball = Ball(pygame.Rect(screenWidth / 2, screenHeight / 2, 5, 5), -5, 0)

    # Decide which paddle this client controls (if not spectator)
    if playerPaddle == "left":
        opponentPaddleObj = rightPaddle
        playerPaddleObj = leftPaddle
    elif playerPaddle == "right":
        opponentPaddleObj = leftPaddle
        playerPaddleObj = rightPaddle
    else:
        # Spectator: no real "player" paddle, but we still need references
        opponentPaddleObj = rightPaddle
        playerPaddleObj = leftPaddle  # dummy; we won't move it from input

    # Scores and previous values for sound & rematch logic
    lScore: int = 0
    rScore: int = 0
    prev_lScore: int = 0
    prev_rScore: int = 0
    prev_ball_y: int = ball.rect.y

    sent_ready: bool = False  # whether THIS client has already sent "ready" for the current game

    running: bool = True

    while running:
        # Wipe the screen
        screen.fill((0, 0, 0))

        # Handle events (local input)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                print("QUIT event received, closing game window.")
                running = False

            elif event.type == pygame.KEYDOWN:
                # Only players (not spectators) can control paddles
                if not is_spectator:
                    if event.key == pygame.K_DOWN:
                        playerPaddleObj.moving = "down"
                    elif event.key == pygame.K_UP:
                        playerPaddleObj.moving = "up"

                    # Press R to send "ready" AFTER game over (only once per game)
                    if (lScore >= WIN_SCORE or rScore >= WIN_SCORE) and event.key == pygame.K_r:
                        if not sent_ready:
                            try:
                                client.sendall(b"ready\n")
                                sent_ready = True
                                print("Sent 'ready' for rematch to server.")
                            except Exception as e:
                                print("Error sending ready:", e)
                                running = False

            elif event.type == pygame.KEYUP:
                if not is_spectator and event.key in (pygame.K_UP, pygame.K_DOWN):
                    playerPaddleObj.moving = ""

        # If network thread reported disconnect, exit game loop
        with state_lock:
            if not shared_state["connected"]:
                print("Main loop: shared_state['connected'] is False, exiting.")
                running = False

        # -------------------------------------------------------------------------------------
        # Send this client's movement to the server ("up", "down", or "").
        # Spectators always send "" (no movement).
        # -------------------------------------------------------------------------------------
        try:
            move_str: str = "" if is_spectator else playerPaddleObj.moving
            client.sendall((move_str + "\n").encode())
        except Exception as e:
            print("Error sending to server:", e)
            running = False

        # -------------------------------------------------------------------------------------
        # Read latest state snapshot from shared_state (non-blocking)
        # -------------------------------------------------------------------------------------
        with state_lock:
            l_y = shared_state["l_y"]
            r_y = shared_state["r_y"]
            b_x = shared_state["b_x"]
            b_y = shared_state["b_y"]
            lScore = shared_state["lScore"]
            rScore = shared_state["rScore"]

        # Detect game-over → new-game transition (server reset scores)
        game_was_over: bool = (prev_lScore >= WIN_SCORE or prev_rScore >= WIN_SCORE)
        game_is_over: bool = (lScore >= WIN_SCORE or rScore >= WIN_SCORE)
        if game_was_over and not game_is_over:
            # Scores went from win back to non-win ⇒ new game started ⇒ clear our ready flag.
            sent_ready = False

        # Update paddles and ball with latest state
        leftPaddle.rect.y = l_y
        rightPaddle.rect.y = r_y

        prev_ball_y = ball.rect.y
        ball.rect.x = b_x
        ball.rect.y = b_y

        # Sound for scoring
        if lScore > prev_lScore or rScore > prev_rScore:
            pointSound.play()
        prev_lScore, prev_rScore = lScore, rScore

        # Simple bounce sound if ball crosses top/bottom boundaries
        if (prev_ball_y > 0 and ball.rect.y <= 0) or (
            prev_ball_y < screenHeight - 10 and ball.rect.y >= screenHeight - 10
        ):
            bounceSound.play()

        # If the game is over, display only the win message (no extra R text)
        if lScore >= WIN_SCORE or rScore >= WIN_SCORE:
            # Big win text (use ALL CAPS so the font has glyphs)
            winText: str = "PLAYER 1 WINS!" if lScore >= WIN_SCORE else "PLAYER 2 WINS!"
            textSurface = winFont.render(winText, False, WHITE, (0, 0, 0))
            textRect = textSurface.get_rect()
            textRect.center = (screenWidth // 2, screenHeight // 2)
            winMessage = screen.blit(textSurface, textRect)
        else:
            # Ball is already updated by server; just draw it.
            pygame.draw.rect(screen, WHITE, ball)
            winMessage = pygame.Rect(0, 0, 0, 0)  # nothing special to update

        # Draw the dotted center line
        for i in centerLine:
            pygame.draw.rect(screen, WHITE, i)

        # Draw paddles
        for paddle in [playerPaddleObj, opponentPaddleObj]:
            pygame.draw.rect(screen, WHITE, paddle)

        pygame.draw.rect(screen, WHITE, topWall)
        pygame.draw.rect(screen, WHITE, bottomWall)

        # Draw logo at top
        screen.blit(logo, logoRect)

        # Draw score using helper code
        scoreRect = updateScore(lScore, rScore, screen, WHITE, scoreFont)

        pygame.display.update(
            [topWall, bottomWall, ball, leftPaddle, rightPaddle, scoreRect, winMessage]
        )
        clock.tick(60)   # 60 FPS

    # Clean up when loop ends
    print("Exiting playGame() cleanly.")
    sock_file.close()
    client.close()
    pygame.quit()
    return


# ---------------------------------------------------------------------------------------------
# joinServer function
# ---------------------------------------------------------------------------------------------
# Author:      Rudwika Manne
# Purpose:     Connect the client to the Pong server using the IP and port from the Tkinter UI,
#              receive the initial configuration line, and then start the game loop.
# Pre:         Tkinter window is running. User has entered IP and port. Server is already
#              running and listening on that address and port.
# Post:        On success, hides the Tkinter window and calls playGame(). On failure, shows
#              an error message in errorLabel and leaves the window open.
def joinServer(ip: str, port: str, errorLabel, app) -> None:
    """
    Fired when the Join button is clicked on the Tkinter screen.
    """
    client: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Validate and convert port
    try:
        server_port: int = int(port)
    except ValueError:
        errorLabel.config(text="Port must be an integer.")
        errorLabel.update()
        return

    # Try to connect to server
    try:
        client.connect((ip, server_port))
    except Exception as e:
        errorLabel.config(text=f"Could not connect: {e}")
        errorLabel.update()
        return

    # Receive initial configuration from server: "width height side\n"
    try:
        cfg: str = client.recv(1024).decode().strip()
        parts = cfg.split()
        if len(parts) != 3:
            errorLabel.config(text=f"Bad config from server: {cfg}")
            errorLabel.update()
            client.close()
            return

        screenWidth: int = int(parts[0])
        screenHeight: int = int(parts[1])
        playerPaddle: str = parts[2]  # "left" or "right" or "spec"

        errorLabel.config(
            text=f"Connected! Screen: {screenWidth}x{screenHeight}, you are {playerPaddle}."
        )
        errorLabel.update()

    except Exception as e:
        errorLabel.config(text=f"Error receiving config: {e}")
        errorLabel.update()
        client.close()
        return

    # Close Tkinter window and start the game
    app.withdraw()  # Hide Tk window
    playGame(screenWidth, screenHeight, playerPaddle, client)
    app.quit()      # End Tk event loop after game exits


# ---------------------------------------------------------------------------------------------
# startScreen function
# ---------------------------------------------------------------------------------------------
# Author:      Rudwika Manne, Jayadeep Kothapalli
# Purpose:     Display a Tkinter-based start screen that asks the user for server IP and port.
#              Shows the project logo and a Join button that calls joinServer().
# Pre:         Tkinter is available (HAS_TK = True) and logo.png is present in IMAGES_DIR.
# Post:        When the user successfully connects, the Tkinter window hides and playGame()
#              starts. Otherwise, error messages are displayed in the same window.
def startScreen() -> None:
    """Tkinter-based start screen with logo, IP, and port fields."""
    app = tk.Tk()
    app.title("Server Info")

    # Load logo for Tkinter
    image = tk.PhotoImage(file=str(IMAGES_DIR / "logo.png"))

    titleLabel = tk.Label(app, image=image)
    titleLabel.image = image  # keep reference so it's not garbage-collected
    titleLabel.grid(column=0, row=0, columnspan=2)

    ipLabel = tk.Label(app, text="Server IP:")
    ipLabel.grid(column=0, row=1, sticky="W", padx=8)

    ipEntry = tk.Entry(app)
    ipEntry.grid(column=1, row=1)
    ipEntry.insert(0, "127.0.0.1")  # default to localhost

    portLabel = tk.Label(app, text="Server Port:")
    portLabel.grid(column=0, row=2, sticky="W", padx=8)

    portEntry = tk.Entry(app)
    portEntry.grid(column=1, row=2)
    portEntry.insert(0, "6000")  # default to 6000, since server uses that

    errorLabel = tk.Label(app, text="")
    errorLabel.grid(column=0, row=4, columnspan=2)

    joinButton = tk.Button(
        app,
        text="Join",
        command=lambda: joinServer(ipEntry.get(), portEntry.get(), errorLabel, app),
    )
    joinButton.grid(column=0, row=3, columnspan=2)

    app.mainloop()


# ---------------------------------------------------------------------------------------------
# joinServer_cli function
# ---------------------------------------------------------------------------------------------
# Author:      Rudwika Manne
# Purpose:     Provide a simple command-line way to connect to the server when Tkinter
#              is not available (e.g., some headless or minimal Python environments).
# Pre:         Program is run in a terminal with stdin/stdout available. Server is running
#              and reachable at the entered IP/port.
# Post:        On success, calls playGame() with the server-provided configuration. On
#              failure, prints an error and returns without starting the game.
def joinServer_cli() -> None:
    """
    Simple command-line join for environments without Tkinter.
    Asks for server IP and port in the terminal.
    """
    ip: str = input("Server IP [127.0.0.1]: ").strip() or "127.0.0.1"
    port_str: str = input("Server Port [6000]: ").strip() or "6000"

    try:
        port: int = int(port_str)
    except ValueError:
        print("Port must be an integer.")
        return

    client: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        client.connect((ip, port))
    except Exception as e:
        print(f"Could not connect to server: {e}")
        return

    try:
        # Expect config line: "width height side\n"
        cfg: str = client.recv(1024).decode().strip()
        parts = cfg.split()
        if len(parts) != 3:
            print(f"Bad config from server: {cfg}")
            client.close()
            return

        screenWidth: int = int(parts[0])
        screenHeight: int = int(parts[1])
        playerPaddle: str = parts[2]  # "left" or "right" or "spec"

        print(f"Connected! Screen: {screenWidth}x{screenHeight}, you are {playerPaddle}.")

    except Exception as e:
        print(f"Error receiving config: {e}")
        client.close()
        return

    playGame(screenWidth, screenHeight, playerPaddle, client)


# ---------------------------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------------------------
if __name__ == "__main__":
    if HAS_TK:
        startScreen()
    else:
        print("Tkinter is not available on this system. Falling back to CLI join.")
        joinServer_cli()