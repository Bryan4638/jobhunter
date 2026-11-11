"""Render de tarjetas de oferta, listas y resumen (docs/06).

Callbacks de oferta (``of:<acción>:<id>[:<arg>]``):
    v ver (tarjeta nueva) · b volver · w redactar · s enviar · e editar · r regenerar
    c cambiar canal · ch elegir canal (arg = índice) · l más tarde · d descartar
    a ya apliqué
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from telethon import Button

from jobhunter.analysis.writer import full_body
from jobhunter.storage.offers import Draft, Match, Offer, OfferSummary

MAX_MESSAGE = 4096
MODALITY = {"remote": "Remoto", "hybrid": "Híbrido", "onsite": "Presencial"}
SENIORITY = {
    "intern": "Prácticas",
    "junior": "Junior",
    "mid": "Mid",
    "senior": "Senior",
    "lead": "Lead",
    "principal": "Principal",
}
PERIOD = {"hour": "hora", "month": "mes", "year": "año"}
CHANNEL_ICON = {"email": "📧", "telegram_dm": "💬", "manual": "🔗"}
CHANNEL_NAME = {"email": "Email", "telegram_dm": "DM", "manual": "Manual"}
STATUS_NOTE = {
    "dismissed": "🗑 <b>Descartada</b>",
    "snoozed": "⏰ <b>Pospuesta</b>: vuelve en el próximo resumen",
    "applied": "✔️ <b>Aplicada</b>",
    "approved": "📤 <b>Envío en curso</b>",
    "send_failed": "❌ <b>Falló el último envío</b>: pulsa ✅ Enviar para reintentar",
    "rejected_rules": "🚫 <b>Descartada por tus reglas</b>",
    "low_score": "📉 <b>Puntuación baja</b>: no se notifica",
}


def score_icon(score: int | None) -> str:
    if score is None:
        return "▫️"
    return "🔥" if score >= 80 else "👍" if score >= 60 else "🔹"


def fmt_amount(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def salary_label(offer: Offer) -> str | None:
    lo, hi = offer.salary_min, offer.salary_max
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None and lo != hi:
        amount = f"{fmt_amount(lo)}–{fmt_amount(hi)}"
    else:
        amount = fmt_amount(hi if hi is not None else lo)  # type: ignore[arg-type]
    currency = f" {offer.salary_currency}" if offer.salary_currency else ""
    period = f"/{PERIOD[offer.salary_period]}" if offer.salary_period in PERIOD else ""
    return f"{amount}{currency}{period}"


def ago(iso: str) -> str:
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    minutes = int((datetime.now(UTC) - when).total_seconds() // 60)
    if minutes < 1:
        return "ahora"
    if minutes < 60:
        return f"hace {minutes} min"
    if minutes < 48 * 60:
        return f"hace {minutes // 60} h"
    return f"hace {minutes // 1440} días"


def channel_line(kind: str, target: str | None, label: str | None = None) -> str:
    icon = CHANNEL_ICON.get(kind, "")
    name = CHANNEL_NAME.get(kind, kind)
    if kind == "telegram_dm" and label and "Autor" in label:
        shown = label.removeprefix("💬 ")
    else:
        shown = target or "sin enlace: aplica a mano"
    return f"{icon} <b>Canal:</b> {name} → {escape(shown)}"


def _bullets(title: str, items: list[str], limit: int = 4) -> list[str]:
    if not items:
        return []
    return [f"\n<b>{title}</b>", *(f"• {escape(i)}" for i in items[:limit])]


def offer_title(offer: Offer | OfferSummary) -> str:
    title = offer.title or "Oferta sin título"
    return f"{title} · {offer.company}" if offer.company else title


def offer_card(
    offer: Offer,
    match: Match | None,
    draft: Draft | None,
    signature: str,
    attachment: str,
    note: str | None = None,
    channel_hint: str | None = None,
) -> str:
    score = match.score if match else None
    seniority = f" ({SENIORITY[offer.seniority]})" if offer.seniority in SENIORITY else ""
    head = f"{score_icon(score)} <b>{score if score is not None else '—'}/100</b> · "
    head += f"<b>{escape(offer.title or 'Oferta')}</b>{seniority}"
    facts = []
    if offer.company:
        facts.append(f"🏢 {escape(offer.company)}")
    if offer.modality:
        where = MODALITY.get(offer.modality, offer.modality)
        facts.append(f"🌍 {where}" + (f" ({escape(offer.location)})" if offer.location else ""))
    if (salary := salary_label(offer)) is not None:
        facts.append(f"💰 {escape(salary)}")
    lines = [head]
    if facts:
        lines.append(" · ".join(facts))
    if offer.stack:
        lines.append("🧰 " + escape(", ".join(offer.stack[:12])))
    origin = f"📍 {escape(offer.chat_title or 'Grupo')} · {ago(offer.posted_at)}"
    if offer.link:
        origin += f' · <a href="{escape(offer.link)}">ver mensaje</a>'
    lines.append(origin)
    if offer.summary:
        lines.append(f"\n<i>{escape(offer.summary)}</i>")

    if match:
        lines += _bullets("✅ Por qué encaja", match.reasons)
        lines += _bullets("⚠️ Carencias", match.gaps)
        lines += _bullets("🚩 Alertas", match.red_flags)
        adjustments = [a for a in match.adjustments if a.get("delta")]
        if adjustments:
            parts = ", ".join(f"{a['label']} {a['delta']:+d}" for a in adjustments)
            lines.append(f"\n🧮 IA {match.llm_score} · ajustes: {escape(parts)}")
    if offer.rejections:
        lines += _bullets("🚫 Descartada por", offer.rejections)
    if offer.injection_suspected:
        signals = ", ".join(offer.injection_signals[:3])
        lines.append(
            f"\n🛡 <b>Posible intento de manipulación de la IA</b> ({escape(signals)}). "
            "El análisis se hizo con las reglas normales."
        )

    if match:
        lines.append("")
        line = channel_line(match.channel.kind, match.channel.target, match.channel.label)
        lines.append(f"{line} · {channel_hint}" if channel_hint else line)
        if match.channel_reason:
            lines.append(f"   <i>Motivo: {escape(match.channel_reason)}</i>")

    if draft:
        body = full_body(draft.body, signature)
        lines.append(f"\n──────── Borrador v{draft.version} ────────")
        if draft.subject:
            lines.append(f"<b>Asunto:</b> {escape(draft.subject)}")
        lines.append(escape(body))
        if draft.channel != "manual":
            lines.append(f"📎 Adjunto: {escape(attachment)}")
        for warning in draft.warnings:
            lines.append(f"⚠️ {escape(warning)}")
    elif match and offer.status not in STATUS_NOTE:
        lines.append("\n📝 Aún no hay borrador.")

    if note or offer.status in STATUS_NOTE:
        lines.append(f"\n{note or STATUS_NOTE[offer.status]}")
    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE:
        text = text[: MAX_MESSAGE - 2] + "…"
    return text


def _cb(action: str, offer_id: int, arg: object | None = None) -> bytes:
    return f"of:{action}:{offer_id}" + (f":{arg}" if arg is not None else "")


def offer_buttons(offer: Offer, match: Match | None, draft: Draft | None) -> list[list[Button]]:
    if offer.status in ("dismissed", "applied", "approved") or match is None:
        return []
    i = offer.id
    later_dismiss = [
        Button.inline("⏰ Más tarde", _cb("l", i)),
        Button.inline("🗑 Descartar", _cb("d", i)),
    ]
    if draft is None:
        return [[Button.inline("📝 Redactar mensaje", _cb("w", i))], later_dismiss]
    if match.channel.kind == "manual":
        first = [Button.inline("✔️ Ya apliqué", _cb("a", i))]
    else:
        first = [Button.inline("✅ Enviar", _cb("s", i))]
    first += [Button.inline("✏️ Editar", _cb("e", i)), Button.inline("🔄 Regenerar", _cb("r", i))]
    second = [Button.inline("📨 Cambiar canal", _cb("c", i)), *later_dismiss]
    return [first, second]


def channel_buttons(offer_id: int, match: Match) -> list[list[Button]]:
    rows = []
    for idx, option in enumerate(match.options[:8]):
        mark = "✓ " if option == match.channel else ""
        rows.append([Button.inline(f"{mark}{option.label}"[:60], _cb("ch", offer_id, idx))])
    rows.append([Button.inline("↩️ Volver", _cb("b", offer_id))])
    return rows


def offers_list(
    title: str, items: list[OfferSummary], footer: str = ""
) -> tuple[str, list[list[Button]]]:
    lines = [title, ""]
    buttons: list[list[Button]] = []
    for n, item in enumerate(items, start=1):
        score = item.score if item.score is not None else "—"
        where = f" · {MODALITY[item.modality]}" if item.modality in MODALITY else ""
        status = " ⏰" if item.status == "snoozed" else ""
        lines.append(f"{n}. {score_icon(item.score)} <b>{score}</b> · {escape(offer_title(item))}"
                     f"{where}{status}")  # fmt: skip
        if item.rejections:
            lines.append(f"   <i>{escape('; '.join(item.rejections))}</i>")
        buttons.append([Button.inline(f"{n}. Ver", _cb("v", item.id))])
    if footer:
        lines += ["", footer]
    # Botones de 3 en 3 para no hacer la lista eterna.
    flat = [b for row in buttons for b in row]
    rows = [flat[k : k + 3] for k in range(0, len(flat), 3)]
    return "\n".join(lines), rows
