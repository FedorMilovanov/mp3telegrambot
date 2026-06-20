# Баптистская серия — источники и первоисточники из mp3telegrambot

## Конфессиональная рамка бота

Бот работает в **реформированно-баптистской рамке точности** (core/prompts.py:1660). Это внутренний guardrail для Gemini, не публикуемая позиция.

### Три уровня:
1. **Абсолютные основания** — Писание, Троица, Христология, Sola Gratia/Fide/Christus
2. **Внутренний guardrail** — крещение верующих (1689 LBCF гл.29), кальвинизм (5 точек), цессационизм, конгрегационализм, претриб премилленаризм
3. **Внутрибратские нюансы** — детали эсхатологии, управления, практики

---

## Баптистские конфессиональные документы (Уровень 1)

| Документ | Год | Описание |
|---|---|---|
| **1689 Лондонское баптистское исповедание (LBCF)** | 1689 | Главный документ реформатских баптистов. Ключевые главы: 1 (Писание), 3 (предопределение), 6 (грех), 7 (завет), 8 (Христос), 10 (призвание), 11 (оправдание), 13 (освящение), 14 (вера), 15 (покаяние), 17 (стойкость), 20 (Евангелие), 26-29 (церковь), 29 (крещение), 32 (суд) |
| **Филадельфийское исповедание** | 1742 | Американская баптистская адаптация 1689 |
| **New Hampshire Confession** | 1833 | Влиятельное баптистское исповедание |
| **Abstract of Principles** | 1858 | Основополагающий документ SBTS (Southern Baptist Theological Seminary) |
| **Baptist Faith & Message** | 2000 | Современное исповедание Southern Baptist Convention |
| **Orthodox Catechism** (Hercules Collins) | 1680 | Баптистская адаптация Гейдельбергского катехизиса |
| **Keach's Catechism** (Benjamin Keach) | 1693 | Баптистский катехизис |

---

## Авторы — реформированные баптисты (Particular / Reformed Baptists)

### Исторические

| Автор | Ключевые труды |
|---|---|
| **Бенджамин Кич** (Benjamin Keach) | Tropologia; Baptist Catechism (Keach's Catechism, 1693) |
| **Геркулес Коллинз** (Hercules Collins) | An Orthodox Catechism (1680) |
| **Джон Гилл** (John Gill) | Exposition of the OT and NT; Body of Divinity; The Cause of God and Truth |
| **Эндрю Фуллер** (Andrew Fuller) | The Gospel Worthy of All Acceptation (definite atonement + duty faith) |
| **Чарльз Сперджен** (C.H. Spurgeon) | Treasury of David; Morning/Evening; Lectures to My Students |
| **Уильям Кэри** (William Carey) | Отец современной миссии |
| **Джеймс П. Бойс** (James P. Boyce) | Abstract of Systematic Theology (основатель SBTS) |
| **Джон Л. Дэгг** (John L. Dagg) | Manual of Theology (первая систематика Southern Baptist) |

### Современные (20–21 вв.)

| Автор | Ключевые труды / Служение |
|---|---|
| **Артур Пинк** (A.W. Pink) | The Sovereignty of God; The Attributes of God |
| **Джон Пайпер** (John Piper) | Desiring God; Let the Nations Be Glad |
| **Водди Бокам** (Voddie Baucham) | Family Driven Faith; Fault Lines |
| **Марк Девер** (Mark Dever) | Nine Marks of a Healthy Church; The Gospel and Personal Evangelism |
| **Джеймс Уайт** (James White) | The Potter's Freedom; Scripture Alone |
| **Томас Аскол** (Thomas Ascol) | Founders Ministries — возрождение кальвинизма у баптистов |
| **Конрад Мбеве** (Conrad Mbewe) | «Сперджен Африки», пастор из Замбии |
| **Питер Мастерс** (Peter Masters) | Metropolitan Tabernacle (после Сперджена) |
| **Том Неттлс** (Tom Nettles) | By His Grace and for His Glory (история кальвинизма у баптистов) |
| **Альберт Молер** (Albert Mohler) | Президент SBTS |
| **А.Х. Стронг** (A.H. Strong) | Systematic Theology (баптистская классика) |

---

## Тематические source packs с баптистскими источниками

Из `core/source_packs.py` — автоматически подбираются по теме:

| Тема | Баптистские источники |
|---|---|
| **Оправдание** | 1689 LBCF гл.11 |
| **Покаяние** | 1689 LBCF гл.15 |
| **Авторитет Писания** | 1689 LBCF гл.1 |
| **Достаточность Писания** | 1689 LBCF гл.1 |
| **Сотериология** | 1689 LBCF гл.10–11 |
| **Освящение** | 1689 LBCF гл.13 |
| **Экклесиология** | 1689 LBCF гл.26–29; Dever — Nine Marks |
| **Цессационизм** | 1689 LBCF гл.1 (достаточность); Masters — The Charismatic Phenomenon |
| **Завет** | 1689 LBCF гл.7 |
| **Спасение младенцев** | 1689 LBCF гл.10 пар.3; Spurgeon — Infant Salvation |
| **Евангелизм** | Fuller — The Gospel Worthy of All Acceptation; 1689 LBCF гл.20 |
| **Миссия** | Fuller — The Gospel Worthy of All Acceptation |
| **История церкви** | Spurgeon — Lectures to My Students; Fuller; Dallimore — Spurgeon |
| **По умолчанию** | 1689 London Baptist Confession; Spurgeon — Sermons |

---

## Архив обработанных видео

Из `docs/generated_pages_archive.md` — все обработанные проповеди:
- **Shepherds' Conference** серия (2026-06-15): 10 видео
  - МакАртур ×4, Лоусон ×2, Пеннингтон ×1, Молер ×1, Джонсон ×1, Q&A ×1
- Ранние (2026-06-11–12): МакАртур ×2, Бики ×1

Все с конспектами, разборами и размышлениями на Telegraph.

---

## Позиция Фуллера — ключевая для бота

> **Эндрю Фуллер** — The Gospel Worthy of All Acceptation:
> definite atonement + duty faith: Христос умер за избранных, но призыв к вере обращён ко всем без исключения.

Это позиция бота: **определённое искупление + искренний всеобщий призыв**. Гиперкальвинизм (отказ от всеобщего призыва) — заблуждение.

---

## Спектр сотериологии в промпте

1. Реформатская (Дорт, 1689, Кальвин, Оуэн, Эдвардс) — ✅ позиция бота
2. Умеренно-кальвинистская / 4-пунктная (Амираут, часть баптистов) — отмечаем
3. Классическое арминианство (Ремонстранты 1610) — заблуждение, но не ересь
4. Пелагианство / полупелагианство — ересь
