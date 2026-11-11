# 09 — Estructura del proyecto

```
telegram-bot/
├── docs/                          # esta documentación
├── src/jobhunter/
│   ├── __init__.py
│   ├── __main__.py                # CLI: `login`, `run`, `eval`
│   ├── app.py                     # composición: crea los clientes, servicios y workers; arranca el loop
│   ├── config.py                  # Settings (pydantic-settings): .env + config.yaml
│   ├── logging.py
│   │
│   ├── domain/                    # modelos puros, sin I/O
│   │   ├── models.py              # RawMessage, Offer, Match, Draft, Application, enums de estado
│   │   ├── schemas.py             # JobExtraction, CandidateProfile, MatchResult, ApplicationDraft
│   │   └── preferences.py
│   │
│   ├── telegram/
│   │   ├── userbot.py             # cliente Telethon (tu cuenta): escuchar, escanear, enviar DM
│   │   ├── bot.py                 # cliente Telethon (bot): registro de handlers
│   │   ├── sources.py             # registro de chats, listener, catch-up
│   │   ├── conversation.py        # qué texto libre espera el bot
│   │   ├── handlers/
│   │   │   ├── commands.py        # /start, /estado, /ayuda, no autorizados
│   │   │   ├── menu.py            # /menu: todas las opciones en botones (ctx.menu_actions)
│   │   │   ├── groups.py          # /grupos, /grupo_add, reenvíos
│   │   │   ├── salary.py          # /salario
│   │   │   ├── cv.py              # /cv, /perfil, subida de PDF
│   │   │   ├── offers.py          # tarjetas y botones, /ofertas, /descartadas, /escanear, /resumen, /reevaluar, /pausa
│   │   │   ├── sending.py         # confirmación final, resultado, /contador, /aplicadas, /stats
│   │   │   ├── thresholds.py      # /umbral
│   │   │   └── text.py            # texto libre (corrección del CV, edición del borrador) y /cancelar
│   │   ├── status_board.py        # mensaje fijado con el contador
│   │   ├── replies.py             # aviso cuando un reclutador responde
│   │   ├── cards.py               # render de tarjetas, resumen y vistas previas (texto + botones)
│   │   └── auth.py                # filtro OWNER_USER_ID
│   │
│   ├── context.py                 # AppContext: dependencias compartidas por los handlers
│   ├── ingestion/
│   │   ├── models.py              # IncomingMessage (independiente de Telethon), enlaces
│   │   ├── text.py                # normalización y hash
│   │   ├── ingestor.py            # prefiltro + dedupe + guardar raw_messages
│   │   ├── prefilter.py           # reglas baratas e idioma
│   │   └── dedupe.py              # casi-duplicados (rapidfuzz)
│   │
│   ├── analysis/
│   │   ├── pipeline.py            # OfferAnalyzer (extraer → reglas → puntuar → canal → borrador) + AnalysisWorker
│   │   ├── rules.py               # deal-breakers y ajustes
│   │   ├── channel_selector.py
│   │   ├── contact_validator.py   # validación literal de emails y @usuarios
│   │   └── writer.py              # redacción, revisiones y firma
│   │
│   ├── llm/
│   │   ├── base.py                # LLMProvider (Protocol), errores comunes
│   │   ├── gemini.py              # GeminiProvider
│   │   ├── rate_limit.py
│   │   ├── guard.py               # saneado, nonce de delimitadores, detector de inyección, validación del borrador
│   │   ├── prompt_store.py        # carga los prompts al arrancar (inmutables) + hash
│   │   └── prompts/               # plantillas .md / .txt
│   │       ├── cv_system.md, cv_extract.md, cv_correct.md
│   │       ├── offer_system.md, offer_extract.md, offer_score.md
│   │       └── draft_system.md, draft_write.md, draft_revise.md
│   │
│   ├── profile/
│   │   ├── cv_loader.py           # PDF → texto (pypdf)
│   │   └── profile_service.py     # texto → CandidateProfile, versiones
│   │
│   ├── sending/
│   │   ├── telegram_sender.py     # destinatario, ¿chat previo?, DM + CV, FloodWait/PeerFlood
│   │   ├── email_sender.py        # aiosmtplib (Gmail) con el CV adjunto
│   │   ├── gateways.py            # implementaciones reales para el Dispatcher
│   │   └── dispatcher.py          # comprobaciones, contador de usuarios nuevos, límites, cola, simulación
│   │
│   ├── scheduler.py               # resumen + seguimientos (digest.time) y contador a medianoche
│   │
│   └── storage/
│       ├── db.py                  # conexión aiosqlite, migraciones
│       ├── repositories.py        # SourceChatRepo, SettingsRepo
│       ├── messages.py            # RawMessageRepo (+ cola de análisis)
│       ├── profiles.py            # ProfileRepo
│       ├── offers.py              # OfferRepo: ofertas, matches, borradores, tarjetas
│       ├── sending.py             # ContactRepo, ApplicationRepo
│       └── migrations/            # 001_init … 004_sending
│
├── scripts/
│   └── eval_llm.py                # evaluación de prompts con fixtures
├── tests/
│   ├── fixtures/offers/*.txt      # ofertas reales anonimizadas + etiquetas .json
│   ├── fixtures/injection/*.txt   # ofertas con intentos de manipulación
│   ├── test_injection.py          # config y prompts inmutables, destinatario literal, alerta
│   ├── test_contact_counter.py
│   ├── test_prefilter.py
│   ├── test_channel_selector.py
│   ├── test_contact_validator.py
│   ├── test_rules.py
│   ├── test_pipeline.py           # con FakeLLMProvider
│   └── test_dispatcher.py         # con FakeSender
├── data/                          # (gitignored) BD, sesiones, CV, logs
│   ├── sessions/
│   ├── cv/
│   └── logs/
├── .env.example
├── config.example.yaml
├── .gitignore
├── pyproject.toml
└── README.md
```

## Dependencias entre capas

```
telegram/handlers ─► analysis, sending, profile, notify ─► llm, storage ─► domain
```
- `domain` no importa nada del proyecto.
- `analysis` y `llm` reciben la configuración como objeto **congelado** (`frozen=True`) y repositorios de solo lectura para `settings` y `profiles`; no pueden escribir la configuración (ver [08 §4](08-seguridad-y-riesgos.md#4-aislamiento-de-la-ia-prompt-injection)).
- `llm`, `storage` y `sending` exponen interfaces; `app.py` inyecta las implementaciones concretas.
- Los tests sustituyen `LLMProvider`, `Sender` y los clientes de Telegram por fakes.

## Convenciones

- Todo el código es asíncrono (`async def`); nada bloqueante en el loop (la extracción del PDF se hace con `asyncio.to_thread`).
- Tipado estricto (`mypy --strict` o `pyright`), lint con `ruff`.
- Mensajes del bot en español; identificadores de código en inglés.
- Los prompts viven en archivos separados para iterarlos sin tocar código.
