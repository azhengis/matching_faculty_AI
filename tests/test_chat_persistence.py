import json, sqlite3, tempfile, os
import web_app

def test_a_long_conversation_keeps_its_opening_when_stored():
    d = tempfile.mkdtemp(); web_app.DB_PATH = os.path.join(d, "t.db")
    web_app._init_profiles_db()
    con = sqlite3.connect(web_app.DB_PATH)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'Jane')")
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (1, 1, 'P')")
    con.commit(); con.close()

    # 300 entries — longer than the old 80 cap, shorter than the new one.
    history = []
    for i in range(150):
        history.append({"role": "user", "content": f"user message {i}"})
        history.append({"role": "assistant", "content": f"assistant reply {i}"})
    web_app._persist_history(1, history)

    con = sqlite3.connect(web_app.DB_PATH)
    stored = json.loads(con.execute("SELECT chat_history FROM projects WHERE id=1").fetchone()[0])
    con.close()
    # The opening survives — it used to be trimmed off at 80.
    assert stored[0]["content"] == "user message 0", "the start of the conversation was lost"
    assert stored[-1]["content"] == "assistant reply 149"
    assert len(stored) == 300

def test_storage_is_still_bounded_for_a_marathon_project():
    d = tempfile.mkdtemp(); web_app.DB_PATH = os.path.join(d, "t.db")
    web_app._init_profiles_db()
    con = sqlite3.connect(web_app.DB_PATH)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'Jane')")
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (1, 1, 'P')")
    con.commit(); con.close()
    history = [{"role": "user", "content": f"m{i}"} for i in range(5000)]
    web_app._persist_history(1, history)
    con = sqlite3.connect(web_app.DB_PATH)
    stored = json.loads(con.execute("SELECT chat_history FROM projects WHERE id=1").fetchone()[0])
    con.close()
    assert len(stored) == web_app.MAX_STORED_TURNS
    assert stored[-1]["content"] == "m4999"   # keeps the most recent
