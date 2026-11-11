import json

import pytest
from telethon.tl.types import (
    InlineButtonTypeUrl,
    KeyboardButton,
    KeyboardButtonRow,
    ReplyInlineMarkup,
)

from jobhunter.analysis.offer_text import PAGE_HEADER, full_source, offer_input
from jobhunter.analysis.web_page import (
    PageError,
    check_url,
    html_to_text,
    offer_links,
    posting_text,
)
from jobhunter.telegram.sources import button_urls


def test_offer_links_skips_chats_and_social() -> None:
    text = (
        "Más info: https://kods.ai/jobs/backend. Escribe a t.me/rrhh o wa.me/5355555 "
        "www.empleo.cu/x"
    )
    links = offer_links(text, ("https://jobs.lever.co/kods/123", "https://t.me/canal/5"))
    assert links == [
        "https://jobs.lever.co/kods/123",
        "https://kods.ai/jobs/backend",
        "https://www.empleo.cu/x",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://192.168.1.1/",
        "http://10.0.0.5:8080/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://user:pass@example.com/",
    ],
)
async def test_check_url_blocks_non_public(url: str) -> None:
    with pytest.raises(PageError):
        await check_url(url)


async def test_check_url_accepts_public_ip() -> None:
    await check_url("https://1.1.1.1/")


def test_html_to_text_visible_content() -> None:
    html = """<html><head><title>Senior Backend · KODS</title>
    <meta name="description" content="Únete a KODS">
    <script>var tracking = "ignorar";</script><style>.x{}</style></head>
    <body><nav>Inicio | Blog</nav><h1>Senior Backend + AI</h1>
    <p>Python, AWS y LLMs.&nbsp;100% remoto.</p><ul><li>5 años</li><li>Inglés B2</li></ul>
    <footer>© 2026</footer></body></html>"""
    text = html_to_text(html)
    assert "Senior Backend · KODS" in text and "Únete a KODS" in text
    assert "Python, AWS y LLMs. 100% remoto." in text
    assert "5 años\n" in text
    assert "tracking" not in text and "Inicio | Blog" not in text and "©" not in text


def test_html_to_text_prefers_job_posting() -> None:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Senior Backend Developer",
        "hiringOrganization": {"@type": "Organization", "name": "KODS"},
        "employmentType": "FULL_TIME",
        "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"@type": "Country", "name": "Latinoamérica"},
        "baseSalary": {
            "currency": "USD",
            "value": {"minValue": 2000, "maxValue": 3500, "unitText": "MONTH"},
        },
        "description": (
            "<p>Construirás agentes con <b>LLMs</b>.</p><p>Aplica en careers@kods.ai</p>"
        ),
    }
    html = (
        f'<html><head><script type="application/ld+json">{json.dumps(posting)}</script></head>'
        "<body><p>Cookies</p></body></html>"
    )
    text = html_to_text(html)
    assert "Puesto: Senior Backend Developer" in text
    assert "Empresa: KODS" in text and "metadatos de la web: remoto" in text
    assert "Salario: 2000–3500 USD MONTH" in text
    assert "Construirás agentes con LLMs." in text and "careers@kods.ai" in text
    assert "Cookies" not in text


def test_posting_text_in_graph() -> None:
    assert "Puesto: QA" in posting_text({"@type": "JobPosting", "title": "QA"})


def test_offer_input_includes_sanitized_page() -> None:
    out = offer_input("Oferta corta", (), 4000, "https://kods.ai/j", "<b>Requisitos</b>: Python")
    assert PAGE_HEADER in out and "https://kods.ai/j" in out
    assert "<" not in out and "‹b›Requisitos" in out
    assert offer_input("Oferta corta", (), 4000) == "Oferta corta"
    assert full_source("a", "b") == "a\nb" and full_source("a", None) == "a"


def test_button_urls_from_inline_markup() -> None:
    class Msg:
        reply_markup = ReplyInlineMarkup(
            rows=[
                KeyboardButtonRow(
                    buttons=[
                        KeyboardButton(
                            text="Ir a la oferta 🔗",
                            type=InlineButtonTypeUrl(url="https://kods.ai/jobs/1"),
                        )
                    ]
                )
            ]
        )

    assert button_urls(Msg()) == ["https://kods.ai/jobs/1"]  # type: ignore[arg-type]


def test_posting_description_double_escaped() -> None:
    text = posting_text(
        {
            "@type": "JobPosting",
            "title": "X",
            "description": "&lt;p&gt;Híbrido en La Habana&lt;/p&gt;",
        }
    )
    assert "Híbrido en La Habana" in text and "<p>" not in text
