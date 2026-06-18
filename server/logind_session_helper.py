#!/usr/bin/python3.9
"""
logind session helper — creates a real logind session and holds it alive.

Spawned by ``session_manager.SessionManager`` as a child process. Calls
``login1.Manager.CreateSession`` via GLib/Gio, keeps the returned
``fifo_fd`` open for the process lifetime (logind destroys the session
as soon as that FD closes), and exits cleanly on SIGTERM.

Why we need this
================
GNOME Shell's ScreenShield queries the current logind session at startup
(``loginManager.js::getCurrentSessionProxy``). If no session is
registered for the user, gnome-shell crashes with::

    TypeError: this._userProxy.Display is null

A plain ``busctl call CreateSession`` does not work because busctl
returns and exits immediately, which closes its copy of ``fifo_fd``,
which tells logind to destroy the session. We need a long-lived process
that holds the fd open.

This helper is run as root (same as the teraguchi server) because
``CreateSession`` requires privilege to register a session for another
uid.

Runs under ``/usr/bin/python3.9`` because ``python3-gobject`` on Rocky 9
is installed for the system Python 3.9 only, not the 3.11 alt-python or
the teraguchi venv.

Usage
=====

::

    /usr/bin/python3.9 logind_session_helper.py <uid> <display>

On success, writes ``<session_id>\\n`` to stdout then blocks on
``signal.pause()`` holding the fifo fd. On CreateSession failure, writes
``ERROR: <message>\\n`` to stderr and exits with code 1.
"""

import os
import signal
import sys

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


def create_session(uid: int, display: str):
    """Call login1.CreateSession and return (session_id, fifo_fd).

    Raises GLib.Error on failure.
    """
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    # CreateSession signature: uusssssussbssa(sv) -> soshusub
    #   uid  pid  service  type  class  desktop  seat  vtnr  tty
    #   display  remote  remote_user  remote_host  properties
    args = GLib.Variant(
        "(uusssssussbssa(sv))",
        (
            uid,
            0,                 # pid (0 = let logind use caller-- i.e. us)
            "teraguchi",       # service
            "x11",             # type
            "user",            # class
            "gnome-classic",   # desktop
            "",                # seat_id
            0,                 # vtnr
            "",                # tty
            display,           # display
            False,             # remote
            "",                # remote_user
            "",                # remote_host
            [],                # properties
        ),
    )

    result, fd_list = bus.call_with_unix_fd_list_sync(
        "org.freedesktop.login1",
        "/org/freedesktop/login1",
        "org.freedesktop.login1.Manager",
        "CreateSession",
        args,
        GLib.VariantType("(soshusub)"),
        Gio.DBusCallFlags.NONE,
        -1,        # timeout: default
        None,      # fd_list in
        None,      # cancellable
    )

    unpacked = result.unpack()
    session_id = unpacked[0]
    fifo_idx = unpacked[3]

    # The 'h' field in the return is an index into the UnixFDList; steal
    # the actual kernel file descriptor so we own it.
    fds = fd_list.steal_fds()
    fifo_fd = fds[fifo_idx]
    # Close any other fds we accidentally received (shouldn't be any)
    for i, fd in enumerate(fds):
        if i != fifo_idx and fd >= 0:
            os.close(fd)

    return session_id, fifo_fd


def main():
    if len(sys.argv) < 3:
        print("usage: logind_session_helper.py <uid> <display>",
              file=sys.stderr)
        sys.exit(2)

    try:
        uid = int(sys.argv[1])
    except ValueError:
        print("ERROR: uid must be an integer", file=sys.stderr)
        sys.exit(2)

    display = sys.argv[2]

    try:
        session_id, fifo_fd = create_session(uid, display)
    except GLib.Error as e:
        print(f"ERROR: CreateSession failed: {e.message}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Tell the parent what we got, then keep the fd alive forever.
    sys.stdout.write(f"{session_id}\n")
    sys.stdout.flush()

    # When the parent wants us to stop, it SIGTERMs us. Closing the fifo
    # fd is what tells logind to destroy the session.
    def _shutdown(signum, frame):
        try:
            os.close(fifo_fd)
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    # Block forever — SIGTERM will call _shutdown and sys.exit.
    while True:
        signal.pause()


if __name__ == "__main__":
    main()
