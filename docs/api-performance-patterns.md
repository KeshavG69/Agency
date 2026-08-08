# Serving heavy lists without hanging — what trycompai/crm does, and where Collecct stands

Notes from reading `trycompai/crm`'s CRUD layer (`apps/api/src/**`) alongside Collecct's
own (`routers/opportunities.py`, `client/crm_store.py`), written to be actionable rather
than admiring. **Collecct is already ahead on one technique and behind on five.**

Source clone: `github.com/trycompai/crm`. Related: `docs/trycompai-deep-dive/03-backend-plumbing.md`.

---

## 0. Why a list endpoint hangs

Four causes, in the order they actually bite:

| # | Cause | Symptom |
|---|---|---|
| 1 | **Returning everything** | payload grows with the customer; one slow org kills the page |
| 2 | **Serial round trips** | rows, then count, then facets, then options — latency adds up |
| 3 | **Unindexed scans** | fine at 500 rows, a cliff at 50,000 |
| 4 | **Deep offset paging** | `skip(10000)` walks 10,000 documents before returning one |

Collecct has already been bitten by #1 and fixed it — the old endpoint is still there,
documented honestly:

```python
# routers/opportunities.py
"""DEPRECATED (kept during the pagination migration): the whole org enriched in one payload.
~10MB/9.5s on a large org — the UI now uses /page + /counts + /{id} instead."""
```

That comment is the whole lesson in two lines. The rest of this doc is about #2–#4.

---

## 1. One shared list contract, with a HARD page cap

```ts
// apps/api/src/trpc/list-input.ts
export const listInput = z.object({
  q: z.string().default(""),
  sort: z.string().default(""),
  dir: z.enum(["asc", "desc"]).default("asc"),
  page: z.number().int().min(1).default(1),
  pageSize: z.number().int().min(1).max(100).default(25),   // ← enforced at the contract
});
```

Every list endpoint takes the same five parameters, and `pageSize` **cannot exceed 100**
— it is rejected by validation before a query is built. There is no code path that
returns an unbounded list.

They also ship shared helpers so no endpoint hand-rolls paging or sorting:

```ts
export function paginate(input) { return { skip: (input.page - 1) * input.pageSize, take: input.pageSize }; }

export function resolveOrderBy(input, columns, fallback) {
  const column = columns[input.sort];         // ← a WHITELIST, keyed by column name
  return column ? column(input.dir) : fallback;
}
```

`resolveOrderBy` is quietly important: sort columns are a **whitelist map**, so a client
cannot ask to sort by an unindexed column (or inject one). Each entry also supplies a
tiebreaker, which is what stops rows shuffling between pages when many share a value:

```ts
const SORTABLE = {
  company: (dir) => [{ company: { name: dir } }, { name: "asc" }],   // ← tiebreak
  lastActivity: (dir) => [{ lastActivityAt: { sort: dir, nulls: "last" } }],
};
```

> **Collecct:** `list_page` caps at `min(limit, 1000)` — 10× their ceiling. Sorting is
> hardcoded to `priority_score desc, _id desc`, which *does* include a stable tiebreak
> (good, and for the same reason). There is no shared contract: `/page`, `/counts` and
> `/facets` each re-declare their filter params via `_list_filters`.

---

## 2. Fire every query for the page CONCURRENTLY

This is the single biggest structural difference.

```ts
// apps/api/src/deals/deals.service.ts
async list(input: DealListInput) {
  const where = this.buildWhere(input);
  const { skip, take } = paginate(input);

  const [rows, total, facetCounts, openValue] = await Promise.all([
    this.db.deal.findMany({ where, skip, take, orderBy: …, select: { …explicit… } }),
    this.db.deal.count({ where }),
    this.facetCounts(input),
    this.db.deal.aggregate({ where: { …where, stage: { in: OPEN } }, _sum: { amount: true } }),
  ]);

  return { rows, total, facetCounts, openValueCents: toCents(openValue._sum.amount) };
}
```

Four separate database operations, **one wall-clock wait**, **one HTTP round trip**. The
page arrives complete: rows, the total, the facet counts, and the pipeline value.

> **Collecct:** the equivalent is spread over **three HTTP calls** the browser makes
> separately — `/page`, `/counts`, `/facets` — and inside `list_page` the count and the
> find run **serially** because pymongo is synchronous:
> ```python
> total = self.opps.count_documents(q)     # round trip 1
> cursor = self.opps.find(q, SLIM_PROJECTION)…   # round trip 2
> ```
> **Fix:** this is the cheapest real win available. Mongo can do both in ONE aggregation:
> ```python
> pipeline = [{"$match": q}, {"$facet": {
>     "rows":  [{"$sort": {"priority_score": -1, "_id": -1}},
>               {"$skip": offset}, {"$limit": limit},
>               {"$project": SLIM_PROJECTION}],
>     "total": [{"$count": "n"}],
>     "status": [ …the existing status_counts facets… ],
> }}]
> ```
> That collapses `/page` + `/counts` into one query and one endpoint. The `$facet` machinery
> is already written (see §3) — it just needs the rows branch added.

---

## 3. Facet counts — Collecct is already BETTER here

Their facet counting fires a query per facet:

```ts
private async facetCounts(input: DealListInput) {
  const where = this.searchFilter(input.q);   // ← note: the SEARCH only, not the full filter
  const [owners, stages, ...closingCounts] = await Promise.all([
    this.db.deal.groupBy({ by: ["ownerId"], where, _count: { _all: true } }),
    this.db.deal.groupBy({ by: ["stage"],   where, _count: { _all: true } }),
    ...CLOSING_WINDOWS.map((w) => this.db.deal.count({ where: { ...where, ...closingFilter(w) } })),
  ]);
}
```

That is `2 + CLOSING_WINDOWS.length` round trips (concurrent, but still N connections).

Collecct does the same job in **one**:

```python
# client/crm_store.py::status_counts
facets = {"all": [{"$count": "n"}]}
for key in ("Bid", "Watch", "No-Bid", "captured", "ingesting", "processing", "new"):
    facets[key] = [{"$match": self._status_clause(key)}, {"$count": "n"}]
res = list(self.opps.aggregate([{"$match": base}, {"$facet": facets}]))
```

**Keep this.** `$facet` runs every branch over one already-materialised match — it is the
right tool and there is nothing to learn from them here.

**The one idea worth taking:** notice their facet counts use `searchFilter(input.q)` and
deliberately **exclude the active facet**. That is what lets a filter pill show
*"Bid (42)"* even while you are looking at Watch — the counts describe what you'd get if
you clicked, not what you're already seeing. Collecct does exactly the same
(`filters.pop("status", None)  # counts span all statuses`). Both are right; it is a
subtle behaviour worth not breaking.

---

## 4. Never `SELECT *` — an explicit column list, always

```ts
select: {
  id: true, name: true, stage: true, amount: true, currency: true,
  expectedCloseDate: true, closedAt: true, lastActivityAt: true, createdAt: true,
  company: { select: COMPANY_SELECT },   // ← 7 named fields, not the whole company
  owner:   { select: OWNER_SELECT },     // ← 4 named fields, not the whole user
}
```

Relations are selected down to named fields too — a deal row carries seven company
columns, not a company record. `COMPANY_SELECT` / `OWNER_SELECT` are module constants, so
every endpoint that embeds a company embeds the *same* seven fields.

> **Collecct:** `SLIM_PROJECTION` is the same idea and is well documented:
> *"Heavy fields (document_text, analyst_rationale, extra, description …) are deliberately
> omitted; they load lazily in the detail pane. Keeps a list page tiny (~a few hundred
> bytes/opp) instead of ~4KB."* **Keep it.** Apply the same discipline to any new list.

---

## 5. Avoid N+1 with one batched `$in` — Collecct already does this

```python
# client/crm_store.py — one query per child collection, not one per parent
for d in self.documents.find({"opportunity_id": {"$in": ids}}).sort("created_at", -1): …
for c in self.calls.find({"opportunity_id": {"$in": ids}}): …
for t in self.tasks.find({"opportunity_id": {"$in": ids}}): …
```

Three queries for any number of opportunities. This is the correct pattern and matches
what Prisma's `select` does under the hood for relations.

---

## 6. Response caching with a TTL

```ts
// apps/api/src/cache/cache.module.ts
const DEFAULT_TTL_MS = 60_000;
CacheModule.registerAsync({
  isGlobal: true,
  useFactory: (config) => {
    const redisUrl = config.get("REDIS_URL");
    if (!redisUrl) { logger.warn("falling back to a per-instance in-memory cache"); return { ttl }; }
    return { ttl, stores: [new KeyvRedis(redisUrl)] };
  },
});
```

A global 60-second cache, Redis-backed when available and gracefully degrading to
in-process memory when not. Used for things that are expensive and tolerate being a
minute stale (facet option lists, model catalogues, workspace identity).

> **Collecct:** Redis is present but used **only** as the Celery broker/result backend.
> Nothing caches an HTTP response.
> **Best candidate by far:** `/facets` (`facet_values`) — a `distinct()` over agency,
> NAICS and set-aside across the whole org, recomputed on **every page load**, to fill
> dropdowns that change perhaps once a day. A 5-minute cache would remove it from the
> hot path entirely. (`company_profile` already proves the pattern — it is Redis-cached
> per org and busted on save.)

---

## 7. Text search: both are doing a full scan

```ts
// theirs
{ name: { contains: term, mode: "insensitive" } }        // Postgres ILIKE '%term%' → seq scan
```
```python
# ours — client/crm_store.py:522
rx = re.escape(q.strip())
{"title": {"$regex": rx, "$options": "i"}}               # unanchored regex → COLLSCAN
```

**Neither can use an index.** A leading-wildcard match scans the collection, and it runs
on every keystroke.

Fixes, cheapest first:
1. **Debounce on the client** (~300 ms) — removes most of the load for free.
2. **Anchor the regex** (`^term`) where prefix matching is acceptable — an anchored regex
   on an indexed field CAN use the index.
3. **A Mongo text index** on `title` + `agency` + `solicitation_number`, using `$text`.
4. Their global search (`search.service.ts`) at least bounds the damage: `PER_KIND = 5`,
   a minimum term length of 2, and three concurrent capped queries — never an unbounded
   scan-and-return.

The `if (term.length < 2) return { hits: [] }` guard is worth copying verbatim: a
one-character search matches everything and is never what anyone meant.

---

## 8. Deep paging: both degrade, neither has solved it

`skip(N)` in Mongo and `OFFSET N` in Postgres both **walk N rows before returning one**.
Page 1 is instant; page 400 is not.

**Keyset (cursor) pagination** is the fix — instead of "skip 10,000", ask for "the rows
after this sort key":

```python
# instead of .skip(offset)
{"$or": [
    {"priority_score": {"$lt": last_score}},
    {"priority_score": last_score, "_id": {"$lt": last_id}},
]}
```

This needs the stable tiebreak Collecct already sorts by (`priority_score desc, _id desc`)
and the index that already exists (`(organization_id, priority_score, _id)`). It is a
real but contained change, and only worth doing once someone actually pages deep — an
infinite-scroll or "load more" UI makes it natural.

---

## 9. The client half — why theirs *feels* instant

Backend work is wasted if the UI blanks on every keystroke. Three patterns, all cheap:

**a. Keep the old page on screen while the new one loads.**
```ts
placeholderData: (previous) => previous,      // contacts-table.tsx, members-table.tsx, …
```
No spinner, no layout jump when changing page, sort or filter — the previous rows stay,
greyed, until the new ones arrive. This single line is most of the perceived speed.

**b. Prefetch the row before it is clicked.**
```ts
const prefetchRecord = usePrefetchRecord();   // fired on row hover
```
The detail pane is usually already loaded by the time the click lands.

**c. Prefetch on the server, hydrate on the client.**
```ts
// app/(app)/contacts/page.tsx  — a React Server Component
await queryClient.prefetchQuery(trpc.contacts.list.queryOptions(params));  // awaited: blocks first paint
void  queryClient.prefetchQuery(trpc.users.list.queryOptions());           // not awaited: fills in behind
```
The primary list is awaited so the first paint has data; secondary lookups are fired
without `await` so they never delay it. Note the deliberate `await` vs `void` split.

---

## Scorecard

| Technique | trycompai | Collecct | Action |
|---|---|---|---|
| Explicit projection / no `SELECT *` | ✅ | ✅ `SLIM_PROJECTION` | keep |
| Batched `$in` (no N+1) | ✅ | ✅ | keep |
| Facet counts in ONE query | ⚠️ N queries | ✅ `$facet` | **keep ours — it's better** |
| Counts exclude the active facet | ✅ | ✅ | keep |
| Hard page-size cap | ✅ 100 | ⚠️ 1000 | lower it |
| Shared list contract + sort whitelist | ✅ | ❌ | adopt |
| Rows + total + facets in one round trip | ✅ | ❌ 3 HTTP calls, serial queries | **adopt — biggest win** |
| Response cache with TTL | ✅ Redis, 60 s | ❌ | adopt for `/facets` |
| Indexed text search | ❌ | ❌ | debounce, then `$text` |
| Keyset pagination | ❌ | ❌ | later, if anyone pages deep |
| `placeholderData: previous` | ✅ | ❌ | adopt (one line) |
| Hover prefetch / server prefetch | ✅ | ❌ | adopt |

---

## Applying this to the API I just added

`routers/intelligence.py` was written before this review and repeats two of the mistakes:

1. **`/tasks/health` makes five serial round trips** — three `count_documents`, one
   `aggregate`, one `find`. Should be a single `$facet` pipeline, exactly like
   `status_counts`.
2. **`/suggestions` sorts by `score` with no supporting index and no paging.** The index
   is `(organization_id, email, status)`; the query filters on `(organization_id, status)`
   and sorts by `score`, so the sort is in-memory. Needs
   `(organization_id, status, score)` and an offset/cursor parameter.
3. `/contacts/{email}/facts` is fine — two small indexed reads on a single contact.

Both are quick fixes and worth doing before the UI starts hitting them.

---

## The order I'd do these

1. **Collapse `/page` + `/counts` into one `$facet` query.** Halves the pipeline's
   round trips and removes a whole endpoint. The hard part (`$facet`) is already written.
2. **Cache `/facets` in Redis for 5 minutes.** A `distinct()` over the org on every page
   load, for dropdowns that change daily.
3. **Fix the two issues in `intelligence.py`** above, before anything depends on them.
4. **Lower the page cap** from 1000 to 100, and debounce search on the client.
5. **`placeholderData: previous`** wherever the pipeline list is rendered — one line,
   and it is most of what makes theirs feel quick.
6. Keyset pagination and a text index **only when the data says so.** Neither is worth
   doing on speculation.
