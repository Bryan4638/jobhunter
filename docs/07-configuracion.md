# 07 — Configuración y puesta en marcha

## Credenciales necesarias

| Credencial | Dónde se obtiene | Variable |
|------------|------------------|----------|
| `api_id` y `api_hash` de Telegram | https://my.telegram.org → *API development tools* → crear app | `TG_API_ID`, `TG_API_HASH` |
| Número de teléfono de tu cuenta | — (solo en el primer login) | `TG_PHONE` |
| Token del bot | Hablar con @BotFather → `/newbot` | `BOT_TOKEN` |
| Tu user ID de Telegram | Escribir a @userinfobot, o el bot lo muestra en `/start` | `OWNER_USER_ID` |
| API key de Gemini | https://aistudio.google.com → *Get API key* | `GEMINI_API_KEY` |
| SMTP (Gmail) | Cuenta Google → Seguridad → Verificación en 2 pasos → *Contraseñas de aplicaciones* | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` |

> **Gmail:** usa una *contraseña de aplicación*, nunca tu contraseña real. Host `smtp.gmail.com`, puerto `587` (STARTTLS).

## `.env` (secretos — nunca se sube a git)

```dotenv
# Telegram - userbot (tu cuenta)
TG_API_ID=1234567
TG_API_HASH=0123456789abcdef0123456789abcdef
TG_PHONE=+34600000000
TG_SESSION_NAME=data/sessions/userbot

# Telegram - bot de control
BOT_TOKEN=123456789:AA...
OWNER_USER_ID=111111111

# IA
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIza...

# Email
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tu.correo@gmail.com
SMTP_PASSWORD=xxxx xxxx xxxx xxxx
SMTP_FROM="Tu Nombre <tu.correo@gmail.com>"

# General
TZ=America/Havana
LOG_LEVEL=INFO
DATABASE_PATH=data/jobhunter.db
```

## `config.yaml` (comportamiento — sí se versiona)

```yaml
sources:
  # Los grupos y canales NO se configuran aquí: se gestionan desde el bot con /grupos y /grupo_add.
  catch_up_on_start: true
  catch_up_max_messages: 200

cv:
  path: data/cv/cv.pdf
  attachment_filename: "CV_Tu_Nombre.pdf"

preferences:
  modalities: [remote]                       # solo remoto: híbrido/presencial = deal-breaker
  remote_regions_ok: []                      # [] = cualquier zona
  min_salary: null                           # desactivado; se activa desde el bot con /salario
  salary_rule: penalize                      # discard | penalize (también desde /salario)
  # Tecnologías: se toman del CV (no hay listas de deseadas ni vetadas).
  exclude_keywords: ["sin remuneración", "no remunerado", "MLM", "inversión inicial"]
  seniority: [junior, mid, senior]
  contract_types: [full_time, part_time, freelance]
  offer_languages: [es]                      # solo español

signature:                                   # se añade al final de cada mensaje
  include: [email, phone, github_portfolio]  # sin LinkedIn
  email: "tu.correo@gmail.com"
  phone: "+52 ..."                           # teléfono / WhatsApp
  github_portfolio: "https://github.com/tu-usuario"

writing:
  tone: profesional-cercano
  style_example: null                        # pendiente P11b: mensaje de ejemplo para imitar el estilo

matching:                  # valores por defecto; /umbral en el bot tiene prioridad
  threshold_immediate: 80
  threshold_digest: 60
  generate_draft_for_digest: lazy   # eager | lazy (al pulsar [Ver])

channels:
  preferred: telegram_dm            # cuando la oferta admite ambos y no indica preferencia
  fallback_to_email_when_dm_limit: true   # si se agotó el límite de usuarios nuevos y hay email
  allow_dm_to_post_author: true

sending:
  require_final_confirmation: true
  telegram_dm:
    max_new_users_per_day: 10       # usuarios sin chat previo; bloquea al alcanzarse
    warn_ratio: 0.8                 # aviso 🟡 al llegar al 80 %
    pin_status_message: true        # mensaje fijado con el contador
    min_delay_seconds: 45
    max_delay_seconds: 180
    send_cv_as_file: true
    max_flood_wait_seconds: 120     # FloodWait mayor → el envío falla en vez de bloquear
    folder_name: "Aplicadas"        # carpeta de Telegram con los reclutadores contactados ("" = no usar)
  email:
    max_per_day: 30
    bcc_self: true                  # copia para tu registro
  dedupe_window_days: 30            # no aplicar 2 veces a la misma empresa/contacto

digest:
  enabled: true
  time: "09:00"
  max_items: 15
  skip_if_empty: true

dedupe:
  window_days: 14
  similarity_threshold: 92

prefilter:
  min_length: 80
  language_heuristic: true
  keywords: [hiring, vacante, vacancy, buscamos, "estamos buscando", oferta, developer,
             desarrollador, engineer, ingeniero, remote, remoto, salario, salary, "$", "€"]

llm:
  provider: gemini
  models:
    analyze: gemini-3.5-flash
    write: gemini-3.5-flash
    fallback: [gemini-3.5-flash-lite]
  max_concurrency: 2
  requests_per_minute: 10
  max_retries: 3
  request_timeout_seconds: 90
  temperature_analyze: 0.1
  temperature_write: 0.7
  max_offer_chars: 4000             # truncado del texto de la oferta
  injection_penalty: 15             # puntos que se restan si se detecta manipulación
# Nota: esta configuración y los prompts son de solo lectura para el pipeline de análisis.
# Solo cambian editando este archivo (y reiniciando) o con comandos del propietario.

links:
  fetch_pages: true                 # leer la página enlazada («Ir a la oferta») para analizar la oferta completa
  max_links: 2                      # enlaces que se prueban por mensaje
  max_page_chars: 6000              # texto de la página que se pasa a la IA
  timeout_seconds: 15

retention:
  discard_raw_after_days: 30
```

## Primera ejecución

```bash
# 1. Dependencias
uv sync

# 2. Configurar
cp .env.example .env            # rellenar los secretos
cp config.example.yaml config.yaml

# 3. Login interactivo del userbot (pide el código SMS/Telegram y la 2FA si la tienes)
uv run python -m jobhunter login

# 4. Arrancar
uv run python -m jobhunter run
```

Después, en Telegram:
1. Abre el chat con tu bot y envía `/start`.
2. Envía tu CV en PDF y confirma el perfil extraído.
3. Añade los grupos con `/grupo_add @grupo` (o reenviando un mensaje del grupo) y revisa `/preferencias`.
4. Lanza `/escanear 3` para probar con ofertas recientes.

## Ejecución continua

### systemd (Linux)
```ini
# ~/.config/systemd/user/jobhunter.service
[Unit]
Description=JobHunter Telegram bot
After=network-online.target

[Service]
WorkingDirectory=%h/Escritorio/Programing/Python/telegram-bot
ExecStart=%h/.local/bin/uv run python -m jobhunter run
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```
```bash
systemctl --user enable --now jobhunter
journalctl --user -u jobhunter -f
```

### Despliegue en VPS

**Opción elegida:** VPS 24/7 (por ejemplo Hetzner, DigitalOcean o Contabo; 1 vCPU y 1 GB de RAM es suficiente) con Docker.

- Imagen `python:3.13-slim` + `uv`; `docker-compose.yml` con `restart: unless-stopped` y el volumen `./data` montado (sesión, BD, CV y logs).
- Login inicial interactivo en el servidor: `docker compose run --rm app python -m jobhunter login`.
- Despliegue: `git pull && docker compose up -d --build`.
- **Seguridad del VPS** (crítico, porque la sesión de tu cuenta de Telegram vive ahí):
  - acceso SSH solo con clave y `PasswordAuthentication no`; usuario sin root;
  - firewall (`ufw`) sin puertos de entrada aparte de SSH (el bot no expone ningún puerto: Telethon usa conexiones salientes);
  - `fail2ban` y actualizaciones automáticas de seguridad;
  - `data/` con permisos `700`; `.env` con `600`;
  - copia de seguridad diaria de `data/jobhunter.db` (sin la sesión) fuera del VPS.
- Desarrollo en local con el mismo `docker compose`; la sesión de desarrollo y la del VPS deben ser **archivos distintos**: no copiar el `.session` entre máquinas, hacer login en cada una.

### systemd (alternativa sin Docker)
El servicio de usuario de arriba funciona igual en el VPS.

## Modo simulación

`SENDING_DRY_RUN=true` (o `sending.dry_run: true`, el valor por defecto): todo funciona igual, pero **nada llega al reclutador**:
- DM: el mensaje y el CV se envían a tus «Mensajes guardados» con la cabecera «🧪 SIMULACIÓN — destinatario real: …».
- Email: se envía a tu propia dirección con el asunto `[SIMULACIÓN → destinatario] …` (si el SMTP no está configurado, solo se registra).
- El contador de usuarios nuevos no cambia y la oferta se puede enviar de verdad después.

Para enviar de verdad: `sending.dry_run: false` en `config.yaml` (o `SENDING_DRY_RUN=false` en `.env`) y reinicia el bot.

`SMTP_FROM` admite `"Tu Nombre <tu.correo@gmail.com>"` o solo la dirección. Con Gmail usa una **contraseña de aplicación** (Cuenta de Google → Seguridad → Verificación en 2 pasos → Contraseñas de aplicaciones), nunca tu contraseña normal.
