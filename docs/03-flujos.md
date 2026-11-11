# 03 — Flujos

## 1. Ingesta y análisis de un mensaje nuevo

```mermaid
sequenceDiagram
    autonumber
    participant G as Grupo
    participant UB as Userbot
    participant ING as Ingestor
    participant PF as Prefiltro/Dedupe
    participant P as Pipeline
    participant L as Gemini
    participant DB as SQLite
    participant N as Notificador

    G->>UB: NewMessage
    UB->>ING: evento
    ING->>DB: INSERT raw_messages (si no existe)
    ING->>PF: mensaje normalizado
    alt no pasa el prefiltro o es duplicado
        PF->>DB: status = filtered / duplicate
    else pasa
        PF->>P: encolar
        P->>P: sanear texto + detector de inyección + nonce del delimitador
    P->>L: clasificar + extraer (JSON) · system_instruction fija, sin historial
        L-->>P: JobOffer | not_a_job
        P->>P: reglas duras (deal-breakers)
        P->>L: puntuar vs perfil (JSON)
        L-->>P: score, razones, carencias
        P->>P: seleccionar canal
        opt score >= umbral_resumen
            P->>L: redactar mensaje
            L-->>P: asunto + cuerpo
        end
        P->>DB: guardar offer + match + draft
        P->>N: score >= umbral_inmediato ? notificar ya : dejar para el resumen
    end
```

**Nota de optimización:** la clasificación y la extracción se hacen en **una sola llamada** al LLM (el esquema incluye `is_job_offer`). La puntuación es una segunda llamada, y la redacción una tercera solo si la puntuación lo merece.

## 2. Aprobación y envío

```mermaid
sequenceDiagram
    autonumber
    actor Yo
    participant BOT as Bot de control
    participant D as Dispatcher
    participant UB as Userbot
    participant SMTP
    participant R as Reclutador
    participant DB as SQLite

    BOT->>Yo: Tarjeta de oferta + borrador [✅ ✏️ 🔄 📨 🗑 ⏰]
    alt ✏️ Editar
        Yo->>BOT: texto nuevo o instrucción ("más corto")
        BOT->>BOT: (si es instrucción) regenerar con el LLM
        BOT->>Yo: nueva vista previa
    else 🔄 Regenerar
        BOT->>Yo: nuevo borrador
    else 📨 Cambiar canal
        BOT->>Yo: opciones [Email x@y] [DM @user] [Manual]
    else 🗑 Descartar
        BOT->>DB: status = dismissed (+ motivo opcional)
    else ✅ Enviar
        opt canal = telegram_dm
            BOT->>UB: ¿hay chat previo con @user? (contacts / get_messages)
            UB-->>BOT: nuevo | conocido
            BOT->>DB: contador usuarios nuevos hoy
        end
        alt DM a usuario nuevo y contador = límite
            BOT->>Yo: "⛔ Límite de usuarios nuevos (10/10)" [📧 Email] [⏰ Mañana] [Cancelar]
        else permitido
            BOT->>Yo: Confirmación final: destinatario, canal, asunto, cuerpo, adjunto<br/>+ "Será el usuario nuevo nº 4 de 10 hoy" [Confirmar] [Cancelar]
            Yo->>BOT: Confirmar
            BOT->>DB: application status = queued
            BOT->>D: encolar
            alt canal = telegram_dm
                D->>D: volver a comprobar el límite (por si cambió mientras estaba en cola)
                D->>UB: send_message(@user, cuerpo)
                D->>UB: send_file(@user, cv.pdf)
                UB->>R: DM
                D->>DB: contacts.first_contacted_at (si es nuevo)
            else canal = email
                D->>SMTP: email con cv.pdf adjunto
                SMTP->>R: email
            end
            D->>DB: status = sent | failed (+ error)
            D->>BOT: resultado
            BOT->>Yo: "✅ Enviado a @user · 👤 Usuarios nuevos hoy: 4/10"
            BOT->>BOT: editar el mensaje de estado fijado
        end
    end
```

## Selección de canal

Entrada: la extracción del LLM (`contacts.emails`, `contacts.telegram_usernames`, `contacts.urls`, `application_instructions`, `preferred_channel_stated`) y el autor del mensaje.

```mermaid
flowchart TD
    A[Oferta extraída] --> B{¿La oferta indica<br/>método explícito?}
    B -- "email (enviar CV a...)" --> E[EMAIL al email indicado]
    B -- "DM / escribir a @user" --> T[TELEGRAM_DM a @user]
    B -- "formulario / enlace" --> M[MANUAL: mostrar enlace + texto sugerido]
    B -- no --> C{¿Hay contactos?}
    C -- "solo email" --> E
    C -- "solo @user" --> T
    C -- "ambos" --> P[Preferencia config<br/>preferred_channel] --> E2[EMAIL o DM]
    C -- "ninguno" --> D{¿Autor es un usuario<br/>con username o accesible?}
    D -- sí --> T2[TELEGRAM_DM al autor]
    D -- "no (canal / admin anónimo)" --> M
```

Reglas adicionales:
- Los emails extraídos se validan con regex y además **deben aparecer literalmente en el texto original** (protege contra alucinaciones del LLM). Lo mismo con los `@usuarios`.
- Si el destinatario resuelto por DM es un bot, un canal o no existe, se pasa a manual y se te avisa.
- El canal elegido y el **motivo** se muestran en la tarjeta ("📧 Email — la oferta dice 'enviar CV a jobs@acme.com'").

## 3. Resumen diario

```mermaid
sequenceDiagram
    participant S as Scheduler
    participant DB as SQLite
    participant N as Notificador
    actor Yo
    S->>DB: ofertas status=pending_digest de las últimas 24h, ORDER BY score DESC
    DB-->>S: lista
    S->>N: construir resumen
    N->>Yo: "📋 Resumen: 7 ofertas" + lista con botón [Ver] por oferta
    Yo->>N: [Ver #12]
    N->>Yo: tarjeta completa con los botones de aprobación
```

- Hora configurable (`digest.time`, por defecto `09:00` en `TZ`).
- Si no hay ofertas, no se envía nada (o un mensaje breve, configurable).
- Las ofertas "⏰ Más tarde" vuelven en el siguiente resumen.

## 4. Carga y actualización del CV

1. Envías el PDF al bot (o ejecutas `/cv` y lo adjuntas).
2. El bot guarda el archivo en `data/cv/cv.pdf` (versionando el anterior).
3. Extrae el texto con `pypdf`. Si sale vacío (PDF escaneado), te avisa: haría falta OCR o un `.txt` complementario.
4. El LLM genera un `CandidateProfile` (JSON) y el bot te lo muestra para confirmar o corregir.
5. Al confirmar, se invalida la caché de puntuaciones y puedes reevaluar las ofertas recientes con `/reevaluar`.

## 5. Escaneo histórico

`/escanear 7` recorre con `iter_messages` los últimos 7 días de cada grupo, de forma pausada (respetando `FloodWait`), y pasa los mensajes por el mismo pipeline. Las ofertas encontradas van al resumen, salvo las de puntuación muy alta, que se notifican agrupadas para no inundar el chat.

## 6. Arranque y recuperación

1. Cargar la config y validar las credenciales.
2. Conectar el userbot y el bot.
3. Reencolar los mensajes que quedaron en `status = processing` y los envíos en `queued`.
4. Opcional: "catch-up" de los mensajes publicados mientras el bot estaba apagado (desde el último `message_id` visto por grupo).
5. Cargar los prompts (solo lectura) y registrar su hash.
6. Recalcular el contador de usuarios nuevos del día desde la BD y actualizar (o crear y fijar) el mensaje de estado.
7. Enviarte "🟢 JobHunter en línea — escuchando N grupos".

## 7. Contador diario

- El scheduler, a las 00:00 (`TZ`), edita el mensaje de estado fijado para mostrar `👤 Usuarios nuevos: 0/10`.
- El contador no se guarda como número suelto: siempre se **calcula** desde `contacts`, así que no se desincroniza tras un reinicio.
