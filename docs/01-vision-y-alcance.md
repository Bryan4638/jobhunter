# 01 — Visión y alcance

## Problema

Los grupos de Telegram de empleo para desarrolladores publican decenas de ofertas al día, muchas repetidas, mal formateadas o irrelevantes para tu perfil. Revisarlas a mano, decidir a cuáles aplicar y escribir un mensaje personalizado a cada reclutador lleva mucho tiempo, y las buenas ofertas se pierden entre el ruido.

## Objetivo

Un asistente personal (de un solo usuario) que:

1. **Escucha** los grupos de empleo usando tu cuenta de Telegram.
2. **Detecta** qué mensajes son ofertas de trabajo y extrae sus datos de forma estructurada.
3. **Puntúa** cada oferta según lo bien que encaja con tu CV y tus preferencias.
4. **Te avisa** por un chat privado con un bot de las mejores ofertas, explicando por qué encajan.
5. **Redacta** un mensaje de aplicación personalizado para cada una.
6. **Envía** ese mensaje (con tu CV adjunto) por el canal correcto, **solo si lo apruebas**.
7. **Registra** a qué ofertas aplicaste, para no repetir y poder hacer seguimiento.

## Usuarios

- **Un único usuario:** tú (el propietario). El bot ignora a cualquier otra persona que le escriba.

## Requisitos funcionales

### RF-1 Ingesta de mensajes
- RF-1.1 Leer en tiempo real los mensajes nuevos de una lista configurable de grupos/canales.
- RF-1.2 Permitir un escaneo histórico bajo demanda (por ejemplo, los últimos N días) con `/escanear`.
- RF-1.3 Guardar cada mensaje con sus metadatos: grupo, autor, fecha, enlace al mensaje y texto.
- RF-1.4 Los grupos y canales se gestionan **exclusivamente desde el bot** (`/grupos`): añadir (por @usuario, enlace `t.me/...`, ID o reenviando un mensaje del grupo), listar, pausar/reanudar y quitar. Tu cuenta debe ser miembro del grupo o canal.

### RF-2 Detección y extracción
- RF-2.1 Prefiltro barato (palabras clave, longitud, idioma) para descartar mensajes que claramente no son ofertas antes de llamar a la IA.
- RF-2.2 Clasificación con IA: ¿es una oferta de trabajo? (sí/no + confianza).
- RF-2.3 Extracción estructurada: puesto, empresa, seniority, stack, modalidad (remoto/híbrido/presencial), ubicación, salario, tipo de contrato, idiomas requeridos, **métodos de contacto** (emails, @usuarios, enlaces) e **instrucciones de aplicación** (por ejemplo, "enviar CV a…", "escribir por DM", "asunto: …").
- RF-2.4 Deduplicación: una oferta publicada en varios grupos se procesa y notifica una sola vez.

### RF-3 Perfil del candidato
- RF-3.1 Cargar el CV desde un PDF (`/cv` subiendo el archivo al bot, o por ruta en la configuración).
- RF-3.2 Extraer el texto y generar con IA un **perfil estructurado** (skills, años de experiencia, seniority, idiomas, ubicación, logros clave).
- RF-3.3 Preferencias editables desde el bot. Valores actuales: solo remoto (cualquier zona), solo ofertas en español, de junior a senior, cualquier contrato. Las tecnologías salen del CV.
- RF-3.4 Salario mínimo **desactivado por defecto**; se activa y cambia desde el bot con `/salario` (cantidad, moneda, periodo y regla: descartar o penalizar).

### RF-4 Puntuación (matching)
- RF-4.1 Puntuación de 0 a 100 por oferta, con razones, coincidencias, carencias y alertas (red flags).
- RF-4.2 Reglas duras previas a la IA (deal-breakers) que descartan sin gastar tokens: por ejemplo, "solo presencial en otra ciudad" o "tecnología vetada".
- RF-4.3 Umbrales configurables:
  - `>= umbral_inmediato` (por defecto 80): aviso inmediato.
  - `>= umbral_resumen` (por defecto 60): entra en el resumen diario.
  - menor: se guarda como descartada (consultable).

### RF-5 Notificación y aprobación
- RF-5.1 Tarjeta de oferta en el chat del bot: puntuación, resumen, por qué encaja, carencias, canal de envío propuesto, enlace al mensaje original y borrador del mensaje.
- RF-5.2 Botones inline: **✅ Enviar**, **✏️ Editar**, **🔄 Regenerar**, **📨 Cambiar canal**, **🗑 Descartar**, **⏰ Más tarde**.
- RF-5.3 Editar: el bot te pide el texto nuevo (o instrucciones como "más corto, menciona mi experiencia con AWS") y vuelve a mostrar la vista previa.
- RF-5.4 Paso de confirmación final antes de enviar, mostrando exactamente el destinatario, el canal, el asunto (si es email), el cuerpo y el adjunto.
- RF-5.5 Resumen diario a una hora configurable con las ofertas del rango medio que aún no se han revisado.

### RF-6 Decisión de canal de envío
- RF-6.1 Si la oferta indica explícitamente un método ("enviar CV a x@y.com"), se usa ese.
- RF-6.2 Si hay un @usuario de contacto o pide DM, se usa DM por Telegram.
- RF-6.3 Si no hay ningún contacto, se usa el DM al autor del mensaje (si es un usuario y no un canal o administrador anónimo).
- RF-6.4 Si solo hay un enlace (formulario o ATS), **no se envía nada**: se muestra el enlace y un texto sugerido para que lo pegues tú.
- RF-6.5 Si hay varios canales válidos y la oferta no indica preferencia, se usa la preferencia configurada y el botón 📨 permite cambiarla.
- RF-6.6 Se respetan las instrucciones de la oferta (asunto del email, código de referencia, idioma, "incluir salario esperado", etc.).

### RF-7 Envío
- RF-7.1 **DM por Telegram** desde tu cuenta: texto y luego el PDF del CV como documento.
- RF-7.2 **Email** por SMTP: asunto, cuerpo y el PDF adjunto.
- RF-7.3 Confirmación del resultado en el chat (enviado / error con el motivo).
- RF-7.4 Límites de envío diarios y espaciado aleatorio entre DMs (ver [08](08-seguridad-y-riesgos.md)).
- RF-7.5 No aplicar dos veces a la misma oferta o reclutador en una ventana configurable (aviso si lo intentas).

### RF-7B Contador de usuarios nuevos contactados
- RF-7B.1 **Usuario nuevo** = usuario de Telegram con el que tu cuenta **no tenía ningún chat privado previo** (sin mensajes anteriores en el diálogo) en el momento del envío. Escribir de nuevo a alguien con quien ya hablaste no cuenta como nuevo.
- RF-7B.2 Contador diario de usuarios nuevos a los que se ha escrito, que se reinicia a las 00:00 en la zona horaria configurada (`TZ`).
- RF-7B.3 El contador está **siempre visible en el chat del bot**:
  - en un **mensaje de estado fijado** (pin) que se actualiza tras cada envío;
  - en cada tarjeta cuyo canal sea un DM a un usuario nuevo;
  - en la confirmación final ("será el usuario nuevo nº 4 de 10 hoy");
  - en la confirmación de envío;
  - bajo demanda con `/contador` (hoy y los últimos 7 días).
- RF-7B.4 El límite diario de usuarios nuevos (`max_new_users_per_day`) **bloquea** los DMs a usuarios nuevos al alcanzarse; se ofrece enviar por email, dejarlo para mañana o cancelar.
- RF-7B.5 Aviso preventivo al llegar al umbral de advertencia (por defecto el 80 % del límite).
- RF-7B.6 También se muestran, por separado y a título informativo, los DMs totales del día (incluidos los contactos ya conocidos) y los emails enviados.

### RF-9 Aislamiento de la IA frente al contenido analizado
- RF-9.1 El texto de los mensajes y ofertas analizados **no puede cambiar el comportamiento de la IA**, ni su prompt de sistema, ni la configuración base del bot (umbrales, preferencias, límites, canal elegido, destinatarios, grupos).
- RF-9.2 El prompt de sistema y la configuración solo se modifican por el propietario (editando archivos o con comandos del bot autenticados), nunca a partir del contenido de una oferta ni de la salida del LLM.
- RF-9.3 Cada análisis es independiente y sin memoria: lo que diga una oferta no influye en el análisis de otra.
- RF-9.4 Los intentos de manipulación detectados se marcan en la oferta y se te muestran como alerta.
- Detalle de los controles en [08 §4](08-seguridad-y-riesgos.md#4-aislamiento-de-la-ia-prompt-injection).

### RF-8 Historial
- RF-8.1 `/aplicadas`: lista de las aplicaciones enviadas con fecha, canal y estado.
- RF-8.2 `/stats`: ofertas vistas, filtradas, notificadas y aplicadas por periodo.

## Requisitos no funcionales

| ID | Requisito |
|----|-----------|
| RNF-1 | Funciona en local (tu PC o un VPS pequeño); un único proceso con asyncio. |
| RNF-2 | Persistencia en SQLite; sin servicios externos aparte de Telegram, Gemini y SMTP. |
| RNF-3 | Latencia: una oferta relevante se notifica en menos de 1 minuto desde que se publica. |
| RNF-4 | Coste: prefiltro y caché para minimizar llamadas al LLM; debe funcionar con el plan gratuito de Gemini a volúmenes normales. |
| RNF-5 | Resiliencia: reintentos con backoff ante errores del LLM, `FloodWait` de Telegram y errores de SMTP; reanudación tras reiniciar. |
| RNF-6 | Seguridad: secretos en `.env`, sesión de Telethon protegida y el bot solo responde a tu `user_id`. |
| RNF-7 | Observabilidad: logs estructurados y comando `/estado` con la salud de cada componente. |
| RNF-8 | Proveedor de IA intercambiable sin tocar la lógica de negocio. |
| RNF-9 | Idioma: la interfaz del bot está en español; los mensajes de aplicación van en el idioma de la oferta. |
| RNF-10 | Inmutabilidad: prompts y configuración base se cargan al arrancar y son de solo lectura para el pipeline de análisis; no existe ningún camino de código desde el contenido analizado (ni desde la salida del LLM) hasta la configuración. |

## Fuera de alcance (v1)

- Varios usuarios o multi-tenant.
- Rellenar formularios web o ATS (LinkedIn, Workable, etc.) de forma automática.
- Leer y responder automáticamente las respuestas de los reclutadores (solo se notifican; ver roadmap).
- Fuentes distintas de Telegram (LinkedIn, webs de empleo).
- Generar CVs adaptados por oferta (posible fase futura).
