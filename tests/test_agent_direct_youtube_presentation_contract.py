from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/"docs"/"agent_direct_dubbing"
def read(name):return (ROOT/name).read_text(encoding="utf8")
def test_route():
 t=read("README.md")
 assert "RUSSIAN_NAMING_AND_DELIVERY.md" in t
 assert "YOUTUBE_PRESENTATION_BY_SURFACE.md" in t
 assert "APJ1159_2026-11-08_EDITORIAL_LOCK_CASE.md" in t
def test_delivery():
 r=read("RUSSIAN_NAMING_AND_DELIVERY.md")
 assert "Джон Пайпер - Предопределяет ли Бог Людей к Аду - Спроси Пастора Джона №450.mp4" in r
 assert "01 - Автор - Тема Выпуска.mp4" in r
 assert "ASCII U+002D" in r and "TXT" in r
 assert "локальной передаче" in r
def test_one_and_ten():
 t=read("README.md")
 assert "Переведи и положи 1 видео" in t
 assert "Переведи и положи 10 видео" in t
 assert "Не генерировать TXT" in t
 assert "LOCAL_HANDOFF_READY" in t and "RIGHTS_APPROVED" in t
def test_clock_patch_and_existing_apj_guard():
 t=read("YANDEX_TRANSLATION_EDITORIAL.md")
 assert "сначала попробовать вырезать только ошибочные слова" in t
 assert "часов" in t and "минут" in t
 assert "## Обязательный STOP-GATE" in t
def test_channel_and_source_safety():
 t=read("YOUTUBE_PRESENTATION_BY_SURFACE.md")
 for x in ("*важные слова*","_выделенные слова_","*VK:*","Research/AGENT_RULES.md",
     "gb-is-my-strength/docs/EDITORIAL-SOURCE-POLICY.md","TheLegendaryPoet/docs/EDITORIAL_JUDGMENT_AND_SOURCE_POLICY.md",
     "5000 БАЙТ","PR #453/#455/#456"):
  assert x in t
 assert "не перегружать" in t and "утверждённые владельцем" in t
