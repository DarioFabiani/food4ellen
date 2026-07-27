"""Helper condivisi per scriptare le risposte del modello nei test.

Il loop agentico si testa sostituendo `claude_client._completion` e passandogli
una sequenza di risposte finte: queste factory le costruiscono con la stessa
forma degli oggetti restituiti da LiteLLM (attributi, non chiavi) — un
`ModelResponse` con `.choices[0].message` e `.choices[0].finish_reason`.
"""
import json
from types import SimpleNamespace


def tool_call(nome: str, argomenti: dict, tool_id: str = "tu_1") -> SimpleNamespace:
    return SimpleNamespace(
        id=tool_id,
        function=SimpleNamespace(name=nome, arguments=json.dumps(argomenti)),
    )


def messaggio(
    testo: str | None = None,
    tool_calls: list | None = None,
    reasoning_content: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(content=testo, tool_calls=tool_calls, reasoning_content=reasoning_content)


def risposta(msg: SimpleNamespace, finish_reason: str = "stop") -> SimpleNamespace:
    scelta = SimpleNamespace(message=msg, finish_reason=finish_reason)
    return SimpleNamespace(choices=[scelta])


def risposta_testo(testo: str, reasoning_content: str | None = None) -> SimpleNamespace:
    return risposta(messaggio(testo, reasoning_content=reasoning_content))


def risposta_tool_use(nome: str, argomenti: dict, tool_id: str = "tu_1") -> SimpleNamespace:
    return risposta(messaggio(tool_calls=[tool_call(nome, argomenti, tool_id)]), finish_reason="tool_calls")


def risposta_troncata(reasoning_content: str = "sto ragionando") -> SimpleNamespace:
    return risposta(messaggio(reasoning_content=reasoning_content), finish_reason="length")


def risposta_vuota() -> SimpleNamespace:
    """Nessun testo e nessun tool_call: caso limite per i test sui retry."""
    return risposta(messaggio())
