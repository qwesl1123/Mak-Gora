# Application Availability

Mak'Gora applies process-local limits to anonymous Socket.IO traffic and retained duel state. These controls contain application-layer resource use without adding a gameplay turn limit or changing duel resolution.

Reverse-proxy and per-IP protections are configured separately through Nginx Proxy Manager and are outside this repository.

## Defaults and environment overrides

All overrides are read when the application starts.

| Setting | Default | Environment variable |
| --- | ---: | --- |
| Maximum queued SIDs | 100 | `MAKGORA_MAX_QUEUED_SIDS` |
| Maximum retained rooms | 50 | `MAKGORA_MAX_ACTIVE_ROOMS` |
| Maximum retained combat-log entries per room | 500 | `MAKGORA_MAX_RETAINED_LOG_ENTRIES` |
| Queue lifetime | 900 seconds (15 minutes) | `MAKGORA_QUEUE_TTL_SECONDS` |
| Prep idle lifetime | 600 seconds (10 minutes) | `MAKGORA_PREP_IDLE_TTL_SECONDS` |
| Prep absolute lifetime | 1,800 seconds (30 minutes) | `MAKGORA_PREP_MAX_LIFETIME_SECONDS` |
| Combat idle lifetime | 900 seconds (15 minutes) | `MAKGORA_COMBAT_IDLE_TTL_SECONDS` |
| Combat absolute lifetime | 7,200 seconds (2 hours) | `MAKGORA_COMBAT_MAX_LIFETIME_SECONDS` |
| Ended-room grace period | 120 seconds (2 minutes) | `MAKGORA_ENDED_TTL_SECONDS` |
| Cleanup interval | 30 seconds | `MAKGORA_CLEANUP_INTERVAL_SECONDS` |
| Socket.IO inbound buffer | 16,384 bytes (16 KiB) | `MAKGORA_SOCKET_MAX_BUFFER_BYTES` |
| Throttle-warning cooldown | 2 seconds | `MAKGORA_THROTTLE_WARNING_COOLDOWN_SECONDS` |
| Stale limiter-record lifetime | 900 seconds (15 minutes) | `MAKGORA_LIMITER_STALE_TTL_SECONDS` |
| Maximum retained SID limiter records | 1,000 | `MAKGORA_MAX_LIMITER_SIDS` |

Each protected event has an independent per-SID token bucket. `Events / window` defines the continuous refill rate, and `Burst` is both the initial and maximum token count.

| Socket.IO event | Events / window | Burst | Events override | Window override | Burst override |
| --- | ---: | ---: | --- | --- | --- |
| `duel_queue` | 3 / 10 seconds | 3 | `MAKGORA_QUEUE_THROTTLE_EVENTS` | `MAKGORA_QUEUE_THROTTLE_WINDOW_SECONDS` | `MAKGORA_QUEUE_THROTTLE_BURST` |
| `duel_lock_in` | 4 / 10 seconds | 4 | `MAKGORA_LOCK_THROTTLE_EVENTS` | `MAKGORA_LOCK_THROTTLE_WINDOW_SECONDS` | `MAKGORA_LOCK_THROTTLE_BURST` |
| `duel_prep_submit` | 12 / 10 seconds | 12 | `MAKGORA_PREP_THROTTLE_EVENTS` | `MAKGORA_PREP_THROTTLE_WINDOW_SECONDS` | `MAKGORA_PREP_THROTTLE_BURST` |
| `duel_action` | 10 / 10 seconds | 8 | `MAKGORA_ACTION_THROTTLE_EVENTS` | `MAKGORA_ACTION_THROTTLE_WINDOW_SECONDS` | `MAKGORA_ACTION_THROTTLE_BURST` |
| `duel_chat` | 8 / 10 seconds | 5 | `MAKGORA_CHAT_THROTTLE_EVENTS` | `MAKGORA_CHAT_THROTTLE_WINDOW_SECONDS` | `MAKGORA_CHAT_THROTTLE_BURST` |

## Lifecycle and activity rules

Expiration uses server-controlled monotonic time. A resource expires when `now >= start + lifetime`; equality is expired. The absolute prep and combat limits are checked before their idle limits. Cleanup runs on the next process-local sweep at or after a deadline, so periodic reclamation can occur up to one cleanup interval later.

- A queue entry starts its 15-minute lifetime when the SID is inserted. Repeating a queue request does not refresh an existing entry. Re-queueing after expiration creates a fresh timestamp.
- A prep room starts its phase and activity clocks when it is created. A successfully validated and committed build update or lock-in refreshes prep activity. Entering combat starts new combat phase and activity clocks.
- A successfully validated and submitted combat action refreshes combat activity. An action may therefore refresh activity before the opponent's action arrives and the turn resolves.
- Malformed, rejected, stale, or throttled events do not refresh room activity. Chat never refreshes prep or combat gameplay activity, even when accepted.
- Prep and combat absolute lifetimes continue advancing despite accepted activity. There is no gameplay turn cap.
- An ended match receives its timestamp once, remains available for final snapshots during the two-minute grace period, and is then removed.
- Disconnects immediately remove the SID's queue and limiter state and clean up any associated retained room.

Exactly one Flask-SocketIO background sweeper is started per application process. It uses `socketio.sleep()`, handles queue, room, and stale-limiter expiration in one pass, and has no per-room timers. State collection and removal occur under the process state lock; notifications and Socket.IO room closure happen after the lock is released. Sweeper exceptions are logged and do not terminate later sweeps.

## Capacity and retained logs

Queue insertion, pairing, room-cap checks, room creation, SID mappings, and cleanup are serialized by the process state lock.

- At most 100 SIDs can remain queued. A new SID is rejected when the queue is full; existing entries are preserved.
- At most 50 rooms are retained across prep, combat, and ended-grace phases. When the room cap is full, queued players remain queued instead of being discarded.
- Each room retains the newest 500 combat-log entries. Older entries are discarded in chronological order, while the monotonic log sequence continues increasing across retention rollovers.
- Snapshots expose at most the newest 30 log entries plus sequence metadata. The sequence, not retained-list length, is the client cursor.

These caps and the sweeper are process-local. Deployments with multiple application processes apply the same configured limits independently in each process.

## Per-SID throttling and local testing

Throttling runs before state lookup and payload validation, so malformed and post-lock spam still consumes that SID's event allowance. A throttled event does not enqueue, update prep, submit an action, resolve a turn, relay chat, append combat logs, or refresh lifecycle activity.

Throttle warnings are suppressed to at most one response per SID across all protected event categories every two seconds. Limiter state is removed on disconnect or after 15 minutes without a protected event. At most 1,000 SID records are retained; admitting another record at the bound evicts the least-recently-seen record.

Limits are keyed by Socket.IO SID, never by IP address. Two browser tabs on the same computer therefore receive independent allowances, and the defaults comfortably support two SIDs queueing into one duel room through prep and combat.

## Socket.IO buffer and startup validation

`MAKGORA_SOCKET_MAX_BUFFER_BYTES` configures Flask-SocketIO's `max_http_buffer_size`. The 16 KiB default limits inbound Socket.IO transport messages only; it does not limit outbound duel snapshots. The minimum accepted value is 4,096 bytes (4 KiB), which leaves room for legitimate queue, prep, action, and chat protocol traffic.

Invalid policy values fail startup with a clear `ValueError`. Integer overrides must parse as integers, numeric-duration and window overrides must be finite, all capacities and durations must be positive, throttle event and burst counts must be positive, and throttle windows must be positive. Retained-log capacity must be at least the 30-entry snapshot window, and the Socket.IO buffer must be at least 4 KiB.
