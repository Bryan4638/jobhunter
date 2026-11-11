# JobHunter Bot — Documentación

Bot personal de Telegram que **lee los grupos de empleo** donde estás unido con tu cuenta, **evalúa cada oferta contra tu CV** usando IA (Gemini), te **reporta las mejores** por un chat privado con el bot y te propone un **mensaje de aplicación** que, **solo si lo apruebas**, se envía al reclutador por **DM de Telegram** o por **email** (con tu CV en PDF adjunto), según lo que pida la oferta.

## Índice

| # | Documento | Contenido |
|---|-----------|-----------|
| 01 | [Visión y alcance](01-vision-y-alcance.md) | Objetivo, requisitos funcionales y no funcionales, fuera de alcance |
| 02 | [Arquitectura](02-arquitectura.md) | Componentes, stack tecnológico, diagrama general |
| 03 | [Flujos](03-flujos.md) | Diagramas de secuencia: ingesta, evaluación, aprobación, envío, resumen |
| 04 | [Modelo de datos](04-modelo-de-datos.md) | Esquema SQLite, estados de una oferta |
| 05 | [IA y prompts](05-ia-y-prompts.md) | Capa de proveedor LLM, prompts, esquemas JSON, control de costes |
| 06 | [Interfaz del bot](06-interfaz-del-bot.md) | Comandos, mensajes, botones inline, ejemplos |
| 07 | [Configuración](07-configuracion.md) | Credenciales, `.env`, `config.yaml`, puesta en marcha |
| 08 | [Seguridad y riesgos](08-seguridad-y-riesgos.md) | ToS de Telegram, anti-baneo, prompt injection, privacidad |
| 09 | [Estructura del proyecto](09-estructura-del-proyecto.md) | Árbol de carpetas y responsabilidades de cada módulo |
| 10 | [Roadmap](10-roadmap.md) | Fases de implementación y criterios de aceptación |
| 11 | [Decisiones y preguntas abiertas](11-decisiones.md) | ADRs y puntos pendientes de definir |

## Resumen en 30 segundos

```
 Grupos de empleo ──► Userbot (tu cuenta) ──► Filtro ──► Gemini: extraer + puntuar vs CV
                                                                      │
                     ┌────────────────────────────────────────────────┘
                     ▼
        Bot de Telegram (chat privado contigo)
        "🔥 92/100 — Backend Python Sr. @ Acme"   [✅ Enviar] [✏️ Editar] [🔄 Regenerar] [🗑 Descartar]
                     │ (apruebas)
                     ▼
        Envío ──► DM por Telegram (tu cuenta) ─ o ─ Email SMTP   (+ CV.pdf adjunto)
```

## Decisiones ya tomadas

- **IA:** Google Gemini (plan gratuito) por ahora, detrás de una interfaz `LLMProvider` para poder cambiarlo más adelante.
- **Canal de envío:** lo decide el sistema según lo que diga la oferta (email o DM). Si admite ambos sin preferencia, se usa DM; si se agotó el límite de usuarios nuevos, se pasa a email.
- **Perfil de búsqueda:** solo ofertas en español, solo remoto, de junior a senior, cualquier tipo de contrato.
- **Despliegue:** VPS 24/7 con Docker; email por Gmail.
- **CV:** PDF. Se extrae su texto para el análisis y se adjunta al enviar.
- **Reportes:** en tiempo real para las ofertas con puntuación alta, más un resumen diario con el resto.
- **Aprobación humana obligatoria:** nunca se envía nada sin tu ✅ explícito.
- **Contador de usuarios nuevos:** el chat muestra en todo momento a cuántos usuarios nuevos (sin chat previo) se ha escrito hoy, con un límite diario que bloquea los DMs al alcanzarse.
- **IA aislada del contenido:** los mensajes analizados nunca cambian el comportamiento de la IA, su prompt ni la configuración base; solo se tratan como datos.
