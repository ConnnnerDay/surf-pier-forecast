"""Create/update SQLite schema and migrate legacy tables into the new DAL schema."""

from storage.sqlite import init_db, get_db, DB_PATH


def main() -> None:
    init_db()
    conn = get_db()
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    conn.close()
    print(f"SQLite initialized at: {DB_PATH}")
    print("Tables:")
    for t in tables:
        print(f" - {t}")


if __name__ == "__main__":
    main()
