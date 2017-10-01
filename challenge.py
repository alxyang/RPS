"""
My implementation is a take on multiplayer online RPS, inspired by IRC.
There is a match-making system that allows you to play against other users.
Each client connects to the server by loggging in via netcat.

Start up server:
    python2 challenge.py
        - must be python 2 for now, sockets lib is easier to work with there
          otherwise you have to encode/decode bytes

Connect clients (in new window):
    nc localhost PORT
        - PORT is default 5001

Example flow:
    0. start server - `python2 challenge.py`
    1. `nc localhost 5001` on 2 different terminal windows to connect 2 clients
    2. `rps start @opponent`
    3. `accept @user` - from other terminal window
    4. start playing!

    Try out many games at once in parallel!
    Try out chat functionality as well to communicate between games.
"""

import socket
import threading
import time
import re

# Globals shared across all connections
CONNS = {}
IN_GAME = {}
MATCHES = {}
MATCH_LOCKS = {}
MOVES = ["ROCK","PAPER","SCISSORS"]
INVALID_OP = "<INVALID>"

class RPS(object):
    def __init__(self):
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(('localhost', 5001))
        self.listener.listen(1)

    def start(self):
        while True:
            # sockets are coming, waiting to accept
            sock, addr = self.listener.accept()
            thread = threading.Thread(target=handle_connection, args=(sock, addr))
            thread.daemon = True
            thread.start()

class Client(object):
    def __init__(self, sock):
        self.nick = None
        self.sock = sock

    def connect(self):
        self.sock.sendall('Welcome to the RPS server! What is your nickname?\n')

        self.login()

        if self.nick is None:
            return

        self.run()
        self.disconnect()

    def get_curr_time(self):
        return time.strftime('[%H:%M:%S]', time.localtime())

    def login(self):
        """
        Nicknames need to be 1-20 chars, case sensitive, alphanumeric,
        and not already taken.
        """

        while True:
            data = self.sock.recv(1024)
            if not data:
                return

            self.nick = data.strip()
            if self.nick in CONNS or not self.nick.isalnum() or \
               len(self.nick) > 20 or len(self.nick) == 0:
                self.sock.sendall("'%s' is taken/invalid. "
                                  "Please pick another name\n" %
                                  self.nick)
                continue

            num_users = len(CONNS.keys())
            users = "[" + ", ".join(CONNS.keys()) + "]"
            self.sock.sendall('You are connected with %d other users: %s \n' %
                              (num_users, users))
            self.sock.sendall("To start a game, enter 'rps start @user' \n")

            # add nick to sockets map
            CONNS[self.nick] = self.sock
            line = '%s *%s has joined*\n' % (self.get_curr_time(), self.nick)
            # send notifications to all other users
            for name, s in CONNS.items():
                if name == self.nick:
                    continue

                s.sendall(line)

            # we've sucessfully initialized a client. return
            return

    def disconnect(self):
        # broadcast disconnection to all other users
        line = '%s *%s has left*\n' % (self.get_curr_time(), self.nick)

        for name, s in CONNS.items():
            if name == self.nick:
                continue

            s.sendall(line)

        del CONNS[self.nick]
        self.sock.close()

    def run(self):
        while True:
            data = self.sock.recv(1024)
            if not data:
                # Client is disconnecting
                return

            line = '%s <%s> %s \n' % (self.get_curr_time(),
                                      self.nick,
                                      data.strip())

            # Check if we are going to 'accept' an incoming request
            opponent = self.accept_request(line)
            if opponent is not None:
                self.game_init(opponent)
                continue

            # Check if we are sending a game start request.
            opponent = self.send_game_request(line)
            if opponent is INVALID_OP:
                continue
            elif opponent is not None:
                self.game_init(opponent)
                continue

            for name, s in CONNS.items():
                if name == self.nick:
                    continue

                s.sendall(line)

    def accept_request(self, line):
        """
        > "accept @USER"

        If you accept, the game starts
        """
        if "accept @" not in line:
            return None

        opponent = set(re.findall("@([a-zA-Z0-9]{1,20})", line))
        if len(opponent) > 1:
            return None

        opponent = list(opponent)[0]
        return opponent

    def send_game_request(self, line):
        """
        start game API > "rps start @USER"
        sends request to other users socket to see if he wants to accept

        if he does, starts the game.
        """
        if "rps start @" not in line:
            return None

        """
        Exit if:
            - more than one user specified
            - user specified is self
            - user doesn't exist
            - user is already in game
        """
        opponent = set(re.findall("@([a-zA-Z0-9]{1,20})", line))
        if len(opponent) > 1:
            self.sock.sendall("> Pick only one opponent.\n")
            return INVALID_OP

        opponent = list(opponent)[0]
        if opponent not in CONNS:
            self.sock.sendall("> <%s> is not online.\n" % opponent)
            return INVALID_OP

        if opponent == self.nick:
            self.sock.sendall("> You can't start a game against yourself. \n")
            return INVALID_OP

        if opponent in IN_GAME:
            self.sock.sendall("> <%s> is already in-game.\n" % opponent)
            return INVALID_OP

        """
        If above conditions hold, send opponent a Game request.
            - Opponent can either accept, or keep chatting to deny
            - request will timeout after a certain period of time
        """
        opponent_sock = CONNS[opponent]
        opponent_sock.sendall("> <%s> has sent you a game request. "
                              "Type 'accept @%s' to join.  \n" %
                              (self.nick, self.nick))

        return opponent

    # Initialize and run the game.
    def game_init(self, opponent):
        IN_GAME[self.nick] = CONNS[self.nick]
        players = [self.nick, opponent]
        players.sort()
        gameid = "-".join(players)

        if gameid not in MATCHES:
            MATCHES[gameid] = {}
            MATCH_LOCKS[gameid] = threading.Lock()

        self.game_run(gameid, opponent)
        self.game_cleanup(gameid)
        del IN_GAME[self.nick]
        return

    def game_cleanup(self, gameid):
        if gameid in MATCHES:
            del MATCHES[gameid]

        if gameid in MATCH_LOCKS:
            del MATCH_LOCKS[gameid]

    def wait_for_opponent(self, opponent, timeout=10):
        self.sock.sendall("> Waiting for opponent to join... \n")
        while opponent not in IN_GAME and timeout > 0:
            time.sleep(1)
            timeout -= 1

        if timeout == 0:
            self.sock.sendall("> Opponent hasn't joined. \n")
            return False

        return True

    def send_opponent_missing(self):
        self.sock.sendall("> Opponent seems to have gone idle/away. "
                          "Returning to chat. \n")
        return

    def game_run(self, gameid, opponent, timeout=10):
        """
        Game has begun.
        """
        if not self.wait_for_opponent(opponent):
            return

        self.sock.sendall("> Welcome to RPS! Choose one of [ROCK, PAPER, SCISSORS]. \n")

        while True:
            data = self.sock.recv(1024)
            if not data:
                return

            if opponent not in IN_GAME or opponent not in CONNS or gameid not in MATCHES:
                self.send_opponent_missing()
                return

            move = data.strip()
            if move.upper() not in MOVES:
                self.sock.sendall("> Invalid Move. Possible commands are "
                                  "[ROCK, PAPER, SCISSORS] \n")
                continue

            MATCHES[gameid][self.nick] = move
            break

        """
        Wait for both players to make a move with a given timeout.
        """
        with MATCH_LOCKS[gameid]:
            if gameid not in MATCHES:
                return

            while len(MATCHES[gameid].keys()) < 2 and timeout > 0:
                # waiting for opponent to move
                time.sleep(1)
                timeout -= 1

                if timeout == 0:
                    self.send_opponent_missing()
                    return

            # calculate winner
            winner = self.calc_winner(self.nick, MATCHES[gameid][self.nick],
                                      opponent, MATCHES[gameid][opponent])

            if winner is None:
                self.sock.sendall("> It was a tie! \n")
                IN_GAME[opponent].sendall("> It was a tie! \n")
            else:
                self.sock.sendall("> Winner was <%s>! \n" % winner)
                IN_GAME[opponent].sendall("> Winner was <%s>! \n" % winner)

            # remove so next thread doesn't enter critical section
            del MATCHES[gameid]
        return

    def calc_winner(self, p1, p1_move, p2, p2_move):

        p1_move = p1_move.upper()
        p2_move = p2_move.upper()
        IN_GAME[self.nick].sendall("> You chose to play %s \n" % (p1_move))
        IN_GAME[self.nick].sendall("> <%s> chose to play %s \n" % (p2, p2_move))
        IN_GAME[p2].sendall("> You chose to play %s \n" % (p2_move))
        IN_GAME[p2].sendall("> <%s> chose to play %s \n" % (p1, p1_move))

        if p1_move == "ROCK" and p2_move == "SCISSORS":
            return p1
        elif p1_move == "ROCK" and p2_move == "PAPER":
            return p2
        elif p1_move == "ROCK" and p2_move == "ROCK":
            return None
        elif p1_move == "PAPER" and p2_move == "ROCK":
            return p1
        elif p1_move == "PAPER" and p2_move == "PAPER":
            return None
        elif p1_move == "PAPER" and p2_move == "SCISSORS":
            return p2
        elif p1_move == "SCISSORS" and p2_move == "ROCK":
            return p2
        elif p1_move == "SCISSORS" and p2_move == "PAPER":
            return p1
        elif p1_move == "SCISSORS" and p2_move == "SCISSORS":
            return None

        return None

def handle_connection(sock, addr):
    client = Client(sock)
    client.connect()

if __name__ == '__main__':
    rps = RPS()
    rps.start()
