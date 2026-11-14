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


def _process_try_turn_lease(database_path, conversation_id, owner_token, queue):
    database = Database(database_path)
    queue.put(database.acquire_conversation_turn(conversation_id, owner_token))


def _process_read_cancel_request(database_path, conversation_id, owner_token, queue):
    database = Database(database_path)
    queue.put(database.conversation_turn_cancel_requested(conversation_id, owner_token))


def test_turn_lease_excludes_a_second_process_and_propagates_cancel(tmp_path):
    import multiprocessing

    database_path = str(tmp_path / "cross-process-turns.sqlite3")
    first_database = Database(database_path)
    conversation_id = first_database.create_conversation()["id"]
    owner_token = "worker-one"
    assert first_database.acquire_conversation_turn(conversation_id, owner_token)

    context = multiprocessing.get_context("spawn")
    acquire_queue = context.Queue()
    contender = context.Process(
        target=_process_try_turn_lease,
        args=(database_path, conversation_id, "worker-two", acquire_queue),
    )
    contender.start()
    contender.join(timeout=10)
    assert contender.exitcode == 0
    assert acquire_queue.get(timeout=2) is False

    assert first_database.request_conversation_turn_cancel(conversation_id)
    cancel_queue = context.Queue()
    observer = context.Process(
        target=_process_read_cancel_request,
        args=(database_path, conversation_id, owner_token, cancel_queue),
    )
    observer.start()
    observer.join(timeout=10)
    assert observer.exitcode == 0
    assert cancel_queue.get(timeout=2) is True

    assert first_database.release_conversation_turn(conversation_id, owner_token)
    assert not first_database.conversation_turn_cancel_requested(
        conversation_id, owner_token
    )
    assert first_database.acquire_conversation_turn(conversation_id, "worker-two")


def test_expired_turn_lease_can_be_recovered_after_process_crash(tmp_path):
    from datetime import datetime, timedelta, timezone

    database_path = str(tmp_path / "expired-turn.sqlite3")
    database = Database(database_path)
    conversation_id = database.create_conversation()["id"]
    assert database.acquire_conversation_turn(conversation_id, "dead-worker")
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with database.session() as connection:
        connection.execute(
            "UPDATE conversation_turn_leases SET lease_expires_at = ? "
            "WHERE conversation_id = ?",
            (expired, conversation_id),
        )

    assert database.acquire_conversation_turn(conversation_id, "replacement-worker")
    assert not database.release_conversation_turn(conversation_id, "dead-worker")
    assert database.release_conversation_turn(conversation_id, "replacement-worker")
