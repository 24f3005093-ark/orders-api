import math
import time
import uuid
import base64
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

TOTAL = 46      # T: orders 1..46
RATE = 18       # R: requests allowed...
WINDOW = 10     # ...per 10 seconds

app = FastAPI()

# ---------- 3. RATE LIMITING ----------
# For each client id, remember the timestamps of their recent requests.
hits = defaultdict(deque)

@app.middleware("http")
async def rate_limiter(request: Request, call_next):
    client_id = request.headers.get("X-Client-Id")
    if client_id:  # only limit requests that identify a client
        now = time.time()
        q = hits[client_id]
        # throw away timestamps older than 10 seconds
        while q and now - q[0] >= WINDOW:
            q.popleft()
        if len(q) >= RATE:
            retry_after = max(1, math.ceil(WINDOW - (now - q[0])))
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers={"Retry-After": str(retry_after)},
            )
        q.append(now)
    return await call_next(request)

# CORS must be added AFTER the rate limiter so it wraps it
# (that way even 429 responses carry CORS headers).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

# ---------- 1. IDEMPOTENT POST ----------
orders_by_key = {}   # Idempotency-Key -> the order we already created

@app.post("/orders", status_code=201)
async def create_order(idempotency_key: Optional[str] = Header(None)):
    if idempotency_key and idempotency_key in orders_by_key:
        return orders_by_key[idempotency_key]      # same order, no duplicate
    order = {"id": str(uuid.uuid4()), "status": "created"}
    if idempotency_key:
        orders_by_key[idempotency_key] = order
    return order

# ---------- 2. CURSOR PAGINATION ----------
def encode(n: int) -> str:
    return base64.urlsafe_b64encode(f"after:{n}".encode()).decode()

def decode(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode().split(":")[1])
    except Exception:
        return 0

@app.get("/orders")
async def list_orders(limit: int = 10, cursor: Optional[str] = None):
    limit = max(1, min(limit, TOTAL))
    last_seen = decode(cursor)
    ids = list(range(last_seen + 1, min(last_seen + limit, TOTAL) + 1))
    items = [{"id": i, "name": f"Order {i}"} for i in ids]
    next_cursor = encode(ids[-1]) if ids and ids[-1] < TOTAL else None
    return {"items": items, "next_cursor": next_cursor}