# 10 — Roadmap

## Fase 0 — Esqueleto ✅ (código listo, pendiente de prueba con credenciales reales)
- `pyproject.toml` con `uv`, estructura de carpetas, `config.py` (inmutable), logging, `.env.example`, `config.example.yaml`, `.gitignore`, Docker.
- Comando `login` del userbot (muestra tu `OWNER_USER_ID`) y `run` con el bot respondiendo solo al propietario.
- SQLite con migraciones (`source_chats`, `settings`).
- Gestión de grupos desde el bot: `/grupos` (pausar, reanudar, quitar, añadir desde tus chats), `/grupo_add`, reenviar un mensaje de un canal.
- `/salario`, `/estado`, `/start`, `/ayuda`; menú de comandos publicado en Telegram.
- Listener que registra en el log los mensajes nuevos de los chats monitorizados.

**Hecho cuando:** el bot responde a `/start`, añades un grupo con `/grupo_add` y los mensajes nuevos de ese grupo aparecen en `data/logs/jobhunter.log`.

## Fase 1 — Ingesta + perfil ✅ (pendiente de prueba con tu CV real)
- Tablas `raw_messages` y `profiles` (migración `002`).
- Escuchar los grupos configurados y guardar los mensajes; catch-up al arrancar.
- Prefiltro (longitud, palabras clave, palabras excluidas, idioma) y dedupe por hash + casi-duplicados.
- Enlaces ocultos de los mensajes y cursor por chat para el catch-up.
- `/cv`: subir el PDF → texto + enlaces → `CandidateProfile` con Gemini → confirmar, corregir, regenerar o descartar; `/perfil`, `/cancelar`.
- `LLMProvider` + `GeminiProvider` (adelantado de la fase 2): JSON con esquema, reintentos, modelo de respaldo, límite de RPM y prompts inmutables.
- `/estado` con mensajes del día y uso de la IA; `/start` con el estado del CV.

**Hecho cuando:** los mensajes de 2–3 grupos reales se guardan, los duplicados se marcan y el perfil del CV se ve correctamente.

## Fase 2 — Análisis y notificación ✅ (pendiente de validar con ofertas reales)
- Migración `003`: `offers`, `matches`, `drafts`, `bot_messages`; `raw_messages.attempts/error`.
- `llm/guard.py`: saneado (sin `<`/`>` ni invisibles), nonce por llamada, detector de inyección, contactos fuera de las líneas sospechosas y validación del borrador. Fixtures en `tests/fixtures/injection/`.
- `analysis/`: extracción (1 llamada), reglas duras antes de puntuar, puntuación (2.ª llamada), selector de canal determinista con validación literal de contactos y redactor (3.ª llamada, solo si la puntuación lo merece) con firma añadida por código.
- Worker en segundo plano: cola de mensajes `new`, reintentos si Gemini está saturado (3 intentos), espera al CV si no hay perfil activo.
- Tarjetas con ✏️ (texto o `!instrucción`) 🔄 📨 ⏰ 🗑 y ✔️ Ya apliqué (canal manual). ✅ Enviar avisa de que el envío llega en la fase 3.
- `/ofertas`, `/descartadas`, `/escanear [días]`, `/resumen` y resumen diario a `digest.time`.
- `/estado` con la cola de análisis y las ofertas del día.

**Pendiente de la fase 2 (pasa a la 4):** `scripts/eval_llm.py` con 30–50 ofertas reales etiquetadas, `/reevaluar` y `/pausa`.

**Hecho cuando:** con `/escanear 3` recibes tarjetas coherentes, con canal y borrador razonables.

## Fase 3 — Envío ✅ (probado con tests; pendiente de un envío real de prueba)
- Migración `004`: `contacts`, `applications`, `offers.dismiss_reason`.
- ✅ Enviar → comprobaciones (destinatario, CV, duplicado, límites) → **confirmación final** con destinatario, «usuario nuevo nº N de 10», asunto, cuerpo y adjunto → cola.
- `Dispatcher`: DM + CV por Telethon (`FloodWait`, `PeerFlood`, privacidad, bloqueos), email + CV por SMTP (Gmail), espaciado aleatorio entre DMs, segunda comprobación del límite al enviar, nunca dos envíos reales de la misma oferta, recuperación tras reinicio.
- **Contador de usuarios nuevos**: mensaje fijado (se edita tras cada envío y a medianoche), línea del canal en la tarjeta, confirmación, resultado y `/contador`. Bloqueo al llegar al límite con alternativas (📧 email, ⏰ mañana).
- **Simulación** (`dry_run`, activa por defecto): el DM llega a tus «Mensajes guardados» y el email a tu propia dirección; el contador no cambia y la oferta se puede enviar de verdad después.
- `/aplicadas`, `/stats`; la tarjeta se edita en el sitio con el resultado.

## Fase 4 — Mejoras
Hechas:
- ✅ Aviso cuando un reclutador responde a tu DM (una vez por aplicación; el bot nunca responde por ti).
- ✅ Motivo al descartar (💰 🧰 🏢 📍) y sugerencias en `/stats` («descartas mucho por salario → /salario»).
- ✅ Recordatorio de seguimiento a los `sending.follow_up_days` (7) días sin respuesta.
- ✅ `/reevaluar [días]`, `/pausa` / `/reanudar`, `/umbral`.
- ✅ `scripts/eval_llm.py` + `tests/fixtures/offers/` (9 casos etiquetados: 100 % el 2026-11-02).
- ✅ Gestión de la cuota diaria de Gemini: el modelo agotado se salta hasta que se recupera y, si no queda ninguno, la cola espera sin perder mensajes.

No implementadas (requieren una decisión o un recurso que no hay):
- CV adaptado por oferta (generar un PDF distinto por oferta: riesgo de calidad y de inventar datos).
- Ofertas en imagen (cada imagen de los grupos gastaría cuota de IA, que en el plan gratuito es muy limitada).
- Segundo proveedor de IA (hace falta una API key de Claude u OpenAI; la interfaz `LLMProvider` ya lo permite).
- Panel web (expondría tu historial en el VPS; habría que añadir autenticación).
- Detectar respuestas por email (necesita acceso IMAP a tu Gmail).

## Prioridad de tests
1. `contact_validator` y `channel_selector` (deciden a quién se escribe).
2. `test_injection` (las ofertas no modifican prompts, configuración, destinatario ni puntuación).
3. `rules` (deal-breakers).
4. `dispatcher` y `contact_counter` (límite de usuarios nuevos, idempotencia: nunca enviar dos veces).
5. `prefilter` y `dedupe`.
6. Pipeline completo con `FakeLLMProvider`.
