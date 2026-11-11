"""Read Logitech G HUB's settings file: the one JSON document of its profiles, cards and
applications.

G HUB keeps them in one row of `%LOCALAPPDATA%\\LGHUB\\settings.db`, a SQLite database in
WAL mode whose newest changes can sit in `settings.db-wal`. This reads the database, or that
document pulled out into a JSON file, under caps on its size, its records and its time, and
hands ghub.py the document to map. Nothing here writes the file or a setting.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

from .json_text import JsonRefused, json_value
from .settings_files import ConfigError, read_capped
from .wording import reason, size_words, without_controls

SQLITE_MAGIC = b"SQLite format 3\x00"
#: The largest settings.db or -wal read. G HUB's own are a few megabytes.
MAX_FILE_BYTES = 64 << 20
#: The largest settings document, JSON file or database row: it bounds what json.loads
#: builds from a crafted file. The owner's decision of 2026-09-28.
MAX_DOCUMENT_BYTES = 16 << 20
#: The most profiles, and the most cards, a file may hold: a one-line record costs a
#: profile object and a row in the window's list. The owner's decision of 2026-09-28.
MAX_RECORDS = 10_000
#: The longest application, profile or card name taken, in characters: a file can use one
#: name thousands of times, and each use costs its length. The owner's decision of 2026-09-28.
MAX_NAME_CHARS = 256
READ_SECONDS = 10.0
#: The longest schema entry SQLite parses. The clock is read only between entries, and one
#: CREATE TABLE of 80,000 UNIQUE constraints, 1.4 MB, parsed for 37 s (SQLite 3.46.1,
#: measured 2026-11-01). G HUB's own entries are a line each.
MAX_SCHEMA_SQL_BYTES = 16 << 10
#: SQLite's memory cap while it reads, room for a MAX_DOCUMENT_BYTES value: the first query
#: parses the whole schema, unselected views included. The limit is process-wide and can only
#: be lowered; nothing else here uses SQLite.
HEAP_BYTES = 256 << 20

#: How SQLite stores a table's schema entry: CREATE TABLE and then the name, whatever the
#: statement said between them. Written by hand, an entry can say CREATE TABLE IF NOT EXISTS
#: data, which SQLite skips where a view or a virtual table already has the name in another
#: case, so the entry says "table" and the SELECT reads the other (SQLite 3.46.1, measured
#: 2026-11-06). Nothing need follow the name here: SQLite calls an entry malformed whose
#: statement makes a table of another name.
STORED_TABLE = re.compile(r"""CREATE TABLE ["'`\[]?data""")

#: The refusal of a -wal owned by a different account from the entry it sits beside.
OTHER_OWNER = "{wal}: owned by a different account from {db}; copy both into a folder of your own"


class GhubError(ValueError):
    """The file could not be read as G HUB's settings, or an import cannot be done as asked.

    Control characters are stripped: the text can quote the file and goes to terminals."""

    def __init__(self, text: str) -> None:
        super().__init__(without_controls(text))


def read_settings(path: Path) -> dict:
    """The settings document, from settings.db or a JSON file, told apart by the first
    bytes: a database renamed on the way from Windows still reads as one."""
    try:
        raw = read_capped(path, MAX_FILE_BYTES)
        if raw.startswith(SQLITE_MAGIC):
            raw = _read_database(path, raw)
        elif len(raw) > MAX_DOCUMENT_BYTES:
            raise GhubError(f"{path}: larger than {size_words(MAX_DOCUMENT_BYTES)}")
    except ConfigError as exc:
        raise GhubError(str(exc)) from None
    except OSError as exc:
        # The file that failed, which can be a -wal (_wal_beside).
        raise GhubError(f"{exc.filename or path} could not be read ({reason(exc)})") from None
    try:
        data = json_value(raw, one_name=False)
    except JsonRefused as exc:
        # Where the stack runs out depends on the machine: here, or in parse's walk, which
        # words it the same.
        raise GhubError(f"{path.name}: {exc}") from None
    if not isinstance(data, dict) or not records(data, "profiles"):
        raise GhubError(f"{path.name} holds no G HUB profiles")
    for kind in ("profiles", "cards"):
        if (count := len(records(data, kind))) > MAX_RECORDS:
            raise GhubError(f"{path.name} holds {count:,} G HUB {kind}; an import reads "
                            f"{MAX_RECORDS:,} at most")
    return data


def _wal_beside(path: Path) -> bytes | None:
    """The -wal SQLite would apply to `path`, read, or None: beside the path given, else
    beside the file a link names, where SQLite looks. It must belong to the account that owns
    the entry it sits beside (OTHER_OWNER): in a folder others may write in, such as /tmp,
    another account could put its own there first and decide what is read. A link's own
    owner, not its file's, holds the -wal beside it: a user's link to a file another account
    owns, on a Windows partition mounted as root, takes the user's own -wal."""
    for beside, owner in ((path, path.lstat().st_uid), (path.resolve(), path.stat().st_uid)):
        candidate = beside.with_name(beside.name + "-wal")
        try:
            # The entry's own owner, a link's not its target's: in a folder with the sticky
            # bit only the folder's owner and the entry's can replace the entry checked.
            if candidate.stat(follow_symlinks=False).st_uid != owner:
                raise GhubError(OTHER_OWNER.format(wal=candidate, db=path.name))
            return read_capped(candidate, MAX_FILE_BYTES)
        except FileNotFoundError:
            pass
        except OSError as exc:
            # os.fstat's and os.read's errors name no file, and read_settings would name `path`.
            exc.filename = exc.filename or str(candidate)
            raise
    return None


def _read_database(path: Path, raw: bytes) -> bytes:
    """The newest settings row, read from a private copy of the database (`raw`) and its -wal
    (_wal_beside).

    Opening the user's own file is not read-only in effect: SQLite creates a -shm beside it
    even read-only, cannot open it in a directory it may not write to, and a read-write
    connection checkpoints the -wal and deletes it on close. SQLite rebuilds the -shm from
    the -wal.
    """
    wal = _wal_beside(path)
    try:
        made = tempfile.TemporaryDirectory(prefix="gearbench-ghub-")
    except OSError as exc:
        # Not the user's file: mkdtemp's error names the folder it could not make, and
        # gettempdir's, which found no folder it can write to, lists each one it tried.
        if exc.filename is None:
            raise GhubError(f"could not copy {path.name}: no temporary folder can be "
                            f"written to") from None
        raise GhubError(f"could not copy {path.name} into {Path(exc.filename).parent}: "
                        f"{reason(exc)}") from None
    with made as tmp:
        copy = Path(tmp) / "settings.db"
        try:
            copy.write_bytes(raw)
            if wal is not None:
                (Path(tmp) / "settings.db-wal").write_bytes(wal)
        except OSError as exc:
            # A full temp folder, not the user's file: its write carries no file name.
            raise GhubError(f"could not copy {path.name} into {Path(tmp).parent}: "
                            f"{reason(exc)}") from None
        try:
            with contextlib.closing(sqlite3.connect(f"{copy.as_uri()}?mode=ro",
                                                    uri=True)) as connection:
                row = _newest_row(connection, path.name)
        except (sqlite3.Error, UnicodeDecodeError) as exc:
            # Only SQLite's own errors carry a code; the module's (a text value that is not
            # UTF-8) do not.
            if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_TOOBIG:
                raise GhubError(f"{path}: its settings are larger than "
                                f"{size_words(MAX_DOCUMENT_BYTES)}") from None
            # The sqlite3 module raises UnicodeDecodeError for SQLite's error text when it
            # quotes bytes of the file that are not UTF-8, as from a damaged schema.
            text = (exc.object.decode("utf-8", "replace")
                    if isinstance(exc, UnicodeDecodeError) else str(exc))
            # Cut: SQLite's text can quote a schema name from the file, up to 16 MiB long,
            # and the refusal goes to the window's dialog.
            raise GhubError(f"{path.name} is not G HUB's settings database "
                            f"({text[:MAX_NAME_CHARS]})") from None
        except MemoryError:
            # What the sqlite3 module raises when SQLite reaches HEAP_BYTES.
            raise GhubError(f"{path.name} needs more than {size_words(HEAP_BYTES)} to read; "
                            f"G HUB's settings are a few megabytes") from None
    if row is None or row[0] is None:
        raise GhubError(f"{path.name} holds no settings")
    return row[0] if isinstance(row[0], bytes) else str(row[0]).encode()


def _newest_row(connection: sqlite3.Connection, name: str) -> tuple | None:
    """The settings table's newest row, from a database anyone may have written: its
    schema's SQL runs under the limits set here, and `_check_schema` refuses a table
    that is not plain."""
    # Before the first query, which parses the schema; this pragma does not.
    connection.execute(f"PRAGMA hard_heap_limit={HEAP_BYTES}")
    connection.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
    connection.setconfig(sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA, False)
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_DOCUMENT_BYTES)
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SCHEMA_SQL_BYTES)
    deadline = time.monotonic() + READ_SECONDS
    # At every VDBE step, so between each two schema entries: at every 1000th, 1000 entries
    # of 16 KB ran 32 s past the deadline's start (measured 2026-11-01).
    connection.set_progress_handler(lambda: time.monotonic() > deadline, 1)
    try:
        _check_schema(connection, name)
        return connection.execute("SELECT file FROM data ORDER BY _id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        if time.monotonic() > deadline:
            raise GhubError(f"{name} took over {READ_SECONDS:g} s to read") from None
        raise


def _check_schema(connection: sqlite3.Connection, name: str) -> None:
    """Raise GhubError unless `data` is a plain table with no generated or
    hidden column, and for a schema entry over MAX_SCHEMA_SQL_BYTES that SQLite
    calls too big. A view, a virtual table or a generated column is code the
    file's author wrote, which the SELECT would run."""
    try:
        # NOT INDEXED: an entry can put an index on SQLite's own table, and a covering one
        # answers this query from its own bytes, not the entries SQLite loaded (SQLite
        # 3.46.1, measured 2026-11-06).
        found = connection.execute(
            "SELECT type, sql FROM sqlite_schema NOT INDEXED WHERE name = 'data'").fetchall()
    except sqlite3.DataError as exc:
        # This first query parses every schema entry under SQLITE_LIMIT_SQL_LENGTH,
        # so a TOOBIG here is the schema's, not the settings row's. One entry over it
        # is called malformed instead, and two too big (SQLite 3.46.1, measured
        # 2026-11-01).
        if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_TOOBIG:
            raise
        raise GhubError(f"{name} is not G HUB's settings database: a schema entry is "
                        f"larger than {size_words(MAX_SCHEMA_SQL_BYTES)}") from None
    if (len(found) != 1 or found[0][0] != "table"
            or STORED_TABLE.match(str(found[0][1])) is None):
        raise GhubError(f"{name} is not G HUB's settings database: it has no plain data table")
    if any(column[6] for column in connection.execute("PRAGMA table_xinfo(data)")):
        raise GhubError(f"{name} is not G HUB's settings database: its data table has a "
                        f"generated or hidden column")


def records(data: Mapping, name: str) -> list[dict]:
    """A list of objects out of the document: `{"name": {"name": [...]}}`,
    which is how G HUB nests them, or a bare list."""
    found = data.get(name)
    if isinstance(found, dict):
        found = found.get(name)
    if not isinstance(found, list):
        return []
    return [item for item in found if isinstance(item, dict)]
