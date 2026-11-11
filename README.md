# JobHunter

Bot personal de Telegram que lee grupos de empleo con tu cuenta, evalúa las ofertas contra tu CV con IA (Gemini) y, con tu aprobación, envía el mensaje de aplicación al reclutador por DM o email.

📚 Documentación completa: [`docs/`](docs/README.md)

## Estado

**Fases 0–3** completadas y **fase 4** en gran parte — ver [roadmap](docs/10-roadmap.md):
- [x] Login de tu cuenta (userbot) y bot de control que solo te responde a ti
- [x] Gestión de grupos/canales desde el bot: `/grupos`, `/grupo_add`, reenviar un mensaje
- [x] Escucha de mensajes nuevos de los grupos monitorizados
- [x] `/salario`, `/estado`, `/start`, `/ayuda`
- [x] Fase 1: CV → perfil con Gemini (`/cv`, `/perfil`), guardado de mensajes, prefiltro, deduplicación y catch-up
- [x] Fase 2: análisis de ofertas con IA, reglas, puntuación, canal, borradores, tarjetas, `/ofertas`, `/escanear`, `/descartadas`, `/resumen`
- [x] Fase 3: envío real (DM y email con el CV), confirmación final, contador de usuarios nuevos fijado, `/contador`, `/aplicadas`, `/stats`
- [x] Fase 4: avisos de respuesta, seguimiento, motivos de descarte, `/reevaluar`, `/pausa`, `/umbral`, `scripts/eval_llm.py`

## Puesta en marcha (local)

Requisitos: [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env                 # rellena TG_API_ID, TG_API_HASH, TG_PHONE, BOT_TOKEN
cp config.example.yaml config.yaml

uv run python -m jobhunter login     # pide el código de Telegram; muestra tu OWNER_USER_ID
# → pon OWNER_USER_ID en .env

uv run python -m jobhunter run
```

En Telegram, abre el chat con tu bot, envía `/start` y añade grupos con `/grupo_add @grupo` o `/grupos` → «Añadir desde mis chats».

## VPS (Docker)

```bash
cp .env.example .env && cp config.example.yaml config.yaml   # y rellena .env
docker compose run --rm app python -m jobhunter login
docker compose up -d --build
docker compose logs -f
```

Lee [docs/07-configuracion.md](docs/07-configuracion.md#despliegue-en-vps) para proteger el servidor: la sesión de tu cuenta de Telegram vive en `data/sessions/`.

## Desarrollo

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
