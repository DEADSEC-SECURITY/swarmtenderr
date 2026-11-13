"""Required explicit PostgreSQL profile; absence of its runtime URL is a failure."""

import os
from swarmtenderr.store import Store
from swarmtenderr.observer import Observer
from swarmtenderr.settings import Settings


def main():
    store = Store(os.environ["SWARMTENDERR_TEST_DATABASE_URL"])
    if not store.postgres:
        raise RuntimeError("This acceptance profile requires PostgreSQL")
    store.initialize()
    settings = Settings(demo=True)
    Observer(settings, store, None).poll()
    snapshot = store.read()
    assert snapshot["demo"] and len(snapshot["torrents"]) == 6
    identity = store.begin_action("a" * 40, "prioritize")
    store.finish_action(identity, "accepted")
    reopened = Store(os.environ["SWARMTENDERR_TEST_DATABASE_URL"])
    assert reopened.read() == snapshot
    assert any(row["id"] == identity and row["outcome"] == "accepted" for row in reopened.history()["actions"])
    print("PostgreSQL schema, observations, audit transactions and reconnection passed")


if __name__ == "__main__":
    main()
