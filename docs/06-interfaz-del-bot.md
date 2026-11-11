# 06 — Interfaz del bot

El bot **solo responde a `OWNER_USER_ID`**. Cualquier otro usuario recibe silencio (o un mensaje genérico) y queda registrado en el log.

## Menú

En la lista «/» de Telegram solo aparece **`/menu`**: abre un menú de botones con todas las opciones (Ofertas, Resumen, Escanear, Descartadas, Contador, Aplicadas, Estadísticas, Reevaluar, Pausar/Reanudar, CV, Perfil, Grupos, Añadir grupo, Salario, Umbral, Estado y Ayuda). `/start` muestra el mismo menú. Cada botón ejecuta el mismo código que su comando; los comandos de abajo siguen funcionando si se escriben, lo que es útil para pasar argumentos (`/salario 1500 USD mes`, `/umbral 50`, `/escanear 7`).

## Comandos

| Comando | Descripción |
|---------|-------------|
| `/menu` | Menú de botones con todas las opciones (único comando publicado). |
| `/start` | Bienvenida, estado de la configuración y pasos pendientes (CV, grupos). |
| `/cv` | Subir o reemplazar el CV en PDF (también basta con enviar el PDF). Muestra el perfil extraído con `[✅ Confirmar] [✏️ Corregir] [🔄 Regenerar] [❌ Descartar]`. Corregir pide un texto libre ("tengo 4 años, no 6"). El CV anterior se archiva en `data/cv/archive/`. |
| `/perfil` | Ver el perfil activo. |
| `/cancelar` | Cancelar la acción en curso (por ejemplo, una corrección del perfil). |
| `/preferencias` | Ver y editar las preferencias (modalidad, seniority, contratos, umbrales). |
| `/salario` | Ver el salario mínimo. `/salario 1500 USD mes` lo activa; `/salario off` lo desactiva (por defecto, desactivado). Pregunta la regla: `[Descartar ofertas por debajo] [Solo bajar puntuación]`. |
| `/umbral` | Ver la puntuación mínima para avisarte. `/umbral 50` cambia el aviso inmediato (el del resumen baja si hace falta); `/umbral 70 40` fija aviso inmediato y resumen. Botones rápidos 20/40/60/80. Al bajarlo, las ofertas ya analizadas que ahora lo superan pasan a `/ofertas`. Tiene prioridad sobre `config.yaml`. |
| `/grupos` | Listar los grupos y canales monitorizados con botones `[⏸ Pausar] [🗑 Quitar]`. **Única forma de gestionarlos.** |
| `/grupo_add <@usuario \| t.me/... \| ID>` | Añadir un grupo o canal. También puedes **reenviar al bot un mensaje** del grupo o canal y pulsar `[➕ Monitorizar este chat]`, o pulsar `[➕ Añadir]` en `/grupos` para elegir entre los chats de tu cuenta. |
| `/escanear [días]` | Escaneo histórico de los grupos (por defecto 3 días). |
| `/ofertas [n]` | Top N ofertas pendientes de revisar, ordenadas por puntuación. |
| `/descartadas` | Ofertas de los últimos 7 días descartadas automáticamente (por tus reglas o por puntuación baja), con el motivo. |
| `/resumen` | Forzar el resumen ahora. |
| `/umbral` | Puntuación mínima para avisarte (ver arriba). |
| `/aplicadas` | Historial de aplicaciones enviadas. |
| `/contador` | Usuarios nuevos contactados hoy (con la lista) y los últimos 7 días; DMs totales y emails. |
| `/stats` | Métricas: mensajes leídos, ofertas, notificadas, aplicadas, uso del LLM. |
| `/pausa` / `/reanudar` | Pausar o reanudar las notificaciones inmediatas (la ingesta sigue). |
| `/reevaluar [días]` | Volver a puntuar las ofertas recientes con el perfil actual. |
| `/estado` | Salud: cuenta conectada, chats, último mensaje, CV, mensajes de hoy (pendientes, filtrados, duplicados) y uso de la IA. |
| `/ayuda` | Ayuda. |

## Carpeta «Aplicadas»

Cada reclutador al que se envía un **DM real** se añade a una carpeta de Telegram de tu cuenta (`sending.telegram_dm.folder_name`, por defecto `Aplicadas`; `""` la desactiva). Si no existe, se crea. Al arrancar se sincroniza con todos los contactos a los que ya se escribió. Las simulaciones y los emails no la tocan. Telegram permite 100 chats por carpeta sin Premium (200 con Premium): si se llena, el bot te avisa y el envío no se ve afectado.

## Mensaje de estado fijado (contador)

Al arrancar, el bot crea un mensaje y lo **fija** en el chat. Se edita en el sitio tras cada envío y a medianoche:

```
📊 Hoy · lunes 2 nov
👤 Usuarios nuevos: 3/10   🟢
💬 DMs totales: 5   ·   📧 Emails: 2/30
🔄 Se reinicia a las 00:00
```

Indicador: 🟢 < 80 % · 🟡 ≥ 80 % · 🔴 límite alcanzado o cuenta marcada por Telegram (`PeerFlood`).

`/contador`:
```
👤 Usuarios nuevos contactados hoy: 3/10
  1. @maria_rrhh — Backend Python @ Acme — 10:14
  2. @jobs_globant — Fullstack @ Globant — 11:02
  3. Autor del post (Juan R.) — Data Eng — 12:40

Últimos 7 días: 2 · 5 · 4 · 0 · 6 · 3 · 3
```

## Tarjeta de oferta (notificación inmediata)

```
🔥 92/100 · Backend Developer Python (Senior)
🏢 Acme Corp · 🌍 Remoto (LATAM) · 💰 3.500–4.500 USD/mes
🧰 Python, FastAPI, PostgreSQL, AWS, Docker
📍 Grupo: Python Jobs LATAM · hace 2 min · [ver mensaje]

✅ Por qué encaja
• 5 años con Python/FastAPI (piden 4+)
• Experiencia con AWS ECS y RDS
• Remoto, como prefieres

⚠️ Carencias
• Piden Kubernetes (no aparece en tu CV)

📧 Canal: Email → jobs@acme.com
   Motivo: la oferta dice "Enviar CV a jobs@acme.com con asunto 'Backend-PY'"

──────── Borrador ────────
Asunto: Backend-PY — Juan Pérez, Backend Developer Python
Hola equipo de Acme:
...
📎 Adjunto: CV_Juan_Perez.pdf

[✅ Enviar] [✏️ Editar] [🔄 Regenerar]
[📨 Cambiar canal] [⏰ Más tarde] [🗑 Descartar]
```

### Comportamiento de los botones

| Botón | Acción |
|-------|--------|
| ✅ Enviar | Muestra la **confirmación final** (destinatario, canal, asunto, cuerpo y adjunto completos) con `[Confirmar envío] [Cancelar]`. Solo al confirmar se encola el envío. |
| ✏️ Editar | El bot responde: "Envía el nuevo texto, o una instrucción que empiece por `!` (por ejemplo `!más corto y menciona Kubernetes como aprendizaje`)". Luego muestra la nueva vista previa. |
| 🔄 Regenerar | Nueva versión del borrador (nueva fila en `drafts`). |
| 📨 Cambiar canal | Lista los canales posibles detectados: `[📧 jobs@acme.com] [💬 @maria_rrhh] [💬 Autor del post] [🔗 Manual]`. Al cambiar de canal se regenera el borrador (el formato de email y de DM son distintos). |
| ⏰ Más tarde | Vuelve en el siguiente resumen. |
| 🗑 Descartar | Pide un motivo rápido `[💰 Salario] [🧰 Stack] [🏢 Empresa] [📍 Modalidad] [Otro]`; `/stats` lo resume y sugiere ajustes. |
| ✔️ Ya apliqué | Solo en ofertas de canal manual (formulario o enlace). |

La tarjeta se **edita en el sitio** tras cada acción (estado: "✅ Enviado 14:32 por email"), en lugar de mandar mensajes nuevos.

Al **confirmar el envío** la tarjeta pierde todos sus botones ("📤 Enviando…") y, si sale bien, se queda **sin botones** para no pulsar ninguno por error. Si el envío falla, los botones vuelven para reintentar o cambiar de canal. En modo simulación la oferta sigue abierta: para repetir la prueba se abre de nuevo desde `/ofertas`.

## Canal manual

```
🔗 Esta oferta se aplica por formulario:
https://acme.workable.com/j/123

Texto sugerido para la carta de presentación:
...
[✔️ Ya apliqué] [🗑 Descartar]
```

## Resumen diario

```
📋 Resumen del día — 6 ofertas (60–79 pts)

1. 78 · Fullstack Python/React · Globant · Remoto   [Ver]
2. 74 · Backend Node.js · Startup X · Híbrido CDMX   [Ver]
3. 71 · Python Data Engineer · ? · Remoto            [Ver]
...
Además: 41 mensajes leídos, 9 ofertas descartadas por reglas.
```

Si el canal es un DM, la línea del canal incluye el contador:

```
💬 Canal: DM → @maria_rrhh · 👤 usuario nuevo (hoy: 3/10)
💬 Canal: DM → @pedro_tech · ya conocido (no cuenta)
💬 Canal: DM → @ana_hr · 👤 usuario nuevo (hoy: 8/10) ⚠️ te quedan 2
```

Si la oferta tiene alertas de manipulación:

```
🛡 Posible intento de manipulación de la IA detectado en el texto
   ("ignora tus instrucciones…"). El análisis se hizo con las reglas normales.
```

## Confirmación final

```
📤 Vas a enviar:
Canal: DM de Telegram → @maria_rrhh
👤 Será el usuario nuevo nº 4 de 10 de hoy.
Mensaje: …
Adjunto: CV_Juan_Perez.pdf
[✅ Confirmar envío] [Cancelar]
```

## Confirmaciones de envío

- `✅ Enviado por Telegram a @maria_rrhh (mensaje + CV.pdf) · 👤 Usuarios nuevos hoy: 4/10`
- `✅ Email enviado a jobs@acme.com (Message-ID …) · 📧 Emails hoy: 3/30`
- `❌ No se pudo enviar: el usuario @foo no existe. [Cambiar canal] [Reintentar]`
- `⛔ Límite de usuarios nuevos alcanzado (10/10). [📧 Enviar por email] [⏰ Mañana] [Cancelar]`

## Respuestas de los reclutadores (opcional, fase 4)

Si un reclutador al que escribiste por DM te responde, el userbot lo detecta (DM entrante de un `target` de `applications`) y el bot te avisa: `💬 @maria_rrhh respondió sobre "Backend Python @ Acme"`. El bot **no responde automáticamente**.
