## Vespera Audit Round 2 — 2026-06-01

---

### CRITICAL

_None identified._

---

### HIGH

---

**H1 · handoff.py · lines 50–55, ~217 · Stale module-level `CLOUD_API_KEY` silently breaks live config for cloud routing**

`CLOUD_API_KEY` is set once at module import from `COMPONENTS["cloud"]["api_key"]`, which itself is `os.getenv("CLOUD_API_KEY", "")` evaluated when `config.py` loaded. In `handle_message()`, the routing decision is:

```python
if complexity >= COMPLEXITY_THRESHOLD:
    if CLOUD_API_KEY:          # ← frozen empty string from import time
        response = respond_cloud(...)
```

If a user adds or changes `CLOUD_API_KEY` via the UI (`/api/settings`), the endpoint correctly updates `os.environ[key]`, and `respond_cloud()` also correctly re-reads via `os.getenv("CLOUD_API_KEY", "")`. But `handle_message()` will never reach `respond_cloud()` for complexity-based routing because the module-level guard remains the empty string. The "live update without restart" promise silently fails for the most important setting in the system — the key that enables cloud AI.

The `[HANDOFF]` self-routing path is unaffected (it calls `respond_cloud()` directly), so partial functionality masks the breakage.

**Fix:** Replace `if CLOUD_API_KEY:` with `if os.getenv("CLOUD_API_KEY", ""):` in `handle_message()`.

---

**H2 · api.py · `_run_and_release()` in `run_cleanup()` / `run_pruning()` · `ThreadPoolExecutor` context manager blocks lock release on timeout**

Both manual-trigger endpoints use the same pattern:

```python
def _run_and_release():
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(_run).result(timeout=300)   # raises TimeoutError after 300s
    except concurrent.futures.TimeoutError:
        app.logger.error("timed out")
    finally:
        _cleanup_lock.release()
```

When `result(timeout=300)` raises `TimeoutError`, Python still executes the `with` block's `__exit__`, which calls `executor.shutdown(wait=True)`. This **blocks** until the submitted work actually finishes — which is exactly the hung task. The `except` clause and `finally` block can't run until `shutdown()` returns. `_cleanup_lock` / `_pruning_lock` are never released for the lifetime of the hang. Any subsequent `/api/cleanup/run` or `/api/prune/run` call returns 409 indefinitely, until process restart.

**Fix:** Remove the inner `ThreadPoolExecutor` entirely — `_run_and_release` is already running in its own daemon thread. Call `_run()` directly, wrap in `signal.alarm` or `threading.Timer` + `.join(timeout=300)` if a hard timeout is needed. Alternatively use `executor.shutdown(wait=False, cancel_futures=True)` (Python 3.9+).

---

**H3 · tools.py · lines 80–88 · Relative path arguments bypass the security check entirely**

The path-argument security check only triggers for args starting with `/` or `~`:

```python
if val.startswith("/") or val.startswith("~"):
    resolved_arg = str(Path(val.replace("~", HOME, 1)).expanduser())
    if not _path_allowed(resolved_arg):
        return f"Error: path not in allowed paths: {val}"
```

A Claude-generated command like `cat ../../etc/shadow` or `cp ../../.ssh/id_rsa /tmp/x` passes unchecked. `run_shell()` sets `cwd=HOME`; with `shell=False` the relative path resolves to `HOME/../../etc/shadow` → `/etc/shadow`, which is outside `ALLOW_PATHS`. The previous Round 1 fix hardened absolute paths but left relative traversal entirely open.

**Fix:** Resolve every argument path against `cwd` and run it through `_path_allowed()` regardless of prefix:

```python
from pathlib import PurePosixPath
candidate = Path(cwd) / val
if not _path_allowed(str(candidate.resolve())):
    return f"Error: path not in allowed paths: {val}"
```

---

### MEDIUM

---

**M1 · api.py · `/api/memories` and `/api/conversations` · Inconsistent response shape breaks frontend contract**

Every other endpoint returns `{"ok": True, ...}`. These two return a bare JSON array:

```python
return jsonify(memories)   # GET /api/memories
return jsonify(convs)      # GET /api/conversations
```

Any frontend code that checks `response.ok` before accessing data will see `undefined` and either silently show nothing or hard-crash with a TypeError. The discrepancy is invisible in manual testing if the UI happens to check `Array.isArray()` instead, but it is a latent breakage waiting for the next frontend developer.

**Fix:** Wrap both: `return jsonify({"ok": True, "memories": memories})` / `{"ok": True, "conversations": convs}`.

---

**M2 · memory/store.py · `init_db()` · Migration is not concurrent-safe on multi-process startup**

`init_db()` uses two separate connections: one for `executescript(schema)` (idempotent) and one for migrations:

```python
with _connect() as conn:
    for col, typedef in [...]:
        if not _column_exists(conn, "conversations", col):
            conn.execute(f"ALTER TABLE conversations ADD COLUMN {col} {typedef}")
```

If `api.py` and `main.py` both start simultaneously (Docker, systemd, or a manual `start.sh`), both connections call `_column_exists()` → `False`, both issue `ALTER TABLE ADD COLUMN used_cloud`. The second one raises `sqlite3.OperationalError: duplicate column name: used_cloud`. The `_connect()` contextmanager re-raises, propagating out of `init_db()`. The calling component (background loop, cleanup crew, etc.) has no try/except around `init_db()`, so the thread exits silently — or in standalone mode, the process crashes with an unhandled exception.

**Fix:** Catch the duplicate-column `OperationalError` specifically:

```python
try:
    conn.execute(f"ALTER TABLE conversations ADD COLUMN {col} {typedef}")
except sqlite3.OperationalError as e:
    if "duplicate column name" not in str(e):
        raise
```

---

**M3 · scheduler.py · `fire_reminder()` / `reschedule_or_complete()` · Non-recurring reminder can double-fire on DB failure**

`fire_reminder()` dispatches callbacks in background threads, then calls `reschedule_or_complete()`. If `cancel_reminder()` raises inside `reschedule_or_complete()` (e.g., transient SQLite error), the outer `except` block resets `claimed_at=NULL`:

```python
except Exception as e:
    log.error("reschedule_or_complete failed ... resetting claim: %s", e)
    with _sched_connect() as conn:
        conn.execute("UPDATE reminders SET claimed_at=NULL WHERE id=?", ...)
```

The reminder is now: `active=1`, `fire_at <= now`, `claimed_at=NULL` — fully eligible for re-firing on the next 30-second cycle. Callbacks already dispatched will fire again. For a non-recurring "take your meds" reminder, this means a duplicate notification. For recurring reminders, the same race applies: if the update of `fire_at` to the next occurrence fails and the claim is reset, the reminder fires continuously once per 30 seconds until the DB error clears.

**Fix:** Separate the "deactivate" step (`active=0`) from the "reset claim" fallback. Only reset `claimed_at` if the reminder has NOT already been deactivated; distinguish cancellation failures from reschedule failures.

---

**M4 · tts.py · `_tts_pyttsx3()` · Not thread-safe — concurrent calls create conflicting engine instances**

```python
def _tts_pyttsx3(text: str) -> str | None:
    engine = pyttsx3.init()
    out = TTS_DIR / f"{uuid.uuid4().hex}.wav"
    engine.save_to_file(text, str(out))
    engine.runAndWait()
```

`pyttsx3.init()` on macOS uses CoreAudio / NSSpeechSynthesizer, which is not reentrant. If the API serves a `/api/chat?tts=true` request while the scheduler fires a reminder simultaneously (which also calls `speak()`), two engines are initialized concurrently. On macOS this results in one or both calls silently producing no audio, or in a hard crash from the underlying speech synthesizer. The `_edge_pool` module-level executor was correctly added for `edge_tts`, but pyttsx3 has no equivalent serialization.

**Fix:** Add a module-level `threading.Lock` (`_pyttsx3_lock`) and serialize `_tts_pyttsx3()` calls behind it.

---

**M5 · memory/store.py + schema.sql · `conversations`, `prune_log`, and orphaned `memory_links` grow without bound**

The schema comment declares conversations "source of truth, never pruned" — by design. But after 7 days of continuous operation there is no size check, no row limit, no `VACUUM` call, no archival hook anywhere in the codebase. Three separate growth vectors:

1. **`conversations`**: every API/CLI/Telegram exchange appends two rows (user + assistant) indefinitely.
2. **`prune_log`**: every pruned memory appends a full-content copy. Background loop fires every 3 minutes; cleanup runs every 5 minutes and prunes routinely. This can accumulate thousands of rows per day.
3. **`memory_links`**: when a memory is soft-deleted (`pruned=1`), its links in `memory_links` are never removed. `get_linked_memories()` filters them out in queries but they occupy space forever. No `ON DELETE CASCADE` on the FK.

After months of continuous operation, the SQLite file can reach hundreds of MB with no alerting and no user-visible indication.

**Fix (minimum):** Add a `trim_conversations(keep_last_n=10000)` utility called periodically (e.g., in `periodic_pruning`); add a similar `trim_prune_log(keep_days=90)`; add `DELETE FROM memory_links WHERE memory_id_a IN (SELECT id FROM memories WHERE pruned=1) OR memory_id_b IN (...)` to the pruning pass; call `PRAGMA incremental_vacuum` or periodic `VACUUM` after bulk deletes.

---

**M6 · api.py · chat.py · Model response content stored without sanitization**

`api.py` stores the assistant's raw response in conversation history:

```python
add_conversation(role="assistant", content=response_text, ...)
```

`chat.py` does the same:

```python
add_conversation(role="assistant", content=response)
```

Neither path calls `_sanitize()` on the model output. By contrast, `background_loop.py` and `cleanup_crew.py` both explicitly sanitize model output before storage:

```python
thought = _sanitize(thought, 500)  # sanitize model output before storage
```

If a cloud or local model returns a response containing injection patterns (e.g., a Claude response that includes the text "new instructions:" as part of a legitimate explanation), that text enters conversation history unsanitized. On the next turn, `get_context()` in `handoff.py` injects recent conversations into prompts — replaying the injection pattern into the next model call.

**Fix:** Call `_sanitize(response_text, 8000)` on the assistant response before calling `add_conversation()` in both `api.py` and `chat.py`.

---

**M7 · utils.py · `_sanitize()` + web_search.py · `_sanitize_result()` · Injection regex false-positives on legitimate content**

The `_INJECTION_RE` pattern includes:

```
ignore\s+(?:all\s+)?previous
new\s+instructions
system\s+prompt
```

These phrases appear routinely in legitimate technical content: "new instructions for configuring...", "the system prompt field", "ignore previous errors in git history", "setting new instructions on an LLM model's system prompt". When `_sanitize()` matches, the entire user message is silently replaced with `"[content removed — possible injection attempt]"` with no user-facing explanation. When `_sanitize_result()` matches in `web_search.py`, a legitimate search result is silently dropped from the search summary, potentially causing an incorrect or empty AI answer.

The current implementation is all-or-nothing: match → entire content replaced. There is no logging to the user in the API response, no way to distinguish a true attack from a false positive, and no mechanism to see how often this triggers.

**Fix:** Log false-positive candidates before discarding. Consider a narrower heuristic (require multiple patterns co-occurring, or check for role-switching context). At minimum, return a user-visible error from the API (`"Your message was flagged as a possible injection attempt"`) rather than silently replacing it — silent replacement leads to confusing "empty" responses.

---

### LOW

---

**L1 · scheduler.py · `run()` · Global `_shutdown` reassignment — partial fix from Round 1**

`background_loop.py` and `cleanup_crew.py` were corrected in Round 1 to use a local `evt` variable. `scheduler.run()` was not:

```python
def run(shutdown_event: threading.Event = None):
    global _shutdown
    if shutdown_event:
        _shutdown = shutdown_event
```

If `telegram_bot.py` calls `scheduler.run(_sched_shutdown)` while `main.py` has already started the scheduler (which cannot happen in normal operation but can happen in tests or if the lock check fails), the second call replaces the module-level `_shutdown`. Any code holding a reference to the old event will never see it set.

**Fix:** Apply the same pattern as the other modules: `evt = shutdown_event if shutdown_event is not None else _shutdown` and use `evt` throughout, without `global`.

---

**L2 · main.py · Early SIGINT before lock acquisition leaves stale lock file**

`_lockfd = None` at module level. SIGINT is registered after `_lockfd` is assigned in `main()`, but if a KeyboardInterrupt arrives during the very brief window before `main()` runs (interpreter startup), or if `sys.exit()` is called before `main()`, the `finally` block runs:

```python
try:
    _lockfd.close()            # AttributeError: 'NoneType' has no attribute 'close'
    lock_file.unlink(...)
except Exception:
    pass                       # swallowed — lock file left on disk
```

`except Exception: pass` swallows the `AttributeError`, `.main.lock` is never deleted. The OS will release the `flock` automatically (no other process holds it), so the next startup will succeed — but the stale file lingers. If `lock_file.exists()` is checked anywhere else before the next startup's flock attempt, it could cause a false "already running" conclusion.

**Fix:** Guard `_lockfd.close()` with `if _lockfd is not None`.

---

**L3 · scheduler.py · `_main_running()` · `FileNotFoundError` caught as `IOError` → false positive → Telegram reminders silently never fire**

```python
if not lock_file.exists():
    return False
fd = None
try:
    fd = open(lock_file, 'r')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return False
except IOError:
    return True     # ← also catches FileNotFoundError
```

TOCTOU: if `main.py` exits and deletes `.main.lock` between the `exists()` check and the `open()` call, `FileNotFoundError` (a subclass of `IOError`/`OSError`) is caught and the function returns `True` — "main is running". `telegram_bot.py` therefore skips starting its local scheduler. Reminder callbacks are never registered, reminders never fire for the entire session. This failure is silent.

**Fix:** Catch `FileNotFoundError` separately and return `False`:

```python
except FileNotFoundError:
    return False   # lock file gone = main exited
except IOError:
    return True    # lock held = main running
```

---

### SUMMARY

**Total new issues: 13 (0 critical, 3 high, 7 medium, 3 low)**

**Overall code health:** The codebase is well-structured and shows genuine care for concurrent safety and input validation, but three compounding runtime-only failures — the stale API key gate that silently disables cloud routing, the blocking executor that permanently wedges the cleanup lock, and the relative-path traversal that survives the hardened shell arg checker — are production-grade bugs that won't surface in development and will be hard to diagnose without this audit.
