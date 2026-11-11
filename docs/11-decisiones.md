# 11 — Decisiones (ADR) y preguntas abiertas

## Decisiones tomadas

### ADR-001 · Userbot + bot separados
**Contexto:** hay que leer grupos de los que no eres admin y escribir a reclutadores que no conocen al bot.
**Decisión:** la cuenta personal (userbot) se usa para leer y enviar DMs; un bot de @BotFather es la interfaz de control.
**Consecuencias:** hace falta el login con teléfono una vez y proteger el `.session`; existe riesgo de spam en la cuenta (ver [08](08-seguridad-y-riesgos.md)).

### ADR-002 · Telethon para ambos clientes
**Alternativas:** Pyrogram (sin mantenimiento activo), python-telegram-bot o aiogram para el bot + Telethon para el userbot.
**Decisión:** Telethon para los dos: una sola librería, un solo loop, botones inline soportados.
**Revisable** si la UX del bot necesita un framework de conversaciones más rico (aiogram FSM).

### ADR-003 · Gemini detrás de `LLMProvider`
**Decisión del usuario:** Gemini "por ahora". Se abstrae para poder cambiarlo sin tocar la lógica de negocio.
**Detalle:** SDK `google-genai`, salida JSON con esquema Pydantic y modelos configurables por tarea.

### ADR-004 · Canal decidido por la oferta, con override
**Decisión del usuario:** email o DM según lo que diga la oferta. El sistema decide de forma determinista a partir de la extracción validada; el botón 📨 permite cambiarlo.

### ADR-005 · CV en PDF, siempre adjunto
El texto se extrae para el análisis y el PDF original se adjunta en email y en DM.

### ADR-006 · Tiempo real + resumen diario
Umbrales de 80 (inmediato) y 60 (resumen), configurables.

### ADR-007 · SQLite local
Suficiente para un único usuario; cero infraestructura.

### ADR-008 · Doble confirmación antes de enviar
✅ Enviar → vista final → Confirmar. Configurable (`require_final_confirmation`), pero activado por defecto.

### ADR-009 · Contador visible de usuarios nuevos contactados
**Decisión del usuario:** para reducir el riesgo de baneo, el chat debe mostrar cuántos usuarios nuevos se han contactado en el día.
**Detalle:** "nuevo" = sin chat privado previo. Se muestra en un mensaje fijado, en las tarjetas, en la confirmación, tras enviar y con `/contador`. El límite diario (`max_new_users_per_day`) bloquea los DMs a usuarios nuevos; el contador se calcula siempre desde la tabla `contacts`.

### ADR-010 · La IA no puede ser reconfigurada por el contenido analizado
**Decisión del usuario:** los mensajes analizados no deben cambiar el comportamiento de la IA ni modificar su prompt o configuración base.
**Detalle:** defensa en capas (ver [08 §4](08-seguridad-y-riesgos.md#4-aislamiento-de-la-ia-prompt-injection)): system_instruction fija y separada, prompts inmutables con hash, delimitadores con nonce, saneado, llamadas sin estado, sin herramientas, esquemas estrictos, configuración congelada y de solo lectura para el pipeline, destinatario y reglas decididos por código, validación del borrador y aprobación humana. La detección de intentos solo informa; la protección no depende de ella.

### ADR-012 · Modelos de Gemini
`gemini-2.5-flash` ya no está disponible. Se usa `gemini-3.5-flash` (principal) y `gemini-3.5-flash-lite` (respaldo ante saturación 429/5xx), verificados con la API el 2026-11-02; `gemini-3.8-flash` daba 503 de forma persistente ese día. Configurable en `llm.models`. La salida estructurada usa `response_json_schema` para conservar `additionalProperties: false`.

### ADR-013 · Análisis en 3 llamadas con decisiones en código
**Decisión:** extracción (incluye «¿es oferta?») → reglas duras → puntuación → canal → borrador. Las reglas se aplican antes de puntuar para ahorrar llamadas; el borrador solo se redacta si la oferta se notifica (o al abrirla desde el resumen).
**Detalle:** un dato desconocido nunca descarta. Los contactos solo cuentan si aparecen literalmente en el mensaje **y** fuera de las líneas con intentos de manipulación. La deduplicación es por mensaje (fase 1), así que no hay `fingerprint` de oferta en v1.

### ADR-014 · Simulación que se puede comprobar
**Decisión:** en `dry_run` el envío se hace de verdad, pero a ti (Mensajes guardados / tu email), con una cabecera de simulación. Así ves exactamente lo que recibiría el reclutador, incluido el adjunto. No cuenta en el contador ni bloquea el envío real posterior.

### ADR-015 · Cuota diaria del plan gratuito de Gemini
**Contexto:** el 2026-11-02 el plan gratuito limitó `gemini-3.5-flash` a 20 peticiones al día (cada oferta usa 2-3).
**Decisión:** ante un 429 de cuota **diaria** no se reintenta: el modelo se marca como agotado hasta el `retryDelay` y se usa el respaldo (`flash-lite`). Si se agotan todos, la cola de análisis espera (reintento cada 15 min) sin gastar los intentos del mensaje. `/estado` lo muestra.
**Consecuencia:** con mucho volumen de ofertas conviene activar facturación en Google AI Studio o subir el umbral para redactar menos borradores.

## Preguntas resueltas (2026-11-02)

| # | Pregunta | Respuesta |
|---|----------|-----------|
| P2 | Idiomas de las ofertas | **Solo español.** Un único CV. Las ofertas en otros idiomas se descartan en el prefiltro o la extracción (`offer_language != "es"`). |
| P3a | Modalidad | **Solo remoto.** Híbrido y presencial son deal-breakers. |
| P3b | Seniority | **Junior, Mid/Semi-senior y Senior** (Lead queda fuera). |
| P3c | Tipo de contrato | **Tiempo completo, medio tiempo y freelance/por proyecto.** |
| P4 | Canal por defecto si la oferta admite email y DM | **DM de Telegram.** Si el límite de usuarios nuevos está agotado, se propone el email automáticamente (si existe). |
| P5 | Firma | **Email, teléfono/WhatsApp y GitHub/portfolio** (sin LinkedIn). |
| P6 | Dónde corre | **VPS 24/7** (Docker). Ver [07](07-configuracion.md#despliegue-en-vps). |
| P7 | Plan de Gemini | **Gratuito, sin anonimizar.** El usuario acepta que Google pueda usar los datos enviados. |
| P8 | Email | **Gmail** (SMTP + contraseña de aplicación). |
| P9 | Ofertas en imagen | **No en v1** (fase 4). |
| P10 | Límite de usuarios nuevos/día | **10.** |
| P11a | Tono | **Profesional-cercano.** |
| P1 | Grupos a monitorizar | **Se gestionan exclusivamente desde el bot** (`/grupos`), no desde archivos de configuración. |
| P3d | Salario mínimo | **Sin filtro de salario por ahora.** Se puede activar y cambiar desde el bot (`/salario`). |
| P3e | Tecnologías | **Las del CV.** No hay listas de tecnologías deseadas ni vetadas en la configuración. |
| P3f | Zona del remoto | **Cualquier zona.** |

## Preguntas abiertas

| # | Pregunta | Valor por defecto mientras tanto |
|---|----------|----------------------------------|
| P11b | Mensaje o plantilla de ejemplo que ya uses, para tomarlo como estilo | Estilo genérico profesional-cercano |

### ADR-011 · Preferencias de búsqueda gestionadas desde el bot
**Decisión del usuario:** los grupos, el salario mínimo y el umbral de puntuación (`/umbral`) se configuran desde el chat con el bot.
**Detalle:** se guardan en la BD (`source_chats` y `settings`). `config.yaml` solo contiene comportamiento técnico y valores por defecto; lo que se cambia desde el bot tiene prioridad. Solo los handlers del propietario escriben esos valores (coherente con ADR-010).
