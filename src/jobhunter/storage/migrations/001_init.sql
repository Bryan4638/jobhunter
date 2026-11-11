-- Fase 0: grupos monitorizados y preferencias editables desde el bot.

CREATE TABLE source_chats (
    id              INTEGER PRIMARY KEY,          -- peer id de Telegram (-100... para canales/supergrupos)
    title           TEXT    NOT NULL,
    username        TEXT,
    kind            TEXT    NOT NULL CHECK (kind IN ('group', 'channel')),
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_message_id INTEGER,
    last_message_at TEXT,
    added_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Clave-valor (JSON). Solo lo escriben los handlers de comandos del propietario.
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
