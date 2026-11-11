import pytest

from jobhunter.telegram.chat_ref import InviteLinkError, parse_chat_ref


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("@python_jobs", "python_jobs"),
        ("python_jobs", "python_jobs"),
        ("https://t.me/python_jobs", "python_jobs"),
        ("t.me/python_jobs", "python_jobs"),
        ("https://telegram.me/python_jobs", "python_jobs"),
        ("https://t.me/python_jobs/1234", "python_jobs"),
        ("https://t.me/s/python_jobs", "python_jobs"),
        ("https://t.me/python_jobs?start=x", "python_jobs"),
        ("https://t.me/c/1234567890/55", -1001234567890),
        ("-1001234567890", -1001234567890),
        ("  12345  ", 12345),
    ],
)
def test_parse_valid(ref: str, expected: str | int) -> None:
    assert parse_chat_ref(ref) == expected


@pytest.mark.parametrize("ref", ["https://t.me/+AbCdEf123", "https://t.me/joinchat/AbCdEf"])
def test_invite_links_rejected(ref: str) -> None:
    with pytest.raises(InviteLinkError):
        parse_chat_ref(ref)


@pytest.mark.parametrize("ref", ["", "@ab", "hola mundo", "https://t.me/", "https://example.com/x"])
def test_invalid(ref: str) -> None:
    with pytest.raises(ValueError):
        parse_chat_ref(ref)
