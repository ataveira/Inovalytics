"""Leitor XML seguro para o subconjunto Pascal VOC (F1-08a). Sem semântica de VOC.

Usa só `xml.parsers.expat` (biblioteca padrão), configurado para recusar tudo o que não é
necessário. Não monta árvore de biblioteca (nada de `ElementTree`, `minidom`, `sax`, `lxml` ou
`defusedxml`) e não usa expressão regular. A defesa principal é recusar DOCTYPE: sem ele só
existem as cinco entidades predefinidas e as referências numéricas, o que elimina por construção
XXE (arquivo e rede), entidade paramétrica, DTD externo, billion laughs e quadratic blowup, sem
depender da versão do expat. Outros recusados: instruções de processamento, CDATA e qualquer
codificação declarada além de UTF-8/US-ASCII. XInclude não é processado (`xi:include` é só um
elemento).

Contrato: `read_xml(text, limits) -> XmlDoc` é pura e determinística. Qualquer defeito levanta
`XmlRejectedError(code, line)` com código estável e mensagem fixa (nunca repete o conteúdo); nenhuma
outra exceção escapa. Os limites de tamanho e de `<` são conferidos antes de criar o parser; os
estruturais (profundidade, elementos, atributos, texto) dentro dos handlers, que abortam sem
percorrer o resto. O documento é entregue ao expat numa única chamada `Parse(dados, True)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from xml.parsers import expat

__all__ = ["XmlDoc", "XmlLimits", "XmlNode", "XmlRejectedError", "read_xml"]

ROOT_NAME = "annotation"
_XML_WS = " \t\r\n"
_BUFFER_SIZE = 8192
_ALLOWED_ENCODINGS = frozenset({"utf-8", "us-ascii"})
_BOM = "﻿"

_MESSAGES = {
    "doctype_proibido": "declaração DOCTYPE (DTD) não é permitida",
    "instrucao_proibida": "instrução de processamento não é permitida",
    "cdata_proibido": "seção CDATA não é permitida",
    "entidade_proibida": "entidade não predefinida não é permitida",
    "encoding_nao_suportado": "codificação declarada não suportada (use UTF-8)",
    "texto_invalido": "o texto não é codificável em UTF-8",
    "raiz_invalida": "o elemento raiz deve ser 'annotation'",
    "xml_malformado": "XML malformado",
}

# Códigos do expat -> código estável. Tudo o que não está aqui vira `xml_malformado`.
_ENTITY_ERRORS = (
    "XML_ERROR_UNDEFINED_ENTITY",
    "XML_ERROR_RECURSIVE_ENTITY_REF",
    "XML_ERROR_ASYNC_ENTITY",
    "XML_ERROR_BINARY_ENTITY_REF",
    "XML_ERROR_ATTRIBUTE_EXTERNAL_ENTITY_REF",
    "XML_ERROR_EXTERNAL_ENTITY_HANDLING",
    "XML_ERROR_ENTITY_DECLARED_IN_PE",
    "XML_ERROR_PARAM_ENTITY_REF",
    "XML_ERROR_AMPLIFICATION_LIMIT_BREACH",
)
_ENCODING_ERRORS = ("XML_ERROR_UNKNOWN_ENCODING", "XML_ERROR_INCORRECT_ENCODING")


def _build_code_map() -> dict[int, str]:
    """Número de erro do expat -> código estável (constantes de `expat.errors` são mensagens)."""
    out: dict[int, str] = {}
    for names, code in (
        (_ENTITY_ERRORS, "entidade_proibida"),
        (_ENCODING_ERRORS, "encoding_nao_suportado"),
    ):
        for n in names:
            message = getattr(expat.errors, n, None)
            number = expat.errors.codes.get(message) if isinstance(message, str) else None
            if number is not None:
                out[number] = code
    return out


_ERROR_CODE_MAP = _build_code_map()


@dataclass(frozen=True)
class XmlLimits:
    """Limites do leitor. `max_markup` é o teto de caracteres `<` por arquivo (um elemento tem 1 ou
    2; comentários e instruções também contam), conferido em C antes de parsear."""

    max_bytes: int = 256 * 1024
    max_markup: int = 60_000
    max_depth: int = 16
    max_elements: int = 20_000
    max_attributes: int = 32
    max_text_chars: int = 1024


class XmlRejectedError(Exception):
    """Documento recusado: `code` estável, `message` fixa, `line` 1-based (None se não há)."""

    def __init__(self, code: str, line: int | None = None, limit: str | None = None) -> None:
        self.code = code
        self.line = line
        self.limit = limit
        self.message = (
            f"limite excedido: {limit}"
            if limit
            else _MESSAGES.get(code, _MESSAGES["xml_malformado"])
        )
        super().__init__(self.message)


@dataclass(frozen=True, slots=True)
class XmlNode:
    """Elemento em ordem de documento. `text` é o texto direto aparado (vazio se não há)."""

    name: str
    depth: int  # a raiz é 1
    parent: int  # índice do pai em `XmlDoc.nodes`; -1 na raiz
    line: int  # 1-based, da marca de abertura
    text: str


@dataclass(frozen=True)
class XmlDoc:
    nodes: tuple[XmlNode, ...]

    def path(self, index: int) -> tuple[str, ...]:
        """Nomes dos elementos da raiz até o nó `index`."""
        names: list[str] = []
        i = index
        while i >= 0:
            names.append(self.nodes[i].name)
            i = self.nodes[i].parent
        names.reverse()
        return tuple(names)


class _Abort(Exception):
    """Levantada pelos handlers para interromper o expat; convertida em `XmlRejectedError`."""

    def __init__(self, code: str, line: int, limit: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.line = line
        self.limit = limit


class _Collector:
    """Estado dos handlers de um parse. Só listas paralelas e contadores (memória limitada)."""

    def __init__(self, parser: Any, lim: XmlLimits) -> None:
        self.p = parser
        self.lim = lim
        self.names: list[str] = []
        self.parents: list[int] = []
        self.depths: list[int] = []
        self.lines: list[int] = []
        self.texts: list[str] = []
        self.parts: dict[int, list[str]] = {}
        self.raw_len: dict[int, int] = {}
        self.stack: list[int] = []
        self.xml_decl_seen = False

    def forbid(self, code: str) -> Any:
        def handler(*_: Any) -> None:
            raise _Abort(code, self.p.CurrentLineNumber)

        return handler

    def xml_decl(self, version: str, encoding: str | None, standalone: int) -> None:
        if encoding is not None and encoding.lower() not in _ALLOWED_ENCODINGS:
            raise _Abort("encoding_nao_suportado", self.p.CurrentLineNumber)

    def start(self, name: str, attrs: Any) -> None:
        lim = self.lim
        line = self.p.CurrentLineNumber
        depth = len(self.stack) + 1
        if depth > lim.max_depth:
            raise _Abort("limite_excedido", line, "max_depth")
        if len(self.names) >= lim.max_elements:
            raise _Abort("limite_excedido", line, "max_elements")
        if len(attrs) > lim.max_attributes:
            raise _Abort("limite_excedido", line, "max_attributes")
        if not self.stack and name != ROOT_NAME:
            raise _Abort("raiz_invalida", line)
        idx = len(self.names)
        self.names.append(name)
        self.parents.append(self.stack[-1] if self.stack else -1)
        self.depths.append(depth)
        self.lines.append(line)
        self.texts.append("")
        self.stack.append(idx)

    def chars(self, data: str) -> None:
        if not self.stack:
            return
        top = self.stack[-1]
        has_content = top in self.parts
        if not has_content and not data.strip(_XML_WS):
            return  # indentação antes de qualquer conteúdo: nunca acumulada
        n = self.raw_len.get(top, 0) + len(data)
        # Teto bruto (2x o limite): espaços ao redor do texto contam, mas não sem limite.
        if n > 2 * self.lim.max_text_chars:
            raise _Abort("limite_excedido", self.lines[top], "max_text_chars")
        self.raw_len[top] = n
        self.parts.setdefault(top, []).append(data)

    def end(self, name: str) -> None:
        idx = self.stack.pop()
        pieces = self.parts.pop(idx, None)
        if pieces is not None:
            self.raw_len.pop(idx, None)
            text = "".join(pieces).strip(_XML_WS)
            if len(text) > self.lim.max_text_chars:
                raise _Abort("limite_excedido", self.lines[idx], "max_text_chars")
            self.texts[idx] = text


def _make_parser(col: _Collector) -> Any:
    # A declaração do documento não escolhe a codificação: o conteúdo é sempre UTF-8.
    p = expat.ParserCreate(encoding="utf-8")
    col.p = p
    p.buffer_text = True
    p.buffer_size = _BUFFER_SIZE
    p.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    p.StartDoctypeDeclHandler = col.forbid("doctype_proibido")
    p.EntityDeclHandler = col.forbid("doctype_proibido")
    p.NotationDeclHandler = col.forbid("doctype_proibido")
    p.ElementDeclHandler = col.forbid("doctype_proibido")
    p.AttlistDeclHandler = col.forbid("doctype_proibido")
    p.ExternalEntityRefHandler = col.forbid("entidade_proibida")
    p.SkippedEntityHandler = col.forbid("entidade_proibida")
    p.ProcessingInstructionHandler = col.forbid("instrucao_proibida")
    p.StartCdataSectionHandler = col.forbid("cdata_proibido")
    p.XmlDeclHandler = col.xml_decl
    p.StartElementHandler = col.start
    p.EndElementHandler = col.end
    p.CharacterDataHandler = col.chars
    return p


def _expat_code(exc: Any) -> str:
    return _ERROR_CODE_MAP.get(getattr(exc, "code", -1), "xml_malformado")


def read_xml(text: str, limits: XmlLimits | None = None) -> XmlDoc:
    """Lê `text` (um documento `annotation`) ou levanta `XmlRejectedError`."""
    lim = limits or XmlLimits()
    try:
        return _read(text, lim)
    except XmlRejectedError:
        raise
    except Exception:  # defesa final: nenhuma outra exceção escapa
        raise XmlRejectedError("xml_malformado") from None


def _read(text: str, lim: XmlLimits) -> XmlDoc:
    if not isinstance(text, str):
        raise XmlRejectedError("texto_invalido")
    # Um caractere ocupa ao menos 1 byte: acima disso o limite em bytes já está excedido.
    if len(text) > lim.max_bytes:
        raise XmlRejectedError("limite_excedido", limit="max_xml_bytes")
    if text.startswith(_BOM):
        text = text[1:]
        if text.startswith(_BOM):  # BOM duplicado não é aceito
            raise XmlRejectedError("xml_malformado", 1)
    try:
        data = text.encode("utf-8")  # estrito: substituto isolado falha
    except UnicodeEncodeError:
        raise XmlRejectedError("texto_invalido") from None
    if len(data) > lim.max_bytes:
        raise XmlRejectedError("limite_excedido", limit="max_xml_bytes")
    if data.count(b"<") > lim.max_markup:  # varredura em C; limite superior de elementos
        raise XmlRejectedError("limite_excedido", limit="max_markup")

    col = _Collector(None, lim)
    parser = _make_parser(col)
    try:
        parser.Parse(data, True)  # uma única chamada (evita reparse quadrático)
    except _Abort as a:
        raise XmlRejectedError(a.code, a.line, a.limit) from None
    except expat.ExpatError as e:
        raise XmlRejectedError(_expat_code(e), getattr(e, "lineno", None)) from None
    return XmlDoc(
        tuple(
            XmlNode(col.names[i], col.depths[i], col.parents[i], col.lines[i], col.texts[i])
            for i in range(len(col.names))
        )
    )
