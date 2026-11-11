-- Fase 1: mensajes recibidos de los grupos y perfiles del candidato.

-- Sin FK a source_chats: quitar un grupo no debe borrar su historial.
CREATE TABLE raw_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL,
    message_id      INTEGER NOT NULL,
    sender_id       INTEGER,
    sender_username TEXT,
    sender_name     TEXT,
    sender_is_user  INTEGER NOT NULL DEFAULT 0,      -- 0 si es canal, admin anónimo o bot
    text            TEXT    NOT NULL DEFAULT '',
    hidden_urls     TEXT    NOT NULL DEFAULT '[]',   -- JSON: enlaces ocultos tras texto
    link            TEXT,
    text_norm       TEXT    NOT NULL DEFAULT '',
    text_hash       TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'new' CHECK (status IN (
                        'new', 'filtered', 'duplicate', 'processing',
                        'not_job', 'processed', 'error')),
    filter_reason   TEXT,
    duplicate_of    INTEGER REFERENCES raw_messages (id) ON DELETE SET NULL,
    posted_at       TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (chat_id, message_id)
);
CREATE INDEX idx_raw_status ON raw_messages (status);
CREATE INDEX idx_raw_hash ON raw_messages (text_hash);
CREATE INDEX idx_raw_posted ON raw_messages (posted_at);

CREATE TABLE profiles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cv_path      TEXT NOT NULL,
    cv_text      TEXT NOT NULL,
    cv_sha256    TEXT NOT NULL,
    profile      TEXT NOT NULL,                       -- JSON CandidateProfile
    status       TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                     'draft', 'active', 'archived', 'discarded')),
    model        TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    confirmed_at TEXT
);
-- Como máximo un perfil activo.
CREATE UNIQUE INDEX one_active_profile ON profiles (status) WHERE status = 'active';
