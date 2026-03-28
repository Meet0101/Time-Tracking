"""One-time: create PostgreSQL database from .env DB_* (run before migrate)."""
import os
from pathlib import Path

import environ
import psycopg2
from psycopg2 import sql

BASE_DIR = Path(__file__).resolve().parent
env = environ.Env()
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))


def main():
    try:
        conn = psycopg2.connect(
            dbname="postgres",
            user=env("DB_USER"),
            password=env("DB_PASSWORD"),
            host=env("DB_HOST"),
            port=env("DB_PORT"),
        )
        conn.autocommit = True
        cur = conn.cursor()
        db_name = env("DB_NAME")
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
        print(f"Database {db_name} created.")
    except psycopg2.Error as e:
        if "already exists" in str(e):
            print(f"Database {env('DB_NAME')} already exists.")
        else:
            print(f"Error: {e}")
    finally:
        if "conn" in locals():
            conn.close()


if __name__ == "__main__":
    main()
