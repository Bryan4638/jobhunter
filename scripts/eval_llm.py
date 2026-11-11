"""Evalúa la extracción y el canal de la IA sobre ofertas etiquetadas (docs/05 «Evaluación»).

Uso:
    uv run python scripts/eval_llm.py            # extracción + reglas + canal
    uv run python scripts/eval_llm.py --score    # además puntúa con tu perfil activo

Cada ``tests/fixtures/offers/<nombre>.txt`` tiene su etiqueta ``<nombre>.json``:
    {"is_job": true, "rejected": false, "channel": "telegram_dm"}
(``rejected`` y ``channel`` son opcionales). Hace llamadas reales a Gemini.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from jobhunter.analysis.channel_selector import Author, select_channel
from jobhunter.analysis.contact_validator import validate_contacts
from jobhunter.analysis.pipeline import MIN_CONFIDENCE, offer_input
from jobhunter.analysis.rules import apply_rules, final_score
from jobhunter.config import Settings, load_app_config
from jobhunter.domain.schemas import JobExtraction, MatchResult
from jobhunter.llm.gemini import GeminiProvider
from jobhunter.llm.guard import detect_injection, new_tag, without_injection_lines
from jobhunter.llm.prompt_store import PromptStore
from jobhunter.storage.db import Database
from jobhunter.storage.profiles import ProfileRepo

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "offers"
AUTHOR = Author(user_id=1, username="autor_del_post", name="Autor", is_user=True)


async def main(score: bool) -> int:
    settings = Settings()  # type: ignore[call-arg]
    config = load_app_config(settings.config_path)
    if settings.gemini_api_key is None:
        print("Falta GEMINI_API_KEY en .env")
        return 1
    llm = GeminiProvider(settings.gemini_api_key.get_secret_value(), config.llm)
    prompts = PromptStore()

    profile = None
    if score:
        db = Database(settings.database_path)
        await db.connect()
        profile = await ProfileRepo(db).active()
        await db.close()
        if profile is None:
            print("No hay perfil activo: sube tu CV con /cv o ejecuta sin --score")
            return 1

    checks = {"is_job": [0, 0], "rejected": [0, 0], "channel": [0, 0]}
    for txt in sorted(FIXTURES.glob("*.txt")):
        label = json.loads(txt.with_suffix(".json").read_text(encoding="utf-8"))
        text = txt.read_text(encoding="utf-8").strip()
        tag = new_tag()
        ex: JobExtraction = (
            await llm.generate_json(
                system=prompts.render("offer_system", tag=tag),
                prompt=prompts.render(
                    "offer_extract", tag=tag, offer_text=offer_input(text, (), 4000)
                ),
                schema=JobExtraction,
            )
        ).value
        is_job = ex.is_job_offer and ex.confidence >= MIN_CONFIDENCE
        got: dict[str, object] = {"is_job": is_job}
        detail = ""
        if is_job:
            signals = detect_injection(text)
            outcome = apply_rules(ex, text, config, None, "penalize", bool(signals))
            got["rejected"] = outcome.rejected
            contacts = validate_contacts(ex.contacts, without_injection_lines(text), ())
            choice = select_channel(
                contacts, ex.preferred_channel_stated, ex.application_instructions, AUTHOR,
                config.channels.preferred, config.channels.allow_dm_to_post_author,
            )  # fmt: skip
            got["channel"] = choice.chosen.kind
            detail = f"{choice.chosen.kind} → {choice.chosen.target}"
            if outcome.rejected:
                detail = "; ".join(outcome.rejections)
            elif score and profile is not None:
                tag = new_tag()
                result: MatchResult = (
                    await llm.generate_json(
                        system=prompts.render("offer_system", tag=tag),
                        prompt=prompts.render(
                            "offer_score",
                            tag=tag,
                            offer_text=offer_input(text, (), 4000),
                            profile_json=profile.profile.model_dump_json(),
                            preferences_json="{}",
                            extraction_json=ex.model_dump_json(exclude={"contacts"}),
                        ),
                        schema=MatchResult,
                    )
                ).value
                detail += f" · {final_score(result.score, outcome)}/100"

        marks = []
        for key in checks:
            if key in label and key in got:
                ok = label[key] == got[key]
                checks[key][0] += ok
                checks[key][1] += 1
                marks.append(("✅" if ok else "❌") + key)
        print(f"{txt.stem:28} {' '.join(marks):40} {detail}")

    print()
    for key, (ok, total) in checks.items():
        if total:
            print(f"{key:10} {ok}/{total} ({100 * ok / total:.0f} %)")
    print(f"IA: {llm.usage.calls} llamadas · {llm.usage.tokens:,} tokens")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--score", action="store_true", help="puntuar también con tu perfil")
    sys.exit(asyncio.run(main(parser.parse_args().score)))
