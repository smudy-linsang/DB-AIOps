import os

os.environ["PYTHONIOENCODING"] = "utf-8"


def inspect_dm_schema() -> None:
    import pyodbc

    conn_str = "DRIVER={DM8 ODBC DRIVER};SERVER=localhost:5236;UID=SYSDBA;PWD=Abcd@1234;"
    conn = pyodbc.connect(conn_str, timeout=5)
    cur = conn.cursor()

    print("=== V$LOCK columns ===")
    try:
        cur.execute("SELECT TOP 1 * FROM V$LOCK")
        print([d[0] for d in cur.description])
    except Exception as exc:
        print(f"Error: {exc}")

    print()
    print("=== V$SESSIONS columns ===")
    try:
        cur.execute("SELECT TOP 1 * FROM V$SESSIONS")
        print([d[0] for d in cur.description])
    except Exception as exc:
        print(f"Error: {exc}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    inspect_dm_schema()