import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from rules import apply_rule, client_language  # noqa: E402

CRIT = {
    "greeting": {"id": "c01", "auto": "greeting", "type": "binary", "na_allowed": False},
    "identification": {"id": "c03", "auto": "identification", "type": "binary", "na_allowed": True,
                       "na_condition": "Запрос не требовал идентификации."},
    "closing": {"id": "c07", "auto": "closing", "type": "binary", "na_allowed": True,
                "na_condition": "Чат оборвался."},
    "gross": {"id": "c08", "auto": "gross", "type": "flag", "na_allowed": False},
}


def dlg(*lines, channel="agent chat"):
    return {"channel": channel, "language": "", "transcript": [{"timestamp": "00:00", "speaker": s, "text": t} for s, t in lines]}


def run(rule, d):
    return apply_rule(d, CRIT[rule])


# ---------- язык ----------
def test_language():
    assert client_language(dlg(("client", "Тарифты қайдан көремін?"))) == "KZ"
    assert client_language(dlg(("client", "Где тариф?"))) == "RU"
    assert client_language(dlg(("client", "Where is my plan?"))) == "EN"


# ---------- приветствие ----------
def test_greeting_yes_all_languages():
    assert run("greeting", dlg(("client", "Где тариф?"), ("operator", "Здравствуйте! Меня зовут Алина. Откройте кабинет.")))["answer"] == "yes"
    assert run("greeting", dlg(("client", "Тариф қайда?"), ("operator", "Сәлеметсіз бе, мен Дана. Кабинетті ашыңыз.")))["answer"] == "yes"
    assert run("greeting", dlg(("client", "Where?"), ("operator", "Hi, I'm Adrian. Open Quotes.")))["answer"] == "yes"


def test_greeting_no_intro_or_no_greeting():
    assert run("greeting", dlg(("client", "Где кабинет?"), ("operator", "Привет, где именно ищете?")))["answer"] == "no"
    assert run("greeting", dlg(("client", "Can't log in"), ("operator", "Open settings then log in again.")))["answer"] == "no"


def test_greeting_wrong_language():
    r = run("greeting", dlg(("client", "Тариф қайда көремін?"), ("operator", "Здравствуйте, меня зовут Алина.")))
    assert r["answer"] == "no" and "не на языке клиента" in r["explanation"]


def test_greeting_not_fooled_by_lowercase_verb():
    # «я посмотрел» — не представление
    assert run("greeting", dlg(("client", "Где деньги?"), ("operator", "Здравствуйте, я посмотрел ваш вопрос.")))["answer"] == "no"


# ---------- завершение ----------
def test_closing_farewell():
    assert run("closing", dlg(("client", "Спасибо"), ("operator", "Пожалуйста. Хорошего вечера!")))["answer"] == "yes"
    assert run("closing", dlg(("client", "Рақмет"), ("operator", "Оқасы жоқ. Сау болыңыз!")))["answer"] == "yes"


def test_closing_operator_closed_without_farewell():
    r = run("closing", dlg(("client", "Почему?"), ("operator", "Тогда не знаю."), ("system", "Оператор закрыл чат.")))
    assert r["answer"] == "no" and r["evidence"] == [2, 3]


def test_closing_abrupt_is_na():
    r = run("closing", dlg(("client", "Вопрос"), ("operator", "Здравствуйте, я Илья."), ("system", "Соединение с клиентом прервано.")))
    assert r["answer"] == "na"


def test_closing_no_farewell_client_last():
    assert run("closing", dlg(("operator", "Откройте сайт."), ("client", "Ашылды, рақмет.")))["answer"] == "no"


def test_closing_call_client_hung_up_is_na():
    d = dlg(("operator", "Есть ли ещё вопросы?"), ("system", "Клиент завершил разговор."), channel="voice call")
    assert run("closing", d)["answer"] == "na"


# ---------- идентификация ----------
def test_identification_verified():
    d = dlg(("operator", "Нужно подтвердить личность."), ("system", "Личность клиента подтверждена."), ("operator", "Вижу ограничение на счёте."))
    assert run("identification", d)["answer"] == "yes"


def test_identification_disclosure_without_verification():
    assert run("identification", dlg(("operator", "Я посмотрел: на вашем счёте есть ограничение на вывод.")))["answer"] == "no"
    assert run("identification", dlg(("operator", "Hi, I'm Tyler. I can see a withdrawal restriction on your account.")))["answer"] == "no"
    assert run("identification", dlg(("operator", "Сіздің шотыңызда шектеу қойылғанын көріп тұрмын.")))["answer"] == "no"


def test_identification_negation_is_not_disclosure():
    d = dlg(("operator", "Причину я не вижу. Если нужно проверить именно ваш счёт, понадобится подтверждение."))
    assert run("identification", d)["answer"] == "na"


def test_identification_general_question_na():
    assert run("identification", dlg(("operator", "Тариф указан в кабинете.")))["answer"] == "na"


# ---------- грубые ошибки ----------
def test_gross_money_to_operator():
    r = run("gross", dlg(("operator", "Здравствуйте, я Кирилл. Скиньте деньги мне на карту, я сам их вложу.")))
    assert r["answer"] == "yes" and r["evidence"] == [1]


def test_gross_private_paid_consultation_all_languages():
    assert run("gross", dlg(("operator", "Можете дать личный номер? За плату объясню.")))["answer"] == "yes"
    assert run("gross", dlg(("operator", "Send me your personal number. I charge for private advice.")))["answer"] == "yes"
    assert run("gross", dlg(("operator", "Жеке нөміріңізді қалдырсаңыз, ақылы түрде айтып беремін.")))["answer"] == "yes"


def test_gross_personal_number_without_payment_is_not_flag():
    assert run("gross", dlg(("operator", "Подскажите номер телефона, по которому регистрировались?")))["answer"] == "no"


def test_gross_insult():
    assert run("gross", dlg(("operator", "Вы вообще читать умеете? Бот всё написал.")))["answer"] == "yes"


def test_gross_clean():
    assert run("gross", dlg(("operator", "Здравствуйте! Откройте кабинет. Хорошего дня!")))["answer"] == "no"


def test_no_rule_returns_none():
    assert apply_rule(dlg(("operator", "x")), {"id": "c02", "auto": "", "type": "binary"}) is None


def test_empathy_insult_is_no_else_model():
    crit = {"id": "c05", "auto": "empathy", "type": "binary"}
    assert apply_rule(dlg(("operator", "Вы вообще читать умеете?")), crit)["answer"] == "no"
    assert apply_rule(dlg(("operator", "Понимаю ваше беспокойство.")), crit) is None


def test_consultation_constraint_and_na():
    crit = {"id": "c04", "auto": "consultation", "type": "scale", "na_allowed": True, "na_condition": "x"}
    r = apply_rule(dlg(("client", "Где посмотреть мой тариф?"), ("operator", "В кабинете.")), crit)
    assert r == {"constraint": "no_na", "request_at": 1}
    assert apply_rule(dlg(("client", "Да"), ("operator", "Здравствуйте")), crit)["answer"] == "na"


def test_need_rule():
    crit = {"id": "c02", "auto": "need", "type": "binary", "na_allowed": True}
    assert apply_rule(dlg(("client", "Приказы отклоняются, где причина?"), ("operator", "Здравствуйте, я Олег. Какая бумага?")), crit)["answer"] == "yes"
    r = apply_rule(dlg(("client", "Где скачать реквизиты пополнения?"), ("operator", "Добрый день, я Виктор. Чем могу помочь?")), crit)
    assert r["answer"] == "no"
    assert apply_rule(dlg(("client", "Где тариф?"), ("operator", "Откройте кабинет.")), crit) is None
