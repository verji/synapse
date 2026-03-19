# Analysis: `device_lists_changes_in_room` Table Growth and Cleanup

**Date:** 2026-03-17
**Context:** The table is enormous in staging and production. We do not use federation. Some users may not log in for 1-2 months.

---

## 1. Purpose of the Table

Introduced in schema version 69 (PR #12321). When a user's device list changes, one row is inserted **per room the user is in**. A user in 200 rooms updating one device generates 200 rows.

The table serves two purposes:

| Purpose | Mechanism |
|---|---|
| **Federation** | A background process (`_handle_new_device_update_async` in `synapse/handlers/device.py:802`) reads unconverted rows and creates entries in `device_lists_outbound_pokes`, which the federation sender transmits to remote servers. |
| **`/sync` optimization** | Used to efficiently determine which users in a client's rooms have changed devices since the last sync token (`synapse/storage/databases/main/devices.py:1452-1496`). |

### Schema

Defined in `synapse/storage/schema/main/delta/69/01device_list_oubound_by_room.sql`:

```sql
CREATE TABLE device_lists_changes_in_room (
    user_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    stream_id BIGINT NOT NULL,
    converted_to_destinations BOOLEAN NOT NULL,
    opentracing_context TEXT
);
```

- `room_id` is plain text, **not a foreign key**.
- `device_id` is plain text, **not a foreign key**.
- Indexes: unique on `(stream_id, room_id)`, partial on `stream_id WHERE NOT converted_to_destinations`, and `(room_id, stream_id)`.

### No Cleanup Exists

There is **no background job, admin API, or any code path** that deletes rows from this table. The related table `device_lists_outbound_pokes` is pruned every 60 minutes by `_prune_old_outbound_device_pokes` (`devices.py:1306`), but `device_lists_changes_in_room` grows indefinitely. This is tracked as an open upstream issue.

---

## 2. Is It Required Without Federation?

**No. Without federation, this table is purely a performance optimization for `/sync`.**

The federation side (`_handle_new_device_update_async`) converts rows into outbound pokes, but with federation disabled those pokes go nowhere. The only remaining consumer is the `/sync` endpoint, where the table provides a faster query path than the fallback.

The source-of-truth data lives in `device_lists_stream`, not in this table.

---

## 3. Rows Referring to Deleted Devices

When a device is deleted via `delete_devices()` (`devices.py:1767-1792`), only the `devices` and `device_auth_providers` tables are cleaned. **No rows are removed from `device_lists_changes_in_room`.** Since `device_id` is not a foreign key, these rows become orphaned.

The `/sync` query (`devices.py:1469-1472`) only selects `DISTINCT user_id` — the `device_id` column is not used in the sync path. These orphaned rows contribute to table bloat but serve no functional purpose.

**Verdict:** Safe to delete.

---

## 4. Rows Referring to Deleted Rooms

When a room is purged via `_purge_room_txn()` (`synapse/storage/databases/main/purge_events.py:349-487`), the method deletes from ~35 tables, but **`device_lists_changes_in_room` is not among them**. Since `room_id` is not a foreign key, these rows become orphaned.

**Verdict:** Safe to delete.

---

## 5. Consequences of Truncating the Table

### 5.1 The `/sync` Code Path Has Two Layers Before This Table

```
/sync (incremental, with since_token)
  |
  +-- Layer 1: StreamChangeCache (in-memory, ~10,000 entries)
  |     Source: device_lists_stream table (NOT device_lists_changes_in_room)
  |     Populated: on startup + real-time via replication
  |     |
  |     +-- HIT  -> returns changed users from cache -> DONE (table never queried)
  |     +-- MISS -> fall through to Layer 2
  |
  +-- Layer 2: get_device_list_changes_in_rooms()
        Queries: device_lists_changes_in_room
        |
        +-- returns Set  -> use result (fast path)
        +-- returns None -> expensive fallback:
              get_users_who_share_room_with_user() (N+1 queries)
              + get_users_whose_devices_changed() (batched device_lists_stream query)
```

Key references:
- Layer 1 cache check: `synapse/handlers/sync.py:1663`
- Layer 2 table query: `synapse/handlers/device.py:170`
- Fallback path: `synapse/handlers/device.py:183-197`

### 5.2 Impact on Active Users (Fresh Sync Tokens)

**None.** Active users have sync tokens within the StreamChangeCache window (~10,000 entries, populated from `device_lists_stream`). Layer 1 always hits. The truncated table is never queried.

### 5.3 Impact on Users Returning From Long Absence

There is a **correctness nuance** (not a performance issue).

The guard logic in `devices.py:1464-1467`:

```python
min_stream_id = await self._get_min_device_lists_changes_in_room()
# On empty table: COALESCE(MIN(stream_id), 0) = 0

if min_stream_id > from_id:  # 0 > positive_token -> False
    return None               # <- never reached on empty table
```

On an empty table, `MIN` returns 0 via `COALESCE`. Since `0 > positive_token` is always `False`, the code proceeds to query the empty table and returns an empty `set()` ("no changes") instead of `None` ("can't tell, use fallback"). This causes a returning user to **miss device list changes for one sync cycle**. On the next sync they receive a fresh token and everything works correctly.

E2EE handles this gracefully — if a client has stale device keys, the recipient cannot decrypt, sends an error, and the sender re-fetches keys.

**This is the same behavior that would happen if the table simply had no rows old enough for the user's token, which is the normal steady-state for any deployment that implements periodic cleanup.**

### 5.4 Impact on Server Restart

**None.** The `StreamChangeCache` is prefilled from `device_lists_stream` (not from `device_lists_changes_in_room`) on startup. Truncating the table does not affect cache warmup.

### 5.5 Impact on Federation (N/A)

Federation is not used. Even if it were, the background converter would simply find no rows to convert — a no-op.

### 5.6 Could It Cause Synapse to Crash or Become Unresponsive?

**No.** No new load is introduced by truncation. The expensive fallback path (`get_users_who_share_room_with_user`) is only reached when BOTH the in-memory cache misses AND the table returns `None`. For active users the cache always hits. For returning users, they arrive gradually and the fallback uses individually-cached methods (`get_rooms_for_user` with 500k cache, `get_users_in_room` with 100k cache).

---

## 6. Mitigation: Sentinel Row

To ensure that returning users with stale tokens correctly trigger the fallback path (instead of getting an empty "no changes" result), insert a sentinel row after truncation:

```sql
TRUNCATE device_lists_changes_in_room;

INSERT INTO device_lists_changes_in_room
  (user_id, device_id, room_id, stream_id, converted_to_destinations, opentracing_context)
VALUES
  ('', '', '', (SELECT COALESCE(MAX(stream_id), 0) FROM device_lists_stream), TRUE, NULL);
```

This makes `MIN(stream_id)` equal to the current stream position. Any returning user with a token older than this correctly gets `None` from the table, triggering the (correct) fallback path instead of an empty-set false negative.

The `_get_min_device_lists_changes_in_room()` method is `@cached()` and never invalidated, so this value is computed once on startup and remains stable.

---

## 7. Recommended Procedure

1. **Schedule a maintenance window** (brief, minutes).
2. **Stop Synapse.**
3. **Truncate the table:**
   ```sql
   TRUNCATE device_lists_changes_in_room;
   ```
4. **Insert the sentinel row** (see Section 6).
5. **Optionally reset the conversion tracking table:**
   ```sql
   UPDATE device_lists_changes_converted_stream_position
   SET stream_id = (SELECT COALESCE(MAX(stream_id), 0) FROM device_lists_stream),
       room_id = '';
   ```
6. **Start Synapse.**
7. **Verify:** Monitor `/sync` response times for the first hour. Expect no degradation for active users.

### Ongoing: Periodic Cleanup

To prevent the table from growing back to its current size, consider implementing a periodic cleanup job (e.g., via cron or a database scheduled task) that deletes rows older than N days:

```sql
DELETE FROM device_lists_changes_in_room
WHERE stream_id < (
    SELECT MIN(stream_id) FROM device_lists_stream
);
```

Or more aggressively, delete rows older than a chosen retention window based on stream_id age. The fallback path ensures correctness regardless of how much data is in this table.

---

## 8. Upstream References

| Reference | URL |
|---|---|
| Issue: "Clear out table periodically" | https://github.com/matrix-org/synapse/issues/13043 |
| Issue: "Table grows in rapid pace" (423 GB) | https://github.com/element-hq/synapse/issues/18054 |
| Issue: "Table is inefficient" (I/O fragmentation) | https://github.com/matrix-org/synapse/issues/14037 |
| PR: Position tracking (mitigated fragmentation) | https://github.com/matrix-org/synapse/pull/14516 |

---

## 9. Key Source Files

| File | What it contains |
|---|---|
| `synapse/storage/schema/main/delta/69/01device_list_oubound_by_room.sql` | Table creation |
| `synapse/storage/databases/main/devices.py:1439-1496` | MIN query, room-based query |
| `synapse/storage/databases/main/devices.py:2093-2134` | INSERT (row creation) |
| `synapse/storage/databases/main/devices.py:893-947` | Fallback: `get_users_whose_devices_changed` |
| `synapse/storage/databases/main/roommember.py:785-794` | Fallback: `get_users_who_share_room_with_user` |
| `synapse/handlers/device.py:164-197` | Orchestration: fast path vs fallback |
| `synapse/handlers/sync.py:1603-1713` | `/sync` device list generation |
| `synapse/handlers/device.py:802-918` | Background converter (federation) |
| `synapse/util/caches/stream_change_cache.py` | In-memory StreamChangeCache |
| `synapse/storage/databases/main/purge_events.py:349-487` | Room purge (does NOT clean this table) |
