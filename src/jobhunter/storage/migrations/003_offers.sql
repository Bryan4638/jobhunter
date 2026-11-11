-- Fase 2: análisis de ofertas, puntuación, borradores y tarjetas del bot.

-- Reintentos del análisis (Gemini saturado) antes de marcar el mensaje como error.
ALTER TABLE raw_messages ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE raw_messages ADD COLUMN error TEXT;

CREATE TABLE offers (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_message_id           INTEGER NOT NULL UNIQUE REFERENCES raw_messages (id),
    title                    TEXT,
    company                  TEXT,
    seniority                TEXT,
    stack                    TEXT NOT NULL DEFAULT '[]',   -- JSON
    modality                 TEXT,
    location                 TEXT,
    salary_min               REAL,
    salary_max               REAL,
    salary_currency          TEXT,
    salary_period            TEXT,
    contract_type            TEXT,
    language                 TEXT,
    contacts                 TEXT NOT NULL DEFAULT '{}',   -- JSON: contactos validados
    application_instructions TEXT,
    summary                  TEXT,
    injection_suspected      INTEGER NOT NULL DEFAULT 0,
    injection_signals        TEXT NOT NULL DEFAULT '[]',   -- JSON
    rejections               TEXT NOT NULL DEFAULT '[]',   -- JSON: motivos de descarte por reglas
    extraction               TEXT NOT NULL,                -- JSON completo de la IA (auditoría)
    model                    TEXT,
    status                   TEXT NOT NULL CHECK (status IN (
                                 'analyzed', 'rejected_rules', 'low_score', 'pending_digest',
                                 'notified', 'snoozed', 'dismissed', 'manual_pending',
                                 'approved', 'applied', 'send_failed')),
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX idx_offers_status ON offers (status);

CREATE TABLE matches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id         INTEGER NOT NULL REFERENCES offers (id) ON DELETE CASCADE,
    profile_id       INTEGER NOT NULL REFERENCES profiles (id),
    score            INTEGER NOT NULL,
    llm_score        INTEGER NOT NULL,
    verdict          TEXT,
    rule_adjustments TEXT NOT NULL DEFAULT '[]',   -- JSON [{rule, delta, label}]
    reasons          TEXT NOT NULL DEFAULT '[]',
    gaps             TEXT NOT NULL DEFAULT '[]',
    red_flags        TEXT NOT NULL DEFAULT '[]',
    pitch_points     TEXT NOT NULL DEFAULT '[]',
    channel          TEXT NOT NULL CHECK (channel IN ('email', 'telegram_dm', 'manual')),
    channel_target   TEXT,
    channel_reason   TEXT,
    channel_options  TEXT NOT NULL DEFAULT '[]',   -- JSON [{kind, target, label}]
    model            TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (offer_id, profile_id)
);

CREATE TABLE drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id    INTEGER NOT NULL REFERENCES offers (id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    channel     TEXT NOT NULL CHECK (channel IN ('email', 'telegram_dm', 'manual')),
    target      TEXT,
    subject     TEXT,
    body        TEXT NOT NULL,
    origin      TEXT NOT NULL CHECK (origin IN ('llm', 'llm_instruction', 'user_edit')),
    instruction TEXT,
    warnings    TEXT NOT NULL DEFAULT '[]',        -- JSON: contactos ajenos detectados, etc.
    model       TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (offer_id, version)
);

-- Mensajes que el bot te envió (tarjetas, resúmenes) ↔ oferta.
CREATE TABLE bot_messages (
    bot_message_id INTEGER PRIMARY KEY,
    offer_id       INTEGER REFERENCES offers (id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('card', 'digest', 'preview', 'confirm')),
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
