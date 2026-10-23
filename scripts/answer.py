"""Demo helper: run a command in a pseudo-terminal and type an answer when a prompt appears.

    python3 scripts/answer.py "[y/N]" y -- python3 -m chorepilot ask "water the lawn" --as Alex

ChorePilot only accepts confirmations from a real terminal (a pipe counts as "no"), so the demo
uses a pty to play the person typing at the prompt.
"""
import os
import pty
import sys


def main():
    expect, answer = sys.argv[1].encode(), sys.argv[2].encode()
    cmd = sys.argv[sys.argv.index("--") + 1:]
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(cmd[0], cmd)
    buf, sent = b"", False
    while True:
        try:
            chunk = os.read(fd, 1024)
        except OSError:
            break
        if not chunk:
            break
        sys.stdout.buffer.write(chunk)
        sys.stdout.flush()
        buf = (buf + chunk)[-4096:]
        if not sent and expect in buf:
            os.write(fd, answer + b"\r")
            sent = True
    _, status = os.waitpid(pid, 0)
    sys.exit(os.waitstatus_to_exitcode(status))


if __name__ == "__main__":
    main()
