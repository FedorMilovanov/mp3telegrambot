# Шрифты, вкоммиченные в `services/fonts/`

Оба семейства распространяются под **SIL Open Font License 1.1**, текст лицензии
лежит рядом с файлами. Это требование лицензии: при распространении шрифтов
(в том числе внутри репозитория) текст OFL должен ехать вместе с ними.

| Файлы | Семейство | Апстрим | Лицензия |
|---|---|---|---|
| `Merriweather-Regular.ttf`, `Merriweather-Bold.ttf` | Merriweather | <https://github.com/EbenSorkin/Merriweather4> | `OFL-Merriweather.txt` |
| `Manrope-Regular.ttf`, `Manrope-Medium.ttf`, `Manrope-SemiBold.ttf`, `Manrope-Bold.ttf`, `Manrope-ExtraBold.ttf` | Manrope | <https://github.com/googlefonts/manrope> | `OFL-Manrope.txt` |

Шрифты нужны, чтобы PDF-конспект (`services/pdf_generator.py`) выглядел одинаково
на любой машине после `git pull`, без установки шрифтов в систему.

Два практических следствия из OFL:

- файлы нельзя переименовывать/пересобирать под тем же именем семейства
  (Reserved Font Name). Если нужен модифицированный вариант — новое имя;
- продавать сами шрифты нельзя; использовать их внутри проекта и в созданных
  документах — можно без ограничений.
