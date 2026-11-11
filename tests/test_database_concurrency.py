from concurrent.futures import ThreadPoolExecutor

from clicoding_orchestrator.database import Database


def test_concurrent_event_appends_allocate_unique_sequences_across_connections(tmp_path):
    database_path = str(tmp_path / "concurrent-events.sqlite3")
    first_database = Database(database_path)
    second_database = Database(database_path)
    conversation_id = first_database.create_conversation()["id"]

    def append(index: int):
        database = first_database if index % 2 == 0 else second_database
        return database.append_event(
            conversation_id,
            role="user",
            content=f"parallel event {index}",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        events = list(executor.map(append, range(40)))

    sequences = sorted(event["sequence"] for event in events)
    assert sequences == list(range(1, 41))
    assert len({event["id"] for event in events}) == 40
    assert len(first_database.list_events(conversation_id)) == 40
