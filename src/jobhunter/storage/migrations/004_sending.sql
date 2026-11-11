-- Fase 3: envíos, contactos (contador de usuarios nuevos). Fase 4: motivos de descarte.

-- Usuarios de Telegram a los que se ha escrito por DM. Base del contador diario:
-- cuenta quien NO tenía chat previo y recibió su primer DM hoy (first_contacted_at).
CREATE TABLE contacts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_user_id         INTEGER NOT NULL UNIQUE,
    username           TEXT,
    display_name       TEXT,
    had_prior_chat     INTEGER NOT NULL DEFAULT 0,
    first_contacted_at TEXT,
    last_contacted_at  TEXT,
    dm_count           INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX idx_contacts_first ON contacts (first_contacted_at);

CREATE TABLE applications (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id         INTEGER NOT NULL REFERENCES offers (id),
    draft_id         INTEGER NOT NULL REFERENCES drafts (id),
    channel          TEXT NOT NULL CHECK (channel IN ('email', 'telegram_dm')),
    target           TEXT NOT NULL,
    attachment_path  TEXT,
    status           TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
                         'queued', 'sending', 'sent', 'failed', 'cancelled')),
    dry_run          INTEGER NOT NULL DEFAULT 0,
    error            TEXT,
    external_id      TEXT,                 -- id del mensaje de Telegram o Message-ID del email
    contact_id       INTEGER REFERENCES contacts (id),
    was_new_contact  INTEGER,              -- 1 si este envío fue el primer contacto con ese usuario
    bot_message_id   INTEGER,              -- mensaje de confirmación en tu chat (se edita con el resultado)
    approved_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    sent_at          TEXT,
    replied_at       TEXT,                 -- fase 4: el reclutador respondió por DM
    follow_up_done   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_app_status ON applications (status);
CREATE INDEX idx_app_offer ON applications (offer_id);

ALTER TABLE offers ADD COLUMN dismiss_reason TEXT;
