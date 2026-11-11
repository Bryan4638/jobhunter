from typing import Any

from telethon.errors import RPCError
from telethon.tl.functions.messages import GetDialogFiltersRequest, UpdateDialogFilterRequest
from telethon.tl.types import (
    DialogFilter,
    DialogFilterDefault,
    InputPeerUser,
    TextWithEntities,
)

from jobhunter.telegram.folders import ApplicationsFolder, next_folder_id


def make_filter(fid: int, title: str, users: list[int]) -> DialogFilter:
    return DialogFilter(
        id=fid,
        title=TextWithEntities(text=title, entities=[]),
        pinned_peers=[],
        include_peers=[InputPeerUser(u, 0) for u in users],
        exclude_peers=[],
    )


class FakeUserbot:
    def __init__(self, filters: list[Any], fail: RPCError | None = None) -> None:
        self.filters = filters
        self.updates: list[UpdateDialogFilterRequest] = []
        self.fail = fail

    async def __call__(self, request: Any) -> Any:
        if isinstance(request, GetDialogFiltersRequest):
            return self.filters
        if isinstance(request, UpdateDialogFilterRequest):
            if self.fail:
                raise self.fail
            self.updates.append(request)
            return True
        raise AssertionError(request)

    async def get_input_entity(self, uid: int) -> InputPeerUser:
        if uid < 0:
            raise ValueError("desconocido")
        return InputPeerUser(uid, 99)


def users_in(req: UpdateDialogFilterRequest) -> list[int]:
    return [p.user_id for p in req.filter.include_peers]


async def test_creates_folder_when_missing() -> None:
    bot = FakeUserbot([DialogFilterDefault(), make_filter(2, "Trabajo", [5])])
    added, error = await ApplicationsFolder(bot, "Aplicadas").add([10, 11])  # type: ignore[arg-type]
    assert (added, error) == (2, None)
    req = bot.updates[0]
    assert req.id == 3 and req.filter.title.text == "Aplicadas"
    assert users_in(req) == [10, 11]


async def test_appends_only_missing_users() -> None:
    bot = FakeUserbot([make_filter(4, "Aplicadas", [10])])
    added, _ = await ApplicationsFolder(bot, "Aplicadas").add([10, 12, 12, -1])  # type: ignore[arg-type]
    assert added == 1
    assert bot.updates[0].id == 4 and users_in(bot.updates[0]) == [10, 12]


async def test_nothing_to_do_and_disabled() -> None:
    bot = FakeUserbot([make_filter(4, "Aplicadas", [10])])
    assert await ApplicationsFolder(bot, "Aplicadas").add([10]) == (0, None)  # type: ignore[arg-type]
    assert await ApplicationsFolder(bot, "").add([1]) == (0, None)  # type: ignore[arg-type]
    assert bot.updates == []


async def test_full_folder_reports_error() -> None:
    bot = FakeUserbot([], fail=RPCError(None, "FILTER_INCLUDE_TOO_MUCH", 400))
    added, error = await ApplicationsFolder(bot, "Aplicadas").add([1])  # type: ignore[arg-type]
    assert added == 0 and error is not None and "100" in error


def test_next_folder_id_skips_used() -> None:
    assert next_folder_id([DialogFilterDefault()]) == 2
    assert next_folder_id([make_filter(2, "a", []), make_filter(3, "b", [])]) == 4
