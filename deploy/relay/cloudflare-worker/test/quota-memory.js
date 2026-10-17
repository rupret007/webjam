import {
  COUNT_EXPIRED_NET_SQL,
  COUNT_EXPIRED_PREFIX_SQL,
  EXPIRE_NET_SQL,
  EXPIRE_PREFIX_SQL,
  NET_SCHEMA_SQL,
  PREFIX_SCHEMA_SQL,
  SELECT_NET_SQL,
  SELECT_PREFIX_SQL,
  UPSERT_NET_SQL,
  UPSERT_PREFIX_SQL,
} from "../src/quota.js";

function sqlCursor(rows = [], changes = 0) {
  return {
    *[Symbol.iterator]() {
      yield* rows;
    },
    toArray() {
      return rows.slice();
    },
    changes,
  };
}

export function memoryQuotaSql(prefixRows, netRows, stats) {
  const keyOf = (scope, id) => `${scope}\t${id}`;
  const expireRows = (rows, scope, day, limit) => {
    const keys = [];
    for (const [key, row] of rows) {
      if (row.scope === scope && row.day !== day) {
        keys.push(key);
      }
    }
    const parsed = Number(limit);
    const max = Number.isFinite(parsed) ? parsed : keys.length;
    const removing = keys.slice(0, Math.max(0, max));
    for (const key of removing) {
      rows.delete(key);
    }
    stats.deletedRows += removing.length;
    stats.rowMutations += removing.length;
    return sqlCursor([], removing.length);
  };
  return {
    exec(query, ...params) {
      if (query === PREFIX_SCHEMA_SQL || query === NET_SCHEMA_SQL) {
        return sqlCursor([]);
      }
      if (query === COUNT_EXPIRED_PREFIX_SQL) {
        const [scope, day] = params;
        let n = 0;
        for (const row of prefixRows.values()) {
          if (row.scope === scope && row.day !== day) {
            n += 1;
          }
        }
        return sqlCursor([{ n }]);
      }
      if (query === COUNT_EXPIRED_NET_SQL) {
        const [scope, day] = params;
        let n = 0;
        for (const row of netRows.values()) {
          if (row.scope === scope && row.day !== day) {
            n += 1;
          }
        }
        return sqlCursor([{ n }]);
      }
      if (query === SELECT_PREFIX_SQL) {
        const [scope, prefix, day] = params;
        const row = prefixRows.get(keyOf(scope, prefix));
        if (!row || row.day !== day) {
          return sqlCursor([]);
        }
        return sqlCursor([row]);
      }
      if (query === UPSERT_PREFIX_SQL) {
        const [scope, prefix, day, at, requests, row_writes, gb_s] = params;
        prefixRows.set(keyOf(scope, prefix), {
          at,
          requests,
          row_writes,
          gb_s,
          day,
          scope,
          prefix,
        });
        stats.prefixUpserts += 1;
        stats.rowMutations += 1;
        return sqlCursor([], 1);
      }
      if (query === EXPIRE_PREFIX_SQL) {
        const [scope, day, limit] = params;
        return expireRows(prefixRows, scope, day, limit);
      }
      if (query === SELECT_NET_SQL) {
        const [scope, net, day] = params;
        const row = netRows.get(keyOf(scope, net));
        if (!row || row.day !== day) {
          return sqlCursor([]);
        }
        return sqlCursor([row]);
      }
      if (query === UPSERT_NET_SQL) {
        const [scope, net, day, requests, row_writes, gb_s] = params;
        netRows.set(keyOf(scope, net), {
          requests,
          row_writes,
          gb_s,
          day,
          scope,
          net,
        });
        stats.netUpserts += 1;
        stats.rowMutations += 1;
        return sqlCursor([], 1);
      }
      if (query === EXPIRE_NET_SQL) {
        const [scope, day, limit] = params;
        return expireRows(netRows, scope, day, limit);
      }
      throw new Error("unsupported sql");
    },
  };
}

export function memoryQuotaStorage() {
  const data = new Map();
  const prefixRows = new Map();
  const netRows = new Map();
  const stats = { prefixUpserts: 0, netUpserts: 0, deletedRows: 0, rowMutations: 0 };
  let puts = 0;
  return {
    data,
    prefixRows,
    netRows,
    stats,
    get puts() {
      return puts;
    },
    get storageMutations() {
      return puts + stats.rowMutations;
    },
    storage: {
      async get(key) {
        return data.get(key);
      },
      async put(key, value) {
        puts += 1;
        data.set(key, value);
      },
      sql: memoryQuotaSql(prefixRows, netRows, stats),
    },
  };
}

export function quotaCheck(quota, { ip, nowMs, writes = 0, charge = "all", scope = "mailbox" }) {
  return quota.fetch(
    new Request("https://quota/check", {
      method: "POST",
      headers: {
        "CF-Connecting-IP": ip,
        "X-WebJam-Now": String(nowMs),
        "X-WebJam-Row-Writes": String(writes),
        "X-WebJam-Quota-Scope": scope,
        "X-WebJam-Quota-Charge": charge,
      },
    }),
  );
}
