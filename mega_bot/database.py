from __future__ import annotations
import asyncpg
from datetime import datetime, timezone
from typing import Any

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT NOT NULL DEFAULT '',
  first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(), last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  starts_count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS users_username_idx ON users (LOWER(username));
CREATE TABLE IF NOT EXISTS groups_seen (
  chat_id BIGINT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', username TEXT,
  first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(), last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  messages_count BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS groups_username_idx ON groups_seen (LOWER(username));
CREATE TABLE IF NOT EXISTS admins (
  user_id BIGINT PRIMARY KEY, username TEXT, display_name TEXT NOT NULL DEFAULT '',
  permissions JSONB NOT NULL DEFAULT '{"stats":true,"search":true,"case_lookup":true,"unban_user":true,"unban_group":true,"ban_user":false,"ban_group":false,"edit_about":false,"edit_team":false,"manage_admins":false,"settings":false}'::jsonb,
  active BOOLEAN NOT NULL DEFAULT TRUE, added_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value JSONB NOT NULL, updated_by BIGINT, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS team_members (
  id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
  username TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS moderation_cases (
  id BIGSERIAL PRIMARY KEY, case_code INTEGER UNIQUE NOT NULL CHECK (case_code BETWEEN 1000 AND 9999),
  target_type TEXT NOT NULL CHECK (target_type IN ('user','group')), target_id BIGINT NOT NULL,
  target_username TEXT, target_name TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL,
  duration_days INTEGER, permanent BOOLEAN NOT NULL DEFAULT FALSE, active BOOLEAN NOT NULL DEFAULT TRUE,
  action TEXT NOT NULL DEFAULT 'ban', issued_by BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), expires_at TIMESTAMPTZ,
  lifted_by BIGINT, lifted_at TIMESTAMPTZ, lift_reason TEXT
);
CREATE INDEX IF NOT EXISTS cases_target_idx ON moderation_cases(target_type, target_id, active);
CREATE TABLE IF NOT EXISTS audit_logs (
  id BIGSERIAL PRIMARY KEY, actor_id BIGINT NOT NULL, action TEXT NOT NULL,
  target_type TEXT, target_id BIGINT, details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS usage_events (
  id BIGSERIAL PRIMARY KEY, user_id BIGINT, chat_id BIGINT NOT NULL, chat_type TEXT NOT NULL,
  event_type TEXT NOT NULL DEFAULT 'message', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS usage_events_date_idx ON usage_events(created_at);
CREATE TABLE IF NOT EXISTS games (
  id TEXT PRIMARY KEY, chat_id BIGINT NOT NULL, creator_id BIGINT NOT NULL,
  kind TEXT NOT NULL, state JSONB NOT NULL DEFAULT '{}'::jsonb,
  active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS donations (
  id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(user_id),
  user_name TEXT NOT NULL DEFAULT '', amount BIGINT NOT NULL CHECK(amount > 0),
  show_name BOOLEAN NOT NULL DEFAULT FALSE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), reviewed_at TIMESTAMPTZ, reviewed_by BIGINT,
  broadcast_sent INTEGER NOT NULL DEFAULT 0, broadcast_failed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS donations_status_idx ON donations(status, created_at DESC);
CREATE TABLE IF NOT EXISTS learned_phrases (
  id BIGSERIAL PRIMARY KEY,
  scope_type TEXT NOT NULL CHECK (scope_type IN ('group','private')),
  scope_id BIGINT NOT NULL,
  trigger_norm TEXT NOT NULL,
  trigger_raw TEXT NOT NULL,
  response TEXT NOT NULL,
  created_by BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (scope_type, scope_id, trigger_norm)
);
CREATE INDEX IF NOT EXISTS learned_phrases_lookup_idx
  ON learned_phrases (scope_type, scope_id, trigger_norm);
CREATE TABLE IF NOT EXISTS group_settings (
  chat_id BIGINT PRIMARY KEY,
  welcome_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  welcome_text TEXT,
  welcome_varied BOOLEAN NOT NULL DEFAULT TRUE,
  welcome_mention BOOLEAN NOT NULL DEFAULT TRUE,
  goodbye_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  goodbye_text TEXT,
  goodbye_varied BOOLEAN NOT NULL DEFAULT TRUE,
  security JSONB NOT NULL DEFAULT '{}'::jsonb,
  moderation JSONB NOT NULL DEFAULT '{}'::jsonb,
  warn_limit INTEGER NOT NULL DEFAULT 5,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS warn_logs (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  message_id BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS warn_logs_chat_user_idx ON warn_logs (chat_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS warn_logs_chat_idx ON warn_logs (chat_id, created_at DESC);
CREATE TABLE IF NOT EXISTS punish_logs (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  punish_type TEXT NOT NULL,
  duration_sec INTEGER,
  warns_at INTEGER NOT NULL DEFAULT 0,
  success BOOLEAN NOT NULL DEFAULT FALSE,
  detail TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS punish_logs_chat_idx ON punish_logs (chat_id, created_at DESC);
CREATE TABLE IF NOT EXISTS group_vips (
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  added_by BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (chat_id, user_id)
);
CREATE INDEX IF NOT EXISTS group_vips_user_idx ON group_vips(user_id);
CREATE TABLE IF NOT EXISTS group_stats_daily (
  chat_id BIGINT NOT NULL,
  day DATE NOT NULL,
  joined INTEGER NOT NULL DEFAULT 0,
  left_count INTEGER NOT NULL DEFAULT 0,
  text_count INTEGER NOT NULL DEFAULT 0,
  photo_count INTEGER NOT NULL DEFAULT 0,
  video_count INTEGER NOT NULL DEFAULT 0,
  voice_count INTEGER NOT NULL DEFAULT 0,
  audio_count INTEGER NOT NULL DEFAULT 0,
  animation_count INTEGER NOT NULL DEFAULT 0,
  sticker_count INTEGER NOT NULL DEFAULT 0,
  document_count INTEGER NOT NULL DEFAULT 0,
  deleted_by_bot INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (chat_id, day)
);
CREATE TABLE IF NOT EXISTS group_user_msgs (
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  day DATE NOT NULL,
  msg_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (chat_id, user_id, day)
);
CREATE INDEX IF NOT EXISTS group_user_msgs_top_idx ON group_user_msgs(chat_id, day, msg_count DESC);
CREATE TABLE IF NOT EXISTS group_warns (
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  warn_count INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS recent_fun (
  chat_id BIGINT NOT NULL,
  kind TEXT NOT NULL,
  item_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (chat_id, kind, item_hash)
);
CREATE TABLE IF NOT EXISTS group_profanity_words (
  chat_id BIGINT NOT NULL,
  word TEXT NOT NULL,
  added_by BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (chat_id, word)
);
CREATE TABLE IF NOT EXISTS whispers (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  sender_id BIGINT NOT NULL,
  sender_name TEXT NOT NULL DEFAULT '',
  recipient_id BIGINT NOT NULL,
  body TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),
  consumed BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS whispers_lookup_idx ON whispers (id, recipient_id);
CREATE INDEX IF NOT EXISTS whispers_expire_idx ON whispers (expires_at) WHERE consumed = FALSE;
CREATE TABLE IF NOT EXISTS user_group_points (
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  points BIGINT NOT NULL DEFAULT 0,
  learn_count INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (chat_id, user_id)
);
"""

class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        async def init_connection(conn):
            await conn.set_type_codec('json', encoder=lambda v: __import__('json').dumps(v, ensure_ascii=False), decoder=__import__('json').loads, schema='pg_catalog')
            await conn.set_type_codec('jsonb', encoder=lambda v: __import__('json').dumps(v, ensure_ascii=False), decoder=__import__('json').loads, schema='pg_catalog')
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=8, command_timeout=30, init=init_connection)
        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA)
            # Lightweight migrations for tables created before new columns existed
            await conn.execute("ALTER TABLE whispers ADD COLUMN IF NOT EXISTS sender_name TEXT NOT NULL DEFAULT ''")
            await conn.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS moderation JSONB NOT NULL DEFAULT '{}'::jsonb")
            await conn.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS welcome_varied BOOLEAN NOT NULL DEFAULT TRUE")
            await conn.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS welcome_mention BOOLEAN NOT NULL DEFAULT TRUE")
            await conn.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS goodbye_enabled BOOLEAN NOT NULL DEFAULT TRUE")
            await conn.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS goodbye_text TEXT")
            await conn.execute("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS goodbye_varied BOOLEAN NOT NULL DEFAULT TRUE")
            await conn.execute("INSERT INTO settings(key,value) VALUES('about', to_jsonb($1::text)) ON CONFLICT(key) DO NOTHING", "MEGA BOT یک ربات اجتماعی برای بله است.")
            await conn.execute("INSERT INTO settings(key,value) VALUES('donations_total', '0'::jsonb) ON CONFLICT(key) DO NOTHING")
            defaults = {
                'donation_card': '6219861958356742',
                'donation_card_name': 'آرتین گلستانی',
                'donation_owner_username': 'commander04',
                'donation_intro': 'میتونی به‌صورت دلخواه از تیم ما حمایت کنی 🌱😭💞\n\nواقعاً دوست داری حمایتمون کنی؟ 😆',
                'donation_instructions': 'برای حمایت، می‌تونی به پیوی مالک ربات بری و پاکت هدیه بفرستی یا مبلغ دلخواهت رو به شماره کارت زیر واریز کنی.\n\nدونیت کاملاً اختیاریه و استفاده از ربات هیچ نیازی به پرداخت نداره؛ همین که کنار مایی برامون ارزشمنده 💚',
            }
            for key, value in defaults.items():
                await conn.execute("INSERT INTO settings(key,value) VALUES($1,to_jsonb($2::text)) ON CONFLICT(key) DO NOTHING", key, value)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    def _pool(self) -> asyncpg.Pool:
        if not self.pool:
            raise RuntimeError('Database is not connected')
        return self.pool

    async def upsert_user(self, user: dict[str, Any]) -> None:
        uid = user.get('id')
        if not uid:
            return
        async with self._pool().acquire() as c:
            await c.execute("""INSERT INTO users(user_id,username,first_name) VALUES($1,$2,$3)
              ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username, first_name=EXCLUDED.first_name, last_seen=NOW()""",
              int(uid), user.get('username'), user.get('first_name') or user.get('name') or '')

    async def upsert_group(self, chat: dict[str, Any]) -> None:
        cid = chat.get('id')
        if not cid:
            return
        async with self._pool().acquire() as c:
            await c.execute("""INSERT INTO groups_seen(chat_id,title,username) VALUES($1,$2,$3)
              ON CONFLICT(chat_id) DO UPDATE SET title=EXCLUDED.title, username=EXCLUDED.username, last_seen=NOW()""",
              int(cid), chat.get('title') or chat.get('name') or '', chat.get('username'))

    async def get_setting(self, key: str, default: Any = None) -> Any:
        async with self._pool().acquire() as c:
            row = await c.fetchrow('SELECT value FROM settings WHERE key=$1', key)
        return row['value'] if row else default

    async def set_setting(self, key: str, value: Any, actor: int) -> None:
        import json
        async with self._pool().acquire() as c:
            await c.execute("""INSERT INTO settings(key,value,updated_by,updated_at) VALUES($1,$2,$3,NOW())
              ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
              key, value, actor)

    async def log(self, actor: int, action: str, target_type: str | None = None, target_id: int | None = None, details: dict | None = None) -> None:
        import json
        async with self._pool().acquire() as c:
            await c.execute('INSERT INTO audit_logs(actor_id,action,target_type,target_id,details) VALUES($1,$2,$3,$4,$5)',
                            actor, action, target_type, target_id, details or {})

    async def is_admin(self, user_id: int) -> bool:
        async with self._pool().acquire() as c:
            return bool(await c.fetchval('SELECT active FROM admins WHERE user_id=$1', user_id))

    async def get_admin(self, user_id: int) -> dict | None:
        async with self._pool().acquire() as c:
            row = await c.fetchrow('SELECT * FROM admins WHERE user_id=$1 AND active=TRUE', user_id)
        return dict(row) if row else None

    async def list_admins(self, limit: int = 50, offset: int = 0) -> list[dict]:
        async with self._pool().acquire() as c:
            rows = await c.fetch('SELECT * FROM admins ORDER BY created_at DESC LIMIT $1 OFFSET $2', limit, offset)
        return [dict(r) for r in rows]

    async def search_target(self, term: str, target_type: str) -> list[dict]:
        async with self._pool().acquire() as c:
            if target_type == 'user':
                if term.lstrip('-').isdigit():
                    rows = await c.fetch('SELECT user_id AS id, username, first_name AS name FROM users WHERE user_id=$1 LIMIT 10', int(term))
                else:
                    name = term.lstrip('@').lower()
                    rows = await c.fetch('SELECT user_id AS id, username, first_name AS name FROM users WHERE LOWER(username) LIKE $1 ORDER BY last_seen DESC LIMIT 10', f'%{name}%')
            else:
                if term.lstrip('-').isdigit():
                    rows = await c.fetch('SELECT chat_id AS id, username, title AS name FROM groups_seen WHERE chat_id=$1 LIMIT 10', int(term))
                else:
                    name = term.lstrip('@').lower()
                    rows = await c.fetch('SELECT chat_id AS id, username, title AS name FROM groups_seen WHERE LOWER(username) LIKE $1 ORDER BY last_seen DESC LIMIT 10', f'%{name}%')
        return [dict(r) for r in rows]

    async def stats(self) -> dict:
        async with self._pool().acquire() as c:
            return {
                'users': await c.fetchval('SELECT COUNT(*) FROM users'),
                'new_today': await c.fetchval("SELECT COUNT(*) FROM users WHERE first_seen >= date_trunc('day', NOW())"),
                'groups': await c.fetchval('SELECT COUNT(*) FROM groups_seen'),
                'group_users': await c.fetchval("SELECT COUNT(DISTINCT user_id) FROM usage_events WHERE chat_type IN ('group','supergroup') AND user_id IS NOT NULL"),
                'banned_users': await c.fetchval("SELECT COUNT(*) FROM moderation_cases WHERE target_type='user' AND active=TRUE AND (expires_at IS NULL OR expires_at>NOW())"),
                'banned_groups': await c.fetchval("SELECT COUNT(*) FROM moderation_cases WHERE target_type='group' AND active=TRUE AND (expires_at IS NULL OR expires_at>NOW())"),
                'donations': await c.fetchval("SELECT COALESCE((value #>> '{}')::bigint,0) FROM settings WHERE key='donations_total'") or 0,
            }

    async def create_donation(self, user_id: int, user_name: str, amount: int, show_name: bool) -> int:
        async with self._pool().acquire() as c:
            row = await c.fetchrow("INSERT INTO donations(user_id,user_name,amount,show_name) VALUES($1,$2,$3,$4) RETURNING id", user_id, user_name, amount, show_name)
        return int(row['id'])

    async def get_donation(self, donation_id: int) -> dict | None:
        async with self._pool().acquire() as c:
            row = await c.fetchrow('SELECT * FROM donations WHERE id=$1', donation_id)
        return dict(row) if row else None

    async def review_donation(self, donation_id: int, approve: bool, reviewer: int) -> dict | None:
        async with self._pool().acquire() as c:
            async with c.transaction():
                row = await c.fetchrow('SELECT * FROM donations WHERE id=$1 FOR UPDATE', donation_id)
                if not row or row['status'] != 'pending':
                    return None
                status = 'approved' if approve else 'rejected'
                await c.execute('UPDATE donations SET status=$2,reviewed_at=NOW(),reviewed_by=$3 WHERE id=$1', donation_id, status, reviewer)
                if approve:
                    await c.execute("UPDATE settings SET value=to_jsonb((COALESCE((value #>> '{}')::bigint,0)+$1)::bigint),updated_by=$2,updated_at=NOW() WHERE key='donations_total'", int(row['amount']), reviewer)
                updated = await c.fetchrow('SELECT * FROM donations WHERE id=$1', donation_id)
        return dict(updated)

    async def private_user_ids(self) -> list[int]:
        async with self._pool().acquire() as c:
            rows = await c.fetch('SELECT user_id FROM users ORDER BY user_id')
        return [int(r['user_id']) for r in rows]

    async def group_chat_ids(self) -> list[int]:
        async with self._pool().acquire() as c:
            rows = await c.fetch('SELECT chat_id FROM groups_seen ORDER BY chat_id')
        return [int(r['chat_id']) for r in rows]

    async def upsert_learned(self, scope_type: str, scope_id: int, trigger_norm: str, trigger_raw: str, response: str, created_by: int | None) -> str:
        """Insert or update a learned phrase. Returns 'inserted' or 'updated'."""
        async with self._pool().acquire() as c:
            existing = await c.fetchval(
                'SELECT id FROM learned_phrases WHERE scope_type=$1 AND scope_id=$2 AND trigger_norm=$3',
                scope_type, int(scope_id), trigger_norm,
            )
            if existing:
                await c.execute(
                    """UPDATE learned_phrases SET response=$4, trigger_raw=$5, created_by=$6, updated_at=NOW()
                       WHERE scope_type=$1 AND scope_id=$2 AND trigger_norm=$3""",
                    scope_type, int(scope_id), trigger_norm, response, trigger_raw, created_by,
                )
                return 'updated'
            await c.execute(
                """INSERT INTO learned_phrases(scope_type,scope_id,trigger_norm,trigger_raw,response,created_by)
                   VALUES($1,$2,$3,$4,$5,$6)""",
                scope_type, int(scope_id), trigger_norm, trigger_raw, response, created_by,
            )
            return 'inserted'

    async def get_learned(self, scope_type: str, scope_id: int, trigger_norm: str) -> str | None:
        async with self._pool().acquire() as c:
            row = await c.fetchrow(
                'SELECT response FROM learned_phrases WHERE scope_type=$1 AND scope_id=$2 AND trigger_norm=$3',
                scope_type, int(scope_id), trigger_norm,
            )
        return row['response'] if row else None

    async def delete_learned(self, scope_type: str, scope_id: int, trigger_norm: str) -> bool:
        async with self._pool().acquire() as c:
            result = await c.execute(
                'DELETE FROM learned_phrases WHERE scope_type=$1 AND scope_id=$2 AND trigger_norm=$3',
                scope_type, int(scope_id), trigger_norm,
            )
        return result.endswith('1') or (result.split()[-1].isdigit() and int(result.split()[-1]) > 0)

    async def clear_learned(self, scope_type: str, scope_id: int) -> int:
        """Delete all learned phrases for one scope. Returns number of deleted rows."""
        async with self._pool().acquire() as c:
            result = await c.execute(
                'DELETE FROM learned_phrases WHERE scope_type=$1 AND scope_id=$2',
                scope_type, int(scope_id),
            )
        # asyncpg returns e.g. "DELETE 5"
        try:
            n = int(result.split()[-1])
        except (ValueError, IndexError, AttributeError):
            n = 0
        return n

    async def add_learn_points(self, chat_id: int, user_id: int, points: int = 5) -> int:
        """Add points for a newly learned phrase in a group. Returns new total."""
        async with self._pool().acquire() as c:
            row = await c.fetchrow(
                """INSERT INTO user_group_points(chat_id, user_id, points, learn_count, updated_at)
                   VALUES($1, $2, $3, 1, NOW())
                   ON CONFLICT(chat_id, user_id) DO UPDATE SET
                     points = user_group_points.points + EXCLUDED.points,
                     learn_count = user_group_points.learn_count + 1,
                     updated_at = NOW()
                   RETURNING points""",
                int(chat_id), int(user_id), int(points),
            )
        return int(row['points']) if row else int(points)

    async def get_learn_points(self, chat_id: int, user_id: int) -> dict:
        async with self._pool().acquire() as c:
            row = await c.fetchrow(
                'SELECT points, learn_count FROM user_group_points WHERE chat_id=$1 AND user_id=$2',
                int(chat_id), int(user_id),
            )
        if not row:
            return {'points': 0, 'learn_count': 0}
        return {'points': int(row['points']), 'learn_count': int(row['learn_count'])}

    async def create_whisper(
        self,
        chat_id: int,
        sender_id: int,
        recipient_id: int,
        body: str,
        sender_name: str = '',
    ) -> int:
        async with self._pool().acquire() as c:
            # opportunistic cleanup of expired whispers
            await c.execute("DELETE FROM whispers WHERE expires_at < NOW() OR (consumed AND created_at < NOW() - INTERVAL '1 day')")
            row = await c.fetchrow(
                """INSERT INTO whispers(chat_id, sender_id, sender_name, recipient_id, body)
                   VALUES($1, $2, $3, $4, $5) RETURNING id""",
                int(chat_id), int(sender_id), (sender_name or '')[:120], int(recipient_id), body,
            )
        return int(row['id'])

    async def get_whisper_for_recipient(self, whisper_id: int, recipient_id: int, chat_id: int) -> dict | None:
        async with self._pool().acquire() as c:
            row = await c.fetchrow(
                """SELECT id, chat_id, sender_id, sender_name, recipient_id, body, expires_at, consumed
                   FROM whispers
                   WHERE id=$1 AND recipient_id=$2 AND chat_id=$3
                     AND expires_at > NOW()""",
                int(whisper_id), int(recipient_id), int(chat_id),
            )
        return dict(row) if row else None

    async def mark_whisper_consumed(self, whisper_id: int) -> None:
        async with self._pool().acquire() as c:
            await c.execute('UPDATE whispers SET consumed=TRUE WHERE id=$1', int(whisper_id))

    async def set_donation_broadcast_counts(self, donation_id: int, sent: int, failed: int) -> None:
        async with self._pool().acquire() as c:
            await c.execute('UPDATE donations SET broadcast_sent=$2,broadcast_failed=$3 WHERE id=$1', donation_id, sent, failed)

    async def record_usage(self, user_id: int | None, chat: dict, event_type: str = 'message') -> None:
        chat_type = chat.get('type') or 'private'
        async with self._pool().acquire() as c:
            await c.execute('INSERT INTO usage_events(user_id,chat_id,chat_type,event_type) VALUES($1,$2,$3,$4)', user_id, int(chat['id']), chat_type, event_type)
            if chat_type in ('group','supergroup'):
                await c.execute('UPDATE groups_seen SET messages_count=messages_count+1,last_seen=NOW() WHERE chat_id=$1', int(chat['id']))

    async def create_case(self, target_type: str, target_id: int, username: str | None, name: str, reason: str, days: int | None, permanent: bool, actor: int) -> dict:
        from datetime import timedelta
        async with self._pool().acquire() as c:
            async with c.transaction():
                await c.execute('SELECT pg_advisory_xact_lock(7402916)')
                used = await c.fetch('SELECT case_code FROM moderation_cases')
                occupied = {int(r['case_code']) for r in used}
                code = next((x for x in range(1000, 10000) if x not in occupied), None)
                if code is None:
                    raise RuntimeError('ظرفیت کدهای پیگیری چهارتایی تمام شده است.')
                expires = None if permanent or not days else datetime.now(timezone.utc) + timedelta(days=days)
                row = await c.fetchrow("""INSERT INTO moderation_cases(case_code,target_type,target_id,target_username,target_name,reason,duration_days,permanent,issued_by,expires_at)
                  VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *""", code,target_type,target_id,username,name,reason,days,permanent,actor,expires)
                await c.execute('INSERT INTO audit_logs(actor_id,action,target_type,target_id,details) VALUES($1,$2,$3,$4,$5)', actor, 'ban_'+target_type, target_type, target_id, {'case_code': code})
        return dict(row)

    async def lift_case(self, target_type: str, target_id: int, actor: int, reason: str = 'رفع محدودیت توسط مدیریت') -> dict | None:
        async with self._pool().acquire() as c:
            row = await c.fetchrow("""UPDATE moderation_cases SET active=FALSE,action='unban',lifted_by=$3,lifted_at=NOW(),lift_reason=$4
              WHERE id=(SELECT id FROM moderation_cases WHERE target_type=$1 AND target_id=$2 AND active=TRUE ORDER BY created_at DESC LIMIT 1) RETURNING *""", target_type,target_id,actor,reason)
            if row:
                await c.execute('INSERT INTO audit_logs(actor_id,action,target_type,target_id,details) VALUES($1,$2,$3,$4,$5)', actor, 'unban_'+target_type, target_type, target_id, {'case_code': row['case_code']})
        return dict(row) if row else None

    async def find_case(self, code: int) -> dict | None:
        async with self._pool().acquire() as c:
            row = await c.fetchrow('SELECT * FROM moderation_cases WHERE case_code=$1', code)
        return dict(row) if row else None

    async def is_restricted(self, target_type: str, target_id: int) -> dict | None:
        async with self._pool().acquire() as c:
            row = await c.fetchrow("SELECT * FROM moderation_cases WHERE target_type=$1 AND target_id=$2 AND active=TRUE ORDER BY created_at DESC LIMIT 1", target_type,target_id)
            if row and row['expires_at'] and row['expires_at'] <= datetime.now(timezone.utc):
                await c.execute('UPDATE moderation_cases SET active=FALSE,action=\'expired\' WHERE id=$1', row['id'])
                return None
        return dict(row) if row else None

    async def lift_restriction(self, target_type: str, target_id: int, actor: int, reason: str = '') -> bool:
        """Deactivate active restriction for user/group. Returns True if a row was updated."""
        async with self._pool().acquire() as c:
            result = await c.execute(
                """UPDATE moderation_cases SET active=FALSE, action='unban', lifted_by=$3, lifted_at=NOW(), lift_reason=$4
                   WHERE target_type=$1 AND target_id=$2 AND active=TRUE""",
                target_type, target_id, actor, reason or 'lifted',
            )
        await self.log(actor, 'unban_' + target_type, target_type=target_type, target_id=target_id, details={'reason': reason})
        return result.endswith('1') or (result.split()[-1].isdigit() and int(result.split()[-1]) > 0)

    async def add_team_member(self, name: str, title: str, description: str, username: str, actor: int) -> None:
        async with self._pool().acquire() as c:
            await c.execute('INSERT INTO team_members(name,title,description,username,sort_order) VALUES($1,$2,$3,$4,COALESCE((SELECT MAX(sort_order)+1 FROM team_members),0))', name,title,description,username)
        await self.log(actor, 'team_member_added', details={'name':name})

    async def get_team(self) -> list[dict]:
        async with self._pool().acquire() as c:
            rows = await c.fetch('SELECT * FROM team_members ORDER BY sort_order,id')
        return [dict(r) for r in rows]

    async def delete_team_member(self, member_id: int, actor: int) -> bool:
        async with self._pool().acquire() as c:
            result = await c.execute('DELETE FROM team_members WHERE id=$1', member_id)
        await self.log(actor, 'team_member_deleted', target_type='team_member', target_id=member_id)
        return result.endswith('1')

    # ── Learned phrases management ──────────────────────────────────────

    async def list_learned(self, scope_type: str, scope_id: int, limit: int = 10, offset: int = 0, search: str | None = None) -> list[dict]:
        async with self._pool().acquire() as c:
            if search:
                q = f'%{search.strip()}%'
                rows = await c.fetch(
                    """SELECT trigger_raw, response, updated_at FROM learned_phrases
                       WHERE scope_type=$1 AND scope_id=$2 AND (trigger_raw ILIKE $3 OR response ILIKE $3)
                       ORDER BY updated_at DESC LIMIT $4 OFFSET $5""",
                    scope_type, int(scope_id), q, limit, offset,
                )
            else:
                rows = await c.fetch(
                    """SELECT trigger_raw, response, updated_at FROM learned_phrases
                       WHERE scope_type=$1 AND scope_id=$2
                       ORDER BY updated_at DESC LIMIT $3 OFFSET $4""",
                    scope_type, int(scope_id), limit, offset,
                )
        return [dict(r) for r in rows]

    async def count_learned(self, scope_type: str, scope_id: int, search: str | None = None) -> int:
        async with self._pool().acquire() as c:
            if search:
                q = f'%{search.strip()}%'
                return int(await c.fetchval(
                    """SELECT COUNT(*) FROM learned_phrases
                       WHERE scope_type=$1 AND scope_id=$2 AND (trigger_raw ILIKE $3 OR response ILIKE $3)""",
                    scope_type, int(scope_id), q,
                ) or 0)
            return int(await c.fetchval(
                'SELECT COUNT(*) FROM learned_phrases WHERE scope_type=$1 AND scope_id=$2',
                scope_type, int(scope_id),
            ) or 0)

    # ── Group settings / welcome / security ─────────────────────────────

    DEFAULT_SECURITY = {
        'antispam': False, 'antilink': False, 'antiad': False, 'antirepeat': False,
        'antiflood': False, 'antiprofane': False, 'antiphoto': False, 'antivideo': False,
        'antivoice': False, 'antiaudio': False, 'antigif': False, 'antisticker': False,
        'antidoc': False, 'anticontact': False,
        'antiforward': False, 'antimention': False, 'antipoll': False, 'antilong': False,
        'joinrestrict': False,
    }

    DEFAULT_MODERATION = {
        'warn_limit': 5,
        'warn_ttl_hours': 0,          # 0 = permanent until cleared
        'punish_type': 'mute',        # mute | kick | ban | warn_only
        'punish_duration_sec': 3600,  # for mute
        'reset_warns_on_punish': True,
        'auto_punish': True,
        'flood_count': 6,
        'flood_window_sec': 8,
        'long_msg_chars': 800,
        'mention_limit': 5,
        # Timed join restriction
        'join_restrict_minutes': 30,       # window length after period starts
        'join_restrict_free_slots': 1,     # how many joins start/fill free slots before kicks
        'join_restrict_action': 'kick',    # kick | warn
        'join_window_start': 0,            # unix ts of active window (0 = none)
        'join_window_count': 0,            # joins counted in current window
    }

    async def get_group_settings(self, chat_id: int) -> dict:
        async with self._pool().acquire() as c:
            row = await c.fetchrow('SELECT * FROM group_settings WHERE chat_id=$1', int(chat_id))
        if not row:
            mod = dict(self.DEFAULT_MODERATION)
            return {
                'chat_id': int(chat_id),
                'welcome_enabled': True,
                'welcome_text': None,
                'welcome_varied': True,
                'welcome_mention': True,
                'goodbye_enabled': True,
                'goodbye_text': None,
                'goodbye_varied': True,
                'security': dict(self.DEFAULT_SECURITY),
                'moderation': mod,
                'warn_limit': int(mod['warn_limit']),
            }
        d = dict(row)
        sec = d.get('security') or {}
        if not isinstance(sec, dict):
            sec = {}
        merged = dict(self.DEFAULT_SECURITY)
        merged.update(sec)
        d['security'] = merged
        mod = d.get('moderation') or {}
        if not isinstance(mod, dict):
            mod = {}
        mmerged = dict(self.DEFAULT_MODERATION)
        mmerged.update(mod)
        if d.get('warn_limit') is not None:
            mmerged['warn_limit'] = int(d['warn_limit'])
        d['moderation'] = mmerged
        d['warn_limit'] = int(mmerged['warn_limit'])
        d['welcome_enabled'] = bool(d.get('welcome_enabled', True))
        d['welcome_varied'] = bool(d.get('welcome_varied', True) if d.get('welcome_varied') is not None else True)
        d['welcome_mention'] = bool(d.get('welcome_mention', True) if d.get('welcome_mention') is not None else True)
        d['goodbye_enabled'] = bool(d.get('goodbye_enabled', True) if d.get('goodbye_enabled') is not None else True)
        d['goodbye_varied'] = bool(d.get('goodbye_varied', True) if d.get('goodbye_varied') is not None else True)
        return d

    async def set_group_setting_fields(self, chat_id: int, **fields) -> None:
        gs = await self.get_group_settings(chat_id)
        welcome_enabled = fields.get('welcome_enabled', gs['welcome_enabled'])
        welcome_text = fields.get('welcome_text', gs.get('welcome_text'))
        welcome_varied = fields.get('welcome_varied', gs.get('welcome_varied', True))
        welcome_mention = fields.get('welcome_mention', gs.get('welcome_mention', True))
        goodbye_enabled = fields.get('goodbye_enabled', gs.get('goodbye_enabled', True))
        goodbye_text = fields.get('goodbye_text', gs.get('goodbye_text'))
        goodbye_varied = fields.get('goodbye_varied', gs.get('goodbye_varied', True))
        security = fields.get('security', gs.get('security') or self.DEFAULT_SECURITY)
        moderation = fields.get('moderation', gs.get('moderation') or self.DEFAULT_MODERATION)
        if not isinstance(moderation, dict):
            moderation = dict(self.DEFAULT_MODERATION)
        warn_limit = int(fields.get('warn_limit', moderation.get('warn_limit', gs.get('warn_limit', 5))))
        moderation = dict(moderation)
        moderation['warn_limit'] = warn_limit
        async with self._pool().acquire() as c:
            await c.execute(
                """INSERT INTO group_settings(
                     chat_id,welcome_enabled,welcome_text,welcome_varied,welcome_mention,
                     goodbye_enabled,goodbye_text,goodbye_varied,
                     security,moderation,warn_limit,updated_at)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW())
                   ON CONFLICT(chat_id) DO UPDATE SET
                     welcome_enabled=EXCLUDED.welcome_enabled,
                     welcome_text=EXCLUDED.welcome_text,
                     welcome_varied=EXCLUDED.welcome_varied,
                     welcome_mention=EXCLUDED.welcome_mention,
                     goodbye_enabled=EXCLUDED.goodbye_enabled,
                     goodbye_text=EXCLUDED.goodbye_text,
                     goodbye_varied=EXCLUDED.goodbye_varied,
                     security=EXCLUDED.security,
                     moderation=EXCLUDED.moderation,
                     warn_limit=EXCLUDED.warn_limit,
                     updated_at=NOW()""",
                int(chat_id),
                bool(welcome_enabled),
                welcome_text,
                bool(welcome_varied),
                bool(welcome_mention),
                bool(goodbye_enabled),
                goodbye_text,
                bool(goodbye_varied),
                security,
                moderation,
                int(warn_limit),
            )

    async def toggle_security(self, chat_id: int, key: str) -> bool:
        gs = await self.get_group_settings(chat_id)
        sec = dict(gs.get('security') or self.DEFAULT_SECURITY)
        if key not in self.DEFAULT_SECURITY:
            return False
        sec[key] = not bool(sec.get(key))
        await self.set_group_setting_fields(chat_id, security=sec)
        return bool(sec[key])

    async def update_moderation(self, chat_id: int, **patch) -> dict:
        gs = await self.get_group_settings(chat_id)
        mod = dict(gs.get('moderation') or self.DEFAULT_MODERATION)
        for k, v in patch.items():
            if k in self.DEFAULT_MODERATION:
                mod[k] = v
        warn_limit = int(mod.get('warn_limit', 5))
        await self.set_group_setting_fields(chat_id, moderation=mod, warn_limit=warn_limit)
        return mod

    # ── VIP ─────────────────────────────────────────────────────────────

    async def add_vip(self, chat_id: int, user_id: int, added_by: int) -> bool:
        async with self._pool().acquire() as c:
            try:
                await c.execute(
                    'INSERT INTO group_vips(chat_id,user_id,added_by) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',
                    int(chat_id), int(user_id), int(added_by),
                )
            except Exception:
                return False
        return True

    async def remove_vip(self, chat_id: int, user_id: int) -> bool:
        async with self._pool().acquire() as c:
            result = await c.execute(
                'DELETE FROM group_vips WHERE chat_id=$1 AND user_id=$2',
                int(chat_id), int(user_id),
            )
        return result.endswith('1') or (result.split()[-1].isdigit() and int(result.split()[-1]) > 0)

    async def is_vip(self, chat_id: int, user_id: int) -> bool:
        async with self._pool().acquire() as c:
            return bool(await c.fetchval(
                'SELECT 1 FROM group_vips WHERE chat_id=$1 AND user_id=$2',
                int(chat_id), int(user_id),
            ))

    async def list_vips(self, chat_id: int, limit: int = 20, offset: int = 0) -> list[dict]:
        async with self._pool().acquire() as c:
            rows = await c.fetch(
                """SELECT v.user_id, v.added_by, v.created_at, u.first_name, u.username
                   FROM group_vips v LEFT JOIN users u ON u.user_id=v.user_id
                   WHERE v.chat_id=$1 ORDER BY v.created_at DESC LIMIT $2 OFFSET $3""",
                int(chat_id), limit, offset,
            )
        return [dict(r) for r in rows]

    async def count_vips(self, chat_id: int) -> int:
        async with self._pool().acquire() as c:
            return int(await c.fetchval('SELECT COUNT(*) FROM group_vips WHERE chat_id=$1', int(chat_id)) or 0)

    # ── Group stats ─────────────────────────────────────────────────────

    async def record_group_message(self, chat_id: int, user_id: int | None, msg: dict) -> None:
        """Increment daily counters based on message content type."""
        from datetime import date
        day = date.today()
        col = 'text_count'
        if msg.get('photo'):
            col = 'photo_count'
        elif msg.get('video'):
            col = 'video_count'
        elif msg.get('voice'):
            col = 'voice_count'
        elif msg.get('audio'):
            col = 'audio_count'
        elif msg.get('animation'):
            col = 'animation_count'
        elif msg.get('sticker'):
            col = 'sticker_count'
        elif msg.get('document'):
            col = 'document_count'
        elif not (msg.get('text') or msg.get('caption')):
            return
        async with self._pool().acquire() as c:
            await c.execute(
                f"""INSERT INTO group_stats_daily(chat_id,day,{col}) VALUES($1,$2,1)
                    ON CONFLICT(chat_id,day) DO UPDATE SET {col}=group_stats_daily.{col}+1""",
                int(chat_id), day,
            )
            if user_id:
                await c.execute(
                    """INSERT INTO group_user_msgs(chat_id,user_id,day,msg_count) VALUES($1,$2,$3,1)
                       ON CONFLICT(chat_id,user_id,day) DO UPDATE SET msg_count=group_user_msgs.msg_count+1""",
                    int(chat_id), int(user_id), day,
                )

    async def record_member_join(self, chat_id: int, count: int = 1) -> None:
        from datetime import date
        async with self._pool().acquire() as c:
            await c.execute(
                """INSERT INTO group_stats_daily(chat_id,day,joined) VALUES($1,$2,$3)
                   ON CONFLICT(chat_id,day) DO UPDATE SET joined=group_stats_daily.joined+EXCLUDED.joined""",
                int(chat_id), date.today(), count,
            )

    async def record_member_left(self, chat_id: int, count: int = 1) -> None:
        from datetime import date
        async with self._pool().acquire() as c:
            await c.execute(
                """INSERT INTO group_stats_daily(chat_id,day,left_count) VALUES($1,$2,$3)
                   ON CONFLICT(chat_id,day) DO UPDATE SET left_count=group_stats_daily.left_count+EXCLUDED.left_count""",
                int(chat_id), date.today(), count,
            )

    async def record_bot_delete(self, chat_id: int) -> None:
        from datetime import date
        async with self._pool().acquire() as c:
            await c.execute(
                """INSERT INTO group_stats_daily(chat_id,day,deleted_by_bot) VALUES($1,$2,1)
                   ON CONFLICT(chat_id,day) DO UPDATE SET deleted_by_bot=group_stats_daily.deleted_by_bot+1""",
                int(chat_id), date.today(),
            )

    async def group_stats_today(self, chat_id: int) -> dict:
        from datetime import date
        async with self._pool().acquire() as c:
            row = await c.fetchrow(
                'SELECT * FROM group_stats_daily WHERE chat_id=$1 AND day=$2',
                int(chat_id), date.today(),
            )
            top = await c.fetch(
                """SELECT m.user_id, m.msg_count, u.first_name, u.username
                   FROM group_user_msgs m LEFT JOIN users u ON u.user_id=m.user_id
                   WHERE m.chat_id=$1 AND m.day=$2 ORDER BY m.msg_count DESC LIMIT 3""",
                int(chat_id), date.today(),
            )
        base = {
            'joined': 0, 'left_count': 0, 'text_count': 0, 'photo_count': 0, 'video_count': 0,
            'voice_count': 0, 'audio_count': 0, 'animation_count': 0, 'sticker_count': 0,
            'document_count': 0, 'deleted_by_bot': 0,
        }
        if row:
            base.update({k: int(row[k] or 0) for k in base})
        return {'day': str(date.today()), 'counts': base, 'top_users': [dict(r) for r in top]}

    # ── Warns ───────────────────────────────────────────────────────────

    async def add_warn(self, chat_id: int, user_id: int, reason: str = '', message_id: int | None = None) -> int:
        async with self._pool().acquire() as c:
            async with c.transaction():
                row = await c.fetchrow(
                    """INSERT INTO group_warns(chat_id,user_id,warn_count,updated_at) VALUES($1,$2,1,NOW())
                       ON CONFLICT(chat_id,user_id) DO UPDATE SET warn_count=group_warns.warn_count+1, updated_at=NOW()
                       RETURNING warn_count""",
                    int(chat_id), int(user_id),
                )
                await c.execute(
                    """INSERT INTO warn_logs(chat_id,user_id,reason,message_id) VALUES($1,$2,$3,$4)""",
                    int(chat_id), int(user_id), (reason or '')[:200], message_id,
                )
        return int(row['warn_count']) if row else 1

    async def get_warns(self, chat_id: int, user_id: int) -> int:
        async with self._pool().acquire() as c:
            return int(await c.fetchval(
                'SELECT warn_count FROM group_warns WHERE chat_id=$1 AND user_id=$2',
                int(chat_id), int(user_id),
            ) or 0)

    async def set_warns(self, chat_id: int, user_id: int, count: int) -> int:
        count = max(0, int(count))
        async with self._pool().acquire() as c:
            if count == 0:
                await c.execute('DELETE FROM group_warns WHERE chat_id=$1 AND user_id=$2', int(chat_id), int(user_id))
                return 0
            row = await c.fetchrow(
                """INSERT INTO group_warns(chat_id,user_id,warn_count,updated_at) VALUES($1,$2,$3,NOW())
                   ON CONFLICT(chat_id,user_id) DO UPDATE SET warn_count=$3, updated_at=NOW()
                   RETURNING warn_count""",
                int(chat_id), int(user_id), count,
            )
        return int(row['warn_count']) if row else count

    async def clear_warns(self, chat_id: int, user_id: int) -> None:
        await self.set_warns(chat_id, user_id, 0)

    async def list_warned_users(self, chat_id: int, limit: int = 15, offset: int = 0) -> list[dict]:
        async with self._pool().acquire() as c:
            rows = await c.fetch(
                """SELECT w.user_id, w.warn_count, w.updated_at, u.first_name, u.username
                   FROM group_warns w LEFT JOIN users u ON u.user_id=w.user_id
                   WHERE w.chat_id=$1 AND w.warn_count>0
                   ORDER BY w.warn_count DESC, w.updated_at DESC
                   LIMIT $2 OFFSET $3""",
                int(chat_id), limit, offset,
            )
        return [dict(r) for r in rows]

    async def count_warned_users(self, chat_id: int) -> int:
        async with self._pool().acquire() as c:
            return int(await c.fetchval(
                'SELECT COUNT(*) FROM group_warns WHERE chat_id=$1 AND warn_count>0', int(chat_id),
            ) or 0)

    async def list_warn_logs(self, chat_id: int, user_id: int | None = None, limit: int = 20) -> list[dict]:
        async with self._pool().acquire() as c:
            if user_id is not None:
                rows = await c.fetch(
                    """SELECT id, chat_id, user_id, reason, message_id, created_at
                       FROM warn_logs WHERE chat_id=$1 AND user_id=$2
                       ORDER BY created_at DESC LIMIT $3""",
                    int(chat_id), int(user_id), limit,
                )
            else:
                rows = await c.fetch(
                    """SELECT id, chat_id, user_id, reason, message_id, created_at
                       FROM warn_logs WHERE chat_id=$1
                       ORDER BY created_at DESC LIMIT $2""",
                    int(chat_id), limit,
                )
        return [dict(r) for r in rows]

    async def expire_warns(self, chat_id: int, ttl_hours: int) -> int:
        """Zero warn counters whose updated_at is older than ttl_hours. Returns affected rows."""
        if ttl_hours <= 0:
            return 0
        async with self._pool().acquire() as c:
            result = await c.execute(
                """UPDATE group_warns SET warn_count=0, updated_at=NOW()
                   WHERE chat_id=$1 AND warn_count>0
                     AND updated_at < NOW() - ($2 || ' hours')::interval""",
                int(chat_id), str(int(ttl_hours)),
            )
        try:
            return int(result.split()[-1])
        except Exception:
            return 0

    async def log_punish(
        self, chat_id: int, user_id: int, punish_type: str, duration_sec: int | None,
        warns_at: int, success: bool, detail: str = '',
    ) -> None:
        async with self._pool().acquire() as c:
            await c.execute(
                """INSERT INTO punish_logs(chat_id,user_id,punish_type,duration_sec,warns_at,success,detail)
                   VALUES($1,$2,$3,$4,$5,$6,$7)""",
                int(chat_id), int(user_id), punish_type, duration_sec, int(warns_at), bool(success), (detail or '')[:300],
            )

    async def list_punish_logs(self, chat_id: int, limit: int = 15) -> list[dict]:
        async with self._pool().acquire() as c:
            rows = await c.fetch(
                """SELECT p.*, u.first_name, u.username FROM punish_logs p
                   LEFT JOIN users u ON u.user_id=p.user_id
                   WHERE p.chat_id=$1 ORDER BY p.created_at DESC LIMIT $2""",
                int(chat_id), limit,
            )
        return [dict(r) for r in rows]

    async def count_warn_logs(self, chat_id: int) -> int:
        async with self._pool().acquire() as c:
            return int(await c.fetchval('SELECT COUNT(*) FROM warn_logs WHERE chat_id=$1', int(chat_id)) or 0)

    # ── Recent fun items (anti-repeat) ──────────────────────────────────

    async def push_recent_fun(self, chat_id: int, kind: str, item: str, keep: int = 8) -> None:
        import hashlib
        h = hashlib.sha256(item.encode('utf-8')).hexdigest()[:24]
        async with self._pool().acquire() as c:
            await c.execute(
                """INSERT INTO recent_fun(chat_id,kind,item_hash) VALUES($1,$2,$3)
                   ON CONFLICT DO NOTHING""",
                int(chat_id), kind, h,
            )
            await c.execute(
                """DELETE FROM recent_fun WHERE chat_id=$1 AND kind=$2 AND item_hash NOT IN (
                     SELECT item_hash FROM recent_fun WHERE chat_id=$1 AND kind=$2
                     ORDER BY created_at DESC LIMIT $3
                   )""",
                int(chat_id), kind, keep,
            )

    async def recent_fun_hashes(self, chat_id: int, kind: str) -> list[str]:
        async with self._pool().acquire() as c:
            rows = await c.fetch(
                'SELECT item_hash FROM recent_fun WHERE chat_id=$1 AND kind=$2 ORDER BY created_at DESC LIMIT 12',
                int(chat_id), kind,
            )
        return [r['item_hash'] for r in rows]

    async def get_profanity_words(self, chat_id: int) -> list[str]:
        async with self._pool().acquire() as c:
            rows = await c.fetch('SELECT word FROM group_profanity_words WHERE chat_id=$1', int(chat_id))
        return [r['word'] for r in rows]
