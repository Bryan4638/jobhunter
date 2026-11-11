"""Página enlazada de una oferta («Ir a la oferta», «Ver más»…): se descarga y se convierte en
texto para que la IA analice la oferta completa (docs/05 «Enlaces»).

Seguridad (docs/08):
- Solo ``http``/``https``; cada salto de redirección se valida de nuevo.
- Nunca direcciones privadas, locales ni reservadas (el bot no puede usarse para sondear tu red).
- Tamaño y tiempo máximos; solo HTML o texto.
- El texto obtenido es **dato no confiable**: entra en el mismo bloque delimitado y saneado
  que el mensaje, pasa por el detector de manipulación y no cambia el prompt ni la config.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from jobhunter.llm.guard import URL_RE

log = logging.getLogger(__name__)

MAX_BYTES = 1_500_000
MAX_REDIRECTS = 4
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)
# Enlaces que no son la página de la oferta: chats, redes y contacto directo.
SKIP_HOSTS = (
    "t.me", "telegram.me", "telegram.org", "wa.me", "whatsapp.com", "chat.whatsapp.com",
    "instagram.com", "facebook.com", "fb.com", "x.com", "twitter.com", "youtube.com",
    "youtu.be", "tiktok.com",
)  # fmt: skip
SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "head", "nav", "footer"}
BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section",
    "article", "header", "table", "dd", "dt", "blockquote", "pre",
}  # fmt: skip


class PageError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PageText:
    url: str
    text: str


def offer_links(text: str, hidden_urls: tuple[str, ...]) -> list[str]:
    """Enlaces candidatos a ser la página de la oferta, en orden de aparición (botones y
    enlaces ocultos primero, que es donde suelen ir «Ir a la oferta»)."""
    found = [*hidden_urls, *(m.rstrip(".,;:!?)") for m in URL_RE.findall(text))]
    out = []
    for url in dict.fromkeys(found):
        if not url.lower().startswith(("http://", "https://")):
            if url.lower().startswith("www."):
                url = "https://" + url
            else:
                continue
        host = (urlsplit(url).hostname or "").lower()
        if not host or any(host == h or host.endswith("." + h) for h in SKIP_HOSTS):
            continue
        out.append(url)
    return out


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def check_url(url: str) -> None:
    """Lanza ``PageError`` si la URL no es http(s) pública."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PageError("solo se admiten enlaces http(s)")
    if parts.username or parts.password:
        raise PageError("enlace con credenciales")
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, parts.port or (443 if parts.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )  # fmt: skip
        except OSError as exc:
            raise PageError(f"no se pudo resolver {host}") from exc
        addresses = [str(info[4][0]) for info in infos]
    if not addresses or not all(_is_public(a) for a in addresses):
        raise PageError(f"{host} no es una dirección pública")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.description = ""
        self.json_ld: list[str] = []
        self._skip = 0
        self._in_title = False
        self._in_json_ld = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_json_ld = True
            self._buf = []
            return
        if tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key in ("description", "og:description") and len(a.get("content", "")) > len(
                self.description
            ):
                self.description = a.get("content", "")
            return
        if tag == "title":
            self._in_title = True
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_json_ld:
            self._in_json_ld = False
            self.json_ld.append("".join(self._buf))
            return
        if tag == "title":
            self._in_title = False
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._buf.append(data)
        elif self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def _strip_html(fragment: str) -> str:
    p = _TextExtractor()
    p.feed(fragment)
    return _clean("".join(p.parts))


def _clean(text: str) -> str:
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    lines = [ln.strip() for ln in text.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def _job_postings(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    items = data if isinstance(data, list) else [data]
    if isinstance(data, dict) and isinstance(data.get("@graph"), list):
        items += data["@graph"]
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "JobPosting" in kinds:
            out.append(item)
    return out


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    if isinstance(value, list):
        return ", ".join(filter(None, (_name(v) for v in value)))
    return str(value or "")


def _salary(value: Any) -> str:
    if not isinstance(value, dict):
        return _name(value)
    amount = value.get("value")
    currency = value.get("currency", "")
    if isinstance(amount, dict):
        low, high = amount.get("minValue"), amount.get("maxValue")
        unit = amount.get("unitText", "")
        figure = f"{low}–{high}" if low and high else str(amount.get("value") or low or high or "")
        return f"{figure} {currency} {unit}".strip()
    return f"{amount or ''} {currency}".strip()


def posting_text(job: dict[str, Any]) -> str:
    """Texto legible de un JobPosting de schema.org (lo publican muchas webs de empleo)."""
    lines = []
    fields = [
        ("Puesto", _name(job.get("title"))),
        ("Empresa", _name(job.get("hiringOrganization"))),
        ("Tipo de empleo", _name(job.get("employmentType"))),
        (
            "Modalidad según los metadatos de la web",
            "remoto" if job.get("jobLocationType") == "TELECOMMUTE" else "",
        ),
        ("Ubicación aceptada", _name(job.get("applicantLocationRequirements"))),
        ("Salario", _salary(job.get("baseSalary"))),
        ("Publicada", _name(job.get("datePosted"))),
        ("Válida hasta", _name(job.get("validThrough"))),
    ]
    for label, value in fields:
        if value:
            lines.append(f"{label}: {value}")
    description = job.get("description")
    if isinstance(description, str) and description:
        lines.append("")
        lines.append(_strip_html(unescape(description)))
    return "\n".join(lines).strip()


def html_to_text(html: str) -> str:
    """Prioriza el JobPosting estructurado; si no hay, el texto visible de la página."""
    p = _TextExtractor()
    p.feed(html)
    p.close()
    postings = [j for raw in p.json_ld for j in _job_postings(raw)]
    if postings:
        return posting_text(postings[0])
    body = _clean("".join(p.parts))
    title = _clean(p.title)
    head = [t for t in (title, _clean(p.description)) if t and t not in body]
    return "\n\n".join([*head, body]).strip()


async def fetch_page(url: str, timeout_s: float = 15.0) -> PageText:
    """Descarga ``url`` (validando cada redirección) y devuelve su texto."""
    current = url
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout_s,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "es,en;q=0.8"},
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(current)
            try:
                async with client.stream("GET", current) as resp:
                    if resp.is_redirect and "location" in resp.headers:
                        current = urljoin(current, resp.headers["location"])
                        continue
                    if resp.status_code >= 400:
                        raise PageError(f"la página respondió {resp.status_code}")
                    ctype = resp.headers.get("content-type", "").lower()
                    if ctype and not any(t in ctype for t in ("html", "text/plain", "xml")):
                        raise PageError(f"no es una página web ({ctype.split(';')[0]})")
                    data = bytearray()
                    async for chunk in resp.aiter_bytes():
                        data += chunk
                        if len(data) > MAX_BYTES:
                            break
                    raw = bytes(data[:MAX_BYTES]).decode(resp.encoding or "utf-8", "replace")
            except httpx.HTTPError as exc:
                raise PageError(f"no se pudo descargar: {type(exc).__name__}") from exc
            text = raw if "text/plain" in ctype else html_to_text(raw)
            return PageText(url=current, text=text)
    raise PageError("demasiadas redirecciones")


async def fetch_offer_page(
    text: str,
    hidden_urls: tuple[str, ...],
    *,
    max_links: int,
    timeout_s: float,
    min_chars: int = 200,
) -> PageText | None:
    """Primera página enlazada con contenido útil, o None. Nunca lanza."""
    for url in offer_links(text, hidden_urls)[:max_links]:
        try:
            page = await fetch_page(url, timeout_s)
        except PageError as exc:
            log.info("Enlace %s no analizado: %s", url, exc)
            continue
        except Exception:  # una web rara nunca debe romper el análisis
            log.exception("Error inesperado leyendo %s", url)
            continue
        if len(page.text) >= min_chars:
            log.info("Página de la oferta leída: %s (%s caracteres)", page.url, len(page.text))
            return page
        log.info("Enlace %s sin contenido útil (¿web que necesita JavaScript?)", url)
    return None
