"""Quién recibe el mensaje: lo decide el código con contactos literales (docs/08 §4)."""

from jobhunter.analysis.channel_selector import Author, select_channel
from jobhunter.analysis.contact_validator import ValidContacts, validate_contacts
from jobhunter.domain.schemas import Contacts

USER = Author(user_id=42, username="poster", name="Ana", is_user=True)
CHANNEL = Author(user_id=None, username=None, name="Canal", is_user=False)


def contacts(emails=(), users=(), urls=()) -> Contacts:
    return Contacts(emails=list(emails), telegram_usernames=list(users), urls=list(urls))


def test_only_literal_contacts_survive() -> None:
    text = "Envía tu CV a Jobs@Acme.com o escribe a @maria_rrhh. Más: https://acme.io/apply"
    v = validate_contacts(
        contacts(
            ["jobs@acme.com", "attacker@evil.com"],
            ["@maria_rrhh", "hacker_x"],
            ["https://acme.io/apply", "https://evil.com"],
        ),
        text,
        (),
    )
    assert v.emails == ("jobs@acme.com",)
    assert v.usernames == ("maria_rrhh",)
    assert v.urls == ("https://acme.io/apply",)
    assert set(v.rejected) == {"attacker@evil.com", "hacker_x", "https://evil.com"}


def test_username_must_be_whole_handle() -> None:
    # "@maria_rrhh_fake" no valida "maria_rrhh" y viceversa
    v = validate_contacts(contacts(users=["maria"]), "Escribe a @maria_rrhh", ())
    assert v.usernames == ()


def test_hidden_urls_and_tme_links() -> None:
    v = validate_contacts(
        contacts(urls=["https://forms.gle/abc", "t.me/recruiter_bob"]),
        "Aplica aquí (enlace)",
        ("https://forms.gle/abc", "https://t.me/recruiter_bob"),
    )
    assert v.urls == ("https://forms.gle/abc",)
    assert v.usernames == ("recruiter_bob",)


def test_stated_email_wins() -> None:
    c = ValidContacts(emails=("jobs@acme.com",), usernames=("maria_rrhh",))
    choice = select_channel(c, "email", "Enviar CV a jobs@acme.com", USER, "telegram_dm")
    assert (choice.chosen.kind, choice.chosen.target) == ("email", "jobs@acme.com")
    assert "jobs@acme.com" in choice.reason


def test_both_contacts_use_preference() -> None:
    c = ValidContacts(emails=("jobs@acme.com",), usernames=("maria_rrhh",))
    assert select_channel(c, "none", None, USER, "telegram_dm").chosen.target == "@maria_rrhh"
    assert select_channel(c, "none", None, USER, "email").chosen.target == "jobs@acme.com"


def test_dm_by_private_goes_to_author() -> None:
    choice = select_channel(ValidContacts(), "telegram_dm", "escribir por privado", USER, "email")
    assert (choice.chosen.kind, choice.chosen.target) == ("telegram_dm", "@poster")


def test_author_without_username_uses_id() -> None:
    author = Author(user_id=7, username=None, name="Pepe", is_user=True)
    choice = select_channel(ValidContacts(), "none", None, author, "telegram_dm")
    assert choice.chosen.target == "id:7"


def test_channel_post_without_contacts_is_manual() -> None:
    choice = select_channel(ValidContacts(), "none", None, CHANNEL, "telegram_dm")
    assert choice.chosen.kind == "manual" and choice.chosen.target is None


def test_author_dm_can_be_disabled() -> None:
    choice = select_channel(ValidContacts(), "none", None, USER, "telegram_dm", allow_author=False)
    assert choice.chosen.kind == "manual"


def test_bot_usernames_are_manual_and_form() -> None:
    c = ValidContacts(usernames=("acme_jobs_bot",), urls=("https://acme.io/apply",))
    choice = select_channel(c, "form", None, USER, "telegram_dm")
    assert choice.chosen.kind == "manual" and choice.chosen.target == "https://acme.io/apply"
    kinds = {(o.kind, o.target) for o in choice.options}
    assert ("manual", "https://t.me/acme_jobs_bot") in kinds
    assert all(o.target != "@acme_jobs_bot" for o in choice.options)


def test_options_include_every_possibility() -> None:
    c = ValidContacts(emails=("a@b.co",), usernames=("maria_rrhh",))
    choice = select_channel(c, "none", None, USER, "telegram_dm")
    targets = [o.target for o in choice.options]
    assert targets == ["a@b.co", "@maria_rrhh", "@poster", None]
