# SPDX-License-Identifier: AGPL-3.0-only
"""Relocatable DLL lookup and optional strict offline validation for the bundle."""
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys

if os.name == "nt":
    site = Path(__file__).resolve().parent
    sys._birdshot_dll_handles = [os.add_dll_directory(str(path)) for path in
                                [Path(sys.executable).parent, site / "torch" / "lib"]
                                if path.is_dir()]

if os.environ.get("BIRDSHOT_OFFLINE") == "1":
    original_connect = socket.socket.connect
    def offline_connect(connection, address):
        if isinstance(address, tuple):
            host = str(address[0])
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = host.casefold() == "localhost"
            if not local:
                audit = os.environ.get("BIRDSHOT_NETWORK_AUDIT")
                if audit:
                    with Path(audit).open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"pid": os.getpid(), "host": host, "blocked": True}) + "\n")
                raise OSError("BirdShot offline mode blocks external network access")
        return original_connect(connection, address)
    socket.socket.connect = offline_connect
