import http.server
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.request


def migrate():
    with sqlite3.connect("/data/application.db") as database:
        database.execute("create table if not exists saved (value text)")
        if not database.execute("select count(*) from saved").fetchone()[0]:
            database.execute(
                "insert into saved values (?)", ("existing-customer-data",)
            )


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "revision": os.environ["RELEASE_REVISION"],
                    "visible": Path("revision.txt").read_text().strip(),
                }
            ).encode()
        )


def smoke():
    import time

    for attempt in range(30):
        try:
            with urllib.request.urlopen("http://localhost:8000") as response:
                body = json.load(response)
            assert body["revision"] == os.environ["RELEASE_REVISION"]
            with sqlite3.connect("/data/application.db") as database:
                assert (
                    database.execute("select value from saved").fetchone()[0]
                    == "existing-customer-data"
                )
            assert (
                Path("/config/private.conf").read_text()
                == "existing-private-configuration\n"
            )
            assert os.environ["PRIVATE_SETTING"] == "existing-env-value"
            print(json.dumps(body), flush=True)
            return
        except OSError:
            if attempt == 29:
                raise
            time.sleep(1)


if __name__ == "__main__":
    if sys.argv[-1] == "migrate":
        migrate()
    elif sys.argv[-1] == "smoke":
        smoke()
    else:
        http.server.HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
