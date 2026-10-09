"""The Part 2 shipment layer (part2-shipment-workflow design section 2): everything that runs
*above* the Part 1 per-document pipeline, once a shipment's documents have their own results.

Every module here is plain code with zero model calls (requirement 15.3): the effective
document set, the cross-document checker, the shipment decision and the draft generator. The
only exception by design is `send.py`, which still makes no model call -- it just happens to be
the one function in this package that is allowed to have a side effect outside `app.db`
(writing the simulated outbox), and only on an explicit operator click (requirement 15.1).
"""

from __future__ import annotations
