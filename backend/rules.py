"""Правила для формальных критериев (гибрид с локальной моделью).

Критерий в чек-листе может иметь поле "auto" — имя правила ниже. Правило возвращает
готовый ответ в формате модели (answer, score, explanation, evidence, ...) или None,
если однозначно решить не может — тогда критерий оценивает модель.

Правила работают по смыслу на RU, KZ и EN и опираются на системные события чата
(«Личность клиента подтверждена», «Оператор закрыл чат», «Соединение прервано»).
"""
from __future__ import annotations

import re
from typing import Callable, Optional

I = re.I  # noqa: E741

# ---------- язык клиента ----------
KZ_LETTERS = re.compile(r"[әғқңөұүһі]", I)
CYR = re.compile(r"[а-яё]", I)
LAT = re.compile(r"[a-z]", I)


def client_language(dialog: dict) -> str:
    text = " ".join(m.get("text") or "" for m in dialog["transcript"] if m.get("speaker") == "client")
    if KZ_LETTERS.search(text):
        return "KZ"
    if len(CYR.findall(text)) >= len(LAT.findall(text)) and CYR.search(text):
        return "RU"
    if LAT.search(text):
        return "EN"
    return (dialog.get("language") or "RU").upper()


# ---------- словари ----------
GREET = {
    "RU": r"здравствуй|добрый (день|вечер)|доброе утро|приветствую|\bпривет",
    "KZ": r"сәлеметсіз|\bсәлем|қайырлы (күн|таң|кеш)|армысыз",
    "EN": r"\bhi\b|\bhello\b|good (morning|afternoon|evening)|\bhey\b",
}
# Ключевые слова — без учёта регистра, имя — с заглавной буквы (ищется без флага re.I).
INTRO = {
    "RU": r"(?i:меня зовут)\s+\S+|(?:^|[\s,.!])(?i:я)\s+[А-ЯЁ][а-яё]+|(?i:на связи)\s+[А-ЯЁ]",
    "KZ": r"(?i:менің атым)|(?:^|[\s,.!])(?i:мен)\s+[А-ЯӘҒҚҢӨҰҮҺІЁ]\w+",
    "EN": r"(?i:\bi['’]?m|\bi am)\s+[A-Z][a-z]+|(?i:my name is)|(?i:this is)\s+[A-Z][a-z]+",
}
FAREWELL = (r"до свидания|всего (доброго|хорошего)|хорошего (дня|вечера)|до встречи|"
            r"сау болыңыз|күніңіз сәтті|кешіңіз сәтті|қош\b|"
            r"goodbye|\bbye\b|have a (good|nice|great)|take care|"
            r"(благодар\w*|спасибо)( вас)? за обращение|thanks? (you )?for (contacting|reaching)")
CLIENT_ENDED = r"клиент завершил разговор|customer ended the call|клиент қоңырауды аяқтады"
ABRUPT = (r"прерван|покинул|отключ|байланыс үзілді|чаттан шықты|"
          r"connection\b.*\blost|left the chat|disconnected")
OP_CLOSED = r"оператор закрыл|оператор чатты жапты|agent closed"
VERIFIED = r"личность клиента подтверждена|жеке басы расталды|identity verified"
# Оператор сообщает данные конкретного счёта
DISCLOSURE = (r"на вашем сч[её]те|я посмотрел|(?<!не )вижу.{0,40}(сч[её]т|ограничен|баланс)|"
              r"шотыңызда.{0,60}көріп|көріп тұрмын|"
              r"I can see.{0,60}(account|restriction|balance)|on your account (there is|I see)")
# Попытка проверки личности в звонке (распознанная речь)
CALL_VERIFY = (r"кодов\w* слов|дат\w* рождения|\bиин\b|шестизначн\w* код|код.{0,30}(пришёл|пришел|смс|sms)|"
               r"по которому регистрац|для идентификац|(код|кот) на ваш телефон")

MONEY_TO_OPERATOR = (r"(мне|мою)\s+на\s+карт|деньги мне|переведите мне|скиньте.{0,20}мне|сам их вложу|"
                     r"лично предлагаю|через компанию оформлять не надо|"
                     r"маған аудар|менің картам|менің карт|"
                     r"send (me )?(the )?money|transfer .{0,30}to me|my (personal )?card|outside the company")
PERSONAL_NUMBER = r"личн\w* номер|персональн\w* номер|personal (phone )?number|жеке нөмір"
PAID_PRIVATE = r"за плату|платн|частн|ақылы|жеке кеңес|i charge|paid|private (advice|consult)"
INSULT = (r"читать умеете|идиот|дурак|тупо[йг]|бестолков|"
          r"can['’]?t you read|are you stupid|idiot|"
          r"ақымақ|оқи алмайсыз ба|миың")
CONFIDENTIAL = r"друго(го|му) клиент|another (client|customer)['’]s|басқа клиенттің"


def _word(text: str, m: re.Match) -> str:
    """Совпадение, расширенное до конца слова: «Здравствуй» → «Здравствуйте»."""
    end = m.end()
    while end < len(text) and (text[end].isalnum() or text[end] in "'’"):
        end += 1
    return text[m.start():end]


def _ops(dialog):
    return [(i, m.get("text") or "") for i, m in enumerate(dialog["transcript"], start=1) if m.get("speaker") == "operator"]


def _res(answer, explanation, evidence=(), rng=None, na_reason="", score=0):
    return {"answer": answer, "score": score, "explanation": explanation, "evidence": list(evidence),
            "checked_from": rng[0] if rng else 0, "checked_to": rng[1] if rng else 0, "na_reason": na_reason}


# ---------- правила ----------

def rule_greeting(dialog: dict, crit: dict) -> Optional[dict]:
    ops = _ops(dialog)
    if not ops:
        return None
    lang = client_language(dialog)
    is_call = dialog.get("channel") == "voice call"
    window = ops[:2] if is_call else ops[:1]  # в звонке распознавание может разбить первую фразу
    text = " ".join(t for _, t in window)
    first = window[0][0]
    greet = re.search(GREET[lang], text, I)
    other = [lg for lg in GREET if lg != lang and re.search(GREET[lg], text, I)]
    intro = re.search(INTRO[lang], text) or (is_call and re.search(r"\[Имя скрыто\]", text))
    rng = (window[0][0], window[-1][0])
    if greet and intro:
        return _res("yes", f"Первая реплика оператора #{first} содержит приветствие («{_word(text, greet)}») и представление на языке клиента ({lang}).",
                    [first], rng)
    if not greet and other:
        return _res("no", f"Оператор поздоровался не на языке клиента ({lang}). Основание — первая реплика #{first}.", [first], rng)
    missing = " и ".join(x for x, ok in (("приветствия", greet), ("представления", intro)) if not ok)
    return _res("no", f"В первом ответе оператора #{first} нет {missing}.", [first], rng)


def rule_closing(dialog: dict, crit: dict) -> Optional[dict]:
    tr = dialog["transcript"]
    ops = _ops(dialog)
    if not ops:
        return _res("na", "Оператор не подключился к диалогу.", na_reason=crit.get("na_condition") or "Чат оборвался.") if crit.get("na_allowed") else None
    tail_ops = ops[-3:] if dialog.get("channel") == "voice call" else ops[-1:]
    for i, t in reversed(tail_ops):
        m = re.search(FAREWELL, t, I)
        if m:
            return _res("yes", f"Оператор попрощался («{_word(t, m)}») в реплике #{i}.", [i], (i, len(tr)))
    last_op = ops[-1][0]
    after = [(i, m) for i, m in enumerate(tr, start=1) if i > last_op]
    abrupt = next((i for i, m in after if m.get("speaker") == "system" and re.search(ABRUPT, m.get("text") or "", I)), None)
    closed = next((i for i, m in after if m.get("speaker") == "system" and re.search(OP_CLOSED, m.get("text") or "", I)), None)
    if dialog.get("channel") == "voice call" and not closed and crit.get("na_allowed"):
        # в распознанных звонках порядок событий в конце бывает перепутан — смотрим последние 3 строки
        tail = [(i, m) for i, m in enumerate(tr, start=1)][-3:]
        ended = next((i for i, m in tail if m.get("speaker") == "system" and re.search(CLIENT_ENDED, m.get("text") or "", I)), None)
        if ended:
            return _res("na", f"Клиент завершил звонок (#{ended}) до прощания оператора.", [last_op, ended],
                        na_reason=f"Разговор завершён клиентом (#{ended}) до возможности завершить его оператору.")
    if abrupt and not closed and crit.get("na_allowed"):
        return _res("na", f"Чат оборвался (системное событие #{abrupt}) до того, как оператор мог завершить диалог.",
                    [abrupt], na_reason=f"Чат оборвался: «{tr[abrupt - 1].get('text')}» (#{abrupt}).")
    if closed:
        return _res("no", f"Оператор закрыл чат без прощания. Основание — последняя реплика оператора #{last_op} и событие #{closed}.",
                    [last_op, closed], (last_op, closed))
    return _res("no", f"В последней реплике оператора #{last_op} нет прощания, хотя диалог не обрывался.",
                [last_op], (last_op, len(tr)))


def rule_identification(dialog: dict, crit: dict) -> Optional[dict]:
    tr = dialog["transcript"]
    verified = next((i for i, m in enumerate(tr, start=1)
                     if m.get("speaker") == "system" and re.search(VERIFIED, m.get("text") or "", I)), None)
    disclosed = next((i for i, t in _ops(dialog) if re.search(DISCLOSURE, t, I)), None)
    if dialog.get("channel") == "voice call" and not verified:
        v = next((i for i, t in _ops(dialog) if re.search(CALL_VERIFY, t, I)), None)
        if v:
            return _res("yes", f"Оператор проводит проверку личности (реплика #{v}).", [v])
        if disclosed:
            return None  # распознанная речь шумная — спорный случай оценивает модель
    if disclosed and (not verified or verified > disclosed):
        return _res("no", f"Оператор сообщил данные по счёту клиента (#{disclosed}) без подтверждения личности"
                          + (f" (подтверждение только в #{verified})." if verified else "."),
                    [disclosed], (1, disclosed))
    if verified:
        ev = [verified] + ([disclosed] if disclosed else [])
        return _res("yes", f"Личность подтверждена системой (#{verified}) до работы с данными счёта.", ev)
    if crit.get("na_allowed"):
        return _res("na", "Оператор не раскрывал данные счёта и не выполнял защищённых действий.",
                    na_reason=crit.get("na_condition") or "Запрос не требовал идентификации.")
    return None


def rule_gross(dialog: dict, crit: dict) -> Optional[dict]:
    ops = _ops(dialog)
    found = []
    for i, t in ops:
        if re.search(MONEY_TO_OPERATOR, t, I):
            found.append((i, "предложение перевести деньги оператору лично (в обход компании)"))
        elif re.search(PERSONAL_NUMBER, t, I) and re.search(PAID_PRIVATE, t, I):
            found.append((i, "просьба дать личный номер для платной частной консультации"))
        elif re.search(INSULT, t, I):
            found.append((i, "оскорбление клиента"))
        elif re.search(CONFIDENTIAL, t, I):
            found.append((i, "раскрытие конфиденциальной информации"))
    if found:
        kinds = "; ".join(sorted({k for _, k in found}))
        ids = [i for i, _ in found]
        return _res("yes", f"Грубая ошибка: {kinds}. Основание — " + ", ".join(f"#{i}" for i in ids) + ".", ids[:3])
    rng = (ops[0][0], ops[-1][0]) if ops else None
    return _res("no", "Оскорблений, предложений услуг в обход компании и раскрытия конфиденциальной информации не выявлено"
                      + (f" (проверены реплики оператора #{rng[0]}–#{rng[1]})." if rng else "."), (), rng)


GENERIC_HELP = (r"чем (могу|можем) (вам )?помочь|с чем помочь|какой у вас вопрос|"
                r"how (can|may) I help|what can I do for you|"
                r"қалай көмектесе аламын|сізге қандай көмек")


def rule_need(dialog: dict, crit: dict) -> Optional[dict]:
    """Выявление потребности: однозначные случаи — правилом, вопрос «хватало ли данных» — модели."""
    tr = dialog["transcript"]
    ops = _ops(dialog)
    if not ops:
        return None
    first_i, first = ops[0]
    client_before = [m for m in tr[:first_i - 1] if m.get("speaker") == "client" and len((m.get("text") or "").split()) >= 3]
    if re.search(GENERIC_HELP, first, I) and client_before:
        return _res("no", f"Клиент уже описал запрос, но оператор в #{first_i} задал шаблонный вопрос, не учтя известные детали.",
                    [first_i], (1, first_i))
    questions = [q for q in re.split(r"(?<=[.!?])\s+", first) if q.strip().endswith("?") and not re.search(GENERIC_HELP, q, I)]
    if questions:
        return _res("yes", f"Оператор уточнил недостающее до ответа: «{questions[0].strip()}» (#{first_i}).", [first_i])
    return None


def rule_consultation(dialog: dict, crit: dict) -> Optional[dict]:
    """N/A консультации — только если клиент так и не сформулировал запрос.
    Если запрос есть, вердикт не выносится, но модели запрещается N/A (ограничение no_na)."""
    req = next((i for i, m in enumerate(dialog["transcript"], start=1)
                if m.get("speaker") == "client" and len((m.get("text") or "").split()) >= 3), None)
    if req is None and crit.get("na_allowed"):
        return _res("na", "Клиент не сформулировал запрос до окончания чата.",
                    na_reason=crit.get("na_condition") or "Запрос не сформулирован.")
    return {"constraint": "no_na", "request_at": req}


def rule_empathy(dialog: dict, crit: dict) -> Optional[dict]:
    """Оскорбление клиента однозначно означает «Нет»; остальное решает модель."""
    hit = next((i for i, t in _ops(dialog) if re.search(INSULT, t, I)), None)
    if hit:
        return _res("no", f"Оператор невежлив: реплика #{hit} содержит оскорбление клиента.", [hit], (hit, hit))
    return None


RULES: dict[str, tuple[str, Callable[[dict, dict], Optional[dict]]]] = {
    "greeting": ("Приветствие и представление", rule_greeting),
    "closing": ("Прощание / обрыв чата", rule_closing),
    "identification": ("Идентификация по системным событиям", rule_identification),
    "gross": ("Грубые ошибки по шаблонам", rule_gross),
    "empathy": ("Эмпатия: оскорбление = «Нет», иначе модель", rule_empathy),
    "consultation": ("Консультация: N/A только без запроса клиента, иначе модель", rule_consultation),
    "need": ("Потребность: уточняющий вопрос = «Да», шаблонный вопрос = «Нет», иначе модель", rule_need),
}


def is_verdict(r: Optional[dict]) -> bool:
    """Правило может вернуть готовый вердикт или только ограничение для модели ({"constraint": ...})."""
    return r is not None and "constraint" not in r


def apply_rule(dialog: dict, crit: dict) -> Optional[dict]:
    name = crit.get("auto")
    if not name or name not in RULES:
        return None
    r = RULES[name][1](dialog, crit)
    if is_verdict(r):
        r["criterion_id"] = crit["id"]
        r["source"] = "rule"
    return r
