"""Helper condivisi per scriptare le risposte del modello nei test.

Il loop agentico si testa sostituendo `claude_client._get_client` e passando a
`messages.create` una sequenza di risposte finte: queste factory le costruiscono
con la stessa forma degli oggetti dell'SDK (attributi, non chiavi).
"""
from types import SimpleNamespace


def blocco_testo(testo: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=testo)


def blocco_thinking(pensiero: str = "sto ragionando") -> SimpleNamespace:
    return SimpleNamespace(type="thinking", thinking=pensiero, signature="firma")


def blocco_tool_use(nome: str, argomenti: dict, tool_id: str = "tu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=nome, input=argomenti)


def risposta(*blocchi, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(content=list(blocchi), stop_reason=stop_reason)


def risposta_testo(testo: str) -> SimpleNamespace:
    return risposta(blocco_testo(testo))


def risposta_tool_use(nome: str, argomenti: dict, tool_id: str = "tu_1") -> SimpleNamespace:
    return risposta(blocco_tool_use(nome, argomenti, tool_id), stop_reason="tool_use")
