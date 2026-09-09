# ADR 0002: transactional queue coordination

Queue mutation is serialized by a single-host SQLite lock. Every local queue
gets a lock database beside its storage root, and independent worker processes
must point at that same coordinator path. Mutations hold `BEGIN IMMEDIATE` while
they inspect and publish queue records, so claim, takeover cleanup, and terminal
publication cannot interleave into duplicate ownership or stale terminal state.
The lock does not make filesystem or Drive object writes one atomic transaction;
terminal records are published before cleanup, and recovery treats leftover
running records as stale when a terminal record already exists.
The lock is re-entrant within a thread because claim and finish call smaller
queue operations internally.

Raw Google Drive objects do not provide compare-and-swap or multi-object
transactions. A production Drive queue therefore fails closed unless a shared
SQLite coordinator is supplied, or an operator explicitly selects
`single_writer=True`. Fencing remains useful for stale publication, but it is a
recovery guard and does not turn Drive into a transactional queue.

The suite covers 120 deterministic repetitions across concurrent claim, expiry
takeover with stale finish, and terminal finish versus cleanup, plus crash and
stale-record recovery cases.
